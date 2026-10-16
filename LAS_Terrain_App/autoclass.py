"""
autoclass.py
Clasificación automática por forma: terreno / estructura / vegetación.

Flujo: voxelizar (centroides) -> rasgos geométricos por vóxel -> modelo -> etiqueta a cada punto.
Los rasgos NO usan color ni intensidad: solo forma local y altura sobre el suelo, así que sirven
para cualquier nube. El modelo se entrena con train_autoclass.py a partir de una nube ya
clasificada; las etiquetas de entrenamiento definen qué es "estructura" (ver GROUPS).

>>> PUNTO DE INTEGRACIÓN <<<
Para cambiar el criterio (más clases, otro modelo, CSF para el suelo) toca: GROUPS (clases de
entrenamiento), `features` (rasgos) y `make_model`. Contrato de `predict_points`: devuelve un
arreglo (N,) con códigos ASPRS (2 suelo, 6 estructura, 5 vegetación).
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
from scipy.ndimage import maximum_filter, minimum_filter

# Grupos de entrenamiento -> clases de la nube de referencia (v5 del Puente Mafil)
GROUPS = {
    1: [2, 26, 11],                                       # terreno (suelo, terraplén, calzada acceso)
    2: [17, 20, 21, 22, 24, 25, 27, 28, 29, 30, 31],      # estructura y equipamiento
    3: [3, 4, 5],                                         # vegetación
}
# Grupo -> código ASPRS de salida
OUT_CODES = {1: 2, 2: 6, 3: 5}
FEATURE_NAMES = ["lin_s", "pla_s", "sca_s", "nz_s", "sig_s", "lin_l", "pla_l", "sca_l", "nz_l",
                 "sig_l", "h", "dzmax", "dzmin"]


def voxelize(points: np.ndarray, voxel: float):
    """Centroides de vóxeles ocupados (relativos al mínimo) + índice de vóxel de cada punto."""
    mn = points.min(axis=0)
    ij = np.floor((points - mn) / voxel).astype(np.int64)
    dims = ij.max(axis=0) + 1
    if float(dims[0]) * float(dims[1]) * float(dims[2]) > 4e18:
        raise ValueError("Nube demasiado extensa para este tamaño de vóxel; aumenta voxel.")
    key = (ij[:, 0] * dims[1] + ij[:, 1]) * dims[2] + ij[:, 2]
    del ij
    _, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    cnt = np.bincount(inv).astype(np.float64)
    cen = np.column_stack([np.bincount(inv, weights=points[:, k] - mn[k]) / cnt for k in range(3)])
    return cen, inv


def _eig(pcd, radius, max_nn):
    pcd.estimate_covariances(o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=max_nn))
    w, v = np.linalg.eigh(np.asarray(pcd.covariances))
    w = np.clip(w, 1e-12, None)
    s = np.sqrt(w[:, 2]) + 1e-9
    return [(np.sqrt(w[:, 2]) - np.sqrt(w[:, 1])) / s,    # linealidad
            (np.sqrt(w[:, 1]) - np.sqrt(w[:, 0])) / s,    # planaridad
            np.sqrt(w[:, 0]) / s,                         # dispersión
            np.abs(v[:, 2, 0]),                           # |nz| de la normal
            np.sqrt(w[:, 2])]                             # extensión principal


def features(cen: np.ndarray, r_small: float = 0.4, r_large: float = 1.0) -> np.ndarray:
    """Matriz (M, 13) de rasgos por vóxel. cen en coordenadas locales (z real relativo)."""
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cen))
    f = _eig(pcd, r_small, 40) + _eig(pcd, r_large, 80)

    # altura sobre el suelo: percentil 3 de z por celda de 1 m
    ij = np.floor(cen[:, :2]).astype(np.int64)
    key = ij[:, 0] * (ij[:, 1].max() + 1) + ij[:, 1]
    _, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    zs = cen[order, 2]
    cnt = np.bincount(inv)
    st = np.r_[0, np.cumsum(cnt)[:-1]]
    zlow = np.array([np.percentile(zs[a:a + n], 3) for a, n in zip(st, cnt)])
    h = cen[:, 2] - zlow[inv]

    # máx/mín de z en ventana ~1.5 m (rejilla 0.5 m): ¿hay algo encima o debajo?
    c5 = np.floor(cen[:, :2] / 0.5).astype(np.int64)
    H, W = c5[:, 0].max() + 1, c5[:, 1].max() + 1
    gmax = np.full((H, W), -1e9)
    gmin = np.full((H, W), 1e9)
    np.maximum.at(gmax, (c5[:, 0], c5[:, 1]), cen[:, 2])
    np.minimum.at(gmin, (c5[:, 0], c5[:, 1]), cen[:, 2])
    gmax = maximum_filter(gmax, size=3)
    gmin = minimum_filter(gmin, size=3)
    dzmax = cen[:, 2] - gmax[c5[:, 0], c5[:, 1]]
    dzmin = cen[:, 2] - gmin[c5[:, 0], c5[:, 1]]
    return np.column_stack(f + [h, dzmax, dzmin]).astype(np.float32)


def make_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    # Pesos por clase codificada (0 terreno, 1 estructura, 2 vegetación): equilibra recall y
    # precisión (probado: "balanced" sube recall pero deja precisión 0.72; sin pesos pierde recall).
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1,
                                          class_weight={0: 2.0, 1: 3.0, 2: 1.0},
                                          early_stopping=False, random_state=0)


def load_model(path: str):
    import joblib
    bundle = joblib.load(path)
    if bundle.get("features") != FEATURE_NAMES:
        raise ValueError("El modelo no coincide con los rasgos actuales; reentrena (train_autoclass.py).")
    return bundle


def predict_points(points: np.ndarray, bundle: dict, voxel: float | None = None) -> np.ndarray:
    """Códigos ASPRS (N,) para cada punto. voxel por defecto = el del entrenamiento."""
    voxel = voxel or bundle["voxel"]
    cen, inv = voxelize(points, voxel)
    pred = bundle["model"].predict(features(cen))
    lut = np.zeros(4, dtype=np.uint8)
    for g, code in OUT_CODES.items():
        lut[g] = code
    return lut[pred][inv]
