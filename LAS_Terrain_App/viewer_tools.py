"""
viewer_tools.py
Herramientas del visor 3D: caja de secciones (recorte en el marco del eje del puente o en el marco
del mundo), consulta de coordenadas/cotas, cotas persistentes y medidas. Todo trabaja en las coordenadas
locales de la nube (restar/sumar `proc.origin` para pasar a reales). La GUI crea un `ViewerTools`
y le delega los clics del visor mientras haya una herramienta activa.
"""
from __future__ import annotations

import csv

import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from vtkmodules.vtkCommonDataModel import vtkPlane
from vtkmodules.vtkRenderingCore import vtkPropPicker

KEEP_PREFIXES = ("ann_", "secbox")          # actores de ayuda: nunca se recortan
HANDLE_PX = 22                              # radio de captura (píxeles) de las asas de la caja
FACES = ("s0", "s1", "t0", "t1", "z0", "z1")
FACE_LABEL = {"s0": "s mín", "s1": "s máx", "t0": "t mín", "t1": "t máx", "z0": "z mín", "z1": "z máx"}
FACE_COLOR = {"s": "#ff5050", "t": "#50d060", "z": "#5090ff"}
SNAP_RADIUS = 0.35                          # m: imán a vértices de sólidos, malla y líneas de referencia


