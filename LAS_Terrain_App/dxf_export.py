"""
dxf_export.py
Exporta las líneas del modelo (aristas de los sólidos, sección, eje, curvas de nivel del terreno,
cotas y medidas) a DXF para AutoCAD.

Georreferencia: las coordenadas se escriben en el sistema del proyecto (p. ej. UTM, `coords="utm"`)
o en local x, y, z respecto al origen de la nube (`coords="local"`). El DXF no tiene un campo estándar
para el CRS, así que además de las coordenadas reales se escribe un `.prj` (WKT) y un `.txt` con el
EPSG y el origen; en AutoCAD Map/Civil 3D se asigna con MAPCSASSIGN o GEO.
"""
from __future__ import annotations

import os

import numpy as np

LAYERS = {   # nombre: color ACI
    "EJE": 1, "SECCION": 4, "LOSA": 5, "VIGA": 30, "BARANDA": 3, "RIEL": 3, "POSTE": 2, "ESTRIBO": 8,
    "MURO": 6, "APOYO": 1, "VIA": 9, "TERRENO_CURVAS": 94, "TERRENO_MALLA": 252, "COTAS": 7, "MEDIDAS": 40,
}
KIND_LAYER = {"losa": "LOSA", "viga": "VIGA", "baranda": "BARANDA", "riel": "RIEL", "poste": "POSTE",
              "estribo": "ESTRIBO", "muro": "MURO", "apoyo": "APOYO", "via": "VIA"}


def chain_segments(a: np.ndarray, b: np.ndarray, decimals: int = 4) -> list:
    """Une segmentos sueltos (a_i -> b_i) en polilíneas (listas de puntos) recorriendo vértices de grado 2."""
    key = lambda p: tuple(np.round(p, decimals))
    adj, pts = {}, {}
    for p, q in zip(a, b):
        kp, kq = key(p), key(q)
        if kp == kq:
            continue
        pts[kp], pts[kq] = p, q
        adj.setdefault(kp, set()).add(kq)
        adj.setdefault(kq, set()).add(kp)
    seen, out = set(), []
    # empezar por los extremos (grado != 2) para no cortar cadenas abiertas por la mitad
    order = [k for k, v in adj.items() if len(v) != 2] + list(adj)
    for start in order:
        for nxt in list(adj[start]):
            if frozenset((start, nxt)) in seen:
                continue
            path, prev, cur = [start, nxt], start, nxt
            seen.add(frozenset((start, nxt)))
            while len(adj[cur]) == 2:
                n2 = next(x for x in adj[cur] if x != prev)
                e = frozenset((cur, n2))
                if e in seen:
                    break
                seen.add(e)
                path.append(n2)
                prev, cur = cur, n2
            out.append(np.array([pts[k] for k in path]))
    return out


def feature_edges(vertices: np.ndarray, triangles: np.ndarray, angle_deg: float = 30.0) -> list:
    """Polilíneas de las aristas de borde y de las aristas con ángulo diedro > angle_deg."""
    V, T = np.asarray(vertices, float), np.asarray(triangles)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    n = n / np.where(ln > 0, ln, 1)[:, None]
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    tri = np.tile(np.arange(len(T)), 3)
    lo, hi = e.min(axis=1), e.max(axis=1)
    key = lo.astype(np.int64) * (len(V) + 1) + hi
    order = np.argsort(key, kind="stable")
    ks, ts = key[order], tri[order]
    first = np.r_[True, ks[1:] != ks[:-1]]
    idx = np.flatnonzero(first)
    cnt = np.diff(np.r_[idx, len(ks)])
    a_, b_ = lo[order][idx], hi[order][idx]
    keep = cnt == 1                                      # borde abierto
    two = np.flatnonzero(cnt == 2)
    if len(two):
        cosang = (n[ts[idx[two]]] * n[ts[idx[two] + 1]]).sum(axis=1)
        keep[two] = cosang < np.cos(np.radians(angle_deg))
    return chain_segments(V[a_[keep]], V[b_[keep]])


def contour_lines(vertices: np.ndarray, triangles: np.ndarray, interval: float = 1.0) -> list:
    """Curvas de nivel del TIN (cada una a su cota, 3D) con matplotlib.tri."""
    from matplotlib.figure import Figure
    import matplotlib.tri as mtri
    V = np.asarray(vertices, float)
    levels = np.arange(np.ceil(V[:, 2].min() / interval) * interval, V[:, 2].max(), interval)
    if len(levels) == 0:
        return []
    tri = mtri.Triangulation(V[:, 0], V[:, 1], np.asarray(triangles))
    cs = Figure().add_subplot().tricontour(tri, V[:, 2], levels=levels)
    out = []
    for lv, segs in zip(cs.levels, cs.allsegs):
        for seg in segs:
            if len(seg) >= 2:
                out.append(np.c_[seg, np.full(len(seg), lv)])
    return out


