# -*- coding: utf-8 -*-
"""Pruebas del motor con geometría sintética de respuesta conocida. Ejecutar: python -I tests/test_engine.py"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import ifc_engine as eng  # noqa: E402


def box(c, s):
    cx, cy, cz = c
    sx, sy, sz = s
    v = np.array([[x, y, z] for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)], float) * [sx / 2, sy / 2, sz / 2] + [cx, cy, cz]
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], np.int32)
    return v.astype(np.float32), f


def model(name, disc, boxes):
    els, geo = [], {}
    for k, (c, s) in enumerate(boxes, 1):
        v, f = box(c, s)
        els.append({"step": k, "guid": f"{name}{k}", "type": "IfcBuildingElementProxy", "name": f"{name}-{k}",
                    "has_geom": True, "bmin": v.min(0).astype(float).tolist(), "bmax": v.max(0).astype(float).tolist()})
        geo[k] = (v, f)
    bm = [e["bmin"] for e in els]
    bM = [e["bmax"] for e in els]
    n = len(els)
    return {"filename": name + ".ifc", "discipline": disc, "elements": els, "geo": geo, "schema": "IFC4",
            "discipline_src": "test", "seconds": 0, "georef": {"crs_valido": False},
            "bbox": [np.min(bm, 0).tolist(), np.max(bM, 0).tolist()],
            "audit": {"total": n, "pset_pct": 100.0, "name_pct": 100.0, "class_pct": 0.0, "geom_pct": 100.0,
                      "proxy_pct": 100.0, "geom_errors": {}}}


def run():
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("OK   " if cond else "FAIL ") + name, extra)
        ok &= bool(cond)

    rules = {"pares": {"SEG|SER": {"choque": "Crítica", "holgura_m": 0.5, "norma": "t", "verificado": False}},
             "defecto": {"choque": "Crítica", "holgura_m": 0.0}}
    A = model("A", "SEG", [((0, 0, 0), (2, 2, 2)), ((50, 0, 0), (2, 2, 2)), ((100, 0, 0), (2, 2, 2))])
    # B1 cruza A1 (choque), B2 a 0.3 m de A2 (holgura), B3 a 0.8 m de A3 (sin hallazgo)
    B = model("B", "SER", [((1.5, 0, 0), (2, 2, 2)), ((51.3, 0, 0), (0.6, 2, 2)), ((101.8, 0, 0), (0.6, 2, 2)) ])
    # B2: borde en x=51.0 .. A2 borde en 51.0 -> ajustar: A2 x∈[49,51], B2 centro 51.6 ancho .6 -> borde 51.3 => holgura 0.3
    B["geo"][2] = box((51.6, 0, 0), (0.6, 2, 2))
    B["elements"][1].update(bmin=B["geo"][2][0].min(0).astype(float).tolist(), bmax=B["geo"][2][0].max(0).astype(float).tolist())
    # B3 borde izq = 101.8-0.3 = 101.5 ; A3 borde der = 101 -> 0.5 exacto... usar 0.8 de separación
    B["geo"][3] = box((102.1, 0, 0), (0.6, 2, 2))
    B["elements"][2].update(bmin=B["geo"][3][0].min(0).astype(float).tolist(), bmax=B["geo"][3][0].max(0).astype(float).tolist())

    res = eng.build_result([A, B], rules)
    kinds = {i["stepA"]: (i["kind"], i["distance"], i["severity"]) for i in res["issues"]}
    check("choque duro detectado en A1", kinds.get(1, ("",))[0] == "choque", str(kinds.get(1)))
    check("holgura detectada en A2 (~0.30 m)", kinds.get(2, ("",))[0] == "holgura" and abs(kinds[2][1] - 0.3) < 0.08, str(kinds.get(2)))
    check("A3 (0.8 m) NO genera hallazgo", 3 not in kinds, str(kinds))
    check("matriz diagonal/cruce SEG×SER = 2", res["matrix"][4][5] == 2 and res["matrix"][5][4] == 2, str(res["matrix"][4]))
    check("severidad choque = Crítica", kinds[1][2] == "Crítica")
    # mismo modelo no choca consigo mismo
    C = model("C", "SEG", [((0, 0, 0), (2, 2, 2)), ((1, 0, 0), (2, 2, 2))])
    r2 = eng.build_result([C, model("D", "PAV", [((200, 0, 0), (1, 1, 1))])], rules)
    check("sin choques intra-modelo por defecto", len(r2["issues"]) == 0)
    r3 = eng.build_result([C, model("D2", "SEG", [((0.5, 0, 0), (1, 1, 1))])], rules)
    check("misma especialidad entre modelos distintos cuenta en diagonal", r3["matrix"][4][4] >= 1, str(r3["matrix"][4]))

    # Pk sobre eje conocido
    ax = eng.Axis(np.array([[0, 0], [1000, 0]]), "t")
    pk, off = ax.locate(420, 15)
    check("Pk = 420 m, desfase 15 m", abs(pk - 420) < 2.1 and abs(off - 15) < 0.1, f"{pk:.1f} {off:.1f}")
    check("formato Pk", eng.fmt_pk(1420.5).startswith("Pk 1+420.50"), eng.fmt_pk(1420.5))
    # eje estimado a partir de puntos en curva con zigzag lateral
    t = np.linspace(0, 2000, 120)
    pts = np.c_[t, 100 * np.sin(t / 600)] + np.c_[np.zeros(120), 6 * (-1) ** np.arange(120)]
    line = eng.estimate_axis(pts)
    a2 = eng.Axis(line, "est")
    check("eje estimado ~ longitud real", 1900 < a2.length < 2300, f"{a2.length:.0f} m")
    # disciplina por nombre
    for fn, exp in [("1B-Z5-PRD-ILU-V13.T1-M3D-001-V-IluminacionProyectada.ifc", "SER"),
                    ("1B-Z5-PRD-SEV-V13.T1-M3D-003-V-BARRERAS.ifc", "SEG"),
                    ("RUTA-DRE-OAARTE.ifc", "DRE"), ("puente_EST.ifc", "EST"), ("calzada_pav.ifc", "PAV")]:
        check(f"disciplina {fn[:30]}… = {exp}", eng.detect_discipline(fn)[0] == exp, eng.detect_discipline(fn)[0])
    # decimación conserva extremos
    v, f = box((0, 0, 0), (10, 10, 10))
    dv, df = eng.cluster_mesh(v.astype(float), f, 0.5)
    check("cluster_mesh no rompe malla simple", len(df) > 0)
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
