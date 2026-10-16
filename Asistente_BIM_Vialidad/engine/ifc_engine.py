# -*- coding: utf-8 -*-
"""Motor real de coordinación BIM vial.

Lee IFC con ifcopenshell, extrae geometría en coordenadas mundo, detecta choques
(duros y de holgura) entre modelos, calcula Pk sobre un eje y audita calidad.
Sin datos inventados: todo valor sale del archivo o de reglas_mc.json.
"""
import os
import re
import sys
import json
import math
import time
import pickle
import hashlib
import datetime
import collections
from multiprocessing import Pool, cpu_count

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DISC = ["GEO", "PAV", "DRE", "EST", "SEG", "SER"]
DISC_NAMES = {
    "GEO": "Geometría y Mov. Tierras",
    "PAV": "Pavimentos y Calzada",
    "DRE": "Drenaje y Obras de Arte",
    "EST": "Puentes y Estructuras",
    "SEG": "Seguridad Vial y Señaliz.",
    "SER": "Servicios Afectados e ITS",
}
OWNERS = {
    "GEO": "Especialista Trazado y Geotecnia",
    "PAV": "Ingeniería de Pavimentos",
    "DRE": "Especialista Hidráulica y Drenaje",
    "EST": "Ingeniería Estructural y Puentes",
    "SEG": "Especialista Seguridad Vial",
    "SER": "Coord. Servicios Afectados e ITS",
}
# (clave de nombre de archivo, disciplina). Clave >=4 letras: subcadena; corta: token exacto.
FILE_KEYS = [
    ("ILU", "SER"), ("ILUMIN", "SER"), ("ITS", "SER"), ("ELE", "SER"), ("SER", "SER"), ("AFECT", "SER"),
    ("SEV", "SEG"), ("SEG", "SEG"), ("BARRERA", "SEG"), ("PARADERO", "SEG"), ("SENAL", "SEG"), ("DEMARC", "SEG"),
    ("DRE", "DRE"), ("DRENAJE", "DRE"), ("ALCANT", "DRE"), ("HID", "DRE"), ("SAN", "DRE"),
    ("PAV", "PAV"), ("CALZ", "PAV"), ("ASFALT", "PAV"),
    ("EST", "EST"), ("PUENTE", "EST"), ("VIAD", "EST"), ("MURO", "EST"),
    ("GEO", "GEO"), ("TRAZ", "GEO"), ("TERR", "GEO"), ("TOPO", "GEO"),
]
CLASS_DISC = [
    (re.compile(r"IFC(ALIGNMENT|EARTHWORKS|GEOGRAPHIC|SITE)", re.I), "GEO"),
    (re.compile(r"IFC(PAVEMENT|COURSE|KERB)", re.I), "PAV"),
    (re.compile(r"IFC(PIPE|FLOW|DISTRIBUTIONCHAMBER)", re.I), "DRE"),
    (re.compile(r"IFC(BRIDGE|BEAM|COLUMN|FOOTING|PILE|WALL|MEMBER)", re.I), "EST"),
    (re.compile(r"IFC(SIGN|RAILING|DISCRETEACCESSORY)", re.I), "SEG"),
    (re.compile(r"IFC(CABLE|LIGHTFIXTURE|ELECTRIC|OUTLET|DISTRIBUTION)", re.I), "SER"),
]


def _norm(s):
    s = s.upper()
    for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"), ("Ñ", "N")):
        s = s.replace(a, b)
    return s


def detect_discipline(filename, class_counts=None):
    tokens = [t for t in re.split(r"[^A-Z0-9]+", _norm(os.path.splitext(os.path.basename(filename))[0])) if t]
    for key, disc in FILE_KEYS:
        for t in tokens:
            if (len(key) >= 4 and key in t) or t == key:
                return disc, "nombre de archivo"
    if class_counts:
        best, bestn = None, 0
        for rx, disc in CLASS_DISC:
            n = sum(v for k, v in class_counts.items() if rx.match(k.upper()))
            if n > bestn:
                best, bestn = disc, n
        if best:
            return best, "clases IFC"
    return "GEO", "sin pistas (revisar)"


# ----------------------------------------------------------------- geometría
_F = None
_S = None


def _worker_init(path):
    global _F, _S
    import ifcopenshell
    import ifcopenshell.geom
    _F = ifcopenshell.open(path)
    _S = ifcopenshell.geom.settings()
    _S.set("use-world-coords", True)


