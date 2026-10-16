"""
reconstruct.py
Pasos 1-2 de la reconstrucción: agrupar la estructura en conjuntos conectados y estimar
el eje del puente.

>>> PUNTO DE INTEGRACIÓN <<<
Los pasos siguientes (planos RANSAC, secciones transversales apiladas, separación de
elementos, extrusión) parten de `StructureResult`: labels por punto + eje (centro, dirección).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

import autoclass


@dataclass
class StructureResult:
    labels: np.ndarray                    # (N,) int32: grupo de cada punto (-1 = no estructura/descartado)
    clusters: list = field(default_factory=list)   # tabla por grupo (ordenada por tamaño)
    axis_center: np.ndarray = None        # (3,) coords locales (restar origin para real)
    axis_dir: np.ndarray = None           # (3,) unitario en XY (z = 0)
    axis_angle_deg: float = 0.0           # azimut desde +X, antihorario
    length: float = 0.0
    width: float = 0.0
    pca_angle_deg: float = 0.0            # dirección inicial (PCA), solo informativa


def structure_clusters(points: np.ndarray, classification: np.ndarray, struct_code: int = 6,
                       voxel: float = 0.15, link: float = 0.35, min_voxels: int = 300,
                       max_clusters: int = 12) -> StructureResult:
    """
    Une vóxeles de estructura que están a <= `link` m entre sí (componentes conectados).
    Los grupos con menos de `min_voxels` vóxeles se descartan (ruido y falsos positivos).
    """
    idx = np.flatnonzero(classification == struct_code)
    labels = np.full(len(points), -1, dtype=np.int32)
    if len(idx) == 0:
        return StructureResult(labels)
    cen, inv = autoclass.voxelize(points[idx], voxel)
    n = len(cen)
    pairs = cKDTree(cen).query_pairs(link, output_type="ndarray")
    g = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, comp = connected_components(g, directed=False)
    sizes = np.bincount(comp)
    order = np.argsort(-sizes)
    order = [c for c in order if sizes[c] >= min_voxels][:max_clusters]
    remap = np.full(len(sizes), -1, dtype=np.int32)
    for new, old in enumerate(order):
        remap[old] = new
    labels[idx] = remap[comp][inv]

    clusters = []
    mn0 = points[idx].min(axis=0)
    for new, old in enumerate(order):
        m = comp == old
        c = cen[m]
        ext = c.max(0) - c.min(0)
        clusters.append({"id": new, "voxels": int(m.sum()),
                         "points": int((labels == new).sum()),
                         "bbox_xyz": [float(v) for v in ext],
                         "centroid_local": [float(v) for v in c.mean(0) + mn0]})
    res = StructureResult(labels, clusters)
    if clusters:
        _fit_axis(points, res)
    return res


def _sharpness(s_coord: np.ndarray, bin_m: float = 0.05) -> float:
    """Nitidez de picos del histograma transversal: sube cuando las líneas longitudinales alinean."""
    h, _ = np.histogram(s_coord, bins=np.arange(s_coord.min(), s_coord.max() + bin_m, bin_m))
    return float((h.astype(np.float64) ** 2).sum())


def _fit_axis(points: np.ndarray, res: StructureResult) -> None:
    """
    Eje del puente en planta.
    1) PCA sobre XY del grupo mayor da una dirección inicial (sesgada por los extremos esviados).
    2) Se afina girando +-8 grados: el ángulo bueno es el que hace más nítidos los picos del
       histograma transversal (barandas, sardineles y vigas son líneas paralelas al eje).
    3) El centro sale de los extremos (percentiles), no de la densidad de puntos.
    """
    pts = points[res.labels == 0]
    if len(pts) > 2_000_000:
        pts = pts[np.random.default_rng(0).choice(len(pts), 2_000_000, replace=False)]
    p = pts[:, :2]
    c0 = p.mean(axis=0)
    w, v = np.linalg.eigh(np.cov((p - c0).T))
    a0 = np.arctan2(v[1, 1], v[0, 1])

    def score(a):
        n = np.array([-np.sin(a), np.cos(a)])
        return _sharpness((p - c0) @ n)

    coarse = np.radians(np.arange(-8.0, 8.01, 0.25))
    best = a0 + coarse[int(np.argmax([score(a0 + da) for da in coarse]))]
    fine = best + np.radians(np.arange(-0.3, 0.301, 0.02))
    a = fine[int(np.argmax([score(x) for x in fine]))]

    d = np.array([np.cos(a), np.sin(a)])
    if d[0] < 0 or (abs(d[0]) < 1e-9 and d[1] < 0):
        d = -d
    n = np.array([-d[1], d[0]])
    t, sv = (p - c0) @ d, (p - c0) @ n
    t_lo, t_hi = np.percentile(t, [0.5, 99.5])
    s_lo, s_hi = np.percentile(sv, [0.5, 99.5])
    ctr = c0 + d * (t_lo + t_hi) / 2 + n * (s_lo + s_hi) / 2
    res.axis_dir = np.array([d[0], d[1], 0.0])
    res.axis_center = np.array([ctr[0], ctr[1], float(pts[:, 2].mean())])
    res.axis_angle_deg = float(np.degrees(np.arctan2(d[1], d[0])))
    res.pca_angle_deg = float(np.degrees(a0))
    res.length = float(t_hi - t_lo)
    res.width = float(s_hi - s_lo)


# ---------------------------------------------------------------- sección transversal
@dataclass
class SectionResult:
    polygons: list = field(default_factory=list)   # cada (K,2) [t, z_rel], cerrado
    grade: np.ndarray = None                       # coeficientes polyfit de z_tope(s)
    ends: dict = field(default_factory=dict)       # s = a + k*t en extremo inicial y final
    s_mid: tuple = (0.0, 0.0)                      # tramo usado para la sección
    n_slabs: int = 0
    raster: np.ndarray = None                      # conteo de cortes con punto (t, z)
    extent: tuple = (0, 0, 0, 0)                   # t0, t1, z0, z1
    lines3d: list = field(default_factory=list)    # polilíneas (M,3) en coords locales
    validation: dict = field(default_factory=dict)
    frame: tuple = None                            # c(2), d(2), n(2)
    mask: np.ndarray = None                        # máscara rellena (t, z_rel)
    cell: float = 0.02


def _robust_poly(x, y, deg=2, it=4, k=3.0):
    m = np.ones(len(x), bool)
    for _ in range(it):
        c = np.polyfit(x[m], y[m], deg)
        r = y - np.polyval(c, x)
        mad = np.median(np.abs(r[m] - np.median(r[m]))) * 1.4826 + 1e-6
        m = np.abs(r) < k * mad
    return np.polyfit(x[m], y[m], deg)


def _group_stats(keys, values, min_n, fn):
    """Aplica fn(values del grupo) por cada valor de keys (enteros) con >= min_n elementos."""
    order = np.argsort(keys, kind="stable")
    ks, vs = keys[order], values[order]
    edges = np.r_[0, np.flatnonzero(np.diff(ks)) + 1, len(ks)]
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a >= min_n:
            out.append((ks[a], fn(vs[a:b])))
    return out


def extract_section(points, res: StructureResult, cell: float = 0.02, slab: float = 0.25,
                    occ_frac: float = 0.15, blur: float = 0.7, simplify: float = 0.02, max_pts: int = 8_000_000,
                    station_step: float = 5.0) -> SectionResult:
    """
    Sección transversal del tablero por apilado de cortes a lo largo del eje.
    1) Marco (s a lo largo, t transversal, z). 2) Rasante z(s) del tope (polinomio robusto);
    z_rel = z - rasante evita que la pendiente difumine la sección. 3) Extremos esviados: recta
    s = a + k t en cada extremo. 4) Se apilan cortes de `slab` m en el tramo central; una celda
    (t, z_rel) es superficie si tiene punto en >= occ_frac de los cortes. 5) Cierre + relleno +
    contorno + simplificación -> polígonos. 6) Líneas 3D: cada vértice se barre a lo largo del
    eje entre los extremos esviados.
    """
    from scipy import ndimage as ndi
    from skimage.measure import approximate_polygon, find_contours

    pts = points[res.labels == 0]
    if len(pts) > max_pts:
        pts = pts[np.random.default_rng(0).choice(len(pts), max_pts, replace=False)]
    c, d = res.axis_center[:2], res.axis_dir[:2]
    n = np.array([-d[1], d[0]])
    q = pts[:, :2] - c
    s, t, z = q @ d, q @ n, pts[:, 2]
    W = res.width

    # rasante: percentil 99 de z por tramo de 0.5 m en una franja central angosta (|t| < 0.5 m):
    # ahí el tope de la losa es lo más alto; una franja ancha o un percentil bajo mezcla vigas y
    # pendiente transversal (probado: residuo 68 cm con p90 en franja ancha, 1 cm con p99 en 0.5 m)
    cen = np.abs(t) < 0.5
    smin = s.min()
    kb = np.floor((s[cen] - smin) / 0.5).astype(np.int64)
    res_b = _group_stats(kb, z[cen], 50, lambda v: np.percentile(v, 99))
    xs = np.array([smin + (k + 0.5) * 0.5 for k, _ in res_b])
    ys = np.array([v for _, v in res_b])
    grade = _robust_poly(xs, ys, deg=3)
    zr = z - np.polyval(grade, s)

    # tope real por t (la losa tiene pendiente transversal): percentil 90 de z_rel por franja de
    # 0.25 m en el tramo central; luego se toman los puntos a +-0.10 m de ese tope
    s5, s95 = np.percentile(s, [20, 80])
    mid = (s > s5) & (s < s95) & (zr < 0.4) & (zr > -0.8)
    tk = np.floor(t[mid] / 0.25).astype(np.int64)
    tops = _group_stats(tk, zr[mid], 200, lambda v: np.percentile(v, 98))
    tx = np.array([(k + 0.5) * 0.25 for k, _ in tops])
    tz_ = np.array([v for _, v in tops])
    ztop = np.interp(t, tx, tz_)
    top = (np.abs(zr - ztop) < 0.10) & (np.abs(t) < 0.45 * W)
    tb = np.floor(t[top] / 0.5).astype(np.int64)
    st, tt_ = s[top], t[top]
    lo_b = _group_stats(tb, st, 100, lambda v: np.percentile(v, 0.5))
    hi_b = _group_stats(tb, st, 100, lambda v: np.percentile(v, 99.5))
    t_b = _group_stats(tb, tt_, 100, lambda v: v.mean())
    tcs = np.array([v for _, v in t_b])
    k_lo, a_lo = np.polyfit(tcs, [v for _, v in lo_b], 1)
    k_hi, a_hi = np.polyfit(tcs, [v for _, v in hi_b], 1)
    ends = {"lo": (float(a_lo), float(k_lo)), "hi": (float(a_hi), float(k_hi))}
    t_ext = np.array([-W / 2, W / 2])
    s_mid = (float((a_lo + k_lo * t_ext).max() + 0.5), float((a_hi + k_hi * t_ext).min() - 0.5))

    # apilar cortes del tramo central
    m = (s > s_mid[0]) & (s < s_mid[1])
    sm, tm, zm = s[m], t[m], zr[m]
    t0, t1 = np.percentile(tm, [0.05, 99.95])
    z0, z1 = np.percentile(zm, [0.05, 99.95])
    nt, nz = int((t1 - t0) / cell) + 1, int((z1 - z0) / cell) + 1
    ok = (tm >= t0) & (tm <= t1) & (zm >= z0) & (zm <= z1)
    si = np.floor((sm[ok] - s_mid[0]) / slab).astype(np.int64)
    ti = np.floor((tm[ok] - t0) / cell).astype(np.int64)
    zi = np.floor((zm[ok] - z0) / cell).astype(np.int64)
    n_slabs = int(si.max()) + 1
    uk = np.unique((si * nt + ti) * nz + zi)      # una vez por (corte, celda)
    raster = np.bincount(uk % (nt * nz), minlength=nt * nz).reshape(nt, nz)
    # las líneas se difuminan unos cm entre cortes: se suaviza la frecuencia antes de umbralar
    freq = ndi.gaussian_filter(raster / n_slabs, blur)
    occ = freq >= occ_frac

    # cierre, relleno y contorno
    closed = ndi.binary_dilation(occ, iterations=3)
    filled = ndi.binary_erosion(ndi.binary_fill_holes(closed), iterations=3)
    lab, nl = ndi.label(filled)
    sizes = ndi.sum(filled, lab, range(1, nl + 1))
    keep = [i + 1 for i, sz in enumerate(sizes) if sz * cell * cell > 0.05]
    filled = np.isin(lab, keep)
    polys = []
    for cnt in find_contours(np.pad(filled, 1).astype(float), 0.5):
        cnt = approximate_polygon(cnt - 1, simplify / cell)
        if len(cnt) >= 4:
            polys.append(np.column_stack((t0 + cnt[:, 0] * cell, z0 + cnt[:, 1] * cell)))

    # validación: distancia de puntos del tramo central al contorno
    val = {}
    if polys:
        bnd = []
        for pl in polys:
            for a, b in zip(pl[:-1], pl[1:]):
                kseg = max(2, int(np.hypot(*(b - a)) / 0.01))
                bnd.append(a + (b - a) * np.linspace(0, 1, kseg)[:, None])
        bnd = np.vstack(bnd)
        sub = np.random.default_rng(1).choice(len(tm), min(300_000, len(tm)), replace=False)
        dist, _ = cKDTree(bnd).query(np.column_stack((tm[sub], zm[sub])))
        near = dist < 0.30      # puntos de otros elementos (estribos, retornos) quedan fuera de la métrica
        val = {"p50_cm": float(np.percentile(dist[near], 50) * 100),
               "p90_cm": float(np.percentile(dist[near], 90) * 100),
               "pct_dentro_5cm": float((dist < 0.05).mean() * 100),
               "pct_dentro_10cm": float((dist < 0.10).mean() * 100),
               "pct_lejos_30cm": float((~near).mean() * 100)}

    # líneas 3D: barrido de cada vértice entre los extremos esviados
    lines = []
    for pl in polys:
        ring_lo, ring_hi = [], []
        for tv, zv in pl:
            s0, s1 = a_lo + k_lo * tv, a_hi + k_hi * tv
            S = np.linspace(s0, s1, max(2, int((s1 - s0) / station_step) + 1))
            xy = c[None, :] + S[:, None] * d[None, :] + tv * n[None, :]
            lines.append(np.column_stack((xy, np.polyval(grade, S) + zv)))
            ring_lo.append(lines[-1][0])
            ring_hi.append(lines[-1][-1])
        lines.append(np.array(ring_lo))
        lines.append(np.array(ring_hi))

    return SectionResult(polys, grade, ends, s_mid, n_slabs, raster, (t0, t1, z0, z1), lines, val,
                         (c, d, n), filled, cell)


def save_section_png(sec: SectionResult, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t0, t1, z0, z1 = sec.extent
    fig, ax = plt.subplots(figsize=(13, 5), dpi=110)
    ax.imshow(np.log1p(sec.raster.T), origin="lower", extent=(t0, t1, z0, z1),
              cmap="gray_r", aspect="equal")
    for pl in sec.polygons:
        ax.plot(pl[:, 0], pl[:, 1], "r-", lw=1.4)
        ax.plot(pl[:, 0], pl[:, 1], "b.", ms=3)
    ax.set_xlabel("t (m, transversal)")
    ax.set_ylabel("z relativo a rasante (m)")
    ax.set_title(f"Sección apilada ({sec.n_slabs} cortes): {len(sec.polygons)} contornos | "
                 f"p50 {sec.validation.get('p50_cm', 0):.1f} cm  p90 {sec.validation.get('p90_cm', 0):.1f} cm")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------- separación de elementos y sólidos
@dataclass
class Element:
    name: str
    kind: str                 # "losa" | "viga" | "baranda"
    ifc_class: str            # IfcSlab | IfcBeam | IfcRailing
    polygon: np.ndarray       # (K,2) [t, z_rel], antihorario
    area: float = 0.0


@dataclass
class Solid:
    name: str
    kind: str
    ifc_class: str
    vertices: np.ndarray      # coords locales (restar origin para reales)
    triangles: np.ndarray
    volume: float = 0.0
    watertight: bool = False
    metrics: dict = field(default_factory=dict)


IFC_CLASS = {"losa": "IfcSlab", "viga": "IfcBeam", "baranda": "IfcRailing"}


def _poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def separate_elements(sec: SectionResult, simplify: float = 0.02, bar_height: float = 0.45,
                      beam_depth: float = 0.9, min_area: float = 0.03) -> list:
    """
    Separa el contorno apilado en losa, vigas y barandas con reglas sobre la máscara (t, z_rel):
    - tope de losa ZT(t): recta robusta sobre la zona central (la losa tiene pendiente transversal);
    - baranda = material a más de `bar_height` m sobre ZT(t);
    - viga = material bajo el intradós de la losa; columnas con espesor > `beam_depth` son vigas y
      el intradós se interpola entre las columnas de solo losa (todo lo que cuelga es viga: ala
      superior, alma y bulbo);
    - losa = el resto (incluye sardineles bajos).
    """
    from scipy import ndimage as ndi
    from skimage.measure import approximate_polygon, find_contours

    M, (t0, t1, z0, z1), cell = sec.mask, sec.extent, sec.cell
    nt, nz = M.shape
    tc = t0 + (np.arange(nt) + 0.5) * cell
    zc = z0 + (np.arange(nz) + 0.5) * cell
    has = M.any(axis=1)
    top = np.where(has, zc[(nz - 1) - np.argmax(M[:, ::-1], axis=1)], np.nan)
    low = np.where(has, zc[np.argmax(M, axis=1)], np.nan)

    cen = has & (np.abs(tc) < 4.3)
    k, b = _robust_poly(tc[cen], top[cen], deg=1)
    ZT = k * tc + b

    thick = top - low
    beam_col = has & ((low < ZT - beam_depth))
    slab_col = has & ~beam_col
    zu = np.interp(tc, tc[slab_col], low[slab_col])          # intradós de la losa (interpolado)
    Z = zc[None, :]
    bar = M & (Z > (ZT + bar_height)[:, None])
    beam = M & (Z < (zu - 0.03)[:, None]) & ~bar
    slab = M & ~beam & ~bar

    def comps(mask):
        lab, n = ndi.label(mask, structure=np.ones((3, 3)))
        out = []
        for i in range(1, n + 1):
            m = lab == i
            if m.sum() * cell * cell >= min_area:
                out.append(m)
        return out

    def outline(m):
        cs = find_contours(np.pad(m, 1).astype(float), 0.5)
        c = max(cs, key=len)
        c = approximate_polygon(c - 1, simplify / cell)[:-1]
        p = np.column_stack((t0 + c[:, 0] * cell, z0 + c[:, 1] * cell))
        return p if _poly_area(p) > 0 else p[::-1]           # antihorario

    els = []
    for m in comps(slab):
        els.append(Element("Losa tablero", "losa", IFC_CLASS["losa"], outline(m)))
    beams = sorted(comps(beam), key=lambda m: float(tc[np.flatnonzero(m.any(axis=1))].mean()))
    for i, m in enumerate(beams, 1):
        els.append(Element(f"Viga {i}", "viga", IFC_CLASS["viga"], outline(m)))
    bars = sorted(comps(bar), key=lambda m: float(tc[np.flatnonzero(m.any(axis=1))].mean()))
    for i, m in enumerate(bars):
        els.append(Element("Baranda izquierda" if i == 0 else "Baranda derecha",
                           "baranda", IFC_CLASS["baranda"], outline(m)))
    for e in els:
        e.area = _poly_area(e.polygon)
    return els


def build_solids(sec: SectionResult, elements: list, stations: int = 9) -> list:
    """
    Extruye cada polígono (t, z_rel) a lo largo del eje entre los extremos esviados y sobre la
    rasante. Cada vértice se barre de s0(t) a s1(t) (extremos esviados). Tapas con earcut.
    """
    import mapbox_earcut as earcut
    import open3d as o3d

    c, d, n = sec.frame
    (a_lo, k_lo), (a_hi, k_hi) = sec.ends["lo"], sec.ends["hi"]
    solids = []
    for el in elements:
        P = el.polygon
        K = len(P)
        f = np.linspace(0, 1, stations)
        s0, s1 = a_lo + k_lo * P[:, 0], a_hi + k_hi * P[:, 0]
        S = s0[None, :] + f[:, None] * (s1 - s0)[None, :]                 # (stations, K)
        xy = c[None, None, :] + S[..., None] * d[None, None, :] + P[None, :, 0, None] * n[None, None, :]
        zz = np.polyval(sec.grade, S) + P[None, :, 1]
        V = np.concatenate((xy, zz[..., None]), axis=2).reshape(-1, 3)
        T = []
        for k in range(stations - 1):
            for i in range(K):
                j = (i + 1) % K
                a, b_, c_, d_ = k * K + i, k * K + j, (k + 1) * K + i, (k + 1) * K + j
                T += [(a, b_, c_), (b_, d_, c_)]
        cap = earcut.triangulate_float64(P.astype(np.float64), np.array([K], dtype=np.uint32)).reshape(-1, 3)
        T += [tuple(r) for r in cap[:, ::-1] + 0]                         # tapa inicial (normal -s)
        T += [tuple(r + (stations - 1) * K) for r in cap]                 # tapa final (normal +s)
        T = np.array(T, dtype=np.int64)
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                         o3d.utility.Vector3iVector(T.astype(np.int32)))
        wt = bool(mesh.is_watertight())
        vol = float(mesh.get_volume()) if wt else 0.0
        if wt and vol < 0:                     # orientación invertida: se voltea todo
            T = T[:, ::-1]
            vol = -vol
        solids.append(Solid(el.name, el.kind, el.ifc_class, V, T, vol, wt))
    return solids


# ------------------------------------------------------------------ estribos
def _beam_lines(sec: SectionResult):
    """Rectas transversales de tope de losa ZT(t) e intradós/fondo de vigas ZB(t) (z relativo)."""
    M, (t0, t1, z0, z1), cell = sec.mask, sec.extent, sec.cell
    nt, nz = M.shape
    tc = t0 + (np.arange(nt) + 0.5) * cell
    zc = z0 + (np.arange(nz) + 0.5) * cell
    has = M.any(axis=1)
    top = np.where(has, zc[(nz - 1) - np.argmax(M[:, ::-1], axis=1)], np.nan)
    low = np.where(has, zc[np.argmax(M, axis=1)], np.nan)
    cen = has & (np.abs(tc) < 4.3)
    kt, bt = _robust_poly(tc[cen], top[cen], deg=1)
    beam_col = has & (low < kt * tc + bt - 0.9)
    kb, bb = np.polyfit(tc[beam_col], low[beam_col], 1)
    return (kt, bt), (kb, bb)


def extract_abutments(points: np.ndarray, sec: SectionResult, width: float, e_back: float = 0.30,
                      margin_t: float = 3.0, w_range=(-3.0, -0.3), band=(0.25, 1.9)) -> list:
    """
    Estribo en cada extremo del tablero, medido con TODOS los puntos de la zona (no depende de la
    clasificación: el cuerpo del estribo suele quedar como terreno o vegetación).
    En el marco del extremo: w = distancia desde la cara de extremo del tablero hacia afuera,
    z_rel = z - rasante en ese extremo.
    1) Cara frontal: pico del histograma de w (2 cm) de los puntos bajo el fondo de las vigas;
       refinada con los puntos a +-5 cm del pico. Se mide extensión en t y cota de apoyo del suelo.
    2) Sección (w, z) = zapata-asiento entre la cara frontal y el espaldar (w = 0) más el espaldar de
       espesor `e_back` (SUPUESTO: la cara posterior no es visible). Cotas: base plana en el suelo,
       asiento a la altura del fondo de vigas ZB(t) y tope a la altura del tablero ZT(t), ambas rectas.
    3) Se extruye en t entre los extremos medidos. Muros de retorno y apoyos: extract_wing_walls y extract_bearings.
    """
    import mapbox_earcut as earcut
    import open3d as o3d
    c, d, n = sec.frame
    (kt, bt), (kb, bb) = _beam_lines(sec)
    out = []
    for name, key, sign in (("Estribo inicial", "lo", -1), ("Estribo final", "hi", 1)):
        a, k = sec.ends[key]
        sel = []
        for i0 in range(0, len(points), 8_000_000):
            blk = points[i0:i0 + 8_000_000]
            q = blk[:, :2] - c
            s, t = q @ d, q @ n
            w = sign * (s - (a + k * t))
            m = (np.abs(t) < width / 2 + margin_t) & (w > w_range[0] - 0.5) & (w < w_range[1] + 0.5)
            if m.any():
                zr = blk[m, 2] - np.polyval(sec.grade, a + k * t[m])
                sel.append(np.column_stack((w[m], t[m], zr)))
        if not sel:
            continue
        A = np.vstack(sel)
        w, t, zr = A[:, 0], A[:, 1], A[:, 2]
        zb = kb * t + bb
        face = (w > w_range[0]) & (w < w_range[1]) & (zr < zb - band[0]) & (zr > zb - band[1])
        if face.sum() < 1000:
            continue
        hist, edges = np.histogram(w[face], bins=np.arange(w_range[0], w_range[1] + 0.02, 0.02))
        pk = edges[int(np.argmax(np.convolve(hist, np.ones(3), "same")))] + 0.01
        near = face & (np.abs(w - pk) < 0.05)
        w_f = float(np.mean(w[near]))
        t_lo, t_hi = np.percentile(t[near], [0.5, 99.5])
        in_t = near & (t > t_lo) & (t < t_hi)
        z_g = float(np.percentile(zr[near & (zr < zb - band[0])], 1))
        flat = face & (np.abs(w - w_f) < 0.5)
        plane_pct = float((np.abs(w[flat] - w_f) < 0.03).mean() * 100)
        # asiento: superficie bajo los fondos de viga entre la cara frontal y el extremo del tablero;
        # su cota respecto a ZB(t) es el espacio que ocupan los apoyos
        seat = (w > w_f + 0.15) & (w < -0.15) & (zr < zb - 0.02) & (zr > zb - 0.35)
        seat_drop = 0.0
        if seat.sum() > 500:                    # moda (1 cm): el asiento es la superficie dominante
            hd, ed = np.histogram(zr[seat] - zb[seat], bins=np.arange(-0.35, -0.019, 0.01))
            seat_drop = float(np.clip(-(ed[int(np.argmax(np.convolve(hd, np.ones(3), "same")))] + 0.005), 0.0, 0.25))

        # sección (w, tipo): g = base en el suelo, s = asiento (ZB), t = tope de tablero (ZT)
        spec = [(w_f, "g"), (e_back, "g"), (e_back, "t"), (0.0, "t"), (0.0, "s"), (w_f, "s")]

        def zfun(kind, tt):
            return {"g": z_g + 0 * tt, "s": kb * tt + bb - seat_drop, "t": kt * tt + bt}[kind]

        V, rings = [], []
        for tt in (t_lo, t_hi):
            ring = []
            for wv, kind in spec:
                s_end = a + k * tt
                s_ = s_end + sign * wv
                xy = c + s_ * d + tt * n
                V.append((xy[0], xy[1], float(np.polyval(sec.grade, s_end)) + float(zfun(kind, tt))))
                ring.append(len(V) - 1)
            rings.append(ring)
        V = np.array(V)
        K = len(spec)
        T = []
        for i in range(K):
            j = (i + 1) % K
            a0, b0, a1, b1 = rings[0][i], rings[0][j], rings[1][i], rings[1][j]
            T += [(a0, b0, a1), (b0, b1, a1)]
        for r_, flip in ((0, True), (1, False)):
            p2 = np.array([[wv, float(zfun(kind, (t_lo, t_hi)[r_]))] for wv, kind in spec])
            cap = earcut.triangulate_float64(p2, np.array([K], dtype=np.uint32)).reshape(-1, 3)
            cap = cap + (0 if r_ == 0 else K)
            T += [tuple(x[::-1]) if flip else tuple(x) for x in cap]
        T = np.array(T, dtype=np.int64)
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                         o3d.utility.Vector3iVector(T.astype(np.int32)))
        wt = bool(mesh.is_edge_manifold() and mesh.is_watertight())
        vol = float(mesh.get_volume()) if wt else 0.0
        if wt and vol < 0:
            T = T[:, ::-1]
            vol = -vol
        sol = Solid(name, "estribo", "IfcWall", V, T, vol, wt)
        sol.metrics = {"w_front_m": w_f, "seat_width_perp_m": abs(w_f) / np.sqrt(1 + k * k),
                       "t_range_m": [float(t_lo), float(t_hi)], "z_ground_rel_m": z_g,
                       "plane_within_3cm_pct": plane_pct, "face_points": int(near.sum()),
                       "e_back_assumed_m": e_back, "seat_drop_m": seat_drop, "w_end": sign,
                       "end": key}
        out.append(sol)
    return out


# ------------------------------------------------------------------ barandas (rieles y postes)
def _prism(poly: np.ndarray, s0: float, s1: float, sec: SectionResult, name: str, kind: str,
           ifc_class: str, step: float = 5.0) -> Solid:
    """Prisma de la sección (t, z_rel) entre s0 y s1 con extremos perpendiculares y sobre la rasante."""
    import mapbox_earcut as earcut
    import open3d as o3d
    c, d, n = sec.frame
    K = len(poly)
    S = np.linspace(s0, s1, max(2, int((s1 - s0) / step) + 2))
    V = []
    for sv in S:
        for tv, zv in poly:
            xy = c + sv * d + tv * n
            V.append((xy[0], xy[1], float(np.polyval(sec.grade, sv)) + zv))
    V = np.array(V)
    m = len(S)
    T = []
    for k in range(m - 1):
        for i in range(K):
            j = (i + 1) % K
            a, b, c_, d_ = k * K + i, k * K + j, (k + 1) * K + i, (k + 1) * K + j
            T += [(a, b, c_), (b, d_, c_)]
    cap = earcut.triangulate_float64(poly.astype(np.float64), np.array([K], dtype=np.uint32)).reshape(-1, 3)
    T += [tuple(r[::-1]) for r in cap]
    T += [tuple(r + (m - 1) * K) for r in cap]
    T = np.array(T, dtype=np.int64)
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                     o3d.utility.Vector3iVector(T.astype(np.int32)))
    wt = bool(mesh.is_watertight())
    vol = float(mesh.get_volume()) if wt else 0.0
    if wt and vol < 0:
        T, vol = T[:, ::-1], -vol
    return Solid(name, kind, ifc_class, V, T, vol, wt)


def _intervals(mask: np.ndarray, edges: np.ndarray, merge: float, min_w: float):
    """Intervalos contiguos de una máscara sobre bins; une huecos < merge y descarta anchos < min_w."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append([edges[i], edges[j + 1]])
            i = j + 1
        else:
            i += 1
    merged = []
    for iv in out:
        if merged and iv[0] - merged[-1][1] < merge:
            merged[-1][1] = iv[1]
        else:
            merged.append(iv)
    return [iv for iv in merged if iv[1] - iv[0] >= min_w]


