# -*- coding: utf-8 -*-
"""Propuestas de pendiente longitudinal; solo lectura, sin deformar familias.

Muestrea el eje cada 25 cm como maximo (minimo nueve puntos). No certifica
la silueta completa, el ancho ni irregularidades menores que ese muestreo.
Solo ofrece ajustar una LocationCurve recta, conservando XY de sus extremos.
"""
import math

from . import revit_elements, surface, units


def extremos(DB, elemento):
    ubicacion = elemento.Location
    if not isinstance(ubicacion, DB.LocationCurve):
        return None
    curva = ubicacion.Curve
    if not isinstance(curva, DB.Line) or not curva.IsBound:
        return None
    return tuple((curva.GetEndPoint(i).X, curva.GetEndPoint(i).Y,
                  curva.GetEndPoint(i).Z) for i in (0, 1))


def motivo_no_adaptable(DB, doc, elemento):
    """No alterar muros, anfitriones, grupos o redes/conexiones adyacentes."""
    movible, motivo = revit_elements.comprobar_movilidad(DB, doc, elemento)
    if not movible:
        return motivo
    if not isinstance(elemento, (DB.FamilyInstance, DB.MEPCurve)):
        return u"Esta clase de elemento no admite el ajuste de eje implementado."
    if elemento.Location.IsReadOnly:
        return u"El eje es de solo lectura."
    try:
        if DB.JoinGeometryUtils.GetJoinedElements(doc, elemento).Count:
            return u"Tiene geometria unida a otros elementos."
        if isinstance(elemento, DB.MEPCurve):
            gestor = elemento.ConnectorManager
        else:
            mep = elemento.MEPModel
            gestor = mep.ConnectorManager if mep is not None else None
            if elemento.StructuralType in (DB.Structure.StructuralType.Beam,
                                            DB.Structure.StructuralType.Brace):
                for i in (0, 1):
                    if DB.Structure.StructuralFramingUtils.IsJoinAllowedAtEnd(
                            elemento, i):
                        return (u"Viga con uniones automaticas: deshabilita sus "
                                u"uniones antes de adaptar la pendiente.")
        if gestor is not None:
            for conector in gestor.Connectors:
                if conector.ConnectorType == DB.ConnectorType.Logical:
                    continue
                if conector.IsConnected:
                    return u"Tiene conectores unidos; no se modificara la red."
    except Exception:
        return u"No se pudieron verificar las conexiones del elemento."
    return u""


class PropuestaPendiente(object):
    def __init__(self, originales, objetivos, pendiente, residuo, muestras):
        self.originales = originales       # XYZ internos (pies)
        self.objetivos = objetivos
        self.porcentaje = pendiente
        self.residuo_m = residuo
        self.muestras = muestras


def evaluar_perfil(originales, superficie, transformacion, opciones):
    """Devuelve (propuesta, nota). Nunca usa rescates ni extrapolaciones."""
    a, b = [transformacion.revit_a_nwd(*p) for p in originales]
    longitud = math.hypot(b[0] - a[0], b[1] - a[1])
    if longitud < 0.10:
        return None, u"Sin eje longitudinal horizontal suficiente; solo nivelacion."
    tramos = max(8, int(math.ceil(longitud / 0.25)))
    if tramos > 400:
        return None, u"Eje mayor de 100 m: requiere revision de perfil por tramos."
    cotas = []
    for i in range(tramos + 1):
        t = float(i) / tramos
        x, y, z = [a[j] + t * (b[j] - a[j]) for j in range(3)]
        # intersecciones() + elegir(): deliberadamente sin altura_cercana().
        impactos = sorted(superficie.intersecciones(x, y))
        cota = surface.elegir(impactos, z, opciones.estrategia) if impactos else None
        if cota is None:
            return None, (u"Pendiente no disponible: falta interseccion real "
                          u"en el {0:.0f}% del eje.".format(t * 100))
        objetivo = cota + opciones.offset_m
        if abs(objetivo - z) > opciones.limite_razonable_m:
            return None, u"Pendiente fuera del limite de desplazamiento permitido."
        cotas.append(objetivo)
    residuo = max(abs(cotas[i] - (cotas[0] + float(i) / tramos *
                                 (cotas[-1] - cotas[0])))
                  for i in range(tramos + 1))
    tolerancia = max(opciones.tolerancia_m, 0.001)
    if residuo > tolerancia:
        return None, (u"Perfil irregular: desviacion {0:.3f} m; requiere familia "
                      u"adaptativa o tramos. No se deformara automaticamente.".
                      format(residuo))
    # No sugerir rotacion cuando la inclinacion ya coincide (aunque falte Z).
    cambio = (cotas[-1] - cotas[0]) - (b[2] - a[2])
    if abs(cambio) <= tolerancia:
        return None, u"La pendiente del eje ya coincide en el perfil muestreado."
    objetivos = tuple((p[0], p[1], transformacion.z_nwd_a_revit_pies(z))
                      for p, z in zip(originales, (cotas[0], cotas[-1])))
    porcentaje = 100.0 * (cotas[-1] - cotas[0]) / longitud
    propuesta = PropuestaPendiente(originales, objetivos, porcentaje,
                                   residuo, len(cotas))
    nota = (u"Pendiente propuesta {0:+.2f}% | {1} muestras | desviacion {2:.3f} m; "
            u"preguntar al aplicar.".format(porcentaje, len(cotas), residuo))
    return propuesta, nota


def evaluar(DB, doc, resultado, superficie, transformacion, opciones):
    if resultado.modo_referencia != revit_elements.INSERCION:
        return None, u"Pendiente: usa la referencia Punto de insercion."
    if not resultado.movible or resultado.superficie_aproximada:
        return None, u"Pendiente no disponible para este resultado."
    originales = extremos(DB, resultado.elemento)
    if originales is None:
        return None, (u"Sin eje recto editable: solo nivelacion. Inclinar o "
                      u"deformar esta familia requiere una adaptacion especifica.")
    motivo = motivo_no_adaptable(DB, doc, resultado.elemento)
    if motivo:
        return None, u"Pendiente: " + motivo
    return evaluar_perfil(originales, superficie, transformacion, opciones)