def _worker_geom(step):
    import ifcopenshell.geom
    try:
        el = _F.by_id(step)
        sh = ifcopenshell.geom.create_shape(_S, el)
        v = np.array(sh.geometry.verts, dtype=np.float64).reshape(-1, 3)
        f = np.array(sh.geometry.faces, dtype=np.int32).reshape(-1, 3)
        return step, v, f, None
    except Exception as e:  # geometría no soportada / vacía
        return step, None, None, str(e)[:120]


def cluster_mesh(v, f, cell):
    """Decimación por agrupación de vértices en celdas de `cell` metros."""
    if len(v) == 0 or len(f) == 0:
        return v, f
    q = np.floor((v - v.min(0)) / cell).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    nv = np.zeros((inv.max() + 1, 3))
    cnt = np.bincount(inv).astype(float)
    for k in range(3):
        nv[:, k] = np.bincount(inv, weights=v[:, k]) / cnt
    nf = inv[f]
    ok = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
    return nv, nf[ok].astype(np.int32)


def decimate_to(v, f, max_tris):
    cell = 0.02
    while len(f) > max_tris and cell < 50:
        v, f = cluster_mesh(v, f, cell)
        cell *= 1.7
    return v, f


def _cache_path(path, cache_dir):
    st = os.stat(path)
    h = hashlib.md5(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|v3".encode()).hexdigest()[:12]
    return os.path.join(cache_dir, f"{os.path.splitext(os.path.basename(path))[0][:40]}_{h}.pkl")


def _psets_summary(el, ue):
    try:
        ps = ue.get_psets(el)
    except Exception:
        return {}
    return {k: v for k, v in ps.items()}


def load_model(path, discipline=None, cache_dir=None, progress=None, max_workers=None):
    """Devuelve dict de modelo con elementos (bbox, malla) y auditoría real."""
    import ifcopenshell
    import ifcopenshell.util.element as ue

    cache_dir = cache_dir or os.path.join(os.path.dirname(HERE), ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    cp = _cache_path(path, cache_dir)
    log = progress or (lambda m, p=None: None)
    fname = os.path.basename(path)
    if os.path.exists(cp):
        with open(cp, "rb") as fh:
            model = pickle.load(fh)
        log(f"{fname}: cargado de caché ({len(model['elements'])} elementos)", 1.0)
        if discipline:
            model["discipline"], model["discipline_src"] = discipline, "indicada por usuario"
        return model

    t0 = time.time()
    log(f"{fname}: abriendo IFC ({os.path.getsize(path) / 1e6:.0f} MB)…", 0.0)
    f = ifcopenshell.open(path)
    schema = f.schema
    els = [e for e in f.by_type("IfcElement")]
    class_counts = collections.Counter(e.is_a() for e in els)
    disc, src = (discipline, "indicada por usuario") if discipline else detect_discipline(fname, class_counts)

    # metadatos y auditoría por elemento
    classif = set()
    for rel in f.by_type("IfcRelAssociatesClassification"):
        for o in rel.RelatedObjects:
            classif.add(o.id())
    elements = []
    n_pset = n_name = n_cls = 0
    for i, e in enumerate(els):
        ps = _psets_summary(e, ue)
        useful = {k: v for k, v in ps.items() if any(kk != "id" for kk in v.keys())}
        has_ps = len(useful) > 0
        name = e.Name or ""
        n_pset += has_ps
        n_name += bool(name.strip())
        n_cls += e.id() in classif
        elements.append({
            "step": e.id(), "guid": e.GlobalId, "type": e.is_a(), "name": name,
            "has_pset": has_ps, "pset_names": list(useful.keys())[:6],
            "has_class": e.id() in classif,
            "has_rep": e.Representation is not None,
        })
    log(f"{fname}: {len(elements)} elementos, extrayendo geometría…", 0.05)

    # geometría en paralelo
    todo = [el["step"] for el in elements if el["has_rep"]]
    size_mb = os.path.getsize(path) / 1e6
    workers = max_workers or (2 if size_mb > 300 else 3 if size_mb > 100 else min(4, max(1, cpu_count() - 1)))
    workers = max(1, min(workers, len(todo) or 1))
    geo = {}
    errors = collections.Counter()
    del f  # liberar memoria en el proceso principal
    if todo:
        with Pool(workers, initializer=_worker_init, initargs=(path,)) as pool:
            done = 0
            for step, v, fa, err in pool.imap_unordered(_worker_geom, todo, chunksize=4):
                done += 1
                if v is not None and len(v) and len(fa):
                    if len(fa) > 6000:
                        v, fa = decimate_to(v, fa, 6000)
                    geo[step] = (v.astype(np.float32), fa)
                else:
                    errors[err or "vacía"] += 1
                if done % 25 == 0 or done == len(todo):
                    log(f"{fname}: geometría {done}/{len(todo)}", 0.05 + 0.9 * done / len(todo))
    for el in elements:
        g = geo.get(el["step"])
        el["has_geom"] = g is not None
        if g is not None:
            v = g[0]
            el["bmin"], el["bmax"] = v.min(0).astype(float).tolist(), v.max(0).astype(float).tolist()

    # georreferenciación
    f = ifcopenshell.open(path) if size_mb < 60 else None
    georef = _read_georef(path, f)

    n = len(elements)
    proxy = sum(1 for e in elements if e["type"] == "IfcBuildingElementProxy")
    n_geom = sum(1 for e in elements if e["has_geom"])
    model = {
        "path": path, "filename": fname, "schema": schema, "discipline": disc, "discipline_src": src,
        "class_counts": dict(class_counts), "elements": elements, "geo": geo,
        "audit": {
            "total": n,
            "pset_pct": round(100 * n_pset / n, 1) if n else 0.0,
            "name_pct": round(100 * n_name / n, 1) if n else 0.0,
            "class_pct": round(100 * n_cls / n, 1) if n else 0.0,
            "geom_pct": round(100 * n_geom / n, 1) if n else 0.0,
            "proxy_pct": round(100 * proxy / n, 1) if n else 0.0,
            "geom_errors": dict(errors),
        },
        "georef": georef, "seconds": round(time.time() - t0, 1),
    }
    bm = [e["bmin"] for e in elements if e["has_geom"]]
    bM = [e["bmax"] for e in elements if e["has_geom"]]
    model["bbox"] = [np.min(bm, 0).tolist(), np.max(bM, 0).tolist()] if bm else None
    with open(cp, "wb") as fh:
        pickle.dump(model, fh, protocol=4)
    log(f"{fname}: listo en {model['seconds']} s", 1.0)
    return model


def _read_georef(path, f):
    """Lee IfcMapConversion / IfcProjectedCRS / IfcSite con regex si el archivo es grande."""
    info = {"mapconversion": None, "crs": None, "crs_valido": False, "site_latlon": None}
    try:
        if f is None:
            import ifcopenshell
            # lectura parcial: sólo cabecera de entidades; abrir completo si hace falta
            f = ifcopenshell.open(path)
        if f.schema != "IFC2X3":
            mc = f.by_type("IfcMapConversion")
            if mc:
                m = mc[0]
                info["mapconversion"] = {"E": m.Eastings, "N": m.Northings, "H": m.OrthogonalHeight}
            crs = f.by_type("IfcProjectedCRS")
            if crs:
                info["crs"] = crs[0].Name
                info["crs_valido"] = bool(crs[0].Name) and not str(crs[0].Name).upper().endswith(":0")
        s = f.by_type("IfcSite")
        if s and s[0].RefLatitude and s[0].RefLongitude:
            info["site_latlon"] = [list(s[0].RefLatitude), list(s[0].RefLongitude)]
    except Exception as e:
        info["error"] = str(e)[:80]
    return info


# -------------------------------------------------------------------- eje / Pk
class Axis:
    def __init__(self, pts, origin="estimado", step=2.0, start_chainage=0.0):
        pts = np.asarray(pts, float)
        seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
        s = np.concatenate([[0], np.cumsum(seg)])
        n = max(2, int(s[-1] / step) + 1)
        ss = np.linspace(0, s[-1], n)
        self.xy = np.c_[np.interp(ss, s, pts[:, 0]), np.interp(ss, s, pts[:, 1])]
        self.s = ss + start_chainage
        self.origin = origin
        self.length = float(s[-1])
        from scipy.spatial import cKDTree
        self.tree = cKDTree(self.xy)

    def locate(self, x, y):
        d, i = self.tree.query([x, y])
        return float(self.s[i]), float(d)

    def polyline(self, maxpts=300):
        k = max(1, len(self.xy) // maxpts)
        return self.xy[::k].round(2).tolist()

    def ticks(self, step=None):
        """Estaciones del eje cada `step` m (Pk, x, y, azimut local)."""
        step = step or (100.0 if self.length <= 2000 else 500.0 if self.length <= 15000 else 1000.0)
        out = []
        s0 = self.s[0]
        m = math.ceil(s0 / step) * step
        while m <= self.s[-1]:
            x = float(np.interp(m, self.s, self.xy[:, 0]))
            y = float(np.interp(m, self.s, self.xy[:, 1]))
            x2 = float(np.interp(m + 1, self.s, self.xy[:, 0]))
            y2 = float(np.interp(m + 1, self.s, self.xy[:, 1]))
            out.append({"pk": m, "txt": fmt_pk(m), "x": round(x, 2), "y": round(y, 2),
                        "ang": round(math.atan2(y2 - y, x2 - x), 4), "major": abs(m % (step * 2)) < 1e-6})
            m += step
        return out


def footprint(v, nbins=12):
    """Contorno 2D liviano de un elemento: punto, polilínea medial o rectángulo."""
    xy = np.asarray(v, float)[:, :2]
    lo, hi = xy.min(0), xy.max(0)
    ext = hi - lo
    if max(ext) < 2.0:
        return {"k": "p", "c": ((lo + hi) / 2).round(2).tolist(), "e": ext.round(2).tolist()}
    c = xy.mean(0)
    u, s, vt = np.linalg.svd(xy - c, full_matrices=False)
    ax = vt[0]
    t = (xy - c) @ ax
    edges = np.linspace(t.min(), t.max(), nbins + 1)
    idx = np.clip(np.digitize(t, edges) - 1, 0, nbins - 1)
    pts = [xy[idx == b].mean(0) for b in range(nbins) if (idx == b).any()]
    w = float(np.ptp((xy - c) @ vt[1]))
    return {"k": "l", "p": np.round(np.array(pts), 2).ravel().tolist(), "w": round(w, 2)}


def estimate_axis(pts_xy):
    """Eje estimado: tronco principal (diámetro del MST) de los centroides, suavizado."""
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import minimum_spanning_tree, dijkstra, connected_components
    pts = np.unique(np.round(np.asarray(pts_xy, float), 1), axis=0)
    if len(pts) < 3:
        return None
    k = min(8, len(pts))
    d, idx = cKDTree(pts).query(pts, k=k)
    rows = np.repeat(np.arange(len(pts)), k - 1)
    cols = idx[:, 1:].ravel()
    w = d[:, 1:].ravel() + 1e-6
    G = coo_matrix((w, (rows, cols)), shape=(len(pts),) * 2).tocsr()
    G = G.maximum(G.T)
    nc, lab = connected_components(G, directed=False)
    big = np.argmax(np.bincount(lab))
    keep = np.where(lab == big)[0]
    G = G[keep][:, keep]
    P = pts[keep]
    T = minimum_spanning_tree(G)
    d0 = dijkstra(T, directed=False, indices=0)
    a = int(np.argmax(np.where(np.isinf(d0), -1, d0)))
    da, pred = dijkstra(T, directed=False, indices=a, return_predecessors=True)
    b = int(np.argmax(np.where(np.isinf(da), -1, da)))
    path = [b]
    while path[-1] != a:
        path.append(int(pred[path[-1]]))
    line = P[path[::-1]]
    # suavizado por media móvil (los centroides alternan lados de la calzada)
    w_ = min(9, max(3, len(line) // 10 * 2 + 1))
    if len(line) > w_:
        ker = np.ones(w_) / w_
        pad = w_ // 2
        ext = np.vstack([np.repeat(line[:1], pad, 0), line, np.repeat(line[-1:], pad, 0)])
        line = np.c_[np.convolve(ext[:, 0], ker, "valid"), np.convolve(ext[:, 1], ker, "valid")]
    if line[0, 1] > line[-1, 1]:  # origen del Pk: extremo de menor Norte (invertible)
        line = line[::-1]
    return line


def axis_from_csv(path):
    pts = []
    with open(path, encoding="utf-8-sig") as fh:
        for ln in fh:
            nums = re.findall(r"-?\d+(?:[.,]\d+)?", ln)
            if len(nums) >= 2:
                pts.append([float(nums[0].replace(",", ".")), float(nums[1].replace(",", "."))])
    return np.array(pts) if len(pts) >= 2 else None


def fmt_pk(m):
    m = max(0.0, m)
    km = int(m // 1000)
    return f"Pk {km}+{m - km * 1000:06.2f}"


# ---------------------------------------------------------------- reglas MC
def load_rules(path=None):
    path = path or os.path.join(os.path.dirname(HERE), "config", "reglas_mc.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rule_for(rules, a, b):
    key = "|".join(sorted([a, b]))
    r = dict(rules.get("defecto", {}))
    r.update(rules.get("pares", {}).get(key, {}))
    r["par"] = key
    return r


# ---------------------------------------------------------------- choques
def _tri_edges(v, f):
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    return v[e[:, 0]], v[e[:, 1]]


def _seg_tri_hits(p0, p1, tris, max_cells=3_000_000):
    """Möller–Trumbore vectorizado: devuelve puntos de intersección segmento×triángulo."""
    out = []
    if len(p0) == 0 or len(tris) == 0:
        return out
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    step = max(1, max_cells // max(1, len(tris)))
    for i in range(0, len(p0), step):
        o = p0[i:i + step, None, :]
        d = (p1[i:i + step] - p0[i:i + step])[:, None, :]
        h = np.cross(d, e2[None])
        a = np.einsum("ijk,jk->ij", h, e1)
        ok = np.abs(a) > 1e-12
        f_ = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
        s = o - v0[None]
        u = f_ * np.einsum("ijk,ijk->ij", s, h)
        q = np.cross(s, e1[None])
        vv = f_ * np.einsum("ijk,ijk->ij", d, q)
        t = f_ * np.einsum("jk,ijk->ij", e2, q)
        hit = ok & (u >= 0) & (vv >= 0) & (u + vv <= 1) & (t >= 0) & (t <= 1)
        ii, jj = np.nonzero(hit)
        if len(ii):
            pts = (p0[i:i + step][ii] + d[ii, 0] * t[ii, jj][:, None])
            out.append(pts)
    return np.vstack(out) if out else []


def _densify(v, f, spacing=0.15, cap=40):
    """Puntos de muestreo sobre la superficie (vértices + subdivisión baricéntrica)."""
    tri = v[f]
    L = np.maximum.reduce([np.linalg.norm(tri[:, 0] - tri[:, 1], axis=1),
                           np.linalg.norm(tri[:, 1] - tri[:, 2], axis=1),
                           np.linalg.norm(tri[:, 2] - tri[:, 0], axis=1)])
    n = np.clip(np.ceil(L / spacing).astype(int), 1, cap)
    pts = [v]
    for nn in np.unique(n):
        sel = tri[n == nn]
        if nn == 1:
            pts.append(sel.mean(1))
            continue
        gi, gj = np.meshgrid(np.arange(nn + 1), np.arange(nn + 1))
        m = (gi + gj) <= nn
        a = (gi[m] / nn)[None, :, None]
        b = (gj[m] / nn)[None, :, None]
        c = 1 - a - b
        P = a * sel[:, 0:1, :] + b * sel[:, 1:2, :] + c * sel[:, 2:3, :]
        pts.append(P.reshape(-1, 3))
    P = np.vstack(pts)
    if len(P) > 120_000:
        P = P[np.random.default_rng(0).choice(len(P), 120_000, replace=False)]
    return P


def _bbox_gap(a_min, a_max, b_min, b_max):
    d = np.maximum(0, np.maximum(np.asarray(a_min) - np.asarray(b_max), np.asarray(b_min) - np.asarray(a_max)))
    return float(np.linalg.norm(d))


def detect_clashes(models, rules, include_intra=False, progress=None, spacing=0.15):
    """Choques duros y de holgura entre elementos de modelos distintos."""
    from shapely import STRtree, box
    from scipy.spatial import cKDTree
    log = progress or (lambda m, p=None: None)
    clashes = []
    tested = 0
    for i in range(len(models)):
        for j in range(i if include_intra else i + 1, len(models)):
            A, B = models[i], models[j]
            rule = rule_for(rules, A["discipline"], B["discipline"])
            clear = float(rule.get("holgura_m", 0.0) or 0.0)
            ea = [e for e in A["elements"] if e["has_geom"]]
            eb = [e for e in B["elements"] if e["has_geom"]]
            if not ea or not eb:
                continue
            tol = clear + 1e-6
            boxes_b = [box(e["bmin"][0] - tol, e["bmin"][1] - tol, e["bmax"][0] + tol, e["bmax"][1] + tol) for e in eb]
            tree = STRtree(boxes_b)
            qa = [box(e["bmin"][0], e["bmin"][1], e["bmax"][0], e["bmax"][1]) for e in ea]
            ia, ib = tree.query(qa, predicate="intersects")
            pairs = []
            for x, y in zip(ia, ib):
                a, b = ea[x], eb[y]
                if A is B and a["step"] >= b["step"]:
                    continue
                if a["bmin"][2] - tol > b["bmax"][2] or b["bmin"][2] - tol > a["bmax"][2]:
                    continue
                pairs.append((a, b))
            log(f"{A['filename']} × {B['filename']}: {len(pairs)} pares candidatos (caja)", None)
            dens_cache = {}
            for k, (a, b) in enumerate(pairs):
                tested += 1
                res = _narrow(A, a, B, b, clear, spacing, dens_cache)
                if res:
                    res.update({"a": a, "b": b, "ia": i, "ib": j, "rule": rule})
                    clashes.append(res)
                if k % 200 == 0:
                    log(f"{A['filename']} × {B['filename']}: pares {k}/{len(pairs)}", None)
    return clashes, tested


def _tris_in(v, f, lo, hi):
    tri = v[f]
    m = (tri.max(1) >= lo).all(1) & (tri.min(1) <= hi).all(1)
    return tri[m]


def _narrow(A, a, B, b, clear, spacing, cache):
    va, fa = A["geo"][a["step"]]
    vb, fb = B["geo"][b["step"]]
    va = va.astype(np.float64)
    vb = vb.astype(np.float64)
    lo = np.maximum(a["bmin"], b["bmin"]) - clear - 1e-3
    hi = np.minimum(a["bmax"], b["bmax"]) + clear + 1e-3
    # 1) choque duro: aristas de una malla atravesando triángulos de la otra
    ta = _tris_in(va, fa, lo, hi)
    tb = _tris_in(vb, fb, lo, hi)
    pt = None
    if len(ta) and len(tb):
        ea0, ea1 = ta.reshape(-1, 3), np.roll(ta, -1, axis=1).reshape(-1, 3)
        eb0, eb1 = tb.reshape(-1, 3), np.roll(tb, -1, axis=1).reshape(-1, 3)
        h1 = _seg_tri_hits(ea0, ea1, tb)
        h2 = _seg_tri_hits(eb0, eb1, ta)
        hits = [h for h in (h1, h2) if len(h)]
        if hits:
            pt = np.vstack(hits)
    if pt is not None and len(pt):
        c = pt.mean(0)
        return {"kind": "choque", "distance": 0.0, "point": c.tolist(), "n_hits": int(len(pt)),
                "zone": [pt.min(0).tolist(), pt.max(0).tolist()], "gap": None,
                "overlap": [float(x) for x in (np.minimum(a["bmax"], b["bmax"]) - np.maximum(a["bmin"], b["bmin"]))]}
    if clear <= 0:
        return None
    # 2) holgura: distancia mínima entre superficies muestreadas
    ka = ("A", a["step"], id(A))
    kb = ("B", b["step"], id(B))
    if ka not in cache:
        cache[ka] = _densify(va, fa, spacing)
    if kb not in cache:
        cache[kb] = _densify(vb, fb, spacing)
    pa, pb = cache[ka], cache[kb]
    ma = (pa >= lo - clear).all(1) & (pa <= hi + clear).all(1)
    mb = (pb >= lo - clear).all(1) & (pb <= hi + clear).all(1)
    pa, pb = pa[ma], pb[mb]
    if len(pa) == 0 or len(pb) == 0:
        pa, pb = cache[ka], cache[kb]
    from scipy.spatial import cKDTree
    d, ix = cKDTree(pb).query(pa)
    k = int(np.argmin(d))
    dist = float(d[k])
    if dist >= clear:
        return None
    mid = (pa[k] + pb[ix[k]]) / 2
    return {"kind": "holgura", "distance": round(dist, 3), "point": mid.tolist(), "n_hits": 0,
            "zone": None, "gap": [pa[k].tolist(), pb[ix[k]].tolist()],
            "overlap": [0.0, 0.0, 0.0]}


# ---------------------------------------------------------------- análisis
def severity_of(c):
    r = c["rule"]
    if c["kind"] == "choque":
        return r.get("choque", "Crítica")
    clear = float(r.get("holgura_m", 0) or 1)
    return "Mayor" if c["distance"] < 0.5 * clear else "Moderada"


def analyze(files, rules=None, axis_csv=None, include_intra=False, progress=None, cache_dir=None, spacing=0.15):
    """files: lista de dict {path, discipline?}."""
    log = progress or (lambda m, p=None: None)
    rules = rules or load_rules()
    models = []
    for k, spec in enumerate(files):
        models.append(load_model(spec["path"], spec.get("discipline"), cache_dir, log))
    return build_result(models, rules, axis_csv, include_intra, log, spacing)


def build_result(models, rules, axis_csv=None, include_intra=False, progress=None, spacing=0.15):
    log = progress or (lambda m, p=None: None)
    t0 = time.time()
    # eje / Pk
    axis, axis_src = None, "sin eje"
    if axis_csv and os.path.exists(axis_csv):
        pts = axis_from_csv(axis_csv)
        if pts is not None:
            axis, axis_src = Axis(pts, "archivo CSV"), "archivo CSV de usuario"
    if axis is None:
        cen = []
        for m in models:
            for e in m["elements"]:
                if e["has_geom"]:
                    cen.append([(e["bmin"][0] + e["bmax"][0]) / 2, (e["bmin"][1] + e["bmax"][1]) / 2])
        line = estimate_axis(cen) if cen else None
        if line is not None:
            axis, axis_src = Axis(line, "estimado"), "estimado de centroides (sin IfcAlignment ni CSV)"
    log("Detectando choques…", None)
    clashes, tested = detect_clashes(models, rules, include_intra, log, spacing)

    # Pk, tramo, severidad
    for c in clashes:
        x, y, z = c["point"]
        if axis:
            pk, off = axis.locate(x, y)
            c["pk"], c["offset"] = pk, off
        else:
            c["pk"], c["offset"] = None, None
        c["severity"] = severity_of(c)
    sev_rank = {"Crítica": 0, "Mayor": 1, "Moderada": 2}
    clashes.sort(key=lambda c: (sev_rank.get(c["severity"], 3), c["pk"] if c["pk"] is not None else 0))
    for n, c in enumerate(clashes, 1):
        c["id"] = f"VIAL-{n:04d}"
    tramo_len = 1000.0
    issues = []
    for c in clashes:
        A, B = models[c["ia"]], models[c["ib"]]
        da, db = A["discipline"], B["discipline"]
        a, b = c["a"], c["b"]
        if c["pk"] is not None:
            k0 = int(c["pk"] // tramo_len)
            level = f"Pk {k0}+000 – {k0 + 1}+000"
            pk_txt = fmt_pk(c["pk"])
        else:
            level, pk_txt = "Sin eje (Pk no calculable)", "—"
        r = c["rule"]
        if c["kind"] == "choque":
            hecho = f"Intersección geométrica de mallas ({c['n_hits']} puntos de cruce)"
        else:
            hecho = f"Distancia mínima {c['distance']:.2f} m < holgura requerida {r.get('holgura_m')} m"
        norma = r.get("norma", "")
        ver = "" if r.get("verificado") else " [regla de ejemplo, verificar con MC vigente]"
        desc = (f"{pk_txt}: {hecho}. «{a['name'][:50]}» ({a['type']} {('#' + str(a['step']))}, {A['filename']}) × "
                f"«{b['name'][:50]}» ({b['type']} #{b['step']}, {B['filename']}). "
                f"{r.get('descripcion', '')} Criterio: {norma}{ver}").strip()
        clear = float(r.get("holgura_m") or 0)
        if c["kind"] == "choque":
            ov = [x for x in c["overlap"] if x > 1e-3]
            pen = min(ov) if ov else 0.0
            deficit = round(max(pen, 0.0), 2)
            accion = (f"Eliminar la intersección: desplazar o redefinir uno de los dos elementos "
                      f"(penetración aproximada en caja: {pen:.2f} m"
                      + (f"; más holgura requerida {clear:.2f} m" if clear else "") + ").")
        else:
            deficit = round(max(clear - c["distance"], 0.0), 2)
            accion = f"Aumentar la separación en al menos {deficit:.2f} m (hoy {c['distance']:.2f} m; requerido {clear:.2f} m)."
        issues.append({
            "zone": c.get("zone"), "gap": c.get("gap"), "deficit": deficit, "accion": accion,
            "id": c["id"], "key": hashlib.md5(f"{a['guid']}|{b['guid']}|{c['kind']}".encode()).hexdigest()[:12],
            "elem": f"{a['type']} #{a['step']} × {b['type']} #{b['step']}",
            "pair": f"{da} × {db}", "specs": [da, db], "level": level, "desc": desc,
            "severity": c["severity"], "owner": OWNERS.get(db, "Coordinación BIM Vial"), "status": "Activa",
            "kind": c["kind"], "distance": c["distance"], "pk": c["pk"], "pk_text": pk_txt,
            "offset": c["offset"], "point": [round(x, 3) for x in c["point"]],
            "guidA": a["guid"], "guidB": b["guid"], "stepA": a["step"], "stepB": b["step"],
            "nameA": a["name"], "nameB": b["name"], "fileA": A["filename"], "fileB": B["filename"],
            "ia": c["ia"], "ib": c["ib"], "rule": {k: r.get(k) for k in ("par", "holgura_m", "norma", "verificado", "descripcion")},
        })
    # matriz real (incluye diagonal = misma especialidad entre modelos distintos)
    matrix = [[0] * 6 for _ in range(6)]
    for it in issues:
        i, j = DISC.index(it["specs"][0]), DISC.index(it["specs"][1])
        matrix[i][j] += 1
        if i != j:
            matrix[j][i] += 1

    # auditoría de modelos
    mods = []
    for m in models:
        au = m["audit"]
        comp = round(np.mean([au["pset_pct"], au["geom_pct"], au["name_pct"], 100 - au["proxy_pct"]]), 1)
        ndi = ndi_of(comp, rules)
        mods.append({
            "code": m["discipline"], "name": DISC_NAMES[m["discipline"]], "file": m["filename"], "format": m["schema"],
            "elements": au["total"], "lod": ndi, "pset": au["pset_pct"], "proxy": au["proxy_pct"],
            "geom_pct": au["geom_pct"], "name_pct": au["name_pct"], "class_pct": au["class_pct"],
            "completitud": comp, "disc_src": m["discipline_src"], "georef": m["georef"],
            "bbox": m["bbox"], "geom_errors": m["audit"]["geom_errors"], "seconds": m["seconds"],
            "foot": model_footprints(m),
        })
    # coherencia de coordenadas entre modelos
    geo_warn = []
    bb = [m["bbox"] for m in models if m["bbox"]]
    if len(bb) > 1:
        lo = np.max([b[0] for b in bb], 0)
        hi = np.min([b[1] for b in bb], 0)
        if (hi[:2] < lo[:2]).any():
            geo_warn.append("Las cajas de los modelos NO se solapan en planta: posible sistema de coordenadas distinto.")
    crs_ok = sum(1 for m in models if m["georef"].get("crs_valido"))
    comp_avg = round(float(np.mean([x["completitud"] for x in mods])), 1) if mods else 0.0
    proxy_avg = round(float(np.mean([x["proxy"] for x in mods])), 1) if mods else 0.0
    total_el = int(sum(m["audit"]["total"] for m in models))
    tramos = collections.Counter(i["level"] for i in issues)
    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1), "pairs_tested": tested,
        "totalElements": total_el, "models": mods, "matrix": matrix, "issues": issues,
        "axis": {"source": axis_src, "length": round(axis.length, 1) if axis else None,
                 "polyline": axis.polyline() if axis else [], "ticks": axis.ticks() if axis else []},
        "georef": {"crs_valid_models": crs_ok, "models": len(models), "warnings": geo_warn},
        "kpi": {"avgCompleteness": comp_avg, "avgProxy": proxy_avg, "tramos": dict(tramos)},
        "rules_version": rules.get("version"),
    }


def ndi_of(comp, rules):
    t = rules.get("ndi_umbrales", {"4": 95, "3": 85, "2": 70})
    for lvl in ("4", "3", "2"):
        if comp >= t.get(lvl, 999):
            return f"NDI {lvl} (est.)"
    return "NDI 1 (est.)"


# ------------------------------------------------------------ malla para visor
def model_footprints(m):
    if "_foot" not in m:
        foot = []
        for e in m["elements"]:
            if not e["has_geom"]:
                continue
            fp = footprint(m["geo"][e["step"]][0])
            fp["s"] = e["step"]
            foot.append(fp)
        m["_foot"] = foot
    return m["_foot"]


def issue_mesh(model_a, step_a, model_b, step_b, centre, max_tris=2500, radius=25.0, ctx_tris=300, ctx_max=40):
    """Mallas de los dos elementos + contexto (otros elementos de ambos modelos a < radius m)."""
    c = np.asarray(centre, float)
    out = {"ctx": []}
    for tag, m, s in (("a", model_a, step_a), ("b", model_b, step_b)):
        v, f = m["geo"][s]
        v = v.astype(np.float64)
        v, f = decimate_to(v, f, max_tris)
        out[tag] = {"v": (v - c).round(3).ravel().tolist(), "f": f.ravel().tolist()}
    for tag, m, s in (("a", model_a, step_a), ("b", model_b, step_b)):
        cand = []
        for e in m["elements"]:
            if not e["has_geom"] or e["step"] == s:
                continue
            d = _bbox_gap(e["bmin"], e["bmax"], c - radius, c + radius)
            if d <= 0:
                mid = (np.asarray(e["bmin"]) + np.asarray(e["bmax"])) / 2
                cand.append((float(np.linalg.norm(mid - c)), e))
        cand.sort(key=lambda t: t[0])
        for _, e in cand[:ctx_max]:
            v, f = m["geo"][e["step"]]
            v, f = decimate_to(v.astype(np.float64), f, ctx_tris)
            out["ctx"].append({"m": tag, "name": e["name"][:40], "v": (v - c).round(2).ravel().tolist(), "f": f.ravel().tolist()})
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ifc", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--axis")
    a = ap.parse_args()
    res = analyze([{"path": p} for p in a.ifc], axis_csv=a.axis,
                  progress=lambda m, p=None: print(m, flush=True))
    s = {k: v for k, v in res.items() if k not in ("issues", "axis")}
    print(json.dumps(s, indent=1, ensure_ascii=False, default=str)[:3000])
    print("issues:", len(res["issues"]))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False)
