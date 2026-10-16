"""
train_autoclass.py
Entrena el modelo de clasificación automática a partir de un LAS ya clasificado.

Uso: python train_autoclass.py <clasificado.las> [--voxel 0.15] [--out models/autoclass.joblib]
Valida con bloques espaciales de 20 m (damero): entrena en bloques pares, prueba en impares,
para no medir la memoria de la vecindad. Luego reentrena con todos los bloques y guarda.
"""
import argparse
import os
import time

import joblib
import laspy
import numpy as np

import autoclass as ac

ap = argparse.ArgumentParser()
ap.add_argument("las")
ap.add_argument("--voxel", type=float, default=0.15)
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "models", "autoclass.joblib"))
ap.add_argument("--max-train", type=int, default=3_000_000, help="vóxeles máx. para entrenar")
a = ap.parse_args()
t0 = time.time()

las = laspy.read(a.las)
xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
cls = np.asarray(las.classification, dtype=np.uint8)
del las
print(f"{len(xyz):,} puntos leídos ({time.time() - t0:.0f}s)", flush=True)

cen, inv = ac.voxelize(xyz, a.voxel)
nv = len(cen)
# etiqueta del vóxel = grupo mayoritario entre sus puntos
votes = np.zeros((nv, 3))
for g, codes in ac.GROUPS.items():
    votes[:, g - 1] = np.bincount(inv, weights=np.isin(cls, codes).astype(np.float64), minlength=nv)
has = votes.sum(1) > 0
y = np.where(has, votes.argmax(1) + 1, 0).astype(np.uint8)
del xyz, cls, inv
print(f"{nv:,} vóxeles ({a.voxel} m); etiquetados {int(has.sum()):,}", flush=True)

X = ac.features(cen)
print(f"rasgos {X.shape} ({time.time() - t0:.0f}s)", flush=True)

blk = (np.floor(cen[:, 0] / 20).astype(int) + np.floor(cen[:, 1] / 20).astype(int)) % 2
rng = np.random.default_rng(0)


def sample(mask):
    idx = np.flatnonzero(mask)
    return rng.choice(idx, a.max_train, replace=False) if len(idx) > a.max_train else idx


tr, te = sample(has & (blk == 0)), np.flatnonzero(has & (blk == 1))
model = ac.make_model().fit(X[tr], y[tr])
pred = model.predict(X[te])
cm = np.zeros((3, 3), int)
for i in (1, 2, 3):
    for j in (1, 2, 3):
        cm[i - 1, j - 1] = ((y[te] == i) & (pred == j)).sum()
print("VALIDACIÓN ESPACIAL (bloques 20 m)")
print("acierto", round(float(np.trace(cm) / cm.sum()), 3))
print("recall   terreno/estructura/vegetación", (np.diag(cm) / cm.sum(1)).round(3))
print("precisión", (np.diag(cm) / np.maximum(cm.sum(0), 1)).round(3))
print(cm, flush=True)

model = ac.make_model().fit(X[sample(has)], y[sample(has)])
os.makedirs(os.path.dirname(a.out), exist_ok=True)
joblib.dump({"model": model, "features": ac.FEATURE_NAMES, "voxel": a.voxel,
             "groups": ac.GROUPS, "trained_on": os.path.basename(a.las),
             "val_acc": float(np.trace(cm) / cm.sum())}, a.out)
print(f"modelo guardado en {a.out} ({time.time() - t0:.0f}s)")
