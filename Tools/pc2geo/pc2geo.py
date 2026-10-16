"""pc2geo v2: de nube de puntos a geometria parametrica de un puente de vigas.
Entrada: puntos locales P (N,3) [+ normales N], colores C, semilla aproximada (cx, cy, angulo, largo, ancho).
Salida: marco del puente, plano de calzada, cajas (losa, voladizos, almas, alas, baranda, bordillo, postes),
malla de terreno (TIN) y reporte de ajuste contra la nube."""
import numpy as np
from scipy import ndimage as ndi
from scipy.signal import find_peaks
from scipy.spatial import Delaunay

# ------------------------------------------------------------------ carga
def load_las(path, voxel=0.05, origin=(0.0, 0.0, 0.0), chunk=4_000_000):
    import laspy
    org = np.array(origin, float); keys = []; pts = []; cols = []
    with laspy.open(path) as f:
        o = np.array([f.header.mins[0], f.header.mins[1], f.header.mins[2]]) - org
        for ch in f.chunk_iterator(chunk):
            X = np.column_stack((np.asarray(ch.x), np.asarray(ch.y), np.asarray(ch.z))) - org
            ij = np.floor((X - o) / voxel).astype(np.int64)
            k = (ij[:, 0] * 200003 + ij[:, 1]) * 20011 + ij[:, 2]
            _, first = np.unique(k, return_index=True)
            keys.append(k[first]); pts.append(X[first].astype(np.float32))
            try: cols.append((np.column_stack((ch.red[first], ch.green[first], ch.blue[first])) >> 8).astype(np.uint8))
            except Exception: cols.append(np.full((len(first), 3), 128, np.uint8))
    K = np.concatenate(keys); _, first = np.unique(K, return_index=True)
    return np.concatenate(pts)[first], np.concatenate(cols)[first]

