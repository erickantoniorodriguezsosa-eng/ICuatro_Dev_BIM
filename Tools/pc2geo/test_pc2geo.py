import numpy as np, os, sys, time, importlib
sys.path.insert(0, os.environ["TEMP"]); import pc2geo as G
T = os.environ["TEMP"]; log = []
def L(s):
    log.append(s); open(os.path.join(T, "tp_log.txt"), "w", encoding="utf8").write("\n".join(log))
e = np.load(os.path.join(T, "e57_5cm.npz")); off = np.load(os.path.join(T, "e57_offset.npy"))
P = e["P"].astype(np.float64) + off; Cc = e["C"]; N = e["N"]
t = time.time(); hag = G.ground_height(P); L(f"suelo {time.time()-t:.0f}s")
f = np.load(os.path.join(T, "frame2.npz")); mu2 = f["mu2"]
ref_ang = np.degrees(np.arctan2(f["a2"][1], f["a2"][0])) % 180
L(f"REF manual: centro {mu2.round(2)} eje {ref_ang:.2f}; losa +0.13/-0.13; almas -5.46,-1.84,1.78,5.42; fondo -1.98; losa B -6.22..6.26; postes ~30/lado sep 1.50")
seeds = [("A 3m/3deg", (mu2[0] + 2.0, mu2[1] - 2.5, ref_ang + 3)), ("B 6m/-5deg", (mu2[0] - 4.0, mu2[1] + 4.5, ref_ang - 5)),
         ("C 5m/4deg", (mu2[0] + 3.5, mu2[1] + 3.5, ref_ang + 4)), ("D 8m/8deg", (mu2[0] - 6.0, mu2[1] - 5.0, ref_ang + 8))]
for nm, (sx, sy, sa) in seeds:
    try:
        t = time.time(); fr = G.refine_frame(P, hag, (sx, sy, sa, 40, 12), N=N)
        if fr is None: L(f"[{nm}] sin resultado"); continue
        L(f"[{nm}] eje {fr['ang']:.2f} (ref {ref_ang:.2f}); dist centro-eje(B) {abs(np.dot(fr['c']-mu2, f['b2'])):.2f} m; A {fr['A0']:.1f}..{fr['A1']:.1f} (ref -24..17); plano sd {fr['sd']:.3f}; {time.time()-t:.0f}s")
        for s in fr['log'][-2:]: L("    " + s)
        if not fr['rails']: L("    sin barandas -> se detiene"); continue
        ms = G.measure_section(P, fr)
        L(f"    losa {ms['losa_bot']:.2f}/{ms['losa_top']:.2f} B {ms['losa_B'][0]:.2f}..{ms['losa_B'][1]:.2f}; fondo {ms['fondo']}; almas " + " ".join(f"[{l:.2f},{r:.2f}]" for l, r in ms['webs']))
        rl = G.measure_rails(P, fr, fr['rails'])
        for k in ("izq", "der"):
            d = rl[k]; sp = np.diff(d['posts']) if len(d['posts']) > 1 else [0]
            L(f"    baranda {k}: bordillo {d['curb']}, rieles {d['rails']}, postes {len(d['posts'])} (sep mediana {np.median(sp):.2f}), B poste {d['post_B']}, B riel {tuple(round(v,2) for v in d['rail_B'])}")
        bx = G.build_boxes(fr, ms, rl); r = G.residuals(P, fr, bx)
        L(f"    AJUSTE {len(bx)} cajas: mediana {r['med']*100:.1f} cm p95 {r['p95']*100:.1f}; inliers<50cm {r['inl']*100:.0f}% mediana {r['med_in']*100:.1f} p95 {r['p95_in']*100:.1f}; <3cm {r['w3']*100:.0f}% <10cm {r['w10']*100:.0f}%")
        if nm.startswith("A"):
            t = time.time(); V, Fc, gc = G.terrain_tin(P, Cc, hag, fr); L(f"    terreno TIN: {len(V)} vert {len(Fc)} tri ({time.time()-t:.0f}s)")
    except Exception as ex:
        import traceback; L(f"[{nm}] ERROR {ex} " + traceback.format_exc().splitlines()[-3])
L("FIN")