def extract_railings(points: np.ndarray, sec: SectionResult, structure: StructureResult,
                     elements: list, bar_height: float = 0.45, post_gap: float = 0.20,
                     post_frac: float = 0.5, tree_filter: bool = True, min_occ: float = 0.5,
                     max_post_spread: float = 0.12, max_post_width: float = 0.6, tree_pts: int = 50,
                     tree_max_run: float = 1.0) -> list:
    """
    Barandas por componentes (la baranda no es continua: rieles continuos + postes cada ~1.5 m).
    1) Región = material a más de `bar_height` sobre el tope de losa, en el rango t de cada baranda
       ya separada (`separate_elements`).
    2) Rieles: el histograma de z (1 cm) tiene picos anchos y densos (tubos horizontales); cada
       intervalo denso es un riel.
    3) Postes: en la franja de z entre los dos primeros rieles solo hay postes; los picos del
       histograma de s (2 cm) dan sus posiciones.
    4) Cada riel se mide con los cortes ENTRE postes (a más de `post_gap` m de cualquiera): rectángulo
       con percentiles 2-98 de t, extendido a lo largo de s. Cada poste se mide en una ventana propia.
    5) Filtro de árboles (`tree_filter`): un candidato a poste se descarta si no es un elemento vertical
       compacto que termina en el riel superior: continuidad en z (fracción de celdas de 5 cm con puntos)
       menor que `min_occ`, dispersión en s de sus puntos no-riel mayor que `max_post_spread`, ancho en t
       mayor que `max_post_width`, material que sigue por encima del riel (tronco, poste de luz) o fuera
       del tramo con rieles. Antes de buscar postes se excluyen los troncos (material compacto de al menos
       `tree_pts` puntos por 0.1 m que sigue sobre el riel superior en un tramo de hasta `tree_max_run` m).
       Los descartes se cuentan en metrics["posts_rejected"] de los rieles del lado.
    """
    (kt, bt), _ = _beam_lines(sec)
    c, d, n = sec.frame
    pts = points[structure.labels == 0]
    out = []
    for el in [e for e in elements if e.kind == "baranda"]:
        tl, th = float(el.polygon[:, 0].min()) - 0.05, float(el.polygon[:, 0].max()) + 0.05
        side = "izquierda" if (tl + th) / 2 < 0 else "derecha"
        sel = []
        for i0 in range(0, len(pts), 8_000_000):
            blk = pts[i0:i0 + 8_000_000]
            q = blk[:, :2] - c
            s, t = q @ d, q @ n
            m = (t > tl) & (t < th)
            if m.any():
                zr = blk[m, 2] - np.polyval(sec.grade, s[m])
                keep = zr > kt * t[m] + bt + bar_height
                sel.append(np.column_stack((s[m][keep], t[m][keep], zr[keep])))
        if not sel:
            continue
        A = np.vstack(sel)
        s, t, zr = A[:, 0], A[:, 1], A[:, 2]
        ze = np.arange(zr.min(), zr.max() + 0.01, 0.01)
        zh = np.convolve(np.histogram(zr, bins=ze)[0], np.ones(3) / 3, "same")
        rails = _intervals(zh > 0.35 * zh.max(), ze, merge=0.03, min_w=0.03)
        if len(rails) < 2:
            continue
        rails.sort()
        # zona de postes: todo lo que no es riel (+-2 cm) bajo el borde superior del último riel
        gm = (zr < rails[-1][1] + 0.02)
        for r0, r1 in rails:
            gm &= ~((zr > r0 - 0.02) & (zr < r1 + 0.02))
        trees = 0
        if tree_filter:
            # troncos y postes de luz: material compacto que sigue por encima del riel superior. Se
            # excluyen del histograma de postes (si no, por su densidad ocultan a los postes vecinos)
            from scipy.ndimage import binary_dilation, label as nd_label
            sb = np.arange(s.min(), s.max() + 0.1, 0.1)
            bad = np.histogram(s[zr > rails[-1][1] + 0.10], bins=sb)[0] >= tree_pts
            runs, nr = nd_label(bad)
            for ri in range(1, nr + 1):
                if (runs == ri).sum() * 0.1 > tree_max_run:      # tramo largo: follaje sobre la baranda, no un tronco
                    bad[runs == ri] = False
            trees = int(nd_label(bad)[1])
            bad = binary_dilation(bad, iterations=3)
            gm &= ~bad[np.clip(np.digitize(s, sb) - 1, 0, len(bad) - 1)]
        se = np.arange(s.min(), s.max() + 0.02, 0.02)
        sh = np.convolve(np.histogram(s[gm], bins=se)[0], np.ones(5) / 5, "same")
        # picos relativos al máximo local (ventana de 4 m): los postes de zonas con menos puntos
        # (oclusión, menor densidad) también se detectan
        from scipy.ndimage import maximum_filter1d
        from scipy.signal import find_peaks
        rel = sh / (maximum_filter1d(sh, size=int(4.0 / 0.02)) + 1e-9)
        pk, _ = find_peaks(rel, height=post_frac, distance=int(0.6 / 0.02))
        pk = [i for i in pk if sh[i] >= 15]
        posts = [0.5 * (se[i] + se[i + 1]) for i in pk]
        posts = np.array(posts)
        far = np.ones(len(s), bool)
        for sp in posts:
            far &= np.abs(s - sp) > post_gap
        rail_start = len(out)
        for r, (z0, z1) in enumerate(rails, 1):
            mr = far & (zr > z0) & (zr < z1)
            if mr.sum() < 500:
                continue
            t_lo, t_hi = np.percentile(t[mr], [2, 98])
            s_lo, s_hi = np.percentile(s[(zr > z0) & (zr < z1)], [0.2, 99.8])
            poly = np.array([[t_lo, z0], [t_hi, z0], [t_hi, z1], [t_lo, z1]])
            sol = _prism(poly, float(s_lo), float(s_hi), sec, f"Riel {r} {side}", "riel", "IfcRailing")
            sol.metrics = {"side": side, "z_rel": [float(z0), float(z1)], "t": [float(t_lo), float(t_hi)],
                           "s": [float(s_lo), float(s_hi)], "points": int(mr.sum())}
            out.append(sol)
        zb0 = float(np.percentile(zr, 0.5))
        zb1 = float(rails[-1][1])
        rail_s = [min(o.metrics["s"][0] for o in out[rail_start:]), max(o.metrics["s"][1] for o in out[rail_start:])] \
            if len(out) > rail_start else [-np.inf, np.inf]
        rejected, kept = trees, 0
        for i, sp in enumerate(posts, 1):
            mp = (np.abs(s - sp) < 0.15) & (zr > zb0) & (zr < zb1 + 0.02)
            if mp.sum() < 100:
                continue
            gp = mp & gm
            spread = float(np.percentile(np.abs(s[gp] - sp), 90)) if gp.sum() > 20 else 0.04
            hw = float(np.clip(spread, 0.02, 0.10))
            t_lo, t_hi = np.percentile(t[mp], [1, 99])
            if tree_filter:
                zbins = np.arange(zb0, zb1, 0.05)
                occ = float((np.histogram(zr[mp], bins=zbins)[0] > 3).mean()) if len(zbins) > 1 else 0.0
                above = int(((np.abs(s - sp) < 0.15) & (zr > zb1 + 0.10) & (zr < zb1 + 0.60)).sum())
                if (occ < min_occ or spread > max_post_spread or (t_hi - t_lo) > max_post_width
                        or above > 0.3 * mp.sum() or sp < rail_s[0] - 0.5 or sp > rail_s[1] + 0.5):
                    rejected += 1
                    continue
            kept += 1
            poly = np.array([[t_lo, zb0], [t_hi, zb0], [t_hi, zb1], [t_lo, zb1]])
            sol = _prism(poly, sp - hw, sp + hw, sec, f"Poste {kept} {side}", "poste", "IfcMember")
            sol.metrics = {"side": side, "s": float(sp), "half_width_s": hw, "points": int(mp.sum())}
            out.append(sol)
        for o in out[rail_start:]:
            o.metrics["posts_rejected"] = rejected
    return out


