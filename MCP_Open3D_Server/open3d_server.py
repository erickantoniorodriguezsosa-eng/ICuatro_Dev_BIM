"""
open3d_server.py
Servidor MCP (FastMCP, transporte stdio) para procesar nubes de puntos con Open3D
y exportar terreno a IFC (IFC2X3, IFC4, IFC4X3).

IMPORTANTE: con stdio, stdout es el canal del protocolo. Nunca usar print() aquí;
para mensajes usar logging (va a stderr).
"""
import logging
import os
import sys
from typing import Optional

import laspy
import numpy as np
import open3d as o3d
from mcp.server.fastmcp import FastMCP
from scipy.spatial import Delaunay

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ifc_export import SCHEMAS, export_terrain_ifc  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("open3d_server")
o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)

mcp = FastMCP("open3d-pointcloud")

ASPRS_UNCLASSIFIED, ASPRS_GROUND = 1, 2


class State:
    """Estado global: una nube, su clasificación (alineada punto a punto) y una malla."""
    cloud: Optional[o3d.geometry.PointCloud] = None
    classes: Optional[np.ndarray] = None  # (N,) uint8 códigos ASPRS
    mesh: Optional[o3d.geometry.TriangleMesh] = None
    source: str = ""
    epsg: Optional[int] = None


class ToolError(Exception):
    pass


