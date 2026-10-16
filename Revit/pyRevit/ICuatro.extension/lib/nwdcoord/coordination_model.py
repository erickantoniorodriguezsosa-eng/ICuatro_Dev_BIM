# -*- coding: utf-8 -*-
"""Deteccion de los Modelos de Coordinacion (NWD/NWC) del documento.

Lo que aqui se puede y no se puede obtener esta medido y documentado en
NWD_CAPABILITY_REPORT.md. Los dos hechos que condicionan este modulo:

  - El vinculo NO es un ImportInstance: es un DirectShape de categoria
    OST_Coordination_Model, con un DirectShapeType asociado.
  - GetExternalFileReference() lanza "This Element does not represent an
    external file", asi que la ruta hay que sacarla de un parametro.
"""

import os

from . import units


def _nombre(DB, elem, defecto=u"N/D"):
    """Nombre de un elemento resuelto de forma fiable.

    En las clases que redefinen la propiedad Name (ElementType y sus
    derivadas) el acceso directo elem.Name falla en IronPython;
    Element.Name.GetValue() resuelve la declarada en la clase base."""
    if elem is None:
        return defecto

    for intento in (
        lambda: DB.Element.Name.GetValue(elem),
        lambda: elem.Name,
    ):
        try:
            valor = intento()
            if valor:
                return valor
        except Exception:
            pass

    return defecto


def nombre_de(DB, elem, defecto=u"N/D"):
    """Nombre de un elemento cualquiera de Revit, para uso externo."""
    return _nombre(DB, elem, defecto)


def ruta_en_parametros(DB, elem):
    """Busca entre los parametros un valor que apunte a un NWD/NWC.

    Se recorre por VALOR y no por nombre de parametro porque ese nombre
    cambia con el idioma de Revit ('Camino', 'Path'...)."""
    try:
        parametros = list(elem.Parameters)
    except Exception:
        return None

    candidatos = []

    for parametro in parametros:
        try:
            if parametro.StorageType != DB.StorageType.String:
                continue
            valor = parametro.AsString()
        except Exception:
            continue

        if valor and os.path.splitext(valor)[1].lower() in (".nwd", ".nwc"):
            candidatos.append(valor)

    if not candidatos:
        return None

    # El nombre del tipo tambien acaba en .nwd; la ruta es el candidato
    # que ademas lleva separadores de carpeta.
    for valor in candidatos:
        if "\\" in valor or "/" in valor:
            return valor

    return candidatos[0]


class CoordinationModelInfo(object):
    """Todo lo que Revit sabe de un vinculo de coordinacion.

    Guarda el ElementId y no solo el nombre (requisito de la seccion 5
    del prompt maestro): el nombre no identifica nada de forma estable."""

    def __init__(self, DB, doc, elem):
        self.DB = DB
        self.element = elem
        self.element_id = elem.Id
        self.id_valor = elem.Id.IntegerValue

        self.clase_net = self._clase(elem)
        self.nombre = _nombre(DB, elem)
        self.ruta = None
        self.nombre_archivo = None
        self.existe = False
        self.tipo_nombre = None

        self.bbox = None            # BoundingBoxXYZ en pies
        self.transform = None       # Transform del GeometryInstance, si lo hay
        self.traslacion = None      # XYZ en pies

        self.workset_id = None
        self.pinned = None
        self.oculto = None
        self.categoria = None

        self._resolver(doc, elem)

    # --------------------------------------------------------------------

    def _clase(self, obj):
        try:
            return obj.GetType().FullName
        except Exception:
            return type(obj).__name__

    def _resolver(self, doc, elem):
        DB = self.DB

        try:
            self.categoria = elem.Category.Name
        except Exception:
            pass

        for atributo in ("Pinned", "WorksetId"):
            try:
                setattr(self,
                        "pinned" if atributo == "Pinned" else "workset_id",
                        getattr(elem, atributo))
            except Exception:
                pass

        try:
            self.oculto = elem.IsHidden(doc.ActiveView)
        except Exception:
            pass

        # --- tipo y ruta del archivo ---
        try:
            type_elem = doc.GetElement(elem.GetTypeId())
        except Exception:
            type_elem = None

        if type_elem is not None:
            self.tipo_nombre = _nombre(DB, type_elem)

            # La via oficial falla en un modelo de coordinacion, pero se
            # intenta igual: otros vinculos si la soportan.
            try:
                ext = type_elem.GetExternalFileReference()
                if ext is not None:
                    self.ruta = DB.ModelPathUtils.\
                        ConvertModelPathToUserVisiblePath(ext.GetAbsolutePath())
            except Exception:
                pass

            if not self.ruta:
                self.ruta = ruta_en_parametros(DB, type_elem)

        if not self.ruta:
            self.ruta = ruta_en_parametros(DB, elem)

        if self.ruta:
            self.nombre_archivo = os.path.basename(self.ruta)
            try:
                self.existe = os.path.isfile(self.ruta)
            except Exception:
                self.existe = False

        # --- caja envolvente ---
        # Con None devuelve null en un modelo de coordinacion; contra una
        # vista si responde. Se prueban ambas en ese orden.
        for vista in (self._vista(doc), None):
            try:
                caja = elem.get_BoundingBox(vista)
            except Exception:
                caja = None

            if caja is not None:
                self.bbox = caja
                break

        # --- transformacion ---
        self._resolver_transform(doc, elem)

    def _vista(self, doc):
        try:
            return doc.ActiveView
        except Exception:
            return None

    def _resolver_transform(self, doc, elem):
        """La transformacion del vinculo, si Revit la expone.

        El unico sitio donde aparece es el GeometryInstance que devuelve
        get_Geometry(), aunque su contenido venga vacio."""
        DB = self.DB

        try:
            opciones = DB.Options()
            opciones.View = doc.ActiveView
            geometria = elem.get_Geometry(opciones)
        except Exception:
            geometria = None

        if geometria is None:
            return

        try:
            for obj in geometria:
                if isinstance(obj, DB.GeometryInstance):
                    self.transform = obj.Transform
                    self.traslacion = obj.Transform.Origin
                    return
        except Exception:
            pass

    # --------------------------------------------------------------------
    # DATOS DERIVADOS
    # --------------------------------------------------------------------

    def caja_en_metros(self):
        """(min, max) de la caja envolvente en metros, o None."""
        if self.bbox is None:
            return None

        p0, p1 = self.bbox.Min, self.bbox.Max

        return (
            [units.pies_a_metros(p0.X),
             units.pies_a_metros(p0.Y),
             units.pies_a_metros(p0.Z)],
            [units.pies_a_metros(p1.X),
             units.pies_a_metros(p1.Y),
             units.pies_a_metros(p1.Z)],
        )

    def tamano_en_metros(self):
        caja = self.caja_en_metros()

        if caja is None:
            return None

        return [caja[1][i] - caja[0][i] for i in range(3)]

    def __repr__(self):
        return "<CoordinationModelInfo {0} '{1}'>".format(
            self.id_valor, self.nombre_archivo or self.nombre
        )