# ------------------------------------------------------------------ muros de retorno y apoyos
def _frame_solid(poly: np.ndarray, t_a: float, t_b: float, sec: SectionResult, a: float, k: float,
                 sign: int, name: str, kind: str, ifc_class: str) -> Solid:
    """
    Prisma del polígono (w, z_rel) extruido entre t_a y t_b en el marco del extremo del tablero
    (w = distancia hacia afuera desde la cara de extremo; z_rel = z - rasante en ese extremo).
    Prueba las dos orientaciones de las tapas hasta obtener una malla cerrada y orientable.
    """
    import mapbox_earcut as earcut
    import open3d as o3d
    c, d, n = sec.frame
    K = len(poly)
    V = []
    for tt in (t_a, t_b):
        s_end = a + k * tt
        for wv, zv in poly:
            xy = c + (s_end + sign * wv) * d + tt * n
            V.append((xy[0], xy[1], float(np.polyval(sec.grade, s_end)) + float(zv)))
    V = np.array(V)
    side = []
    for i in range(K):
        j = (i + 1) % K
        side += [(i, j, K + i), (j, K + j, K + i)]
    cap = earcut.triangulate_float64(np.asarray(poly, np.float64), np.array([K], dtype=np.uint32)).reshape(-1, 3)
    x_, y_ = np.asarray(poly)[:, 0], np.asarray(poly)[:, 1]
    expected = 0.5 * abs(float(np.dot(x_, np.roll(y_, -1)) - np.dot(y_, np.roll(x_, -1)))) * abs(t_b - t_a)
    best = None
    for flip_side in (False, True):
        sd = [tuple(x[::-1]) for x in side] if flip_side else side
        for flip_cap in (False, True):
            c0 = [tuple(r[::-1]) if flip_cap else tuple(r) for r in cap]
            c1 = [tuple((r + K)[::-1]) if not flip_cap else tuple(r + K) for r in cap]
            T = np.array(sd + c0 + c1, dtype=np.int64)
            Vc = V - V.mean(0)
            vol = float(np.einsum("ij,ij->i", Vc[T[:, 0]], np.cross(Vc[T[:, 1]], Vc[T[:, 2]])).sum() / 6)
            if abs(abs(vol) - expected) < 0.03 * expected:      # orientación correcta: volumen = área x espesor
                mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(Vc),
                                                 o3d.utility.Vector3iVector(T.astype(np.int32)))
                if mesh.is_edge_manifold() and mesh.is_watertight():
                    best = (T, vol)
                    break
        if best:
            break
    if best is None:
        return Solid(name, kind, ifc_class, V, T, 0.0, False)
    T, vol = best
    if vol < 0:
        T, vol = T[:, ::-1], -vol
    return Solid(name, kind, ifc_class, V, T, vol, True)