class DxfDialog(QDialog):
    def __init__(self, parent, epsg: int, has_mesh: bool, default_path: str):
        super().__init__(parent)
        self.setWindowTitle("Exportar líneas a AutoCAD (DXF)")
        lo = QVBoxLayout(self)
        form = QFormLayout()
        self.cb_coords = QComboBox()
        self.cb_coords.addItem("UTM (coordenadas reales, georreferenciado)", "utm")
        self.cb_coords.addItem("Local x, y, z (origen de la nube)", "local")
        self.sp_epsg = QDoubleSpinBox()
        self.sp_epsg.setDecimals(0)
        self.sp_epsg.setRange(0, 99999)
        self.sp_epsg.setValue(epsg)
        self.sp_epsg.setPrefix("EPSG: ")
        self.sp_angle = QDoubleSpinBox()
        self.sp_angle.setRange(5, 89)
        self.sp_angle.setValue(30)
        self.sp_angle.setSuffix(" °")
        self.sp_cont = QDoubleSpinBox()
        self.sp_cont.setRange(0, 50)
        self.sp_cont.setValue(1.0 if has_mesh else 0.0)
        self.sp_cont.setSuffix(" m (0 = sin curvas)")
        self.sp_cont.setEnabled(has_mesh)
        self.chk_mesh = QCheckBox("Incluir malla del terreno como 3DFACE")
        self.chk_mesh.setEnabled(has_mesh)
        form.addRow("Coordenadas", self.cb_coords)
        form.addRow("CRS", self.sp_epsg)
        form.addRow("Ángulo mínimo de arista", self.sp_angle)
        form.addRow("Curvas de nivel cada", self.sp_cont)
        lo.addLayout(form)
        lo.addWidget(self.chk_mesh)
        note = QLabel("Líneas: eje, sección, aristas de cada elemento (capas por tipo), curvas de nivel, "
                      "cotas y medidas. Se escribe un .prj y un _georef.txt junto al DXF.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        lo.addWidget(note)
        bar = QHBoxLayout()
        self.path = default_path
        self.lbl = QLabel(default_path)
        b = QPushButton("Examinar…")
        b.clicked.connect(self._browse)
        bar.addWidget(self.lbl, 1)
        bar.addWidget(b)
        lo.addLayout(bar)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lo.addWidget(bb)

    def _browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Exportar DXF", self.path, "DXF (*.dxf)")
        if p:
            self.path = p
            self.lbl.setText(p)

    def config(self) -> dict:
        p = self.path if self.path.lower().endswith(".dxf") else self.path + ".dxf"
        epsg = int(self.sp_epsg.value())
        return {"path": p, "coords": self.cb_coords.currentData(), "epsg": epsg or None,
                "edge_angle": self.sp_angle.value(), "contour_interval": self.sp_cont.value(),
                "terrain_mesh": self.chk_mesh.isChecked()}


class ViewerTools:
    def __init__(self, win):
        self.win = win
        self.plotter = win.plotter
        self.proc = win.proc
        self.mode = None                   # None | "consulta" | "cota" | "medida"
        self._pending_a = None             # primer punto de una medida
        self._picker = vtkPropPicker()
        self._planes = []                  # vtkPlane x 6 si la caja está activa
        self._snap = None                  # (KDTree, puntos) de referencia
        self._snap_key = None
        self.spins = {}
        self.chk_box = None
        self._drag = None                  # dict mientras se arrastra un asa
        self._wrap_add_mesh()
        self._install_drag()

    # ----------------------------------------------------------- marco y límites de la caja
    def frame(self):
        """(c, d, n, nombre): eje del puente si hay sección; si no, los ejes del mundo."""
        sec = self.proc.section
        if self.frame_mode == "puente" and sec is not None and sec.frame is not None:
            c, d, n = (np.asarray(x, float) for x in sec.frame)
            return c, d, n
        return np.zeros(2), np.array([1.0, 0.0]), np.array([0.0, 1.0])

    frame_mode = "puente"

    def scene_points(self, max_pts=300000):
        vs = [s.vertices for s in self.proc.solids]
        if self.proc.mesh is not None:
            vs.append(self.proc.mesh.vertices)
        if vs:
            return np.vstack(vs)
        p = self.proc.points
        if p is None:
            return None
        return p[::max(1, len(p) // max_pts)]

    def full_range(self):
        """Límites (s0, s1, t0, t1, z0, z1) de toda la escena en el marco activo."""
        V = self.scene_points()
        if V is None:
            return None
        c, d, n = self.frame()
        q = V[:, :2] - c
        s, t = q @ d, q @ n
        return [float(s.min()), float(s.max()), float(t.min()), float(t.max()), float(V[:, 2].min()),
                float(V[:, 2].max())]

    def current_bounds(self):
        return [self.spins[k].value() for k in ("s0", "s1", "t0", "t1", "z0", "z1")]

    # --------------------------------------------------------------------- caja de secciones
    def set_box(self, on=None, bounds=None, frame=None, show=None):
        if frame is not None:
            self.frame_mode = frame
            self.cb_frame.blockSignals(True)
            self.cb_frame.setCurrentIndex(0 if frame == "puente" else 1)
            self.cb_frame.blockSignals(False)
            self.reset_bounds()
        if bounds is not None:
            for k, v in zip(("s0", "s1", "t0", "t1", "z0", "z1"), bounds):
                if v is not None:
                    self.spins[k].blockSignals(True)
                    self.spins[k].setValue(float(v))
                    self.spins[k].blockSignals(False)
        if show is not None:
            self.chk_show.blockSignals(True)
            self.chk_show.setChecked(bool(show))
            self.chk_show.blockSignals(False)
        if on is not None:
            self.chk_box.blockSignals(True)
            self.chk_box.setChecked(bool(on))
            self.chk_box.blockSignals(False)
        self._update_box()
        return self.box_state()

    def box_state(self):
        return {"on": self.chk_box.isChecked(), "frame": self.frame_mode, "bounds": self.current_bounds()}

    def reset_bounds(self):
        r = self.full_range()
        if r is None:
            return
        pad = 0.5
        vals = [r[0] - pad, r[1] + pad, r[2] - pad, r[3] + pad, r[4] - pad, r[5] + pad]
        for k, v in zip(("s0", "s1", "t0", "t1", "z0", "z1"), vals):
            sp = self.spins[k]
            sp.blockSignals(True)
            sp.setRange(vals[0] - 200 if k[0] == "s" else (vals[2] - 200 if k[0] == "t" else vals[4] - 200),
                        vals[1] + 200 if k[0] == "s" else (vals[3] + 200 if k[0] == "t" else vals[5] + 200))
            sp.setValue(v)
            sp.blockSignals(False)

    def _make_planes(self):
        c, d, n = self.frame()
        s0, s1, t0, t1, z0, z1 = self.current_bounds()
        c3 = np.array([c[0], c[1], 0.0])
        d3, n3 = np.array([d[0], d[1], 0.0]), np.array([n[0], n[1], 0.0])
        z3 = np.array([0.0, 0.0, 1.0])
        defs = [(c3 + s0 * d3, d3), (c3 + s1 * d3, -d3), (c3 + t0 * n3, n3), (c3 + t1 * n3, -n3),
                (np.array([0, 0, z0]), z3), (np.array([0, 0, z1]), -z3)]
        planes = []
        for o, nv in defs:
            p = vtkPlane()
            p.SetOrigin(*o)
            p.SetNormal(*nv)
            planes.append(p)
        return planes

    def _update_box(self):
        on = self.chk_box.isChecked()
        self._planes = self._make_planes() if on else []
        self._apply_all()
        self._draw_outline()
        self.win._request_render()

    def _apply_to(self, actor):
        m = getattr(actor, "GetMapper", lambda: None)()
        if m is None:
            return
        m.RemoveAllClippingPlanes()
        if not self._planes:
            return
        for p in self._planes:
            m.AddClippingPlane(p)

    def _apply_all(self):
        for name, a in list(self.plotter.renderer.actors.items()):
            if name.startswith(KEEP_PREFIXES):
                continue
            self._apply_to(a)

    def _wrap_add_mesh(self):
        orig = self.plotter.add_mesh

        def add_mesh(*a, **kw):
            res = orig(*a, **kw)
            act = res[0] if isinstance(res, tuple) else res
            name = kw.get("name") or ""
            if self._planes and not str(name).startswith(KEEP_PREFIXES):
                try:
                    self._apply_to(act)
                except Exception:
                    pass
            return res
        self.plotter.add_mesh = add_mesh

    def face_centers(self):
        """Centro 3D (coords locales) y eje unitario de cada cara de la caja."""
        c, d, n = self.frame()
        s0, s1, t0, t1, z0, z1 = self.current_bounds()
        sm, tm, zm = (s0 + s1) / 2, (t0 + t1) / 2, (z0 + z1) / 2

        def at(s, t, z):
            xy = c + s * d + t * n
            return np.array([xy[0], xy[1], z])
        d3, n3 = np.array([d[0], d[1], 0.0]), np.array([n[0], n[1], 0.0])
        return {"s0": (at(s0, tm, zm), d3), "s1": (at(s1, tm, zm), d3), "t0": (at(sm, t0, zm), n3),
                "t1": (at(sm, t1, zm), n3), "z0": (at(sm, tm, z0), np.array([0.0, 0.0, 1.0])),
                "z1": (at(sm, tm, z1), np.array([0.0, 0.0, 1.0]))}

    def _draw_handles(self):
        for k in FACES:
            self.plotter.remove_actor(f"secbox_h_{k}")
        if not (self.chk_box.isChecked() and self.chk_show.isChecked()):
            return
        fc = self.face_centers()
        b = self.current_bounds()
        r = max(0.5, 0.03 * max(b[1] - b[0], b[3] - b[2], b[5] - b[4]))
        for k, (cen, _) in fc.items():
            hot = self._drag is not None and self._drag["face"] == k
            self.plotter.add_mesh(pv.Sphere(radius=r * (1.5 if hot else 1.0), center=cen),
                                  color="#ffffff" if hot else FACE_COLOR[k[0]], name=f"secbox_h_{k}", pickable=False)

    # ----------------------------------------------------------------- arrastre con el cursor
    def _install_drag(self):
        iren = self.plotter.iren.interactor
        self._drag_cmds = {}
        for ev, fn, pri in (("LeftButtonPressEvent", self._on_press, 30.0), ("MouseMoveEvent", self._on_move, 30.0),
                            ("LeftButtonReleaseEvent", self._on_release, 30.0)):
            tag = iren.AddObserver(ev, fn, pri)
            cmd = iren.GetCommand(tag)
            cmd.SetPassiveObserver(True)       # sigue recibiendo eventos aunque el estilo tenga el foco
            self._drag_cmds[ev] = cmd

    def _to_display(self, p):
        ren = self.plotter.renderer
        ren.SetWorldPoint(float(p[0]), float(p[1]), float(p[2]), 1.0)
        ren.WorldToDisplay()
        x, y, _ = ren.GetDisplayPoint()
        return np.array([x, y])

    def handle_at(self, x, y):
        """Cara cuya asa está a menos de HANDLE_PX del cursor (la más cercana) o None."""
        if not (self.chk_box.isChecked() and self.chk_show.isChecked()):
            return None
        best = None
        for k, (cen, _) in self.face_centers().items():
            dpx = float(np.linalg.norm(self._to_display(cen) - np.array([x, y])))
            if dpx <= HANDLE_PX and (best is None or dpx < best[1]):
                best = (k, dpx)
        return best[0] if best else None

    def begin_drag(self, face, x, y):
        cen, v = self.face_centers()[face]
        u = self._to_display(cen + v) - self._to_display(cen)        # píxeles por metro a lo largo del eje
        ppm = float(np.linalg.norm(u))
        if ppm < 1e-6:
            return False
        self._drag = {"face": face, "x0": x, "y0": y, "u": u / ppm, "ppm": ppm, "v0": self.spins[face].value()}
        self._draw_handles()
        return True

    def move_drag(self, x, y):
        dg = self._drag
        delta = float((np.array([x - dg["x0"], y - dg["y0"]]) @ dg["u"]) / dg["ppm"])
        val = dg["v0"] + delta
        k = dg["face"]
        mn, mx = self.spins[k[0] + "0"], self.spins[k[0] + "1"]
        if k.endswith("0"):
            val = min(val, mx.value() - 0.05)
        else:
            val = max(val, mn.value() + 0.05)
        sp = self.spins[k]
        sp.blockSignals(True)
        sp.setValue(val)
        sp.blockSignals(False)
        self._update_box()
        self.lbl_info.setText(f"Cara {FACE_LABEL[k]} = {sp.value():.3f}")

    def end_drag(self):
        self._drag = None
        self._draw_handles()
        self.win._request_render()

    def _on_press(self, obj, ev):
        cmd = self._drag_cmds[ev]
        cmd.SetAbortFlag(0)
        if self.mode is not None:
            return
        x, y = obj.GetEventPosition()
        face = self.handle_at(x, y)
        if face is not None and self.begin_drag(face, x, y):
            cmd.SetAbortFlag(1)                # el estilo de cámara no recibe este clic

    def _on_move(self, obj, ev):
        cmd = self._drag_cmds[ev]
        cmd.SetAbortFlag(0)
        if self._drag is None:
            return
        x, y = obj.GetEventPosition()
        self.move_drag(x, y)
        cmd.SetAbortFlag(1)

    def _on_release(self, obj, ev):
        cmd = self._drag_cmds[ev]
        cmd.SetAbortFlag(0)
        if self._drag is not None:
            self.end_drag()
            cmd.SetAbortFlag(1)

    def drag_face(self, face, dx=0, dy=0, x=None, y=None):
        """Simula con eventos reales un arrastre del asa `face`; sirve para el puente y las pruebas."""
        cen, _ = self.face_centers()[face]
        px = self._to_display(cen)
        x0, y0 = (int(px[0]), int(px[1])) if x is None else (int(x), int(y))
        iren = self.plotter.iren.interactor
        iren.SetEventInformation(x0, y0, 0, 0)
        iren.LeftButtonPressEvent()
        iren.SetEventInformation(x0 + int(dx), y0 + int(dy), 0, 0)
        iren.MouseMoveEvent()
        iren.LeftButtonReleaseEvent()
        return self.box_state()

    def _draw_outline(self):
        self.plotter.remove_actor("secbox_outline")
        self._draw_handles()
        if not (self.chk_box.isChecked() and self.chk_show.isChecked()):
            return
        c, d, n = self.frame()
        s0, s1, t0, t1, z0, z1 = self.current_bounds()
        pts = []
        for z in (z0, z1):
            for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1)):
                xy = c + s * d + t * n
                pts.append([xy[0], xy[1], z])
        pts = np.array(pts)
        edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        lines = np.array([[2, a, b] for a, b in edges]).ravel()
        poly = pv.PolyData(pts)
        poly.lines = lines
        self.plotter.add_mesh(poly, color="#ffd400", line_width=2, name="secbox_outline", pickable=False)

    def preset(self, kind: str, pos=None, thick=1.0):
        """kind: all | transversal (corte en s=pos) | longitudinal (corte en t=pos) | planta (z >= pos)."""
        self.reset_bounds()
        r = self.full_range()
        h = thick / 2
        if kind == "transversal":
            p = (r[0] + r[1]) / 2 if pos is None else pos
            self.set_box(on=True, bounds=[p - h, p + h, None, None, None, None])
        elif kind == "longitudinal":
            p = (r[2] + r[3]) / 2 if pos is None else pos
            self.set_box(on=True, bounds=[None, None, p - h, p + h, None, None])
        elif kind == "planta":
            p = (r[4] + r[5]) / 2 if pos is None else pos
            self.set_box(on=True, bounds=[None, None, None, None, None, p])
        else:
            self.set_box(on=False)
            return self.box_state()
        return self.box_state()

    # ------------------------------------------------------------------- clic y consulta
    def set_mode(self, mode):
        self.mode = None if mode in (None, "ninguna") else mode
        self._pending_a = None
        for m, b in self.mode_btns.items():
            b.blockSignals(True)
            b.setChecked(m == self.mode)
            b.blockSignals(False)
        self.btn_face.blockSignals(True)
        self.btn_face.setChecked(self.mode == "cara")
        self.btn_face.blockSignals(False)
        self.lbl_info.setText({"consulta": "Consulta: clic en el modelo.", "cota": "Cota: clic para fijar una cota.",
                               "medida": "Medida: clic en el punto A y luego en el B.",
                               "cara": "Cara de la caja: clic en el modelo para llevar la cara elegida a ese punto.",
                               None: "Herramienta apagada: el clic selecciona elementos."}[self.mode])

    def _snap_tree(self):
        key = (len(self.proc.solids), None if self.proc.mesh is None else len(self.proc.mesh.vertices),
               id(self.proc.section))
        if self._snap_key != key:
            from scipy.spatial import cKDTree
            vs = [s.vertices for s in self.proc.solids]
            if self.proc.mesh is not None:
                vs.append(self.proc.mesh.vertices)
            if self.proc.section is not None:
                vs += [np.asarray(l, float) for l in self.proc.section.lines3d]
            P = np.vstack(vs) if vs else None
            self._snap = (cKDTree(P), P) if P is not None else None
            self._snap_key = key
        return self._snap

    def pick_world(self, x, y, snap=True):
        """Posición 3D local bajo el píxel (x, y); None si no hay geometría. Con imán a vértices cercanos."""
        self._picker.Pick(x, y, 0, self.plotter.renderer)
        act = self._picker.GetActor()
        if act is None:
            return None
        name = next((k for k, v in self.plotter.renderer.actors.items() if v is act), "")
        if name.startswith(KEEP_PREFIXES):
            return None
        p = np.array(self._picker.GetPickPosition(), float)
        if snap:
            st = self._snap_tree()
            if st is not None:
                dist, i = st[0].query(p)
                if dist <= SNAP_RADIUS:
                    return st[1][i].copy()
        return p

    def handle_click(self, x, y, add=False) -> bool:
        """True si la herramienta consumió el clic."""
        if self.mode is None:
            return False
        p = self.pick_world(x, y)
        if p is None:
            self.lbl_info.setText("Sin geometría bajo el cursor.")
            return True
        self.at_point(p)
        return True

    def at_point(self, p):
        if self.mode == "cara":
            self.set_face_from_point(self.cb_face.currentData(), p)
        elif self.mode == "consulta":
            self.lbl_info.setText(self.describe(p))
        elif self.mode == "cota":
            self.proc.add_annotation("cota", p=p)
            self.lbl_info.setText(self.describe(p))
            self.refresh_annotations()
        elif self.mode == "medida":
            if self._pending_a is None:
                self._pending_a = np.asarray(p, float)
                self.lbl_info.setText("Punto A fijado. Clic en el punto B.")
                self._draw_pending()
            else:
                an = self.proc.add_annotation("medida", a=self._pending_a, b=p)
                self._pending_a = None
                self.lbl_info.setText(self.describe_measure(an))
                self.refresh_annotations()

    def set_face_from_point(self, face, p):
        """Mueve la cara `face` de la caja a la posición del punto `p` (coords locales)."""
        c, d, n = self.frame()
        q = np.asarray(p[:2], float) - c
        val = {"s": float(q @ d), "t": float(q @ n), "z": float(p[2])}[face[0]]
        mn, mx = self.spins[face[0] + "0"], self.spins[face[0] + "1"]
        val = min(val, mx.value() - 0.05) if face.endswith("0") else max(val, mn.value() + 0.05)
        if not self.chk_box.isChecked():
            self.set_box(on=True)
        self.set_box(bounds=[val if k == face else None for k in FACES])
        self.lbl_info.setText(f"Cara {FACE_LABEL[face]} fijada en {val:.3f}")

    def describe(self, p) -> str:
        o = self.proc.origin
        r = np.asarray(p, float) + o
        s = f"E {r[0]:.3f}  N {r[1]:.3f}  Cota {r[2]:.3f}\nLocal x {p[0]:.3f}  y {p[1]:.3f}  z {p[2]:.3f}"
        sec = self.proc.section
        if sec is not None and sec.frame is not None:
            c, d, n = sec.frame
            q = np.asarray(p[:2]) - c
            ss, tt = float(q @ d), float(q @ n)
            s += f"\nEje: s {ss:.3f}  t {tt:.3f}"
            try:
                s += f"  | sobre rasante {p[2] - float(np.polyval(sec.grade, ss)):+.3f} m"
            except Exception:
                pass
        st = self._snap_tree()
        names = self.nearest_element(p)
        if names:
            s += f"\nElemento más cercano: {names[0]} ({names[1]:.2f} m)"
        return s

    def nearest_element(self, p):
        best = None
        for s in self.proc.solids:
            d = float(np.min(np.linalg.norm(s.vertices - p, axis=1)))
            if best is None or d < best[1]:
                best = (s.name, d)
        return best

    @staticmethod
    def describe_measure(an) -> str:
        sl = "—" if an["slope_pct"] is None else f"{an['slope_pct']:.2f} %"
        return (f"Medida #{an['id']}: 3D {an['dist3d']:.3f} m | horizontal {an['dist_h']:.3f} m | "
                f"ΔZ {an['dz']:+.3f} m | pendiente {sl}")

    # ------------------------------------------------------------- anotaciones en el visor
    def _draw_pending(self):
        self.plotter.remove_actor("ann_pending")
        if self._pending_a is not None:
            self.plotter.add_mesh(pv.PolyData(np.array([self._pending_a])), color="#ffd400", point_size=14,
                                  render_points_as_spheres=True, name="ann_pending", pickable=False)
        self.win._request_render()

    def refresh_annotations(self):
        for nm in ("ann_cotas", "ann_cotas_lbl", "ann_med", "ann_med_lbl", "ann_pending"):
            self.plotter.remove_actor(nm)
        o = self.proc.origin
        cotas = [a for a in self.proc.annotations if a["type"] == "cota"]
        meds = [a for a in self.proc.annotations if a["type"] == "medida"]
        if cotas:
            P = np.array([a["p"] for a in cotas])
            self.plotter.add_mesh(pv.PolyData(P), color="#ff4fd8", point_size=12, render_points_as_spheres=True,
                                  name="ann_cotas", pickable=False)
            self.plotter.add_point_labels(P, [a["text"] or f"{a['id']}: Z {a['z']:.3f}" for a in cotas],
                                          name="ann_cotas_lbl", font_size=14, text_color="white", shape=None,
                                          show_points=False, always_visible=True, pickable=False)
        if meds:
            pts, cells, mids, labels = [], [], [], []
            for a in meds:
                i = len(pts)
                pts += [a["a"], a["b"]]
                cells += [2, i, i + 1]
                mids.append((a["a"] + a["b"]) / 2)
                labels.append(a["text"] or f"#{a['id']}  {a['dist3d']:.3f} m")
            poly = pv.PolyData(np.array(pts))
            poly.lines = np.array(cells)
            self.plotter.add_mesh(poly, color="#ffd400", line_width=3, name="ann_med", pickable=False)
            self.plotter.add_point_labels(np.array(mids), labels, name="ann_med_lbl", font_size=14,
                                          text_color="#ffd400", shape=None, show_points=False,
                                          always_visible=True, pickable=False)
        self._fill_table()
        self.win._request_render()

    def _fill_table(self):
        t = self.table
        t.setRowCount(len(self.proc.annotations))
        for r, a in enumerate(self.proc.annotations):
            if a["type"] == "cota":
                desc = f"E {a['e']:.3f}  N {a['n']:.3f}  Z {a['z']:.3f}"
            else:
                sl = "—" if a["slope_pct"] is None else f"{a['slope_pct']:.1f} %"
                desc = f"3D {a['dist3d']:.3f} | H {a['dist_h']:.3f} | ΔZ {a['dz']:+.3f} | {sl}"
            for c, v in enumerate((str(a["id"]), a["type"], desc)):
                t.setItem(r, c, QTableWidgetItem(v))
        t.resizeColumnsToContents()

    def delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.proc.annotations[r]
        self.refresh_annotations()

    def clear_annotations(self):
        self.proc.annotations.clear()
        self.refresh_annotations()

    def export_csv(self, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self.win, "Exportar cotas y medidas", "cotas_medidas.csv", "CSV (*.csv)")
        if not path:
            return None
        o = self.proc.origin
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["id", "tipo", "E_A", "N_A", "Z_A", "E_B", "N_B", "Z_B", "dist3d", "dist_horizontal", "dz",
                        "pendiente_pct", "texto"])
            for a in self.proc.annotations:
                if a["type"] == "cota":
                    w.writerow([a["id"], "cota", f"{a['e']:.4f}", f"{a['n']:.4f}", f"{a['z']:.4f}", "", "", "", "", "",
                                "", "", a["text"]])
                else:
                    A, B = a["a"] + o, a["b"] + o
                    w.writerow([a["id"], "medida", *[f"{v:.4f}" for v in A], *[f"{v:.4f}" for v in B],
                                f"{a['dist3d']:.4f}", f"{a['dist_h']:.4f}", f"{a['dz']:.4f}",
                                "" if a["slope_pct"] is None else f"{a['slope_pct']:.3f}", a["text"]])
        return path

    # ------------------------------------------------------------------------- panel
    def build_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        # --- caja de secciones
        gb = QGroupBox("Caja de secciones")
        g = QGridLayout(gb)
        self.chk_box = QCheckBox("Activar recorte")
        self.chk_box.toggled.connect(self._box_toggled)
        self.chk_show = QCheckBox("Ver caja")
        self.chk_show.setChecked(True)
        self.chk_show.toggled.connect(lambda _: self._update_box())
        self.cb_frame = QComboBox()
        self.cb_frame.addItems(["Marco: eje del puente (s, t, z)", "Marco: mundo (x, y, z)"])
        self.cb_frame.currentIndexChanged.connect(self._frame_changed)
        g.addWidget(self.chk_box, 0, 0)
        g.addWidget(self.chk_show, 0, 1)
        g.addWidget(self.cb_frame, 1, 0, 1, 3)
        for r, (lab, k0, k1) in enumerate((("Longitudinal s", "s0", "s1"), ("Transversal t", "t0", "t1"),
                                           ("Cota z (local)", "z0", "z1")), start=2):
            g.addWidget(QLabel(lab), r, 0)
            for c, k in enumerate((k0, k1), start=1):
                sp = QDoubleSpinBox()
                sp.setDecimals(2)
                sp.setSingleStep(0.5)
                sp.setRange(-9999, 9999)
                sp.valueChanged.connect(lambda _v: self._update_box() if self.chk_box.isChecked() else None)
                self.spins[k] = sp
                g.addWidget(sp, r, c)
        row = QHBoxLayout()
        for text, kind in (("Todo", "all"), ("Corte transversal", "transversal"), ("Corte longitudinal", "longitudinal"),
                           ("Planta", "planta")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: self.preset(k))
            row.addWidget(b)
        g.addLayout(row, 5, 0, 1, 3)
        rowf = QHBoxLayout()
        self.cb_face = QComboBox()
        for k in FACES:
            self.cb_face.addItem(f"Cara {FACE_LABEL[k]}", k)
        self.btn_face = QPushButton("Fijar con clic")
        self.btn_face.setCheckable(True)
        self.btn_face.setToolTip("Clic en el modelo: la cara elegida pasa por ese punto")
        self.btn_face.clicked.connect(lambda checked: self.set_mode("cara" if checked else None))
        rowf.addWidget(self.cb_face, 1)
        rowf.addWidget(self.btn_face)
        g.addLayout(rowf, 6, 0, 1, 3)
        hint = QLabel("Arrastra las esferas de la caja (rojo s, verde t, azul z) para mover cada cara.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        g.addWidget(hint, 7, 0, 1, 3)
        v.addWidget(gb)
        # --- cotas y medidas
        gm = QGroupBox("Cotas y medidas")
        lm = QVBoxLayout(gm)
        rb = QHBoxLayout()
        self.mode_btns = {}
        for text, m in (("Consultar", "consulta"), ("Cota", "cota"), ("Medir", "medida")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.clicked.connect(lambda checked, mm=m: self.set_mode(mm if checked else None))
            self.mode_btns[m] = b
            rb.addWidget(b)
        lm.addLayout(rb)
        self.lbl_info = QLabel("Herramienta apagada: el clic selecciona elementos.")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lm.addWidget(self.lbl_info)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Tipo", "Valores"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(120)
        lm.addWidget(self.table)
        rb2 = QHBoxLayout()
        for text, fn in (("Borrar selección", self.delete_selected), ("Borrar todo", self.clear_annotations),
                         ("Exportar CSV", lambda: self.export_csv())):
            b = QPushButton(text)
            b.clicked.connect(fn)
            rb2.addWidget(b)
        lm.addLayout(rb2)
        v.addWidget(gm)
        v.addStretch(1)
        return w

    def _box_toggled(self, on):
        if on and all(sp.value() == 0 for sp in self.spins.values()):
            self.reset_bounds()
        self._update_box()

    def _frame_changed(self, i):
        self.frame_mode = "puente" if i == 0 else "mundo"
        self.reset_bounds()
        if self.chk_box.isChecked():
            self._update_box()