def id_categoria(DB):
    """Id de la categoria 'Modelos de Coordinacion'.

    El nombre del BuiltInCategory difiere entre versiones de la API, asi
    que se prueban las dos formas conocidas."""
    for nombre in ("OST_Coordination_Model", "OST_CoordinationModel"):
        bic = getattr(DB.BuiltInCategory, nombre, None)

        if bic is not None:
            try:
                return int(bic)
            except Exception:
                continue

    return None


def detectar(DB, doc, registro=None):
    """Todos los modelos de coordinacion del documento.

    Se busca por categoria y no por clase: el elemento es un DirectShape,
    y filtrar por esa clase traeria tambien todos los DirectShape que el
    usuario tenga en el modelo por otros motivos."""
    categoria = id_categoria(DB)

    if categoria is None:
        if registro:
            registro.aviso(u"Esta version de Revit no expone la categoria "
                           u"de modelos de coordinacion.")
        return []

    try:
        elementos = (
            DB.FilteredElementCollector(doc)
            .OfCategoryId(DB.ElementId(categoria))
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as error:
        if registro:
            registro.excepcion(u"Recorrido de modelos de coordinacion", error)
        return []

    modelos = []

    for elemento in elementos:
        try:
            modelo = CoordinationModelInfo(DB, doc, elemento)
            modelos.append(modelo)

            if registro:
                caja = modelo.caja_en_metros()
                if caja is None:
                    registro.aviso(
                        u"Modelo de coordinacion {0}: sin caja envolvente.",
                        modelo.id_valor)
                else:
                    tamano = [caja[1][i] - caja[0][i] for i in range(3)]
                    registro.info(
                        u"Modelo de coordinacion {0}: nombre={1}, "
                        u"ruta={2}, existe={3}, "
                        u"bbox_min=({4:.3f},{5:.3f},{6:.3f}) m, "
                        u"bbox_max=({7:.3f},{8:.3f},{9:.3f}) m, "
                        u"tamano=({10:.3f},{11:.3f},{12:.3f}) m",
                        modelo.id_valor, modelo.nombre_archivo or modelo.nombre,
                        modelo.ruta or u"(sin ruta)", modelo.existe,
                        caja[0][0], caja[0][1], caja[0][2],
                        caja[1][0], caja[1][1], caja[1][2],
                        tamano[0], tamano[1], tamano[2])

                if modelo.transform is not None:
                    try:
                        origen = modelo.transform.Origin
                        registro.info(
                            u"Transform interno expuesto por Revit para "
                            u"{0}: origen=({1:.6f},{2:.6f},{3:.6f}) pies",
                            modelo.id_valor, origen.X, origen.Y, origen.Z)
                    except Exception:
                        registro.aviso(
                            u"Modelo {0}: Revit expuso transformacion, "
                            u"pero no se pudo leer su origen.",
                            modelo.id_valor)
        except Exception as error:
            if registro:
                registro.excepcion(u"Lectura de un modelo de coordinacion",
                                   error, elemento.Id)

    if registro:
        registro.info(u"Modelos de coordinacion detectados: {0}", len(modelos))

    return modelos