def extract_wing_walls(points: np.ndarray, sec: SectionResult, width: float, abutments: list,
                       e_back: float = 0.30, thickness: float = 0.30, w_max: float = 16.0,
                       col: float = 0.05, min_height: float = 0.20, min_points: int = 30) -> list:
    """
    Muro de retorno en cada lado de cada estribo, medido con todos los puntos tras el espaldar.
    La nube ve la cara EXTERIOR (vertical, a t ~ +-ancho/2); la cara interior queda bajo el relleno.
    En el marco del extremo (w hacia afuera, z_rel sobre la rasante del extremo):
    1) Cara: pico del histograma de t (1 cm) de los puntos a menos de 0.35 m sobre el tablero
       (así se excluyen barandas y postes) en +-0.4/+0.8 m del borde del tablero.
    2) Perfil: por columnas de `col` m en w, cota alta (p98, limitada a ZT+0.35) y baja (p2, el
       terreno que corta el muro) de los puntos de la cara (+-4 cm). Cuenta como muro la corrida
       continua desde el espaldar de columnas con al menos `min_points` puntos y `min_height` m.
    3) Polígono (w, z) suavizado, simplificado (Douglas-Peucker 3 cm) y extruido hacia el eje
       `thickness` m (SUPUESTO: la cara interior no es visible).
    """
    from scipy.ndimage import median_filter
    from skimage.measure import approximate_polygon
    (kt, bt), _ = _beam_lines(sec)
    c, d, n = sec.frame
    out = []
    for ab in abutments:
        key, sign = ab.metrics["end"], ab.metrics["w_end"]
        a, k = sec.ends[key]
        sel = []
        for i0 in range(0, len(points), 8_000_000):
            blk = points[i0:i0 + 8_000_000]
            q = blk[:, :2] - c
            s, t = q @ d, q @ n
            w = sign * (s - (a + k * t))
            m = (np.abs(t) < width / 2 + 1.0) & (np.abs(t) > width / 2 - 0.5) & (w > e_back - 0.05) & (w < w_max)
            if m.any():
                zr = blk[m, 2] - np.polyval(sec.grade, a + k * t[m])
                sel.append(np.column_stack((w[m], t[m], zr)))
        if not sel:
            continue
        A = np.vstack(sel)
        for side in (-1, 1):
            B = A[np.sign(A[:, 1]) == side]
            if len(B) < 1000:
                continue
            w, t, zr = B[:, 0], B[:, 1], B[:, 2]
            zt = kt * t + bt
            cand = (zr < zt + 0.35) & (zr > zt - 5.0) & (w < e_back + 8.0)
            tt = side * t[cand]
            lo_t, hi_t = width / 2 - 0.4, width / 2 + 0.8
            hist, ed = np.histogram(tt, bins=np.arange(lo_t, hi_t, 0.01))
            if hist.max() < 500:
                continue
            pk = ed[int(np.argmax(np.convolve(hist, np.ones(3), "same")))] + 0.005
            face = (np.abs(side * t - pk) < 0.04) & (zr < zt + 0.35)
            t_f = float(side * np.mean(side * t[face]))
            wf, zf = w[face], zr[face]
            edges = np.arange(e_back, w_max + col, col)
            idx = np.digitize(wf, edges) - 1
            nb = len(edges) - 1
            top = np.full(nb, np.nan)
            bot = np.full(nb, np.nan)
            ok = np.zeros(nb, bool)
            order = np.argsort(idx, kind="stable")
            ids, starts = np.unique(idx[order], return_index=True)
            for u, st, en in zip(ids, starts, list(starts[1:]) + [len(order)]):
                if 0 <= u < nb and en - st >= min_points:
                    zz = zf[order[st:en]]
                    top[u], bot[u] = np.percentile(zz, [98, 2])
                    ok[u] = top[u] - bot[u] >= min_height
            if not ok.any():
                continue
            first = int(np.argmax(ok))
            if edges[first] > e_back + 0.6:
                continue
            last, gap = first, 0
            for u in range(first, nb):
                if ok[u]:
                    last, gap = u, 0
                else:
                    gap += 1
                    if gap * col > 0.25:
                        break
            seg = np.arange(first, last + 1)
            wc = edges[seg] + col / 2
            tp = np.where(ok[seg], top[seg], np.nan)
            bp = np.where(ok[seg], bot[seg], np.nan)
            for arr in (tp, bp):                       # huecos pequeños: interpolar
                good = ~np.isnan(arr)
                arr[~good] = np.interp(wc[~good], wc[good], arr[good])
            win = max(3, int(0.4 / col) | 1)
            tp, bp = median_filter(tp, win, mode="nearest"), median_filter(bp, win, mode="nearest")
            L = float(wc[-1] - wc[0] + col)
            if L < 1.0:
                continue
            tpts = np.column_stack((np.r_[wc[0] - col / 2, wc, wc[-1] + col / 2], np.r_[tp[0], tp, tp[-1]]))
            bpts = np.column_stack((np.r_[wc[0] - col / 2, wc, wc[-1] + col / 2], np.r_[bp[0], bp, bp[-1]]))
            tpts = approximate_polygon(tpts, 0.03)
            bpts = approximate_polygon(bpts, 0.03)
            poly = np.vstack((bpts, tpts[::-1]))            # recorrido: base hacia afuera y tope de vuelta
            poly = poly[np.r_[True, np.any(np.abs(np.diff(poly, axis=0)) > 1e-6, axis=1)]]
            name = (f"Muro de retorno {'inicial' if key == 'lo' else 'final'} "
                    f"{'izquierdo' if side < 0 else 'derecho'}")
            sol = _frame_solid(poly, t_f, t_f - side * thickness, sec, a, k, sign, name, "muro", "IfcWall")
            sol.metrics = {"abutment": ab.name, "side": "izquierdo" if side < 0 else "derecho",
                           "t_face_m": t_f, "length_m": L, "w_range_m": [float(wc[0] - col / 2), float(wc[-1] + col / 2)],
                           "top_z_rel_m": [float(tp.min()), float(tp.max())],
                           "height_m": [float((tp - bp).min()), float((tp - bp).max())],
                           "face_points": int(face.sum()), "thickness_assumed_m": thickness}
            out.append(sol)
    return out


