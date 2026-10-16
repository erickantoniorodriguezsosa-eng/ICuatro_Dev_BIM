# -*- coding: utf-8 -*-
"""Conversiones de unidades, centralizadas.

Revit trabaja internamente en PIES. Toda la geometria que viene del NWD
llega en METROS. Mezclar ambas es el error mas facil de cometer y el mas
dificil de detectar, porque no falla: simplemente coloca las cosas donde
no van.

Regla del proyecto (seccion 28 del prompt maestro): ninguna conversion
suelta fuera de este modulo.
"""

METROS_POR_PIE = 0.3048
PIES_POR_METRO = 1.0 / METROS_POR_PIE


def metros_a_pies(valor):
    """Metros -> pies (unidad interna de Revit)."""
    return valor * PIES_POR_METRO


def pies_a_metros(valor):
    """Pies (unidad interna de Revit) -> metros."""
    return valor * METROS_POR_PIE


def xyz_metros_a_pies(DB, x, y, z):
    """Punto en metros -> XYZ de Revit en pies."""
    return DB.XYZ(
        x * PIES_POR_METRO,
        y * PIES_POR_METRO,
        z * PIES_POR_METRO,
    )


def formato_m(valor_en_pies, decimales=3):
    """Un valor interno de Revit, mostrado en metros."""
    return u"{0:.{1}f}".format(valor_en_pies * METROS_POR_PIE, decimales)


def verificar_contra_revit(DB):
    """Comprueba la constante contra UnitUtils de Revit.

    La equivalencia pie/metro es exacta por definicion (0.3048), asi que
    la constante basta y evita depender de una API que cambio de forma
    entre versiones. Aun asi se contrasta con Revit una vez, para que si
    algun dia no coincidieran quede registrado en el diagnostico en lugar
    de producir un desplazamiento silencioso.

    Devuelve (coincide, valor_revit, mensaje)."""
    try:
        tipo = getattr(DB, "UnitTypeId", None)

        if tipo is not None and hasattr(tipo, "Meters"):
            # Revit 2021+ (ForgeTypeId). Es el camino en Revit 2022.
            valor = DB.UnitUtils.ConvertFromInternalUnits(1.0, tipo.Meters)
            origen = "UnitTypeId.Meters"
        else:
            # Camino heredado, por si se ejecuta en una version anterior.
            valor = DB.UnitUtils.ConvertFromInternalUnits(
                1.0, DB.DisplayUnitType.DUT_METERS
            )
            origen = "DisplayUnitType.DUT_METERS"

        coincide = abs(valor - METROS_POR_PIE) < 1e-9

        return coincide, valor, "1 pie = {0} m segun {1}".format(valor, origen)
    except Exception as error:
        return None, None, "No se pudo consultar UnitUtils: {0}".format(error)
