import pye57, numpy as np, os, time, gc
import pye57.e57 as _m
_m.SUPPORTED_POINT_FIELDS.update({"nor:normalX": "f", "nor:normalY": "f", "nor:normalZ": "f"})
from scipy.spatial import cKDTree
T = os.environ["TEMP"]
p = r"C:\Users\erick.rodriguez\Downloads\_AYUDA.ESTRUCTURA.PUENTE.MAFIL\_Puente.formato.e57"
log = []
def L(s):
    log.append(s); open(os.path.join(T, "e57_log.txt"), "w", encoding="utf8").write("\n".join(log))
t = time.time()
e = pye57.E57(p); h = e.get_header(0)
try:
    L(f"bounds: x {h.xMinimum:.2f}..{h.xMaximum:.2f} y {h.yMinimum:.2f}..{h.yMaximum:.2f} z {h.zMinimum:.2f}..{h.zMaximum:.2f}")
except Exception as ex:
    L(f"bounds n/d {ex}")
try:
    L(f"pose: {h.scan_node['pose']['translation']['x'].value()} {h.scan_node['pose']['translation']['y'].value()} {h.scan_node['pose']['translation']['z'].value()}")
except Exception as ex:
    L("sin pose (nube fusionada)")
d = e.read_scan_raw(0)
L(f"leido {len(d['cartesianX'])} pts en {time.time()-t:.0f}s; campos {list(d.keys())}; dtype x {d['cartesianX'].dtype} nx {d['nor:normalX'].dtype} col {d['colorRed'].dtype} int {d['intensity'].dtype}")
X = np.column_stack((d['cartesianX'], d['cartesianY'], d['cartesianZ']))
N = np.column_stack((d['nor:normalX'], d['nor:normalY'], d['nor:normalZ'])).astype(np.float32)
I = d['intensity'].astype(np.float32)
Cc = np.column_stack((d['colorRed'], d['colorGreen'], d['colorBlue']))
del d; gc.collect()
L(f"xyz min {X.min(0)} max {X.max(0)}")
nl = np.linalg.norm(N, axis=1)
L(f"normales: |n| pctl 1/50/99 {np.percentile(nl[::50],[1,50,99]).round(3)}; cero {np.mean(nl<1e-6)*100:.1f}%")
L(f"intensidad: min {I.min():.3f} max {I.max():.3f} p50 {np.median(I[::50]):.3f}; color max {Cc.max()}")
# origen y submuestreo 5 cm con normales promediadas
cx, cy = 678070.0, 5603135.0
off = np.array([cx, cy, 0.0])
if X[:, 0].min() > 1e5:   # coordenadas UTM completas
    Xl = X - off; L("coordenadas E57 en UTM completas; resto offset")
else:
    Xl = X.copy(); L("coordenadas E57 ya locales")
Xl = Xl.astype(np.float32)
S = 0.05
o = Xl.min(0); ij = np.floor((Xl - o) / S).astype(np.int64)
key = (ij[:, 0] * 200003 + ij[:, 1]) * 20011 + ij[:, 2]
u, first, inv = np.unique(key, return_index=True, return_inverse=True)
cnt = np.bincount(inv).astype(np.float32)
Nm = np.column_stack([np.bincount(inv, N[:, j]) for j in range(3)]) / cnt[:, None]
nn = np.linalg.norm(Nm, axis=1, keepdims=True); Nm = (Nm / np.maximum(nn, 1e-9)).astype(np.float32)
P5 = Xl[first]; C5 = Cc[first]; I5 = I[first]
L(f"submuestreo 5 cm: {len(P5)} pts ({time.time()-t:.0f}s)")
np.savez(os.path.join(T, "e57_5cm.npz"), P=P5, C=C5, I=I5, N=Nm)
# comparacion con el LAS (pc5.npz: P local)
las = np.load(os.path.join(T, "pc5.npz"))["P"]
tree = cKDTree(las)
q = P5[::20]
dist, _ = tree.query(q, workers=-1)
L(f"E57 vs LAS (5cm, muestra {len(q)}): distancia al LAS mas cercano pctl 50/90/99 {np.percentile(dist,[50,90,99]).round(3)} m; <5cm {np.mean(dist<0.05)*100:.0f}%")
dist2, _ = cKDTree(P5).query(las[::40], workers=-1)
L(f"LAS vs E57: pctl 50/90/99 {np.percentile(dist2,[50,90,99]).round(3)} m; <5cm {np.mean(dist2<0.05)*100:.0f}%")
L("FIN")