def extract_bearings(abutments: list, sec: SectionResult, elements: list, length: float = 0.35,
                     width_max: float = 0.50, default_height: float = 0.05) -> list:
    """
    Un apoyo bajo cada viga en cada estribo. Medido: posición transversal (centro de cada viga),
    ancho del ala inferior y espesor (diferencia entre el fondo de viga ZB y el asiento medido).
    SUPUESTO: posición longitudinal en la mitad del asiento y largo `length` m (el apoyo queda tapado
    por la viga y no se ve en la nube). Si el estribo no tiene espacio medido, usa `default_height`.
    """
    (kt, bt), (kb, bb) = _beam_lines(sec)
    out = []
    beams = [e for e in elements if e.kind == "viga"]
    for ab in abutments:
        key, sign = ab.metrics["end"], ab.metrics["w_end"]
        a, k = sec.ends[key]
        w_f = ab.metrics["w_front_m"]
        h = ab.metrics.get("seat_drop_m", 0.0)
        measured = h >= 0.01
        if not measured:
            h = default_height
        w_c = w_f / 2.0
        L = min(length, 0.8 * abs(w_f))
        for i, e in enumerate(beams, 1):
            p = e.polygon
            zmin = float(p[:, 1].min())
            low = p[p[:, 1] < zmin + 0.04]
            tc = float(0.5 * (low[:, 0].max() + low[:, 0].min()))
            wd = float(min(width_max, low[:, 0].max() - low[:, 0].min() - 0.10))
            zb = kb * tc + bb
            poly = np.array([[w_c - L / 2, zb - h], [w_c + L / 2, zb - h], [w_c + L / 2, zb], [w_c - L / 2, zb]])
            name = f"Apoyo {'inicial' if key == 'lo' else 'final'} viga {i}"
            sol = _frame_solid(poly, tc - wd / 2, tc + wd / 2, sec, a, k, sign, name, "apoyo", "IfcBearing")
            sol.metrics = {"abutment": ab.name, "beam": e.name, "t_center_m": tc, "width_m": wd,
                           "length_assumed_m": L, "height_m": h, "height_measured": measured,
                           "w_center_assumed_m": w_c}
            out.append(sol)
    return out