def tool(fn):
    """Convierte ToolError/excepciones en texto de error (el servidor nunca se cae)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolError as e:
            return f"Error: {e}"
        except Exception as e:
            log.exception("fallo en %s", fn.__name__)
            return f"Error inesperado: {type(e).__name__}: {e}"
    return mcp.tool()(wrapper)


def _cloud() -> o3d.geometry.PointCloud:
    if State.cloud is None:
        raise ToolError("No hay nube cargada. Usa load_point_cloud primero.")
    return State.cloud


def _mesh() -> o3d.geometry.TriangleMesh:
    if State.mesh is None or len(State.mesh.triangles) == 0:
        raise ToolError("No hay malla. Usa generate_terrain_mesh primero.")
    return State.mesh


def _set_cloud(pcd, classes):
    State.cloud, State.classes = pcd, classes.astype(np.uint8)
    State.mesh = None  # la malla anterior ya no corresponde a la nube


def _load_las(path):
    las = laspy.read(path)
    xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
    keep = np.isfinite(xyz).all(axis=1)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz[keep])  # float64: sin pérdida en UTM
    dims = set(las.point_format.dimension_names)
    if {"red", "green", "blue"} <= dims:
        rgb = np.column_stack((las.red, las.green, las.blue)).astype(np.float64)[keep]
        if rgb.size and rgb.max() > 0:
            rgb /= 65535.0 if rgb.max() > 255 else 255.0  # 16 bits, o 8 bits mal guardados
            pcd.colors = o3d.utility.Vector3dVector(rgb)
    classes = np.asarray(las.classification, dtype=np.uint8)[keep]
    return pcd, classes


# ------------------------------------------------------------------ nube
@tool
def load_point_cloud(filepath: str) -> str:
    """Carga .las, .laz o .ply en memoria (reemplaza nube y malla anteriores)."""
    path = os.path.expanduser(filepath)
    if not os.path.isfile(path):
        raise ToolError(f"no existe el archivo '{path}'.")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".las", ".laz"):
        pcd, classes = _load_las(path)
    elif ext == ".ply":
        pcd = o3d.io.read_point_cloud(path)
        classes = np.full(len(pcd.points), ASPRS_UNCLASSIFIED, dtype=np.uint8)
    else:
        raise ToolError(f"extensión '{ext}' no soportada (usa .las, .laz o .ply).")
    if pcd.is_empty():
        raise ToolError("el archivo no contiene puntos.")
    _set_cloud(pcd, classes)
    State.source = path
    return f"Cargada: {len(pcd.points):,} puntos desde {path}"


@tool
def get_cloud_info() -> str:
    """Resumen de la nube: puntos, bounding box, normales, colores, clases y malla."""
    pcd = _cloud()
    pts = np.asarray(pcd.points)
    mn, mx = pts.min(axis=0), pts.max(axis=0)
    codes, counts = np.unique(State.classes, return_counts=True)
    lines = [
        f"Origen: {State.source}",
        f"Puntos: {len(pts):,}",
        f"BBox min (X,Y,Z): {mn[0]:.3f}, {mn[1]:.3f}, {mn[2]:.3f}",
        f"BBox max (X,Y,Z): {mx[0]:.3f}, {mx[1]:.3f}, {mx[2]:.3f}",
        f"Dimensiones (dX,dY,dZ): {mx[0]-mn[0]:.3f}, {mx[1]-mn[1]:.3f}, {mx[2]-mn[2]:.3f}",
        f"Normales: {'sí' if pcd.has_normals() else 'no'}",
        f"Colores: {'sí' if pcd.has_colors() else 'no'}",
        "Clases ASPRS: " + ", ".join(f"{c}={n:,}" for c, n in zip(codes, counts)),
        f"EPSG: {State.epsg or 'no definido'}",
        f"Malla: {len(State.mesh.triangles):,} triángulos" if State.mesh else "Malla: no",
    ]
    return "\n".join(lines)


@tool
def set_crs(epsg: int) -> str:
    """Define el código EPSG del sistema de coordenadas (se usa al exportar IFC4/IFC4X3)."""
    if epsg <= 0:
        raise ToolError("EPSG debe ser un entero positivo (ej. 32718).")
    State.epsg = int(epsg)
    return f"EPSG:{epsg} definido."


@tool
def downsample(voxel_size: float) -> str:
    """Diezma con voxel_down_sample (voxel_size en unidades de la nube). Conserva clases."""
    pcd = _cloud()
    if voxel_size <= 0:
        raise ToolError("voxel_size debe ser > 0.")
    before = len(pcd.points)
    pts = np.asarray(pcd.points)
    new, _, groups = pcd.voxel_down_sample_and_trace(
        voxel_size, pts.min(axis=0), pts.max(axis=0))
    # clase del voxel = máxima del grupo (terreno=2 gana sobre sin clasificar=1)
    classes = np.array([State.classes[np.asarray(g)].max() for g in groups], dtype=np.uint8)
    _set_cloud(new, classes)
    return f"Puntos: {before:,} -> {len(new.points):,}"


@tool
def remove_noise(nb_neighbors: int = 20, std_ratio: float = 2.0) -> str:
    """Quita outliers estadísticos y actualiza la nube."""
    pcd = _cloud()
    if nb_neighbors < 1 or std_ratio <= 0:
        raise ToolError("nb_neighbors >= 1 y std_ratio > 0.")
    before = len(pcd.points)
    new, ind = pcd.remove_statistical_outlier(nb_neighbors=nb_neighbors, std_ratio=std_ratio)
    _set_cloud(new, State.classes[np.asarray(ind)])
    return f"Puntos: {before:,} -> {len(new.points):,} ({before - len(new.points):,} eliminados)"


@tool
def crop_bbox(min_x: float, min_y: float, max_x: float, max_y: float,
              min_z: Optional[float] = None, max_z: Optional[float] = None) -> str:
    """Recorta la nube a una caja (coordenadas de la nube). Z opcional."""
    pcd = _cloud()
    if min_x >= max_x or min_y >= max_y:
        raise ToolError("min debe ser menor que max en X e Y.")
    p = np.asarray(pcd.points)
    m = (p[:, 0] >= min_x) & (p[:, 0] <= max_x) & (p[:, 1] >= min_y) & (p[:, 1] <= max_y)
    if min_z is not None:
        m &= p[:, 2] >= min_z
    if max_z is not None:
        m &= p[:, 2] <= max_z
    if not m.any():
        raise ToolError("el recorte dejaría la nube vacía; no se aplicó.")
    before = len(p)
    _set_cloud(pcd.select_by_index(np.flatnonzero(m)), State.classes[m])
    return f"Puntos: {before:,} -> {int(m.sum()):,}"


@tool
def estimate_normals(radius: float = 1.0, max_nn: int = 30) -> str:
    """Calcula normales (orientadas hacia +Z)."""
    pcd = _cloud()
    if radius <= 0 or max_nn < 3:
        raise ToolError("radius > 0 y max_nn >= 3.")
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=max_nn))
    pcd.orient_normals_to_align_with_direction(np.array([0.0, 0.0, 1.0]))
    return "Normales calculadas."


# --------------------------------------------------------- clasificación
@tool
def classify_ground(method: str = "grid", cell_size: float = 2.0, height_tol: float = 0.5) -> str:
    """
    Clasifica terreno (ASPRS 2) vs resto (1). method='grid': puntos a <= height_tol sobre
    el mínimo de su celda cell_size (terreno ondulado). method='ransac': plano único
    (height_tol = distancia), solo para terreno casi plano.
    """
    pcd = _cloud()
    p = np.asarray(pcd.points)
    if method == "grid":
        if cell_size <= 0 or height_tol < 0:
            raise ToolError("cell_size > 0 y height_tol >= 0.")
        ij = np.floor((p[:, :2] - p[:, :2].min(axis=0)) / cell_size).astype(np.int64)
        key = ij[:, 0] * (ij[:, 1].max() + 1) + ij[:, 1]
        _, inv = np.unique(key, return_inverse=True)
        inv = inv.ravel()
        zmin = np.full(inv.max() + 1, np.inf)
        np.minimum.at(zmin, inv, p[:, 2])
        mask = (p[:, 2] - zmin[inv]) <= height_tol
    elif method == "ransac":
        _, inl = pcd.segment_plane(distance_threshold=max(height_tol, 1e-6),
                                   ransac_n=3, num_iterations=2000)
        mask = np.zeros(len(p), dtype=bool)
        mask[inl] = True
    else:
        raise ToolError("method debe ser 'grid' o 'ransac'.")
    State.classes = np.where(mask, ASPRS_GROUND, ASPRS_UNCLASSIFIED).astype(np.uint8)
    State.mesh = None
    return f"Terreno: {int(mask.sum()):,} | No terreno: {int((~mask).sum()):,}"


@tool
def extract_class(class_code: int = ASPRS_GROUND) -> str:
    """Conserva solo los puntos de una clase ASPRS (2 = terreno) y descarta el resto."""
    pcd = _cloud()
    m = State.classes == class_code
    if not m.any():
        raise ToolError(f"no hay puntos con clase {class_code}.")
    before = len(m)
    _set_cloud(pcd.select_by_index(np.flatnonzero(m)), State.classes[m])
    return f"Puntos: {before:,} -> {int(m.sum()):,} (clase {class_code})"


# ----------------------------------------------------------------- malla
@tool
def generate_terrain_mesh(only_ground: bool = True, max_edge: float = 0.0) -> str:
    """
    TIN Delaunay 2.5D (triangula en XY, Z es atributo). only_ground usa solo clase 2
    (si no hay terreno clasificado, usa todos). max_edge > 0 descarta triángulos con
    una arista XY mayor (huecos y bordes falsos).
    """
    pcd = _cloud()
    p = np.asarray(pcd.points)
    if only_ground and (State.classes == ASPRS_GROUND).any():
        p = p[State.classes == ASPRS_GROUND]
    if len(p) < 3:
        raise ToolError("se necesitan al menos 3 puntos.")
    p = np.unique(p.round(6), axis=0)  # duplicados rompen la triangulación
    try:
        # Centrar XY: Qhull con coordenadas UTM (~1e6-1e7) descarta puntos por precisión.
        tris = Delaunay(p[:, :2] - p[:, :2].mean(axis=0)).simplices
    except Exception as e:
        raise ToolError(f"Delaunay falló (¿puntos colineales?): {e}")
    a, b, c = p[tris[:, 0], :2], p[tris[:, 1], :2], p[tris[:, 2], :2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    tris = np.where((cross < 0)[:, None], tris[:, [0, 2, 1]], tris)  # normales hacia +Z
    if max_edge > 0:
        e = np.maximum.reduce([np.linalg.norm(a - b, axis=1), np.linalg.norm(b - c, axis=1),
                               np.linalg.norm(c - a, axis=1)])
        tris = tris[e <= max_edge]
    if len(tris) == 0:
        raise ToolError("no quedaron triángulos (max_edge muy pequeño).")
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(p),
                                     o3d.utility.Vector3iVector(tris.astype(np.int32)))
    State.mesh = mesh
    return f"Malla: {len(p):,} vértices, {len(tris):,} triángulos"


@tool
def get_mesh_info() -> str:
    """Resumen de la malla: vértices, triángulos, área 3D/planimétrica, rango de Z."""
    m = _mesh()
    v, t = np.asarray(m.vertices), np.asarray(m.triangles)
    area3d = m.get_surface_area()
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    area2d = 0.5 * np.abs((b[:, 0]-a[:, 0])*(c[:, 1]-a[:, 1]) - (b[:, 1]-a[:, 1])*(c[:, 0]-a[:, 0])).sum()
    return (f"Vértices: {len(v):,}\nTriángulos: {len(t):,}\n"
            f"Área planimétrica: {area2d:,.2f}\nÁrea superficial 3D: {area3d:,.2f}\n"
            f"Z min/max: {v[:, 2].min():.3f} / {v[:, 2].max():.3f}")


@tool
def smooth_mesh(method: str = "taubin", iterations: int = 10, lambda_filter: float = 0.5,
                mu: float = -0.53, preserve_xy: bool = True) -> str:
    """
    Suaviza la malla (filtros de Open3D). method: 'simple' (promedio de vecinos),
    'laplacian' (lambda_filter = intensidad) o 'taubin' (no encoge el volumen; usa
    lambda_filter y mu). preserve_xy=True mantiene X,Y originales y solo suaviza Z
    (recomendado en terreno: evita que los bordes se encojan).
    El suavizado se aleja de los puntos medidos: informa cuánto cambió Z.
    """
    m = _mesh()
    if not 1 <= iterations <= 1000:
        raise ToolError("iterations debe estar entre 1 y 1000.")
    before = np.asarray(m.vertices).copy()
    # Open3D da NaN/pierde precisión con coordenadas UTM grandes: suavizar centrado en 0.
    center = before.mean(axis=0)
    work = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(before - center), m.triangles)
    if method == "simple":
        out = work.filter_smooth_simple(number_of_iterations=iterations)
    elif method == "laplacian":
        out = work.filter_smooth_laplacian(number_of_iterations=iterations, lambda_filter=lambda_filter)
    elif method == "taubin":
        out = work.filter_smooth_taubin(number_of_iterations=iterations, lambda_filter=lambda_filter, mu=mu)
    else:
        raise ToolError("method debe ser 'simple', 'laplacian' o 'taubin'.")
    # Vértices sin triángulos (quedan al filtrar con max_edge) dan NaN: no se tocan.
    used = np.zeros(len(before), dtype=bool)
    used[np.asarray(m.triangles).ravel()] = True
    after = before.copy()
    after[used] = (np.asarray(out.vertices) + center)[used]
    if not np.isfinite(after[used]).all():
        raise ToolError("el suavizado produjo valores no finitos; la malla no se modificó.")
    if preserve_xy:
        after[:, :2] = before[:, :2]
        out.vertices = o3d.utility.Vector3dVector(after)
    out.remove_degenerate_triangles()
    out.compute_vertex_normals()
    State.mesh = out
    dz = after[:, 2] - before[:, 2]
    return (f"Suavizado '{method}' x{iterations} (preserve_xy={preserve_xy}). "
            f"Cambio en Z: RMS {np.sqrt((dz**2).mean()):.4f}, máx {np.abs(dz).max():.4f}")


@tool
def compute_volume(reference_z: float) -> str:
    """
    Volumen entre la malla y un plano horizontal z=reference_z. Corte = sobre el plano,
    relleno = bajo el plano. Triángulos que cruzan el plano se aproximan por su Z medio.
    """
    m = _mesh()
    v, t = np.asarray(m.vertices), np.asarray(m.triangles)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    area = 0.5 * np.abs((b[:, 0]-a[:, 0])*(c[:, 1]-a[:, 1]) - (b[:, 1]-a[:, 1])*(c[:, 0]-a[:, 0]))
    h = (a[:, 2] + b[:, 2] + c[:, 2]) / 3.0 - reference_z
    cut, fill = float((area * np.clip(h, 0, None)).sum()), float((area * np.clip(-h, 0, None)).sum())
    return f"Corte: {cut:,.2f} | Relleno: {fill:,.2f} | Neto (corte-relleno): {cut - fill:,.2f} (unidades³)"


# ------------------------------------------------------------ exportación
@tool
def save_point_cloud(output_filepath: str) -> str:
    """Guarda la nube. Formatos: .ply (recomendado), .obj (solo vértices)."""
    pcd = _cloud()
    path = os.path.expanduser(output_filepath)
    ext = os.path.splitext(path)[1].lower()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if ext == ".ply":
        if not o3d.io.write_point_cloud(path, pcd):
            raise ToolError("Open3D no pudo escribir el archivo.")
    elif ext == ".obj":
        with open(path, "w", encoding="utf-8") as f:
            np.savetxt(f, np.asarray(pcd.points), fmt="v %.6f %.6f %.6f")
    else:
        raise ToolError(f"extensión '{ext}' no soportada (usa .ply u .obj).")
    return f"Guardada: {len(pcd.points):,} puntos en {path}"


@tool
def export_mesh(output_filepath: str) -> str:
    """Exporta la malla a .ply, .obj o .stl."""
    m = _mesh()
    path = os.path.expanduser(output_filepath)
    if os.path.splitext(path)[1].lower() not in (".ply", ".obj", ".stl"):
        raise ToolError("usa extensión .ply, .obj o .stl.")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    m.compute_vertex_normals()
    if not o3d.io.write_triangle_mesh(path, m):
        raise ToolError("Open3D no pudo escribir la malla.")
    return f"Malla guardada: {len(m.triangles):,} triángulos en {path}"


@tool
def export_ifc(output_filepath: str, version: str = "IFC4", name: str = "Terreno") -> str:
    """
    Exporta la malla de terreno a IFC. version: 'IFC2X3', 'IFC4', 'IFC4X3' o 'ALL'
    (escribe las tres, con sufijo _IFC2X3/_IFC4/_IFC4X3 antes de .ifc).
    Usa el EPSG definido con set_crs para georreferenciar (IFC4 e IFC4X3).
    """
    m = _mesh()
    v, t = np.asarray(m.vertices), np.asarray(m.triangles)
    path = os.path.expanduser(output_filepath)
    base, ext = os.path.splitext(path)
    if ext.lower() != ".ifc":
        raise ToolError("la ruta de salida debe terminar en .ifc")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    versions = list(SCHEMAS) if version.upper() == "ALL" else [version]
    out = []
    for ver in versions:
        target = f"{base}_{ver.upper()}.ifc" if len(versions) > 1 else path
        try:
            export_terrain_ifc(v, t, target, ver, State.epsg, name)
        except ValueError as e:
            raise ToolError(str(e))
        out.append(f"{ver.upper()}: {target} ({os.path.getsize(target) / 1e6:.1f} MB)")
    return "IFC exportado:\n" + "\n".join(out)


if __name__ == "__main__":
    mcp.run()  # transporte stdio por defecto