# ------------------------------------------------------------------ suelo
def ground_height(P, cs=0.5):
    x0, y0 = P[:, 0].min(), P[:, 1].min()
    gx = ((P[:, 0] - x0) / cs).astype(int); gy = ((P[:, 1] - y0) / cs).astype(int)
    GW, GH = gx.max() + 1, gy.max() + 1
    zm = np.full(GW * GH, np.inf, np.float32); np.minimum.at(zm, gy * GW + gx, P[:, 2].astype(np.float32))
    a = np.where(np.isinf(zm), np.nan, zm).reshape(GH, GW)
    for _ in range(25):
        if not np.isnan(a).any(): break
        pad = np.pad(a, 1, constant_values=np.nan)
        st = np.stack([pad[1 + dy:1 + dy + GH, 1 + dx:1 + dx + GW] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
        with np.errstate(all='ignore'): mn = np.nanmin(st, 0)
        a = np.where(np.isnan(a), mn, a)
    surf = np.where(np.isnan(a), np.nanmedian(a), a); prev = 0
    for w in [1, 2, 3, 5, 8, 12, 16, 24]:
        op = ndi.maximum_filter(ndi.minimum_filter(surf, size=2 * w + 1, mode='nearest'), size=2 * w + 1, mode='nearest')
        dh = min(0.3 + np.tan(np.radians(25)) * (w - prev) * cs * 1.5, 2.5)
        surf = np.where(surf - op > dh, op, surf); prev = w
    return P[:, 2] - surf[gy, gx]

# ------------------------------------------------------------------ marco
def frame(c, ang_deg):
    a = np.array([np.cos(np.radians(ang_deg)), np.sin(np.radians(ang_deg))]); return np.asarray(c, float), a, np.array([-a[1], a[0]])

def to_frame(P, c, a, b):
    d = P[:, :2] - c; return d @ a, d @ b

def fit_plane(A, B, Z, it=5):
    M = np.column_stack((np.ones(len(A)), A, B)); co = np.linalg.lstsq(M, Z, rcond=None)[0]
    for _ in range(it):
        r = Z - M @ co; k = np.abs(r) < max(0.03, 1.5 * np.std(r)); co = np.linalg.lstsq(M[k], Z[k], rcond=None)[0]
    return co, float(np.std((Z - M @ co)[k]))

def _rail_peaks(B, wd, step=0.05):
    h, e = np.histogram(B, bins=np.arange(-wd, wd, step)); hs = np.convolve(h, np.ones(5) / 5, 'same')
    if hs.max() == 0: return None, hs, e
    pk, _ = find_peaks(hs, height=hs.max() * 0.35, distance=int(2.0 / step))
    return pk, hs, e

def refine_frame(P, hag, seed, N=None, iters=5):
    cx, cy, ang, Lg, Wd = seed; log = []; co = None; sd = None; rails = None
    for it in range(iters):
        c, a, b = frame((cx, cy), ang); A, B = to_frame(P, c, a, b)
        # 1) cota de la calzada: pico de Z en la zona central, y plano robusto sobre esa capa
        wide = (np.abs(A) < Lg / 2 + 6) & (np.abs(B) < Wd / 2 + 8) & (hag > 1.0)
        if N is not None: wide &= N[:, 2] > 0.95
        if wide.sum() < 500: log.append(f"iter {it}: pocos puntos elevados cerca de la semilla"); break
        zz = P[wide, 2]; hz, ez = np.histogram(zz, bins=np.arange(zz.min(), zz.max() + 0.1, 0.1))
        z0 = ez[np.argmax(np.convolve(hz, np.ones(3), 'same'))] + 0.05          # cota dominante = calzada
        win = wide & (np.abs(P[:, 2] - z0) < 0.7)
        co, sd = fit_plane(A[win], B[win], P[win, 2])
        win2 = win & (np.abs(P[:, 2] - (co[0] + co[1] * A + co[2] * B)) < 0.15) & (np.abs(B) < Wd / 2 - 0.8)
        if win2.sum() > 500: co, sd = fit_plane(A[win2], B[win2], P[win2, 2])
        # 2) barandas: maximizar nitidez lateral variando el angulo
        best = None
        for d in np.arange(-6, 6.01, 0.25):
            c2, a2, b2 = frame((cx, cy), ang + d); A2, B2 = to_frame(P, c2, a2, b2)
            H2 = P[:, 2] - (co[0] + co[1] * A2 + co[2] * B2)
            s = (np.abs(A2) < Lg / 2) & (np.abs(B2) < Wd / 2 + 8) & (H2 > 0.4) & (H2 < 1.5)
            h, _ = np.histogram(B2[s], bins=np.arange(-Wd / 2 - 8, Wd / 2 + 8, 0.05))
            sc = float((h.astype(float) ** 2).sum())
            if best is None or sc > best[0]: best = (sc, d)
        ang += best[1]
        c, a, b = frame((cx, cy), ang); A, B = to_frame(P, c, a, b)
        H = P[:, 2] - (co[0] + co[1] * A + co[2] * B)
        s = (np.abs(A) < Lg / 2) & (np.abs(B) < Wd / 2 + 8) & (H > 0.4) & (H < 1.5)
        pk, hs, e = _rail_peaks(B[s], Wd / 2 + 8)
        pair = None
        if pk is not None and len(pk) >= 2:      # par de picos con separacion ~ ancho esperado, el de mayor masa
            cand = [(hs[i] + hs[j], i, j) for ii, i in enumerate(pk) for j in pk[ii + 1:] if abs((e[j] - e[i]) - Wd) < 3.0]
            if cand: _, i, j = max(cand); pair = (i, j)
        if pair:
            left, right = e[pair[0]] + 0.025, e[pair[1]] + 0.025; shift = (left + right) / 2
            cx, cy = c + shift * b; rails = (left - shift, right - shift)
            log.append(f"iter {it}: giro {best[1]:+.2f} deg, barandas {left:.2f}/{right:.2f} (ancho {right-left:.2f}), centro {shift:+.2f} m, plano sd {sd:.3f}")
        else:
            log.append(f"iter {it}: giro {best[1]:+.2f} deg, sin 2 lineas de baranda")
    if co is None: return None
    c, a, b = frame((cx, cy), ang); A, B = to_frame(P, c, a, b)
    H = P[:, 2] - (co[0] + co[1] * A + co[2] * B)
    m = (np.abs(B) < 5) & (np.abs(H) < 0.35) & (hag > 1.0)
    h, e = np.histogram(A[m], bins=np.arange(-80, 80, 0.5)); occ = h > max(10, 0.3 * np.median(h[h > 0]))
    i0 = int(np.searchsorted(e, 0.0)) - 1
    lo = i0
    while lo > 0 and occ[lo - 1]: lo -= 1
    hi = i0
    while hi < len(occ) - 1 and occ[hi + 1]: hi += 1
    return dict(c=c, a=a, b=b, ang=ang % 180, plane=co, sd=sd, A0=float(e[lo]), A1=float(e[hi + 1]), rails=rails, log=log)

# ------------------------------------------------------------------ medicion de seccion
def measure_section(P, fr, mid=(-0.3, 0.0)):
    c, a, b, co = fr['c'], fr['a'], fr['b'], fr['plane']
    A, B = to_frame(P, c, a, b); H = P[:, 2] - (co[0] + co[1] * A + co[2] * B)
    L_ = fr['A1'] - fr['A0']; a0 = fr['A0'] + L_ * (0.5 + mid[0]); a1 = fr['A0'] + L_ * (0.5 + mid[1])
    sec = (A > a0) & (A < a1) & (np.abs(B) < 8); out = {}
    m = sec & (np.abs(B) < 5) & (H > -0.8) & (H < 0.5)
    hh, e = np.histogram(H[m], bins=np.arange(-0.8, 0.5, 0.01)); hh = np.convolve(hh, np.ones(3) / 3, 'same'); pk, _ = find_peaks(hh, height=hh.max() * 0.2, distance=14)
    pk = sorted(pk, key=lambda i: -hh[i])[:2]; hv = sorted(float(e[i]) for i in pk)
    out['losa_bot'], out['losa_top'] = hv[0], hv[-1]
    m = sec & (H > out['losa_bot'] - 1.8) & (H < out['losa_bot'] - 0.1) & (np.abs(B) < 7.5)
    hb, eb = np.histogram(B[m], bins=np.arange(-7.5, 7.5, 0.05)); hs = np.convolve(hb, np.ones(3) / 3, 'same')
    pk, _ = find_peaks(hs, height=hs.max() * 0.4, distance=20); webs = []
    for p in pk:                                       # centro grueso; caras finas a 1 cm dentro de +-0.6 m
        cb = eb[p] + 0.025; v = B[m & (np.abs(B - cb) < 0.6)]
        h1, e1 = np.histogram(v, bins=np.arange(cb - 0.6, cb + 0.6, 0.01)); h1 = np.convolve(h1, np.ones(3) / 3, 'same')
        ok = np.where(h1 > h1.max() * 0.35)[0]; webs.append((float(e1[ok[0]]), float(e1[ok[-1] + 1])))
    out['webs'] = webs
    # fondo de vigas: pico horizontal mas profundo bajo las almas (evita el terreno del cauce)
    inweb = np.zeros(len(P), bool)
    for l, r in webs: inweb |= (B > l - 0.45) & (B < r + 0.45)
    m = sec & inweb & (H < out['losa_bot'] - 0.4) & (H > out['losa_bot'] - 3.2)
    hh, e = np.histogram(H[m], bins=np.arange(out['losa_bot'] - 3.2, out['losa_bot'] - 0.4, 0.02))
    pk, _ = find_peaks(hh, height=hh.max() * 0.3, distance=5)
    out['fondo'] = float(e[pk[0]]) if len(pk) else None   # pico mas profundo
    # alas inferiores
    flanges = []
    for l, r in webs:
        if out['fondo'] is None: break
        s = sec & (H > out['fondo'] - 0.05) & (H < out['fondo'] + 0.25) & (np.abs(B - (l + r) / 2) < 1.0)
        if s.sum() > 200: flanges.append(tuple(float(x) for x in np.percentile(B[s], [3, 97])))
        else: flanges.append((l - 0.25, r + 0.25))
    out['flanges'] = flanges
    s = sec & (H > out['losa_bot'] - 0.05) & (H < out['losa_top'] + 0.05) & (np.abs(B) < 7.2)
    out['losa_B'] = tuple(float(x) for x in np.percentile(B[s], [0.3, 99.7]))
    return out

# ------------------------------------------------------------------ barandas
def measure_rails(P, fr, rails_B):
    c, a, b, co = fr['c'], fr['a'], fr['b'], fr['plane']
    A, B = to_frame(P, c, a, b); H = P[:, 2] - (co[0] + co[1] * A + co[2] * B)
    out = {}
    a_lo, a_hi = fr['A0'] + 0.4, fr['A1'] - 0.4
    for nm, rb in (("izq", rails_B[0]), ("der", rails_B[1])):
        s0 = (np.abs(B - rb) < 0.45) & (A > a_lo) & (A < a_hi) & (H > -0.1) & (H < 1.9)
        nb = int((a_hi - a_lo) / 0.1)
        hb = np.arange(-0.1, 1.9, 0.05); occ = []
        for h0 in hb:
            s = s0 & (H >= h0) & (H < h0 + 0.05)
            cnt, _ = np.histogram(A[s], bins=nb, range=(a_lo, a_hi)); occ.append(np.mean(cnt >= 2))
        occ = np.array(occ); isr = occ > 0.6
        bands = []; i = 0
        while i < len(isr):
            if isr[i]:
                j = i
                while j + 1 < len(isr) and isr[j + 1]: j += 1
                bands.append((float(hb[i]), float(hb[j] + 0.05))); i = j + 1
            else: i += 1
        bands = [bd for bd in bands if bd[1] - bd[0] >= 0.08]
        curb = bands[0] if bands and bands[0][0] < 0.35 else None
        rail = [bd for bd in bands if bd is not curb]
        if rail:
            g0 = curb[1] if curb else 0.0; gaps = [(g0, rail[0][0])] + [(rail[k][1], rail[k + 1][0]) for k in range(len(rail) - 1)]
            g = max(gaps, key=lambda t: t[1] - t[0])
        else: g = None
        posts = []; pb = None
        if g and g[1] - g[0] > 0.1:
            sp = s0 & (H > g[0] + 0.03) & (H < g[1] - 0.03)
            cnt, e = np.histogram(A[sp], bins=np.arange(a_lo, a_hi, 0.05)); hs = np.convolve(cnt, np.ones(3) / 3, 'same')
            pk, _ = find_peaks(hs, height=hs.max() * 0.35, distance=int(0.8 / 0.05), prominence=hs.max() * 0.2)
            posts = [float(e[i] + 0.025) for i in pk]
            if posts:
                sel = np.zeros(len(P), bool)
                for x in posts: sel |= np.abs(A - x) < 0.3
                bs = B[sp & sel[np.arange(len(P))]] if False else B[sp & sel]
                pb = tuple(float(v) for v in np.percentile(bs, [2, 98])) if len(bs) > 50 else None
        sr = s0 & np.any([(H >= bd[0]) & (H <= bd[1]) for bd in rail], axis=0) if rail else None
        rb_ext = tuple(float(v) for v in np.percentile(B[sr], [5, 95])) if sr is not None and sr.sum() > 100 else (rb - 0.15, rb + 0.15)
        out[nm] = dict(curb=curb, rails=rail, post_gap=g, posts=posts, post_B=pb, rail_B=rb_ext)
    return out

# ------------------------------------------------------------------ cajas y mallas
def build_boxes(fr, ms, rl, post_w=0.06):
    a0, a1 = fr['A0'], fr['A1']; bx = []
    bl, br = ms['losa_B']; bx.append(("Losa", a0, a1, bl, br, ms['losa_bot'], ms['losa_top']))
    bx.append(("Voladizo izq", a0, a1, bl, ms['webs'][0][0] - 0.0 if ms['webs'] else bl + 0.5, ms['losa_bot'] - 0.12, ms['losa_bot'])) if False else None
    for i, ((l, r), (fl, fr_)) in enumerate(zip(ms['webs'], ms['flanges'])):
        bx.append((f"Alma {i+1}", a0, a1, l, r, ms['fondo'], ms['losa_bot']))
        bx.append((f"Ala inferior {i+1}", a0, a1, fl, fr_, ms['fondo'], ms['fondo'] + 0.20))
    for nm in ("izq", "der"):
        d = rl[nm]; sgn = -1 if nm == "izq" else 1
        if d['curb']:
            bx.append((f"Bordillo {nm}", a0, a1, min(d['rail_B'][0], d['rail_B'][1]) - 0.25, max(d['rail_B'][0], d['rail_B'][1]) + 0.25, d['curb'][0], d['curb'][1]))
        for k, (h0, h1) in enumerate(d['rails']): bx.append((f"Riel {nm} {k+1}", a0, a1, d['rail_B'][0], d['rail_B'][1], h0, h1))
        if d['posts'] and d['post_B'] and d['post_gap']:
            top = d['rails'][-1][1] if d['rails'] else d['post_gap'][1]; bot = d['curb'][1] if d['curb'] else d['post_gap'][0]
            for k, x in enumerate(d['posts']): bx.append((f"Poste {nm} {k+1}", x - post_w / 2, x + post_w / 2, d['post_B'][0], d['post_B'][1], bot, top))
    return bx

def boxes_to_mesh(boxes, fr):
    c, a, b, co = fr['c'], fr['a'], fr['b'], fr['plane']; V = []; F = []
    for nm, x0, x1, y0, y1, z0, z1 in boxes:
        base = len(V)
        for (aa, bb, hh) in [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]:
            xy = c + aa * a + bb * b; V.append((float(xy[0]), float(xy[1]), float(co[0] + co[1] * aa + co[2] * bb + hh)))
        for f in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]: F.append(tuple(base + i for i in f))
    return V, F

def residuals(P, fr, boxes, mask_fn=None, n=600000, seed=1):
    c, a, b, co = fr['c'], fr['a'], fr['b'], fr['plane']
    A, B = to_frame(P, c, a, b); H = P[:, 2] - (co[0] + co[1] * A + co[2] * B)
    z = (A > fr['A0'] - 0.5) & (A < fr['A1'] + 0.5) & (np.abs(B) < 7.3) & (H > -2.3) & (H < 1.6)
    idx = np.where(z)[0]; rng = np.random.default_rng(seed); idx = rng.choice(idx, min(n, len(idx)), replace=False)
    pts = np.column_stack((A[idx], B[idx], H[idx])); d = np.full(len(pts), np.inf)
    for nm, x0, x1, y0, y1, z0, z1 in boxes:
        ctr = np.array([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]); half = np.array([(x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2])
        q = np.abs(pts - ctr) - half; dd = np.abs(np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(1), 0)); d = np.minimum(d, dd)
    inl = d < 0.5
    return dict(n=len(d), med=float(np.median(d)), p95=float(np.percentile(d, 95)), inl=float(inl.mean()), med_in=float(np.median(d[inl])), p95_in=float(np.percentile(d[inl], 95)), w3=float(np.mean(d < 0.03)), w10=float(np.mean(d < 0.10)))

# ------------------------------------------------------------------ terreno
def terrain_tin(P, C, hag, fr=None, core=(0.08, 0.25), gmax=0.25):
    m = hag < gmax; g = P[m].astype(np.float64); gc = C[m]
    if fr is not None:
        A, B = to_frame(g, fr['c'], fr['a'], fr['b']); isc = (np.abs(B) < 14) & (A > fr['A0'] - 20) & (A < fr['A1'] + 20)
    else: isc = np.zeros(len(g), bool)
    cell = np.where(isc, core[0], core[1])
    ij = np.floor((g[:, :2] - g[:, :2].min(0)) / cell[:, None]).astype(np.int64)
    key = ij[:, 0] * 200003 + ij[:, 1] + isc.astype(np.int64) * 10_000_000_000
    _, first = np.unique(key, return_index=True); g, gc, isc = g[first], gc[first], isc[first]
    tri = Delaunay(g[:, :2]); s = tri.simplices
    e = np.stack([np.linalg.norm(g[s[:, i], :2] - g[s[:, j], :2], axis=1) for i, j in [(0, 1), (1, 2), (2, 0)]], 1).max(1)
    dz = np.ptp(g[s][:, :, 2], axis=1); lim = np.where(isc[s].all(1), 0.6, 1.5)
    keep = (e < lim) & (dz < 0.6); s = s[keep]
    used = np.unique(s); remap = np.full(len(g), -1); remap[used] = np.arange(len(used))
    return g[used], remap[s], gc[used]