# ------------------------------------------------------------------------------- vía
def _fit_plane_line(t, z, tol, rng, it=150):
    """Recta z = a + b*t por RANSAC + mínimos cuadrados sobre los inliers (None si no hay)."""
    best = None
    for _ in range(it):
        i, j = rng.choice(len(t), 2, replace=False)
        if abs(t[i] - t[j]) < 1.0:
            continue
        b = (z[j] - z[i]) / (t[j] - t[i])
        a = z[i] - b * t[i]
        n = int((np.abs(z - (a + b * t)) < tol).sum())
        if best is None or n > best[0]:
            best = (n, a, b)
    if best is None:
        return None
    _, a, b = best
    m = np.abs(z - (a + b * t)) < tol
    return tuple(np.linalg.lstsq(np.c_[np.ones(int(m.sum())), t[m]], z[m], rcond=None)[0])


def _running_median(y, k=5):
    pad = k // 2
    yp = np.pad(y, pad, mode="edge")
    return np.array([np.median(yp[i:i + k]) for i in range(len(y))])


def extract_road(points: np.ndarray, ground: np.ndarray, frame: tuple, s_lo: float, s_hi: float,
                 step: float = 1.0, window: float = 2.0, reach: float = 80.0, core: float = 3.0,
                 tol: float = 0.05, thickness: float = 0.10, t_max: float = 14.0,
                 min_core: int = 30, seed: int = 0):
    """
    Vía de acceso (calzada) a ambos lados del tablero. Se separa del terreno natural porque es
    una superficie plana con peralte constante en cada sección transversal: por tramo de
    `step` m a lo largo del eje se ajusta z = a + b*t (RANSAC) a los puntos de suelo del núcleo
    |t| < `core`; la vía es la franja contigua (huecos <= 0.6 m) de puntos a menos de `tol` m del
    plano. Plano y bordes se suavizan entre tramos (mediana móvil de 5) y la máscara final son
    todos los puntos de suelo dentro de los bordes y del plano.

    frame = (c, d, n) con c (2,) centro, d (2,) eje, n (2,) normal en planta (coords locales);
    s_lo / s_hi: s del borde exterior del tablero en cada extremo (las vías empiezan allí).
    Devuelve (mask (N,) bool sobre `points`, solids, info). El espesor del sólido es supuesto.
    """
    import open3d as o3d
    c, d, n = frame
    idx_g = np.flatnonzero(ground)                  # solo suelo: evita arreglos de N puntos en float64
    q = points[idx_g, :2] - c
    s_g, t_g = q @ d, q @ n
    keep = (np.abs(t_g) < t_max) & ((s_g > s_hi) | (s_g < s_lo))
    idx_c, s, t = idx_g[keep], s_g[keep], t_g[keep]
    z = points[idx_c, 2]
    rng = np.random.default_rng(seed)
    mask = np.zeros(len(points), bool)
    solids, info = [], {"sides": {}}
    for sgn, s_edge, name in ((1.0, s_hi, "final"), (-1.0, s_lo, "inicial")):
        u = sgn * (s - s_edge)                      # distancia desde el borde del tablero (> 0 hacia afuera)
        rows = []
        bins = np.arange(-t_max, t_max + 1e-9, 0.2)
        in_core = np.abs(bins[:-1]) < core
        for u0 in np.arange(0.0, reach, step):
            m = (u >= u0) & (u < u0 + window)
            ts, zs = t[m], z[m]
            cc = np.abs(ts) < core
            if cc.sum() < min_core:
                continue
            f = _fit_plane_line(ts[cc], zs[cc], tol, rng)
            if f is None:
                continue
            a, b = f
            inl = np.abs(zs - (a + b * ts)) < tol
            h, _ = np.histogram(ts[inl], bins)
            hc = h[in_core][h[in_core] > 0]
            if len(hc) == 0:
                continue
            ok = h >= max(2, 0.1 * float(np.median(hc)))
            k0 = int(np.searchsorted(bins, 0.0)) - 1
            if not ok[k0]:
                continue
            lo = k0
            while lo > 0 and ok[max(0, lo - 3):lo].any():
                lo -= 1
            hi = k0
            while hi < len(ok) - 1 and ok[hi + 1:hi + 4].any():
                hi += 1
            while not ok[lo]:
                lo += 1
            while not ok[hi]:
                hi -= 1
            rows.append((u0 + window / 2, a, b, bins[lo], bins[hi + 1]))
        if len(rows) < 5:
            continue
        R = np.array(rows)
        for col in range(1, 5):
            R[:, col] = _running_median(R[:, col], 5)
        uu = np.arange(float(R[0, 0]), float(R[-1, 0]) + 1e-9, step)
        A, B, TL, TR = (np.interp(uu, R[:, 0], R[:, k]) for k in (1, 2, 3, 4))
        # --- máscara: puntos de suelo dentro de bordes y plano
        ui = np.clip(np.searchsorted(uu, u, side="left"), 0, len(uu) - 1)
        inside = (u >= uu[0] - step / 2) & (u <= uu[-1] + step / 2)
        res = z - (A[ui] + B[ui] * t)
        sel = inside & (t >= TL[ui]) & (t <= TR[ui]) & (np.abs(res) < tol)
        mask[idx_c[sel]] = True
        # --- sólido: prisma a lo largo del eje, caras laterales y tapas
        sv = s_edge + sgn * uu
        base = c[None, :] + sv[:, None] * d[None, :]
        top_l = np.c_[base + TL[:, None] * n[None, :], A + B * TL]
        top_r = np.c_[base + TR[:, None] * n[None, :], A + B * TR]
        bot_l, bot_r = top_l.copy(), top_r.copy()
        bot_l[:, 2] -= thickness
        bot_r[:, 2] -= thickness
        m_ = len(uu)
        V = np.concatenate([top_l, top_r, bot_r, bot_l])           # 4 anillos de m_ vértices
        T = []
        for i in range(m_ - 1):
            for r0, r1 in ((0, 1), (1, 2), (2, 3), (3, 0)):
                p, q_, p2, q2 = r0 * m_ + i, r1 * m_ + i, r0 * m_ + i + 1, r1 * m_ + i + 1
                T += [(p, q_, p2), (q_, q2, p2)]
        for e in (0, m_ - 1):                                    # tapas
            a_, b_, c_, d_ = e, m_ + e, 2 * m_ + e, 3 * m_ + e
            T += [(a_, b_, c_), (a_, c_, d_)] if e == 0 else [(a_, c_, b_), (a_, d_, c_)]
        T = np.array(T, dtype=np.int64)
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                         o3d.utility.Vector3iVector(T.astype(np.int32)))
        wt = bool(mesh.is_watertight())
        vol = float(mesh.get_volume()) if wt else 0.0
        if wt and vol < 0:
            T = T[:, ::-1]
            vol = -vol
        sol = Solid(f"Vía {name}", "via", "IfcSlab", V, T, vol, wt)
        sol.metrics = {"length_m": float(uu[-1] - uu[0]), "width_m": float(np.median(TR - TL)),
                       "t_left_m": float(np.median(TL)), "t_right_m": float(np.median(TR)),
                       "cross_slope_pct": float(np.median(B) * 100), "points": int(sel.sum()),
                       "thickness_assumed_m": thickness}
        solids.append(sol)
        info["sides"][name] = sol.metrics
    info["points"] = int(mask.sum())
    return mask, solids, info
