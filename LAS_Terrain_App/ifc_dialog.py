"""
ifc_dialog.py
Ventana de exportación a IFC: versiones, categorías IFC (clase y tipo predefinido por tipo de
elemento), alcance, estructura espacial, propiedades y georreferencia. Las restricciones se calculan
con los esquemas reales de IfcOpenShell (ifc_schema_info), no con una lista fija.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

import ifc_schema_info as info

VERSIONS = ("IFC2X3", "IFC4", "IFC4X3")

# tipo de elemento: (etiqueta, clases IFC permitidas, contenedor en la jerarquía de puente)
KINDS = {
    "losa": ("Losa de tablero", ["IfcSlab", "IfcPlate", "IfcCovering", "IfcBuildingElementProxy"],
             "Superestructura > Tablero (DECK)"),
    "viga": ("Vigas", ["IfcBeam", "IfcMember", "IfcBuildingElementProxy"],
             "Superestructura > Tablero (DECK)"),
    "baranda": ("Barandas (sólido simple)", ["IfcRailing", "IfcMember", "IfcBuildingElementProxy"],
                "Superestructura > Elementos de superficie y seguridad"),
    "riel": ("Rieles de baranda", ["IfcRailing", "IfcMember", "IfcBuildingElementProxy"],
             "Superestructura > Elementos de superficie y seguridad"),
    "poste": ("Postes de baranda", ["IfcMember", "IfcColumn", "IfcBuildingElementProxy"],
              "Superestructura > Elementos de superficie y seguridad"),
    "estribo": ("Estribos", ["IfcWall", "IfcFooting", "IfcBuildingElementProxy"],
                "Subestructura > una parte ABUTMENT por estribo"),
    "muro": ("Muros de retorno", ["IfcWall", "IfcBuildingElementProxy"],
             "Subestructura > en la parte ABUTMENT de su estribo"),
    "apoyo": ("Apoyos bajo vigas", ["IfcBearing", "IfcBuildingElementProxy"],
              "Subestructura > en la parte ABUTMENT de su estribo"),
    "terreno": ("Terreno (malla)", ["IfcGeographicElement", "IfcBuildingElementProxy"], "Sitio (IfcSite)"),
}
AUTO, NONE = "__auto__", "__none__"


class IfcExportDialog(QDialog):
    def __init__(self, parent, counts: dict, default_classes: dict, epsg: int, n_visible: int,
                 n_selected: int, default_path: str, default_types: dict | None = None):
        super().__init__(parent)
        self.default_types = default_types or {}
        self.setWindowTitle("Exportar a IFC")
        self.resize(1080, 720)
        self.rows = {}
        v = QVBoxLayout(self)

        gv = QGroupBox("Versiones de IFC (se genera un archivo por versión)")
        hv = QHBoxLayout(gv)
        self.chk_ver = {}
        for ver in VERSIONS:
            cb = QCheckBox(ver)
            cb.setChecked(True)
            cb.setToolTip(info.structure_note(ver))
            cb.toggled.connect(self._refresh)
            hv.addWidget(cb)
            self.chk_ver[ver] = cb
        hv.addStretch(1)
        v.addWidget(gv)
        self.lbl_struct = QLabel()
        self.lbl_struct.setWordWrap(True)
        self.lbl_struct.setStyleSheet("color: #aaa;")
        v.addWidget(self.lbl_struct)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Incluir", "Categoría (cantidad)", "Clase IFC",
                                              "Tipo predefinido", "Contenedor en la jerarquía", "Restricciones"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        for c, w in ((0, 60), (1, 170), (2, 180), (3, 190), (4, 220)):
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        for kind in KINDS:
            if counts.get(kind):
                self._add_row(kind, counts[kind], default_classes.get(kind, KINDS[kind][1][0]))
        self.table.resizeRowsToContents()
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        gs = QGroupBox("Alcance")
        ls = QVBoxLayout(gs)
        self.scope = QButtonGroup(self)
        self.rb = {}
        for key, text in (("all", "Todos los elementos"),
                          ("visible", f"Solo los visibles en el visor ({n_visible})"),
                          ("selected", f"Solo los seleccionados ({n_selected})")):
            rb = QRadioButton(text)
            self.scope.addButton(rb)
            ls.addWidget(rb)
            self.rb[key] = rb
        self.rb["all"].setChecked(True)
        self.rb["selected"].setEnabled(n_selected > 0)
        row.addWidget(gs)

        go = QGroupBox("Estructura y propiedades")
        lo = QVBoxLayout(go)
        self.chk_hier = QCheckBox("Jerarquía de puente (IfcBridge / IfcBridgePart; ensamblajes en IFC4 e IFC2X3)")
        self.chk_hier.setChecked(True)
        self.chk_hier.setToolTip("Desmarcada: todos los elementos cuelgan directamente de IfcSite")
        self.chk_ps = QCheckBox("Propiedades comunes buildingSMART (Pset_*Common)")
        self.chk_ps.setChecked(True)
        self.chk_qto = QCheckBox("Cantidades base (Qto_*BaseQuantities; solo IFC4 e IFC4X3)")
        self.chk_qto.setChecked(True)
        self.chk_custom = QCheckBox("Pset_Open3D: vértices, volumen y supuestos de modelado")
        self.chk_custom.setChecked(True)
        for w in (self.chk_hier, self.chk_ps, self.chk_qto, self.chk_custom):
            lo.addWidget(w)
        row.addWidget(go, 1)

        gg = QGroupBox("Georreferencia y validación")
        lg = QVBoxLayout(gg)
        self.chk_geo = QCheckBox("Georreferenciar (IfcMapConversion; solo IFC4 e IFC4X3)")
        self.chk_geo.setChecked(epsg > 0)
        self.chk_geo.toggled.connect(self._refresh)
        self.sp_epsg = QSpinBox()
        self.sp_epsg.setRange(0, 99999)
        self.sp_epsg.setValue(epsg)
        self.sp_epsg.setPrefix("EPSG: ")
        self.sp_epsg.setToolTip("0 = sin georreferencia. Ej.: 32718 (WGS84 / UTM 18S)")
        self.cb_coords = QComboBox()
        self.cb_coords.addItem("Coordenadas del modelo: UTM (reales)", "utm")
        self.cb_coords.addItem("Coordenadas del modelo: local x, y, z", "local")
        self.cb_coords.setCurrentIndex(0 if epsg > 0 else 1)
        self.cb_coords.setToolTip("UTM: los elementos quedan en coordenadas reales.\n"
                                  "Local: el origen del modelo es la esquina mínima de los elementos; con EPSG el "
                                  "desplazamiento a reales va en IfcMapConversion.")
        self.chk_val = QCheckBox("Validar con IfcOpenShell al terminar")
        self.chk_val.setChecked(True)
        for w in (self.chk_geo, self.sp_epsg, self.cb_coords, self.chk_val):
            lg.addWidget(w)
        row.addWidget(gg, 1)
        v.addLayout(row)

        hp = QHBoxLayout()
        hp.addWidget(QLabel("Archivo:"))
        self.ed_path = QLineEdit(default_path)
        hp.addWidget(self.ed_path, 1)
        b = QPushButton("Examinar…")
        b.clicked.connect(self._browse)
        hp.addWidget(b)
        v.addLayout(hp)
        self.lbl_hint = QLabel("Con varias versiones se agrega el sufijo _IFC2X3, _IFC4 o _IFC4X3 al nombre.")
        self.lbl_hint.setStyleSheet("color: #888;")
        v.addWidget(self.lbl_hint)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Exportar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self._refresh()

    # ------------------------------------------------------------------ filas
    def _add_row(self, kind, count, default_cls):
        label, classes, container = KINDS[kind]
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QCheckBox()
        chk.setChecked(True)
        chk.toggled.connect(self._refresh)
        holder = QLabel()           # centra la casilla
        cell = QHBoxLayout(holder)
        cell.addWidget(chk, alignment=Qt.AlignCenter)
        cell.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(r, 0, holder)
        self.table.setItem(r, 1, QTableWidgetItem(f"{label} ({count})"))
        cls = QComboBox()
        cls.addItems(classes)
        if default_cls in classes:
            cls.setCurrentText(default_cls)
        cls.currentTextChanged.connect(lambda _t, k=kind: self._class_changed(k))
        self.table.setCellWidget(r, 2, cls)
        pt = QComboBox()
        pt.currentIndexChanged.connect(lambda _i, k=kind: self._refresh_row(k))
        self.table.setCellWidget(r, 3, pt)
        self.table.setItem(r, 4, QTableWidgetItem(container))
        self.table.setItem(r, 5, QTableWidgetItem(""))
        for c in (1, 4, 5):
            self.table.item(r, c).setFlags(Qt.ItemIsEnabled)
        self.rows[kind] = {"row": r, "chk": chk, "cls": cls, "pt": pt}

    def _versions(self):
        return [v for v in VERSIONS if self.chk_ver[v].isChecked()]

    def _class_changed(self, kind):
        self._fill_types(kind)
        self._refresh_row(kind)

    def _fill_types(self, kind):
        """Tipos predefinidos válidos: unión de las versiones elegidas, marcando los que no valen en todas."""
        rw = self.rows[kind]
        cls = rw["cls"].currentText()
        vers = self._versions() or list(VERSIONS)
        prev = rw["pt"].currentData()
        rw["pt"].blockSignals(True)
        rw["pt"].clear()
        rw["pt"].addItem("(predeterminado por versión)", AUTO)
        rw["pt"].addItem("(sin tipo)", NONE)
        seen = []
        for ver in vers:
            for t in info.predefined_types(ver, cls):
                if t not in seen:
                    seen.append(t)
        for t in seen:
            ok = [x for x in vers if t in info.predefined_types(x, cls)]
            rw["pt"].addItem(t if len(ok) == len(vers) else f"{t}  (solo {', '.join(ok)})", t)
        idx = rw["pt"].findData(prev) if prev is not None else 0
        rw["pt"].setCurrentIndex(max(idx, 0))
        rw["pt"].blockSignals(False)

    def _restrictions(self, kind):
        rw = self.rows[kind]
        cls, pt = rw["cls"].currentText(), rw["pt"].currentData()
        notes = []
        for ver in self._versions():
            if not info.class_exists(ver, cls):
                notes.append(f"{ver}: {cls} no existe, se usa IfcBuildingElementProxy")
                continue
            valid = info.predefined_types(ver, cls)
            if pt == NONE:
                continue
            eff = pt
            if pt == AUTO:                       # el predeterminado de la categoría para esa versión
                d = self.default_types.get(kind)
                eff = d.get(ver, d.get("default")) if isinstance(d, dict) else d
                if cls != KINDS[kind][1][0]:     # con otra clase el predeterminado no aplica
                    eff = None
            if not eff:
                continue
            if not valid:
                notes.append(f"{ver}: {cls} no tiene tipo predefinido, se omite {eff}")
            elif eff not in valid:
                notes.append(f"{ver}: '{eff}' no es válido en {cls}, se omite")
        if cls == "IfcBuildingElementProxy":
            notes.append("clase genérica: pierde la semántica del elemento")
        return "; ".join(notes) if notes else "Sin restricciones en las versiones elegidas"

    def _refresh_row(self, kind):
        rw = self.rows[kind]
        it = self.table.item(rw["row"], 5)
        it.setText(self._restrictions(kind))
        warn = "no " in it.text() or "omite" in it.text() or "genérica" in it.text()
        it.setForeground(QBrush(QColor("#e8c547" if warn else "#5fd68a")))
        self.table.resizeRowToContents(rw["row"])

    def _refresh(self, *_):
        vers = self._versions()
        self.lbl_struct.setText("\n".join(info.structure_note(v) for v in vers) or "Elige al menos una versión.")
        for kind in self.rows:
            self._fill_types(kind)
            self._refresh_row(kind)
        any_geo = any(v != "IFC2X3" for v in vers)
        self.chk_geo.setEnabled(any_geo)
        self.sp_epsg.setEnabled(any_geo and self.chk_geo.isChecked())
        self.chk_qto.setEnabled(any_geo)
        ok = bool(vers) and any(rw["chk"].isChecked() for rw in self.rows.values())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar IFC", self.ed_path.text(), "IFC (*.ifc)")
        if path:
            self.ed_path.setText(path)

    # ------------------------------------------------------------------ resultado
    def config(self) -> dict:
        cats = {}
        for kind, rw in self.rows.items():
            d = rw["pt"].currentData()
            cats[kind] = {"include": rw["chk"].isChecked(), "ifc_class": rw["cls"].currentText(),
                          "predefined_type": None if d == AUTO else ("" if d == NONE else d)}
        scope = next(k for k, rb in self.rb.items() if rb.isChecked())
        epsg = self.sp_epsg.value() if self.chk_geo.isChecked() and self.chk_geo.isEnabled() else None
        return {"versions": self._versions(), "categories": cats, "scope": scope,
                "hierarchy": self.chk_hier.isChecked(), "psets": self.chk_ps.isChecked(),
                "qto": self.chk_qto.isChecked(), "custom": self.chk_custom.isChecked(),
                "georef": epsg is not None, "epsg": epsg, "coords": self.cb_coords.currentData(), "validate": self.chk_val.isChecked(),
                "path": self.ed_path.text().strip()}
