# -*- coding: utf-8 -*-
"""Previsualizacion y resaltado de los resultados (seccion 15).

Colorea los elementos en la vista activa segun lo que habria que hacer
con ellos, para poder mirarlo ANTES de tocar el modelo.

Se usa OverrideGraphicSettings, que existe en Revit 2022 y es reversible
sin dejar rastro en el modelo. Se descartaron las otras opciones que
sugiere el prompt: DirectContext3D obliga a un servidor de dibujo
externo, y TemporaryViewMode aisla en lugar de distinguir estados.

Aunque un cambio de overrides no altera geometria, Revit lo considera
una modificacion de la vista y necesita transaccion.
"""

from . import vertical_analysis as analisis


# Colores por estado. Verde OK, naranja subir, azul bajar, gris sin dato,
# rojo problema: la escala se lee sin leyenda.
COLORES = {
    analisis.OK: (80, 170, 90),
    analisis.SUBIR: (235, 145, 40),
    analisis.BAJAR: (60, 130, 220),
    analisis.PENDIENTE: (135, 90, 200),
    analisis.SIN_SUPERFICIE: (150, 150, 150),
    analisis.FUERA_DE_RANGO: (200, 60, 200),
    analisis.ERROR: (220, 60, 60),
}


class Previsualizacion(object):
    """Aplica y retira los overrides de una tanda de resultados.

    Recuerda que elementos toco para poder limpiarlos exactamente, sin
    barrer la vista entera ni tocar overrides que hubiera puesto el
    usuario por su cuenta."""

    def __init__(self, DB, doc, uidoc, registro=None):
        self.DB = DB
        self.doc = doc
        self.uidoc = uidoc
        self.registro = registro
        self.tocados = []

    # ------------------------------------------------------------------

    def _ajustes(self, estado, relleno_solido=None):
        DB = self.DB
        rojo, verde, azul = COLORES.get(estado, (120, 120, 120))
        color = DB.Color(rojo, verde, azul)

        ajustes = DB.OverrideGraphicSettings()

        try:
            ajustes.SetProjectionLineColor(color)
            ajustes.SetCutLineColor(color)
        except Exception:
            pass

        try:
            ajustes.SetSurfaceTransparency(20)
        except Exception:
            pass

        if relleno_solido is not None:
            try:
                ajustes.SetSurfaceForegroundPatternId(relleno_solido)
                ajustes.SetSurfaceForegroundPatternColor(color)
                ajustes.SetCutForegroundPatternId(relleno_solido)
                ajustes.SetCutForegroundPatternColor(color)
            except Exception:
                pass

        return ajustes

    def _relleno_solido(self):
        """Id del patron de relleno solido, si existe en el documento.

        Sin el, el color solo se ve en las aristas; con el, la superficie
        del elemento se tine y se distingue de un vistazo."""
        DB = self.DB

        try:
            patron = DB.FillPatternElement.GetFillPatternElementByName(
                self.doc, DB.FillPatternTarget.Drafting, "<Solid fill>")

            if patron is None:
                for candidato in DB.FilteredElementCollector(self.doc)\
                        .OfClass(DB.FillPatternElement).ToElements():
                    if candidato.GetFillPattern().IsSolidFill:
                        patron = candidato
                        break

            return patron.Id if patron is not None else None
        except Exception:
            return None

    # ------------------------------------------------------------------

    def aplicar(self, resultados, vista=None):
        """Colorea los elementos segun su estado. Devuelve cuantos toco.

        Debe llamarse dentro de una transaccion abierta por quien llama:
        asi la previsualizacion y su limpieza pueden agruparse."""
        vista = vista or self.doc.ActiveView
        relleno = self._relleno_solido()

        cache = {}
        guardados = set((v, e) for v, e, _ in self.tocados)
        pintados = 0

        for resultado in resultados:
            if resultado.element_id is None:
                continue

            estado = resultado.estado

            if estado not in cache:
                cache[estado] = self._ajustes(estado, relleno)

            try:
                # Guardar la vista y el override previo: la ventana permanece
                # abierta mientras el usuario navega a otras vistas.
                clave = (vista.Id, resultado.element_id)
                anterior = None
                if clave not in guardados:
                    anterior = vista.GetElementOverrides(resultado.element_id)
                vista.SetElementOverrides(resultado.element_id, cache[estado])
                if anterior is not None:
                    self.tocados.append((vista.Id, resultado.element_id, anterior))
                    guardados.add(clave)
                pintados += 1
            except Exception as error:
                if self.registro:
                    self.registro.excepcion(
                        u"Override de previsualizacion", error,
                        resultado.id_valor)

        return pintados

    def limpiar(self, vista=None):
        """Restaura cada vista original aunque ahora este activa otra vista.

        vista se conserva por compatibilidad; nunca redirige la limpieza.
        """
        limpiados = 0
        pendientes = []

        for vista_id, element_id, anterior in self.tocados:
            try:
                original = self.doc.GetElement(vista_id)
                if original is None or self.doc.GetElement(element_id) is None:
                    continue
                original.SetElementOverrides(element_id, anterior)
                limpiados += 1
            except Exception as error:
                pendientes.append((vista_id, element_id, anterior))
                if self.registro:
                    self.registro.excepcion(u"Restaurar previsualizacion", error)

        self.tocados = pendientes

        return limpiados


def mostrar_en_revit(uidoc, doc, element_id, hacer_zoom=True):
    """Selecciona un elemento y lo encuadra en la vista (seccion 15).

    Es lo que se llama al pinchar una fila de la tabla de resultados."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId

    try:
        ids = List[ElementId]()
        ids.Add(element_id)
        uidoc.Selection.SetElementIds(ids)
    except Exception:
        return False

    if not hacer_zoom:
        return True

    try:
        elemento = doc.GetElement(element_id)
        caja = elemento.get_BoundingBox(None) or \
               elemento.get_BoundingBox(doc.ActiveView)

        if caja is None:
            return True

        # Un margen proporcional al elemento: encuadrar su caja exacta
        # lo deja pegado a los bordes y sin contexto alrededor.
        ancho = max(caja.Max.X - caja.Min.X,
                    caja.Max.Y - caja.Min.Y,
                    caja.Max.Z - caja.Min.Z)
        margen = max(ancho, 3.0)

        from Autodesk.Revit.DB import XYZ
        minimo = XYZ(caja.Min.X - margen, caja.Min.Y - margen,
                     caja.Min.Z - margen)
        maximo = XYZ(caja.Max.X + margen, caja.Max.Y + margen,
                     caja.Max.Z + margen)

        for uiview in uidoc.GetOpenUIViews():
            if uiview.ViewId == doc.ActiveView.Id:
                uiview.ZoomAndCenterRectangle(minimo, maximo)
                break
    except Exception:
        pass

    return True


def leyenda():
    """Pares (estado, etiqueta, color) para dibujar la leyenda."""
    return [
        (estado, analisis.ETIQUETAS_ESTADO[estado], COLORES[estado])
        for estado in (analisis.OK, analisis.SUBIR, analisis.BAJAR,
                       analisis.PENDIENTE,
                       analisis.SIN_SUPERFICIE, analisis.FUERA_DE_RANGO,
                       analisis.ERROR)
    ]
