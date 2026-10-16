# -*- coding: utf-8 -*-
"""Conversion entre las coordenadas del NWD y las de Revit.

Punto unico de conversion (requisito de la seccion 6 del prompt maestro:
"NO repartir correcciones de coordenadas por distintas partes del
codigo"). Si algo sale desplazado, se corrige aqui y en ningun otro
sitio.

Lo medido en este proyecto (NWD_CAPABILITY_REPORT.md §4):

  - El NWD esta en coordenadas proyectadas (UTM) y Revit las guarda
    relativas a su punto base.
  - La relacion entre ambos es una TRASLACION PURA: los tamanos de las
    dos cajas coinciden a 0,2 mm sobre 22 km, sin rotacion ni escala.
  - dZ = 0: la cota del NWD es directamente la cota de Revit.

Nada de eso se da por supuesto: la traslacion se deriva en cada
ejecucion y se comprueba antes de usarla.
"""

from . import units


class Transformacion(object):
    """Traslacion entre el NWD (metros) y Revit (pies internos).

    Los desplazamientos se guardan en METROS porque es la unidad en la
    que llegan las dos cajas que los originan; la conversion a pies se
    hace al final de cada operacion."""

    def __init__(self, dx=0.0, dy=0.0, dz=0.0):
        self.dx = dx
        self.dy = dy
        self.dz = dz

    # ------------------------------------------------------------------
    # CONVERSIONES
    # ------------------------------------------------------------------

    def nwd_a_revit_m(self, x, y, z):
        """Punto del NWD (m) -> punto de Revit (m)."""
        return (x + self.dx, y + self.dy, z + self.dz)

    def nwd_a_revit(self, x, y, z):
        """Punto del NWD (m) -> punto de Revit en PIES internos."""
        return (
            units.metros_a_pies(x + self.dx),
            units.metros_a_pies(y + self.dy),
            units.metros_a_pies(z + self.dz),
        )

    def revit_a_nwd(self, x, y, z):
        """Punto de Revit en PIES internos -> punto del NWD (m)."""
        return (
            units.pies_a_metros(x) - self.dx,
            units.pies_a_metros(y) - self.dy,
            units.pies_a_metros(z) - self.dz,
        )

    def z_nwd_a_revit_pies(self, z_nwd_m):
        """Cota del NWD (m) -> cota de Revit en pies.

        Es la conversion que mas se usa: una vez por elemento analizado."""
        return units.metros_a_pies(z_nwd_m + self.dz)

    def caja_revit_a_nwd(self, min_pies, max_pies, margen_m=0.0):
        """Caja de Revit (XYZ en pies) -> (minX, minY, maxX, maxY) en
        metros NWD, para pedirle esa zona al extractor.

        El margen se aplica en metros y sirve para que la superficie
        cubra algo mas que los propios elementos: si un elemento cae
        justo en el borde, sin margen no habria triangulos bajo el."""
        x0, y0, _ = self.revit_a_nwd(min_pies.X, min_pies.Y, min_pies.Z)
        x1, y1, _ = self.revit_a_nwd(max_pies.X, max_pies.Y, max_pies.Z)

        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        return (x0 - margen_m, y0 - margen_m, x1 + margen_m, y1 + margen_m)

    def __repr__(self):
        return "<Transformacion dx={0:.4f} dy={1:.4f} dz={2:.4f} m>".format(
            self.dx, self.dy, self.dz
        )


class DiagnosticoTransformacion(object):
    """Por que se acepto o se rechazo una transformacion derivada.

    Se conserva junto a la transformacion para poder mostrarlo: una
    traslacion mal derivada coloca toda la superficie en otro sitio sin
    dar ningun error, asi que el usuario tiene que poder verla."""

    def __init__(self):
        self.valida = False
        self.motivo = u""
        self.desplazamientos = []      # por eje: (desde_min, desde_max, discrepancia)
        self.tamano_nwd = [0.0, 0.0, 0.0]
        self.tamano_revit = [0.0, 0.0, 0.0]
        self.discrepancia_maxima = 0.0
        self.desviacion_tamano = 0.0
        self.traslacion_pura = False
        self.dz_es_cero = False


def derivar(caja_nwd, caja_revit,
            tolerancia_discrepancia_m=0.05,
            tolerancia_tamano=0.02):
    """Deriva la traslacion NWD -> Revit contrastando ambas cajas.

    'caja_nwd'   : (minimos, maximos) en metros, del extractor.
    'caja_revit' : (minimos, maximos) en metros, del vinculo en Revit.

    El metodo se apoya en una comprobacion que lo hace fiable: el
    desplazamiento se calcula DOS VECES, desde los minimos y desde los
    maximos. Si hay rotacion o escala, ambos valores difieren; si
    coinciden, es una traslacion pura y el valor es correcto.

    Devuelve (Transformacion, DiagnosticoTransformacion). La
    transformacion se devuelve siempre, incluso si el diagnostico la
    marca como no valida, para que quien llame pueda decidir si la usa
    bajo su responsabilidad."""
    diagnostico = DiagnosticoTransformacion()

    if not caja_nwd or not caja_revit:
        diagnostico.motivo = u"Falta la caja de uno de los dos lados."
        return Transformacion(), diagnostico

    min_nwd, max_nwd = caja_nwd
    min_revit, max_revit = caja_revit

    desplazamientos = []

    for eje in range(3):
        tamano_nwd = max_nwd[eje] - min_nwd[eje]
        tamano_revit = max_revit[eje] - min_revit[eje]

        diagnostico.tamano_nwd[eje] = tamano_nwd
        diagnostico.tamano_revit[eje] = tamano_revit

        desde_min = min_revit[eje] - min_nwd[eje]
        desde_max = max_revit[eje] - max_nwd[eje]
        discrepancia = abs(desde_min - desde_max)

        diagnostico.desplazamientos.append((desde_min, desde_max, discrepancia))
        diagnostico.discrepancia_maxima = max(
            diagnostico.discrepancia_maxima, discrepancia)

        referencia = max(abs(tamano_revit), 1.0)
        diagnostico.desviacion_tamano = max(
            diagnostico.desviacion_tamano,
            abs(tamano_nwd - tamano_revit) / referencia)

        # Se promedian ambas derivaciones: si son coherentes da igual
        # cual se tome, y si difieren poco el promedio reparte el error.
        desplazamientos.append((desde_min + desde_max) / 2.0)

    transformacion = Transformacion(*desplazamientos)

    diagnostico.traslacion_pura = (
        diagnostico.discrepancia_maxima <= tolerancia_discrepancia_m)
    diagnostico.dz_es_cero = abs(transformacion.dz) < 0.01

    if diagnostico.desviacion_tamano > tolerancia_tamano:
        diagnostico.motivo = (
            u"Las dos cajas no miden lo mismo (difieren un {0:.1f}%). "
            u"Probablemente el NWD sondeado no es el que esta vinculado, "
            u"o se extrajo solo una parte."
            .format(diagnostico.desviacion_tamano * 100))
        return transformacion, diagnostico

    if not diagnostico.traslacion_pura:
        diagnostico.motivo = (
            u"El desplazamiento derivado desde los minimos y desde los "
            u"maximos no coincide ({0:.4f} m de diferencia): hay rotacion "
            u"o escala entre el NWD y el proyecto."
            .format(diagnostico.discrepancia_maxima))
        return transformacion, diagnostico

    diagnostico.valida = True
    diagnostico.motivo = (
        u"Traslacion pura confirmada (discrepancia maxima {0:.5f} m)."
        .format(diagnostico.discrepancia_maxima))

    return transformacion, diagnostico