def _utm_wkt(epsg: int):
    """WKT ESRI de WGS84 / UTM (EPSG 32601-32660 norte, 32701-32760 sur); None para otros EPSG."""
    if 32601 <= epsg <= 32660:
        zone, south = epsg - 32600, False
    elif 32701 <= epsg <= 32760:
        zone, south = epsg - 32700, True
    else:
        return None
    lon0 = -183 + 6 * zone
    return (f'PROJCS["WGS_1984_UTM_Zone_{zone}{"S" if south else "N"}",GEOGCS["GCS_WGS_1984",'
            f'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
            f'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],'
            f'PARAMETER["False_Northing",{10000000.0 if south else 0.0}],PARAMETER["Central_Meridian",{lon0}.0],'
            f'PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')


def write_dxf(path: str, lines: dict, annotations: list | None = None, origin=(0.0, 0.0, 0.0),
              coords: str = "utm", epsg: int | None = None, mesh: tuple | None = None) -> dict:
    """
    lines: {capa: [ndarray (N,3) en coords locales]}; annotations: [{"type": "cota"|"medida", ...}] en locales;
    mesh: (vertices, triangles) locales del terreno para 3DFACE (opcional).
    Devuelve un resumen con el archivo, conteos y los archivos auxiliares (.prj, .txt).
    """
    import ezdxf
    if coords not in ("utm", "local"):
        raise ValueError("coords debe ser 'utm' o 'local'")
    o = np.asarray(origin, float) if coords == "utm" else np.zeros(3)
    doc = ezdxf.new("R2018")
    doc.units = 6                                           # metros
    msp = doc.modelspace()
    for nm, col in LAYERS.items():
        doc.layers.add(nm, color=col)
    n_lines = n_pts = n_txt = 0
    for layer, polys in lines.items():
        if layer not in doc.layers:
            doc.layers.add(layer, color=7)
        for p in polys:
            p = np.asarray(p, float) + o
            if len(p) >= 2:
                msp.add_polyline3d(p.tolist(), dxfattribs={"layer": layer})
                n_lines += 1
    th = 0.5
    for an in annotations or []:
        if an["type"] == "cota":
            p = np.asarray(an["p"], float) + o
            msp.add_point(p.tolist(), dxfattribs={"layer": "COTAS"})
            txt = an.get("text") or f"Z={p[2]:.3f}"
            t = msp.add_text(txt, height=th, dxfattribs={"layer": "COTAS"})
            t.set_placement((p[0] + 0.3, p[1] + 0.3, p[2]))
            n_pts += 1
            n_txt += 1
        elif an["type"] == "medida":
            a, b = np.asarray(an["a"], float) + o, np.asarray(an["b"], float) + o
            msp.add_line(a.tolist(), b.tolist(), dxfattribs={"layer": "MEDIDAS"})
            m = (a + b) / 2
            t = msp.add_text(an.get("text") or f"{np.linalg.norm(b - a):.3f} m", height=th,
                             dxfattribs={"layer": "MEDIDAS"})
            t.set_placement((m[0], m[1], m[2]))
            n_lines += 1
            n_txt += 1
    n_faces = 0
    if mesh is not None:
        V, T = np.asarray(mesh[0], float) + o, np.asarray(mesh[1])
        for a, b, c in T:
            msp.add_3dface([V[a].tolist(), V[b].tolist(), V[c].tolist(), V[c].tolist()],
                           dxfattribs={"layer": "TERRENO_MALLA"})
        n_faces = len(T)
    doc.header["$PROJECTNAME"] = f"EPSG:{epsg}" if epsg else "local"
    doc.saveas(path)
    base = os.path.splitext(path)[0]
    aux = []
    info = [f"Sistema de coordenadas del DXF: {'UTM (coordenadas reales)' if coords == 'utm' else 'local x, y, z'}",
            f"EPSG: {epsg if epsg else 'no definido'}",
            f"Origen local (real E, N, cota): {origin[0]:.4f}, {origin[1]:.4f}, {origin[2]:.4f}",
            "Para asignar el CRS en AutoCAD Map/Civil 3D: MAPCSASSIGN (o GEO) con el EPSG indicado."]
    with open(base + "_georef.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(info) + "\n")
    aux.append(base + "_georef.txt")
    if epsg and coords == "utm":
        wkt = _utm_wkt(int(epsg))
        if wkt is None:
            try:
                from pyproj import CRS
                wkt = CRS.from_epsg(int(epsg)).to_wkt(version="WKT1_ESRI")
            except Exception:
                wkt = None
        if wkt:
            with open(base + ".prj", "w", encoding="utf-8") as f:
                f.write(wkt)
            aux.append(base + ".prj")
    return {"file": path, "aux": aux, "coords": coords, "epsg": epsg, "polylines": n_lines, "points": n_pts,
            "texts": n_txt, "faces3d": n_faces, "layers": {k: len(v) for k, v in lines.items()}}
