# -*- coding: utf-8 -*-
"""Crea una tuberia entre puntos de insercion de elementos modelados.

La ventana permanece abierta mientras se trabaja en Revit. La seleccion de
elementos y la creacion se ejecutan mediante ExternalEvent para respetar el
contexto de la API de Revit 2022.
"""

__title__ = "Generar\nTuberia NWD"
__author__ = "Ingenieria Cuatro S.A."
__persistentengine__ = True
__doc__ = (
    "Crea una tuberia entre los puntos de insercion de dos elementos "
    "modelados, despues de validar el NWD/IFC."
)

import os
import sys
import traceback

import clr
clr.AddReference("PresentationFramework")

# Una sola ventana por sesion. Se comprueba antes de recargar nwdcoord para no
# reemplazar el documento que conserva una instancia ya abierta.
from System import AppDomain
from System.Windows import WindowState
from pyrevit import script

_VENTANA_KEY = "ICuatro.GenerarTuberiaNWD.Window"
_existente = AppDomain.CurrentDomain.GetData(_VENTANA_KEY)
if _existente is not None and not getattr(_existente, "_cerrada", True):
    if _existente.WindowState == WindowState.Minimized:
        _existente.WindowState = WindowState.Normal
    _existente.Activate()
    script.exit()

# pyRevit mantiene los modulos importados entre ejecuciones del boton.
try:
    _recargar_modulo = reload
except NameError:
    from importlib import reload as _recargar_modulo

_NWD_MODULOS_RECARGADOS = 0
for _nombre_modulo in sorted(
        [n for n in list(sys.modules)
         if n == "nwdcoord" or n.startswith("nwdcoord.")],
        key=len, reverse=True):
    try:
        _recargar_modulo(sys.modules[_nombre_modulo])
        _NWD_MODULOS_RECARGADOS += 1
    except Exception:
        pass

clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Collections.Generic import List
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import DB, UI, forms, revit

from nwdcoord import coordination_model
from nwdcoord import pipeline
from nwdcoord import revit_elements
from nwdcoord import surface as superficie_mod
from nwdcoord.logging_utils import Registro
from nwdcoord.modeless import AccionesRevit


TITULO = u"Generar Tuberia NWD"
doc = revit.doc
uidoc = revit.uidoc


def safe_handler(func):
    """Impide que una excepcion de WPF cierre la sesion de Revit."""
    def envoltorio(self, sender, args):
        try:
            return func(self, sender, args)
        except Exception as error:
            try:
                self._estado(u"Error: {0}".format(error))
                self.registro.excepcion(u"Manejador de interfaz", error)
            except Exception:
                pass

            forms.alert(
                u"Ocurrio un error inesperado:\n\n{0}\n\n{1}".format(
                    error, traceback.format_exc()),
                title=TITULO,
            )

    return envoltorio


def revit_handler(func):
    """Los callbacks WPF solicitan; ExternalEvent accede a la API de Revit."""
    protegido = safe_handler(func)

    @safe_handler
    def solicitar(self, sender, args):
        if getattr(self, "_cargando", True) or self._cerrada:
            return
        self.acciones.solicitar(lambda: protegido(self, sender, args))

    return solicitar


def _nombre_elemento(elemento):
    try:
        return coordination_model.nombre_de(DB, elemento, u"Sin nombre")
    except Exception:
        return u"Sin nombre"


def _nombre_tipo(tipo_elem):
    """Nombre del tipo (p.ej. ConduitType). Element.Name.GetValue falla en
    algunos ElementType de IronPython (quedaba 'Sin nombre' en el
    registro); se cae al parametro de nombre de tipo."""
    for intento in (
        lambda: DB.Element.Name.__get__(tipo_elem),
        lambda: DB.Element.Name.GetValue(tipo_elem),
        lambda: tipo_elem.get_Parameter(
            DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString(),
        lambda: tipo_elem.get_Parameter(
            DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString(),
    ):
        try:
            valor = intento()
            if valor:
                return valor
        except Exception:
            pass
    return u"Sin nombre"


def _nombre_familia_de(tipo_elem):
    try:
        familia = tipo_elem.FamilyName
    except Exception:
        familia = None
    if familia:
        return familia
    try:
        return tipo_elem.get_Parameter(
            DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME).AsString() or None
    except Exception:
        return None


def _nombre_familia_tipo(tipo_elem):
    """'Familia: Tipo', como lo muestra Revit; solo 'Tipo' si coinciden."""
    nombre = _nombre_tipo(tipo_elem)
    familia = _nombre_familia_de(tipo_elem)
    if familia and familia != nombre:
        return u"{0}: {1}".format(familia, nombre)
    return nombre


def _texto_modelo(modelo):
    nombre = modelo.nombre_archivo or modelo.nombre or u"Sin nombre"
    return u"{0} | ElementId {1}".format(nombre, modelo.id_valor)


def _elegir_modelo(modelos):
    if len(modelos) == 1:
        return modelos[0]

    etiquetas = [_texto_modelo(modelo) for modelo in modelos]
    elegida = forms.SelectFromList.show(
        etiquetas,
        title=u"Seleccionar modelo de coordinacion",
        button_name=u"Usar este modelo",
        multiselect=False,
    )

    if elegida is None:
        return None

    try:
        return modelos[etiquetas.index(elegida)]
    except (ValueError, IndexError):
        return None


def _seleccion_actual():
    return revit_elements.de_la_seleccion(uidoc, doc)


class _TeclasFinalizar(object):
    """Hook de teclado (WH_KEYBOARD_LL) activo solo mientras dura la
    seleccion en pantalla: Espacio o Enter se traducen a Esc, que es la
    unica tecla que la API de Revit reconoce para salir de PickObject.
    Se instala en el hilo de Revit (que bombea mensajes durante el pick).
    Si ctypes no puede instalarlo, la seleccion sigue funcionando con Esc."""

    _WH_KEYBOARD_LL = 13
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _VK_RETURN = 0x0D
    _VK_SPACE = 0x20
    _VK_ESCAPE = 0x1B
    _KEYEVENTF_KEYUP = 0x0002

    def __init__(self, registro):
        self.registro = registro
        self.hook = None
        self.proc = None
        self.finalizado = False
        self._user32 = None

    def instalar(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [("vkCode", wintypes.DWORD),
                            ("scanCode", wintypes.DWORD),
                            ("flags", wintypes.DWORD),
                            ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

            HOOKPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            user32.CallNextHookEx.restype = ctypes.c_long
            user32.CallNextHookEx.argtypes = [
                wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

            def proc(n_code, w_param, l_param):
                try:
                    if (n_code >= 0 and
                            w_param in (self._WM_KEYDOWN, self._WM_SYSKEYDOWN)):
                        tecla = ctypes.cast(
                            l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        if tecla.vkCode in (self._VK_RETURN, self._VK_SPACE):
                            self.finalizado = True
                            user32.keybd_event(self._VK_ESCAPE, 0, 0, 0)
                            user32.keybd_event(
                                self._VK_ESCAPE, 0, self._KEYEVENTF_KEYUP, 0)
                            return 1
                except Exception:
                    pass
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            self.proc = HOOKPROC(proc)
            self._user32 = user32
            self.hook = user32.SetWindowsHookExW(
                self._WH_KEYBOARD_LL, self.proc,
                kernel32.GetModuleHandleW(None), 0)
            if not self.hook:
                raise RuntimeError(u"SetWindowsHookEx devolvio 0")
            return True
        except Exception as error:
            self.registro.aviso(
                u"Sin atajo Espacio/Enter para terminar la seleccion "
                u"(hook de teclado no disponible): {0}", error)
            self.hook = None
            return False

    def quitar(self):
        if self.hook and self._user32 is not None:
            try:
                self._user32.UnhookWindowsHookEx(self.hook)
            except Exception:
                pass
        self.hook = None
        self.proc = None


def _punto_insercion(elemento):
    """Devuelve el LocationPoint real; no inventa centro de BoundingBox."""
    if elemento is None:
        return None
    try:
        ubicacion = elemento.Location
    except Exception:
        ubicacion = None
    if ubicacion is None or not isinstance(ubicacion, DB.LocationPoint):
        return None
    try:
        return ubicacion.Point
    except Exception:
        return None


def _conectores_libres(elemento):
    """Conectores de tuberia sin uso del elemento (p.ej. camaras/equipos
    con cara de conexion). Vacio para elementos sin MEPModel (arquitectonicos).

    IsConnected solo es valido en conectores PhysicalConn: en conectores
    logicos lanza excepcion ('Connection status is available only for
    connectors of PhysicalConn type'), por eso se filtra antes de leerlo."""
    try:
        modelo_mep = elemento.MEPModel
    except Exception:
        modelo_mep = None
    if modelo_mep is None:
        return []
    try:
        conectores = list(modelo_mep.ConnectorManager.Connectors)
    except Exception:
        return []

    libres = []
    for conector in conectores:
        try:
            if conector.ConnectorType != DB.ConnectorType.Physical:
                continue
            if conector.IsConnected:
                continue
        except Exception:
            continue
        libres.append(conector)
    # Cualquier dominio sirve como referencia de llegada: las cajas y
    # postes traen conectores electricos, no de conduit. Prioridad:
    # conduit > electrico > otros; dentro del mismo dominio, el llamador
    # elige el mas cercano al vecino.
    libres.sort(key=_prioridad_conector)
    return libres


def _prioridad_conector(conector):
    try:
        dominio = conector.Domain
    except Exception:
        return 2
    if dominio == DB.Domain.DomainCableTrayConduit:
        return 0
    if dominio == DB.Domain.DomainElectrical:
        return 1
    return 2


_MARGEN_INTERSECCION_FT = 16.4  # ~5 m; cruza cualquier equipo/tanque tipico


def _centro_de_cara(cara):
    """Centro (bbox UV evaluado) de una Face: da un punto real dentro de
    la cara para caras simples (planas rectangulares, laterales de
    cilindro), suficiente para que la tuberia entre recta al centro en
    vez de en el punto de interseccion crudo (que puede caer cerca de un
    borde o esquina y verse torcido)."""
    bbox_uv = cara.GetBoundingBox()
    centro_uv = DB.UV(
        (bbox_uv.Min.U + bbox_uv.Max.U) / 2.0,
        (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0)
    return cara.Evaluate(centro_uv)


def _es_cara_vertical(cara):
    """Cara de pared (normal casi horizontal): por ahi debe entrar la
    tuberia, no por la tapa ni el fondo."""
    try:
        bbox_uv = cara.GetBoundingBox()
        centro_uv = DB.UV(
            (bbox_uv.Min.U + bbox_uv.Max.U) / 2.0,
            (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0)
        normal = cara.ComputeNormal(centro_uv)
    except Exception:
        return False
    return abs(normal.Z) < 0.5


def _caras_de(solidos):
    caras = []
    for solido in solidos:
        try:
            caras.extend(list(solido.Faces))
        except Exception:
            continue
    return caras


def _cara_mas_cercana(solidos, punto):
    """Cara cuyo centro esta mas cerca de punto. Prefiere caras verticales
    (paredes); solo si no hay ninguna considera tapas/fondos. Se usa
    cuando la linea de aproximacion no llega a cruzar el solido (la
    tuberia queda fuera del elemento)."""
    caras = _caras_de(solidos)
    verticales = [cara for cara in caras if _es_cara_vertical(cara)]
    mejor = None
    for cara in (verticales or caras):
        try:
            centro = _centro_de_cara(cara)
        except Exception:
            continue
        distancia = centro.DistanceTo(punto)
        if mejor is None or distancia < mejor[0]:
            mejor = (distancia, cara)
    return mejor[1] if mejor else None


def _solidos_de(elemento):
    try:
        opciones = DB.Options()
        opciones.ComputeReferences = False
        opciones.DetailLevel = DB.ViewDetailLevel.Fine
        geometria = elemento.get_Geometry(opciones)
    except Exception:
        return []
    if geometria is None:
        return []

    solidos = []
    for geo_obj in geometria:
        try:
            if isinstance(geo_obj, DB.Solid) and geo_obj.Volume > 1e-9:
                solidos.append(geo_obj)
            elif isinstance(geo_obj, DB.GeometryInstance):
                for sub in geo_obj.GetInstanceGeometry():
                    if isinstance(sub, DB.Solid) and sub.Volume > 1e-9:
                        solidos.append(sub)
        except Exception:
            continue
    return solidos


def _punto_en_pared(elemento, base, punto_referencia, z):
    """Punto de entrada en la PARED del elemento a la cota z ya decidida.

    La linea de aproximacion se lanza horizontal a esa cota, desde la
    planta del vecino hacia la planta del punto base: asi entra siempre
    por una pared y a la altura correcta. Antes se lanzaba hacia el punto
    base 3D (origen de familia, a veces en la tapa): con elementos casi al
    mismo nivel entraba por la tapa y se perdia la profundidad.

    Planta del punto: en pared plana, el centro de la cara (entra
    centrada); en pared curva (cilindro), el punto de cruce (la cara
    lateral es una sola y su centro UV puede caer al lado opuesto).
    Sin cruce, la pared cuya planta esta mas cerca del vecino.

    None si el elemento no tiene solido ni pared; el llamador cae al punto
    de insercion."""
    solidos = _solidos_de(elemento)
    if not solidos:
        return None

    cara_golpeada = None
    punto_golpe = None
    if punto_referencia is not None:
        inicio = DB.XYZ(punto_referencia.X, punto_referencia.Y, z)
        fin = DB.XYZ(base.X, base.Y, z)
        direccion_bruta = fin - inicio
        largo = direccion_bruta.GetLength()
        if largo >= 1e-6:
            direccion = direccion_bruta.Normalize()
            try:
                linea = DB.Line.CreateBound(
                    inicio, inicio + direccion * (largo + _MARGEN_INTERSECCION_FT))
            except Exception:
                linea = None
            if linea is not None:
                mejor = None
                for solido in solidos:
                    try:
                        resultado = solido.IntersectWithCurve(
                            linea, DB.SolidCurveIntersectionOptions())
                    except Exception:
                        continue
                    if resultado is None:
                        continue
                    for indice in range(resultado.SegmentCount):
                        curva = resultado.GetCurveSegment(indice)
                        for extremo in (curva.GetEndPoint(0),
                                        curva.GetEndPoint(1)):
                            distancia = inicio.DistanceTo(extremo)
                            if distancia < 1e-6:
                                continue
                            if mejor is None or distancia < mejor[0]:
                                mejor = (distancia, extremo)
                if mejor is not None:
                    punto_golpe = mejor[1]
                    for cara in _caras_de(solidos):
                        if not _es_cara_vertical(cara):
                            continue
                        try:
                            proyeccion = cara.Project(punto_golpe)
                        except Exception:
                            continue
                        if proyeccion is not None and proyeccion.Distance < 0.01:
                            cara_golpeada = cara
                            break

    if cara_golpeada is not None:
        if isinstance(cara_golpeada, DB.PlanarFace):
            try:
                centro = _centro_de_cara(cara_golpeada)
                return DB.XYZ(centro.X, centro.Y, z)
            except Exception:
                pass
        return DB.XYZ(punto_golpe.X, punto_golpe.Y, z)

    referencia = punto_referencia if punto_referencia is not None else base
    cara = _cara_mas_cercana(solidos, DB.XYZ(referencia.X, referencia.Y, z))
    if cara is None or not _es_cara_vertical(cara):
        return None
    try:
        centro = _centro_de_cara(cara)
    except Exception:
        return None
    return DB.XYZ(centro.X, centro.Y, z)


_MARGEN_BORDE_FT = 0.1  # ~3 cm dentro del elemento; no entrar justo en la arista


def _cota_desde_punto_base(elemento, base, profundidad_ft):
    """Cota de llegada medida desde el punto base del elemento:
    base.Z - profundidad (positivo = hacia abajo). Siempre se cumple.
    Devuelve (z, dentro): dentro=True si esa cota cae dentro de la altura
    real del elemento (entra por la pared); False si queda fuera -p.ej.
    poste sobre el terreno con la tuberia enterrada bajo el- y entonces
    la tuberia llega al centro del elemento en planta, a esa cota."""
    z_objetivo = base.Z - profundidad_ft
    try:
        caja = elemento.get_BoundingBox(None)
    except Exception:
        caja = None
    if caja is None:
        return z_objetivo, False

    z_min, z_max = caja.Min.Z, caja.Max.Z
    dentro = z_min + _MARGEN_BORDE_FT <= z_objetivo <= z_max - _MARGEN_BORDE_FT
    return z_objetivo, dentro


def _punto_conexion(elemento, punto_referencia, profundidad_ft=0.0):
    """Punto real donde debe llegar la tuberia, en orden de preferencia:
    1) el conector MEP libre de tuberia (manda tal cual, sin profundidad);
    2) la pared real del solido: planta = centro de la cara (interseccion
       geometrica, o la mas cercana si la linea no cruza el solido); cota
       = medida desde el punto base con la profundidad, validada contra la
       altura del elemento y ajustada a su centro si no cumple;
    3) su punto de insercion (LocationPoint) menos la profundidad, si no
       hay geometria solida."""
    conectores = _conectores_libres(elemento)
    if conectores:
        # Solo el grupo de mayor prioridad (conduit antes que electrico);
        # dentro de el, el mas cercano al vecino en planta.
        mejor = _prioridad_conector(conectores[0])
        candidatos = [c for c in conectores if _prioridad_conector(c) == mejor]
        conector = candidatos[0]
        if punto_referencia is not None and len(candidatos) > 1:
            referencia_plana = DB.XYZ(punto_referencia.X, punto_referencia.Y, 0)
            conector = min(candidatos, key=lambda c: DB.XYZ(
                c.Origin.X, c.Origin.Y, 0).DistanceTo(referencia_plana))
        try:
            return conector.Origin
        except Exception:
            pass

    base = _punto_insercion(elemento)
    if base is None:
        return None

    # Primero la cota (desde el punto base). Si cae dentro de la altura del
    # elemento (camara enterrada), entra por la pared a esa cota. Si queda
    # fuera (poste sobre terreno, tuberia enterrada), llega al centro del
    # elemento en planta, a esa cota.
    z, dentro = _cota_desde_punto_base(elemento, base, profundidad_ft)
    if dentro:
        punto_pared = _punto_en_pared(elemento, base, punto_referencia, z)
        if punto_pared is not None:
            return punto_pared
    return DB.XYZ(base.X, base.Y, z)


def _etiqueta_catalogo(elemento):
    """Fila de la lista: nombre de familia, tipo e id, cada uno rotulado."""
    familia = _nombre_familia_de(elemento) or u"Sin familia"
    return u"{0}  |  Tipo: {1}  |  ElementId {2}".format(
        familia, _nombre_tipo(elemento), elemento.Id.IntegerValue)


class OpcionTipo(object):
    """Fila de la lista desplegable de familias de tuberia."""

    def __init__(self, tipo):
        self.tipo = tipo
        self.Etiqueta = _etiqueta_catalogo(tipo)


def _catalogo(categoria, clases):
    """Tipos cargados/creados en el proyecto, agrupados como los agrupa
    Revit: por categoria MEP, no por clase .NET exacta. Filtrar solo por
    OfClass deja fuera variantes validas de la misma categoria (p.ej.
    FlexPipeType junto a PipeType en OST_PipeCurves)."""
    try:
        elementos = list(
            DB.FilteredElementCollector(doc)
            .OfCategory(categoria)
            .WhereElementIsElementType()
            .ToElements()
        )
    except Exception:
        return []
    return [elemento for elemento in elementos if isinstance(elemento, clases)]


def _niveles():
    try:
        return list(
            DB.FilteredElementCollector(doc)
            .OfClass(DB.Level)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []


def _nivel_cercano(punto):
    niveles = _niveles()
    if not niveles:
        return None
    return min(niveles, key=lambda nivel: abs(nivel.Elevation - punto.Z))


def _texto_punto(punto):
    if punto is None:
        return u"No valido (sin LocationPoint)"
    factor = 0.3048
    return u"({0:.3f}, {1:.3f}, {2:.3f}) m".format(
        punto.X * factor, punto.Y * factor, punto.Z * factor)


def _leer_profundidad_m(texto):
    """0.0 sin error si el campo esta vacio; nunca negativa."""
    texto = (texto or u"").strip().replace(u",", u".")
    if not texto:
        return 0.0, None
    try:
        valor = float(texto)
    except ValueError:
        return None, u"Profundidad invalida: '{0}'.".format(texto)
    if valor < 0:
        return None, u"La profundidad debe ser un valor positivo, en metros."
    return valor, None


_MAX_DIST_CONTINUAR_FT = 10.0  # ~3 m: mas lejos, la nueva tanda no se encadena
_TOL_COLINEAL_FT = 0.05  # ~1.5 cm: desvio maximo para fundir tramos alineados


def _con_pendientes(puntos):
    """Ruta enterrada con pendiente: entre elementos a distinta altura el
    tramo va recto e inclinado desde el punto alto hasta el punto bajo,
    en vez de horizontal - vertical - horizontal (esa 'escalera' metia dos
    codos por desnivel y no corresponde a una canalizacion en zanja).

    Pocas conexiones: los puntos intermedios que quedan alineados con el
    tramo anterior y el siguiente (misma direccion y pendiente) se funden
    en un solo tramo, asi la tuberia solo lleva union donde de verdad
    cambia de rumbo o de pendiente."""
    if len(puntos) < 3:
        return list(puntos)
    ruta = [puntos[0]]
    for indice in range(1, len(puntos) - 1):
        anterior, actual, siguiente = ruta[-1], puntos[indice], puntos[indice + 1]
        try:
            eje = DB.Line.CreateBound(anterior, siguiente)
            desvio = eje.Distance(actual)
        except Exception:
            desvio = None
        if desvio is not None and desvio <= _TOL_COLINEAL_FT:
            continue
        ruta.append(actual)
    ruta.append(puntos[-1])
    return ruta


def _ordenar_secuencia(elementos, punto_inicial=None):
    """Ordena los elementos por su POSICION para que la lista sea el
    recorrido real de la tuberia, sin depender del orden de seleccion.

    1. Extremos de la ruta: el par de elementos mas alejados entre si.
    2. Arranque: el extremo mas cercano a punto_inicial (final de la
       ultima tuberia creada) si existe; si no, el extremo A.
    3. Cadena por vecino mas cercano desde ese extremo.
    4. Pase 2-opt: invierte tramos que se cruzan o hacen zigzag, hasta
       que ningun cambio acorte el recorrido.
    Elementos sin punto de insercion van al final (fallan despues con
    mensaje claro)."""
    elementos = list(elementos)
    puntos = {}
    for elemento in elementos:
        try:
            puntos[elemento.Id.IntegerValue] = _punto_insercion(elemento)
        except Exception:
            puntos[elemento.Id.IntegerValue] = None

    validos = [e for e in elementos if puntos[e.Id.IntegerValue] is not None]
    invalidos = [e for e in elementos if puntos[e.Id.IntegerValue] is None]
    if len(validos) < 2:
        return validos + invalidos

    def punto(elemento):
        return puntos[elemento.Id.IntegerValue]

    def distancia(a, b):
        return punto(a).DistanceTo(punto(b))

    # 1. extremos
    extremo_a, extremo_b, mayor = validos[0], validos[1], -1.0
    for i in range(len(validos)):
        for j in range(i + 1, len(validos)):
            d = distancia(validos[i], validos[j])
            if d > mayor:
                extremo_a, extremo_b, mayor = validos[i], validos[j], d

    # 2. arranque
    inicio = extremo_a
    if punto_inicial is not None:
        if (punto(extremo_b).DistanceTo(punto_inicial)
                < punto(extremo_a).DistanceTo(punto_inicial)):
            inicio = extremo_b

    # 3. cadena por vecino mas cercano
    ordenados = [inicio]
    restantes = [e for e in validos if e is not inicio]
    while restantes:
        ultimo = ordenados[-1]
        siguiente = min(restantes, key=lambda e: distancia(ultimo, e))
        restantes.remove(siguiente)
        ordenados.append(siguiente)

    # 4. 2-opt sobre camino abierto
    n = len(ordenados)
    mejorado = True
    while mejorado and n > 3:
        mejorado = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                antes = distancia(ordenados[i - 1], ordenados[i])
                despues = distancia(ordenados[i - 1], ordenados[j])
                if j + 1 < n:
                    antes += distancia(ordenados[j], ordenados[j + 1])
                    despues += distancia(ordenados[i], ordenados[j + 1])
                if despues + 1e-9 < antes:
                    ordenados[i:j + 1] = ordenados[i:j + 1][::-1]
                    mejorado = True
    return ordenados + invalidos


def _validar_ruta(elementos, profundidad_ft, punto_previo=None):
    """Puntos de la ruta, en orden, con la profundidad ya restada.

    punto_previo (opcional): final de la ultima tuberia creada en esta
    sesion de la ventana; si se pasa, la ruta arranca ahi para que la
    secuencia de creacion sea continua entre un 'Crear tuberia' y otro."""
    if elementos is None or len(elementos) < 2:
        return None, u"Marca al menos dos elementos para definir la ruta."

    ids = [elemento.Id.IntegerValue for elemento in elementos]
    if len(set(ids)) != len(ids):
        return None, u"Hay un elemento marcado mas de una vez en la ruta."

    insercion = [_punto_insercion(elemento) for elemento in elementos]
    for indice, punto in enumerate(insercion):
        if punto is None:
            elemento = elementos[indice]
            return None, (
                u"El elemento '{0}' (ElementId {1}) no tiene LocationPoint. "
                u"Debe ser una instancia modelada mediante punto de insercion."
                .format(_nombre_elemento(elemento), elemento.Id.IntegerValue)
            )

    puntos = []
    for indice, elemento in enumerate(elementos):
        # Referencia en planta: el punto de conexion ya calculado del
        # vecino anterior (mas exacto que su punto de insercion).
        if indice > 0:
            vecino = puntos[indice - 1]
        elif punto_previo is not None:
            vecino = punto_previo
        else:
            vecino = insercion[indice + 1]
        # La profundidad no es un offset global: se mide desde el punto
        # base de cada elemento y se valida contra su altura real dentro
        # de _punto_conexion.
        puntos.append(_punto_conexion(elemento, vecino, profundidad_ft))

    # Si la ruta nueva arranca en el mismo elemento donde termino la
    # anterior, el punto previo coincide con el primero: anteponerlo
    # duplicaria el tramo (ida y vuelta sobre la misma linea).
    if (punto_previo is not None and
            punto_previo.DistanceTo(puntos[0]) > _TOL_COINCIDENCIA_FT):
        puntos = [punto_previo] + puntos

    puntos = _con_pendientes(puntos)

    # No se rechaza aqui por puntos "muy cerca": Revit exige un largo minimo
    # de tramo mayor que ShortCurveTolerance para Conduit/Pipe, y varia
    # segun el tipo. En vez de bloquear toda la ruta, _crear_ruta fusiona
    # sobre la marcha los tramos que Revit rechace por ser muy cortos.
    return puntos, None


def _connector_cercano(conectores, punto):
    mejor, mejor_dist = None, None
    for conector in conectores:
        try:
            distancia = conector.Origin.DistanceTo(punto)
        except Exception:
            continue
        if mejor_dist is None or distancia < mejor_dist:
            mejor, mejor_dist = conector, distancia
    return mejor


_TOL_COINCIDENCIA_FT = 0.05  # ~1.5 cm: mas lejos, Revit mueve el tramo


def _conectores_libres_de(tuberia):
    """Conectores fisicos aun sin conectar de un tramo: un extremo ya
    unido por un codo previo no debe volver a elegirse."""
    try:
        conectores = list(tuberia.ConnectorManager.Connectors)
    except Exception:
        return []
    libres = []
    for conector in conectores:
        try:
            if conector.ConnectorType != DB.ConnectorType.Physical:
                continue
            if conector.IsConnected:
                continue
        except Exception:
            continue
        libres.append(conector)
    return libres


def _conectar_segmentos(doc, tuberia_a, tuberia_b, punto_union, registro):
    """Empalma dos tramos consecutivos con un fitting en el punto comun.

    Lectura del sentido: se toma, de cada tramo, el extremo LIBRE mas
    cercano al punto de union, y se exige que ambos coincidan entre si.
    Si no coinciden, Revit desplaza uno de los tramos para cerrar el hueco
    y el codo queda invertido/apuntando a otro lado; en ese caso se deja
    sin unir y se registra, en vez de romper la geometria."""
    conector_a = _connector_cercano(_conectores_libres_de(tuberia_a), punto_union)
    conector_b = _connector_cercano(_conectores_libres_de(tuberia_b), punto_union)
    if conector_a is None or conector_b is None:
        registro.aviso(
            u"Sin extremo libre para unir en {0}.", _texto_punto(punto_union))
        return

    try:
        separacion = conector_a.Origin.DistanceTo(conector_b.Origin)
    except Exception:
        separacion = 0.0
    if separacion > _TOL_COINCIDENCIA_FT:
        registro.aviso(
            u"Extremos no coincidentes en {0} ({1:.1f} mm); no se inserta "
            u"codo para no desplazar el tramo.",
            _texto_punto(punto_union), separacion * 304.8)
        return

    fitting = None
    try:
        fitting = doc.Create.NewElbowFitting(conector_a, conector_b)
    except Exception:
        fitting = None
    if fitting is None:
        try:
            fitting = doc.Create.NewUnionFitting(conector_a, conector_b)
        except Exception as error:
            registro.excepcion(
                u"No se pudo conectar los tramos en {0}"
                .format(_texto_punto(punto_union)), error)
            return

    # Verificacion: el fitting debe haber dejado ambos extremos unidos.
    # Si Revit lo inserto pero sin cerrar la union (tramo demasiado corto
    # para el radio del codo), se elimina para no dejar un codo suelto.
    try:
        unidos = conector_a.IsConnected and conector_b.IsConnected
    except Exception:
        unidos = True
    if not unidos:
        try:
            doc.Delete(fitting.Id)
        except Exception:
            pass
        registro.aviso(
            u"Codo en {0} no cerro la union (tramo corto para el radio); "
            u"se elimino.", _texto_punto(punto_union))


_NUDGE_ORIENTACION_FT = 0.005  # ~1.5 mm: mayor que ShortCurveTolerance, y
                               # el codo sigue leyendo ~90 grados


def _evitar_vertical_pura(origen, destino):
    """Revit no puede derivar la orientacion (rotacion de la seccion) de un
    tramo de Conduit exactamente vertical: el vector de referencia se
    calcula con el eje Z, y Z x Z = 0 (indefinido) cuando el tramo tambien
    es paralelo a Z -> Revit avisa 'orientacion no valida'. Se desplaza el
    punto final una fraccion de mm en X para romper la degeneracion; el
    desplazamiento no se guarda en la ruta logica, solo se usa para crear."""
    tolerancia = doc.Application.ShortCurveTolerance
    if (abs(destino.X - origen.X) < tolerancia and
            abs(destino.Y - origen.Y) < tolerancia):
        return DB.XYZ(destino.X + _NUDGE_ORIENTACION_FT, destino.Y, destino.Z)
    return destino


def _tramo_existente(origen, destino):
    """Conduit ya modelado con estos mismos extremos (en cualquier sentido),
    o None. Evita crear dos veces el mismo tramo: p.ej. al pulsar 'Crear'
    otra vez con las mismas filas, o al volver a escoger un elemento que
    ya tiene tuberia."""
    tol = _TOL_COINCIDENCIA_FT
    minimo = DB.XYZ(min(origen.X, destino.X) - tol,
                    min(origen.Y, destino.Y) - tol,
                    min(origen.Z, destino.Z) - tol)
    maximo = DB.XYZ(max(origen.X, destino.X) + tol,
                    max(origen.Y, destino.Y) + tol,
                    max(origen.Z, destino.Z) + tol)
    try:
        filtro = DB.BoundingBoxIntersectsFilter(DB.Outline(minimo, maximo))
        candidatos = (DB.FilteredElementCollector(doc)
                      .OfClass(DB.Electrical.Conduit)
                      .WherePasses(filtro))
    except Exception:
        return None
    for conduit in candidatos:
        try:
            curva = conduit.Location.Curve
            a = curva.GetEndPoint(0)
            b = curva.GetEndPoint(1)
        except Exception:
            continue
        directo = (a.DistanceTo(origen) <= tol and b.DistanceTo(destino) <= tol)
        inverso = (a.DistanceTo(destino) <= tol and b.DistanceTo(origen) <= tol)
        if directo or inverso:
            return conduit
    return None


def _crear_ruta(elementos, puntos, tipo_tuberia, profundidad_m, registro,
                tuberia_previa=None):
    """Crea uno o mas tramos de canalizacion (Tubo/Conduit), en orden, y
    empalma los intermedios. Conduit no usa tipo de sistema (a diferencia
    de Pipe/Duct): solo necesita el ConduitType y el nivel.

    Si un tramo es mas corto que el minimo que Revit acepta (el limite
    real depende del ConduitType, no solo de ShortCurveTolerance), en vez
    de abortar toda la ruta se fusiona con el siguiente punto y se sigue
    intentando: asi los elementos muy cercanos tambien quedan conectados.

    tuberia_previa (opcional): ultima tuberia creada en una llamada previa
    de esta ventana; si sigue siendo valida, el primer tramo nuevo se
    empalma con ella para que la secuencia de creacion sea continua."""
    if tipo_tuberia is None:
        return [], u"Localiza primero la familia de tuberia."

    nivel = _nivel_cercano(puntos[0])
    if nivel is None:
        return [], u"El proyecto no contiene niveles."

    registro.info(
        u"Creacion de ruta solicitada: {0} punto(s); tipo=ElementId {1} "
        u"{2}; nivel=ElementId {3} {4}; profundidad={5:.3f} m",
        len(puntos), tipo_tuberia.Id.IntegerValue,
        _nombre_familia_tipo(tipo_tuberia), nivel.Id.IntegerValue,
        _nombre_elemento(nivel), profundidad_m,
    )
    tuberias = []
    omitidos = 0
    try:
        # swallow_errors: los avisos de Revit (p.ej. orientacion de un tramo
        # vertical) no abren dialogo ni abortan la transaccion.
        with revit.Transaction(u"Crear tuberia desde puntos de insercion",
                               swallow_errors=True):
            cursor = puntos[0]
            anterior = None
            try:
                if tuberia_previa is not None and tuberia_previa.IsValidObject:
                    anterior = tuberia_previa
            except Exception:
                anterior = None
            indice = 1
            while indice < len(puntos):
                # El punto real de union debe ser el mismo que uso Create,
                # nudge incluido: si el siguiente tramo arranca del punto
                # original (sin nudge) en vez de este, queda una separacion
                # de _NUDGE_ORIENTACION_FT entre los dos tramos y el codo
                # se ubica mal (union torcida/flotante).
                destino = _evitar_vertical_pura(cursor, puntos[indice])
                existente = _tramo_existente(cursor, destino)
                if existente is not None:
                    registro.aviso(
                        u"Tramo {0}-{1} ya existe (ElementId {2}); no se "
                        u"duplica.", _texto_punto(cursor),
                        _texto_punto(destino), existente.Id.IntegerValue)
                    omitidos += 1
                    anterior = existente
                    cursor = destino
                    indice += 1
                    continue
                try:
                    tuberia = DB.Electrical.Conduit.Create(
                        doc, tipo_tuberia.Id, cursor, destino, nivel.Id)
                except Exception as error_tramo:
                    if indice + 1 < len(puntos):
                        registro.aviso(
                            u"Punto {0} muy cerca del anterior; se fusiona "
                            u"con el siguiente ({1}): {2}",
                            indice + 1, indice + 2, error_tramo)
                        indice += 1
                        continue
                    raise
                tuberias.append(tuberia)
                if anterior is not None:
                    _conectar_segmentos(doc, anterior, tuberia, cursor, registro)
                anterior = tuberia
                cursor = destino
                indice += 1
    except Exception as error:
        registro.excepcion(u"Conduit.Create", error)
        return [], u"Revit no pudo crear la tuberia: {0}".format(error)

    if not tuberias:
        if omitidos:
            return [], (u"La tuberia de esta ruta ya existe ({0} tramo(s)); "
                        u"no se creo nada para no duplicarla.".format(omitidos))
        return [], u"Todos los puntos de la ruta quedaron muy cerca entre si."

    registro.info(u"Tuberia(s) creada(s): {0}", u", ".join(
        unicode(tuberia.Id.IntegerValue) for tuberia in tuberias))
    return tuberias, None


def _patron_solido():
    try:
        for patron in (DB.FilteredElementCollector(doc)
                       .OfClass(DB.FillPatternElement)):
            try:
                if patron.GetFillPattern().IsSolidFill:
                    return patron.Id
            except Exception:
                continue
    except Exception:
        pass
    return None


class _ResaltadoTemporal(object):
    """Resalta en la vista activa los elementos ya escogidos mientras dura
    la seleccion en pantalla. PickObject limpia la seleccion de Revit al
    arrancar, asi que SetElementIds no servia para diferenciarlos; se usan
    sobreescrituras graficas de vista dentro de un TransactionGroup que se
    revierte al terminar: no queda rastro ni entrada en Deshacer."""

    _COLOR = (255, 128, 0)  # naranja

    def __init__(self, registro):
        self.registro = registro
        self.vista = None
        self.grupo = None
        self.resaltados = set()
        try:
            self.vista = uidoc.ActiveView
            self.grupo = DB.TransactionGroup(doc, u"Resaltar seleccion")
            self.grupo.Start()
        except Exception as error:
            self.registro.aviso(u"Sin resaltado temporal: {0}", error)
            self.grupo = None

    def _ajustes(self, activar):
        ajustes = DB.OverrideGraphicSettings()
        if not activar:
            return ajustes
        color = DB.Color(*self._COLOR)
        ajustes.SetProjectionLineColor(color)
        ajustes.SetProjectionLineWeight(8)
        ajustes.SetCutLineColor(color)
        ajustes.SetCutLineWeight(8)
        patron = _patron_solido()
        if patron is not None:
            ajustes.SetSurfaceForegroundPatternId(patron)
            ajustes.SetSurfaceForegroundPatternColor(color)
            ajustes.SetCutForegroundPatternId(patron)
            ajustes.SetCutForegroundPatternColor(color)
        return ajustes

    def marcar(self, elementos):
        if self.grupo is None or self.vista is None:
            return
        actuales = set(e.Id.IntegerValue for e in elementos)
        try:
            transaccion = DB.Transaction(doc, u"Resaltar seleccion")
            transaccion.Start()
            for elemento in elementos:
                if elemento.Id.IntegerValue not in self.resaltados:
                    self.vista.SetElementOverrides(
                        elemento.Id, self._ajustes(True))
            for id_valor in self.resaltados - actuales:
                self.vista.SetElementOverrides(
                    DB.ElementId(id_valor), self._ajustes(False))
            transaccion.Commit()
            self.resaltados = actuales
            uidoc.RefreshActiveView()
        except Exception as error:
            self.registro.aviso(u"No se pudo resaltar: {0}", error)

    def terminar(self):
        if self.grupo is None:
            return
        try:
            if self.grupo.HasStarted():
                self.grupo.RollBack()
        except Exception as error:
            self.registro.aviso(u"No se pudo quitar el resaltado: {0}", error)
        self.grupo = None
        try:
            uidoc.RefreshActiveView()
        except Exception:
            pass


def _seleccionar_creadas(tuberias):
    try:
        ids = List[DB.ElementId]()
        for tuberia in tuberias:
            ids.Add(tuberia.Id)
        uidoc.Selection.SetElementIds(ids)
        uidoc.ShowElements(ids)
    except Exception:
        pass


def _detectar_origen(registro):
    """Deteccion NWD/IFC conservada del flujo previamente validado."""
    puentes = pipeline.estado_puentes()
    with forms.ProgressBar(
            title=u"Detectando modelos de coordinacion...",
            indeterminate=True):
        modelos = coordination_model.detectar(DB, doc, registro)

    if not modelos:
        return None, None, None, None, [], puentes, (
            u"No se encontro ningun modelo de coordinacion NWD/NWC en el "
            u"documento activo."
        )

    modelo = _elegir_modelo(modelos)
    if modelo is None:
        return None, None, None, None, [], puentes, None

    with forms.ProgressBar(
            title=u"Validando NWD, IFC y transformacion...",
            indeterminate=True):
        transformacion, diagnostico, origen = \
            pipeline.resolver_transformacion(modelo, registro)
        superficies = pipeline.superficies_del_origen(origen, registro)

    if transformacion is None or diagnostico is None or not diagnostico.valida:
        motivo = (diagnostico.motivo if diagnostico is not None else
                  u"No se pudo resolver la transformacion.")
        return modelo, origen, transformacion, diagnostico, superficies, \
            puentes, motivo
    return modelo, origen, transformacion, diagnostico, superficies, \
        puentes, None


# ---------------------------------------------------------------------------
# CAD vinculado: deteccion, capas y trazado sobre el terreno del NWD
# ---------------------------------------------------------------------------

_TOL_ENCADENAR_FT = 0.02        # ~6 mm: extremos que se consideran el mismo
_TOL_SIMPLIFICAR_FT = 0.5       # ~15 cm: desvio maximo al fundir tramos de CAD
_PASO_TERRENO_FT = 0.05 / 0.3048   # cache de consultas de cota (5 cm)
_PASO_MUESTREO_FT = 5.0 / 0.3048   # muestreo del terreno a lo largo del tramo
_MARGEN_ZONA_NWD_M = 25.0
_LIMITE_SEGMENTOS_AVISO = 2000


class FilaCad(object):
    """Un archivo CAD vinculado o importado en el documento."""

    def __init__(self, elemento):
        self.elemento = elemento
        self.Id = elemento.Id.IntegerValue
        self.vinculado = bool(getattr(elemento, "IsLinked", False))
        self.Tipo = u"Vinculo" if self.vinculado else u"Importado"
        self.Nombre = u"CAD_{0}".format(self.Id)
        self.Estado = u"-"
        self.Vista = u""
        try:
            if elemento.ViewSpecific:
                vista = doc.GetElement(elemento.OwnerViewId)
                self.Vista = u" [vista: {0}]".format(_nombre_elemento(vista))
        except Exception:
            pass
        try:
            tipo = doc.GetElement(elemento.GetTypeId())
        except Exception:
            tipo = None
        if tipo is not None:
            try:
                parametro = tipo.get_Parameter(
                    DB.BuiltInParameter.IMPORT_SYMBOL_NAME)
                nombre = parametro.AsString() if parametro else None
                self.Nombre = nombre or _nombre_tipo(tipo)
            except Exception:
                pass
            if self.vinculado:
                try:
                    referencia = tipo.GetExternalFileReference()
                    if referencia is not None:
                        self.Estado = u"{0}".format(
                            referencia.GetLinkedFileStatus())
                except Exception:
                    pass
            else:
                self.Estado = u"Incrustado"
        self.Etiqueta = u"{0}  |  {1} ({2}){3}  |  ElementId {4}".format(
            self.Nombre, self.Tipo, self.Estado, self.Vista, self.Id)


def _cads_del_documento():
    """ImportInstance de todo el documento: vinculos DWG/DXF/DGN e
    importaciones, de modelo o de vista. Los de vista se marcan."""
    try:
        elementos = list(
            DB.FilteredElementCollector(doc)
            .OfClass(DB.ImportInstance)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        return []
    filas = [FilaCad(e) for e in elementos]
    filas.sort(key=lambda f: (bool(f.Vista), not f.vinculado,
                              f.Nombre.lower()))
    return filas


def _nombre_capa(objeto):
    """Capa del DWG = subcategoria del GraphicsStyle del objeto. No usar
    Element.Name: en GraphicsStyle devuelve el nombre del CAD entero."""
    try:
        gs_id = objeto.GraphicsStyleId
    except Exception:
        return u"(sin capa)"
    if gs_id is None or gs_id == DB.ElementId.InvalidElementId:
        return u"(sin capa)"
    try:
        estilo = doc.GetElement(gs_id)
    except Exception:
        return u"(sin capa)"
    if estilo is None:
        return u"(sin capa)"
    try:
        categoria = estilo.GraphicsStyleCategory
        if categoria is not None and categoria.Name:
            return categoria.Name
    except Exception:
        pass
    try:
        if estilo.Name:
            return estilo.Name
    except Exception:
        pass
    return u"(sin capa)"


def _pares(puntos):
    minimo = doc.Application.ShortCurveTolerance
    return [(puntos[i], puntos[i + 1]) for i in range(len(puntos) - 1)
            if puntos[i].DistanceTo(puntos[i + 1]) >= minimo]


def _segmentos_de_curva(curva):
    if isinstance(curva, DB.Line):
        try:
            return _pares([curva.GetEndPoint(0), curva.GetEndPoint(1)])
        except Exception:
            return []
    try:
        return _pares(list(curva.Tessellate()))
    except Exception:
        pass
    try:
        return _pares([curva.GetEndPoint(0), curva.GetEndPoint(1)])
    except Exception:
        return []


def _recolectar_capas(geometria, mapa, profundidad=0):
    """Recorre la geometria del CAD (anidada por bloque en
    GeometryInstance) y agrupa los segmentos rectos por capa."""
    if geometria is None or profundidad > 8:
        return
    for objeto in geometria:
        if isinstance(objeto, DB.GeometryInstance):
            _recolectar_capas(objeto.GetInstanceGeometry(), mapa,
                              profundidad + 1)
            continue
        segmentos = []
        if isinstance(objeto, DB.PolyLine):
            segmentos = _pares(list(objeto.GetCoordinates()))
        elif isinstance(objeto, DB.Curve):
            segmentos = _segmentos_de_curva(objeto)
        if segmentos:
            mapa.setdefault(_nombre_capa(objeto), []).extend(segmentos)


class FilaCapa(object):
    def __init__(self, nombre, segmentos):
        self.Nombre = nombre
        self.segmentos = segmentos
        self.Marcada = False
        self.Segmentos = len(segmentos)
        longitud = 0.0
        z_min = z_max = None
        for p0, p1 in segmentos:
            longitud += p0.DistanceTo(p1)
            for z in (p0.Z, p1.Z):
                z_min = z if z_min is None or z < z_min else z_min
                z_max = z if z_max is None or z > z_max else z_max
        self.longitud_ft = longitud
        self.Longitud = u"{0:.1f}".format(longitud * 0.3048)
        self.Cota3D = (u"Si" if z_min is not None and (z_max - z_min) > 0.01
                       else u"No")


def _capas_de_cad(elemento):
    opciones = DB.Options()
    opciones.IncludeNonVisibleObjects = False
    mapa = {}
    try:
        _recolectar_capas(elemento.get_Geometry(opciones), mapa)
    except Exception:
        return []
    filas = [FilaCapa(nombre, segs) for nombre, segs in mapa.items()]
    filas.sort(key=lambda f: (-f.longitud_ft, f.Nombre.lower()))
    return filas


def _encadenar(segmentos):
    """Une segmentos sueltos del CAD en polilineas continuas (extremos
    coincidentes dentro de _TOL_ENCADENAR_FT). Devuelve listas de puntos.
    Cada polilinea es una ruta: un tramo por vertice, empalmados."""
    def clave(p):
        return (int(round(p.X / _TOL_ENCADENAR_FT)),
                int(round(p.Y / _TOL_ENCADENAR_FT)),
                int(round(p.Z / _TOL_ENCADENAR_FT)))

    nodos = {}
    for indice, (p0, p1) in enumerate(segmentos):
        nodos.setdefault(clave(p0), []).append(indice)
        nodos.setdefault(clave(p1), []).append(indice)
    usados = set()
    rutas = []

    def recorrer(indice, punto_inicio):
        usados.add(indice)
        p0, p1 = segmentos[indice]
        if clave(p0) != clave(punto_inicio):
            p0, p1 = p1, p0
        puntos = [p0, p1]
        actual = p1
        while True:
            vecinos = [i for i in nodos.get(clave(actual), []) if i not in usados]
            if len(vecinos) != 1:
                break
            siguiente = vecinos[0]
            usados.add(siguiente)
            a, b = segmentos[siguiente]
            if clave(a) != clave(actual):
                a, b = b, a
            puntos.append(b)
            actual = b
        return puntos

    # Primero desde extremos libres (grado 1), luego lo que quede (ciclos)
    for k, indices in nodos.items():
        if len(indices) != 1:
            continue
        indice = indices[0]
        if indice in usados:
            continue
        p0, p1 = segmentos[indice]
        inicio = p0 if clave(p0) == k else p1
        rutas.append(recorrer(indice, inicio))
    for indice in range(len(segmentos)):
        if indice not in usados:
            rutas.append(recorrer(indice, segmentos[indice][0]))
    return rutas


def _simplificar(puntos, tolerancia):
    """Douglas-Peucker: quita vertices que se desvian menos de la
    tolerancia de la recta entre sus vecinos conservados."""
    if len(puntos) < 3:
        return list(puntos)
    resultado = [puntos[0]]
    pila = [(0, len(puntos) - 1)]
    conservar = set([0, len(puntos) - 1])
    while pila:
        inicio, fin = pila.pop()
        if fin - inicio < 2:
            continue
        try:
            eje = DB.Line.CreateBound(puntos[inicio], puntos[fin])
            distancias = [(eje.Distance(puntos[i]), i)
                          for i in range(inicio + 1, fin)]
        except Exception:
            distancias = [(0.0, i) for i in range(inicio + 1, fin)]
        mayor, indice = max(distancias)
        if mayor > tolerancia:
            conservar.add(indice)
            pila.append((inicio, indice))
            pila.append((indice, fin))
    return [puntos[i] for i in range(len(puntos)) if i in conservar]


class ConsultorTerreno(object):
    """Cota del terreno del Modelo de Coordinacion (NWD/IFC) en (x, y),
    con el mismo motor que NWD Coordinator: zona extraida/cacheada en
    .nwcache y consultada por interpolacion sobre la malla."""

    def __init__(self, modelo, transformacion, origen, caja_ft, registro):
        self.transformacion = transformacion
        self.superficie = None
        self.cache = {}
        self.detalle = u"sin datos"
        try:
            caja_zona = transformacion.caja_revit_a_nwd(
                caja_ft[0], caja_ft[1], _MARGEN_ZONA_NWD_M)
            registro.info(
                u"Zona de terreno para tuberia CAD: caja=({0:.2f},{1:.2f})-"
                u"({2:.2f},{3:.2f}) m, margen={4:.0f} m",
                caja_zona[0], caja_zona[1], caja_zona[2], caja_zona[3],
                _MARGEN_ZONA_NWD_M)
            superficie, informe = pipeline.preparar_superficie(
                modelo, caja_zona, origen=origen, registro=registro)
            if superficie is None:
                self.detalle = u"sin datos ({0})".format(
                    (informe or {}).get("error") or u"error desconocido")
                return
            self.superficie = superficie
            self.detalle = u"{0} triangulos".format(informe.get("triangulos"))
        except Exception as error:
            self.detalle = u"sin datos (error: {0})".format(error)

    def z_en(self, x, y):
        clave = (round(x / _PASO_TERRENO_FT), round(y / _PASO_TERRENO_FT))
        if clave in self.cache:
            return self.cache[clave]
        valor = None
        if self.superficie is not None:
            x_nwd, y_nwd, _ = self.transformacion.revit_a_nwd(x, y, 0.0)
            z_m = self.superficie.altura(
                x_nwd, y_nwd, estrategia=superficie_mod.MAS_ALTA)
            if z_m is not None:
                valor = self.transformacion.z_nwd_a_revit_pies(z_m)
        self.cache[clave] = valor
        return valor


def _ruta_bajo_terreno(puntos, consultor, profundidad_ft):
    """Cota de cada vertice = terreno - profundidad. Con terreno, cada
    tramo se muestrea cada _PASO_MUESTREO_FT para que la tuberia siga el
    relieve por toda la superficie; luego se simplifica para dejar solo
    los quiebres reales. Sin terreno en un punto: cota del CAD -
    profundidad. Devuelve (puntos, sin_terreno)."""
    sin_terreno = 0
    muestras = []
    for indice, punto in enumerate(puntos):
        if indice > 0 and consultor is not None:
            previo = puntos[indice - 1]
            largo = DB.XYZ(previo.X, previo.Y, 0).DistanceTo(
                DB.XYZ(punto.X, punto.Y, 0))
            pasos = int(largo / _PASO_MUESTREO_FT)
            for k in range(1, pasos + 1):
                t = float(k) / (pasos + 1)
                muestras.append(DB.XYZ(previo.X + (punto.X - previo.X) * t,
                                       previo.Y + (punto.Y - previo.Y) * t,
                                       previo.Z + (punto.Z - previo.Z) * t))
        muestras.append(punto)

    resultado = []
    for punto in muestras:
        z_terreno = consultor.z_en(punto.X, punto.Y) if consultor else None
        if z_terreno is None:
            sin_terreno += 1
            z = punto.Z - profundidad_ft
        else:
            z = z_terreno - profundidad_ft
        resultado.append(DB.XYZ(punto.X, punto.Y, z))
    if consultor is not None:
        resultado = _simplificar(resultado, _TOL_SIMPLIFICAR_FT)
    return resultado, sin_terreno


def _caja_de_segmentos(segmentos):
    minimos = maximos = None
    for p0, p1 in segmentos:
        for p in (p0, p1):
            if minimos is None:
                minimos = [p.X, p.Y, p.Z]
                maximos = [p.X, p.Y, p.Z]
                continue
            for i, v in enumerate((p.X, p.Y, p.Z)):
                minimos[i] = min(minimos[i], v)
                maximos[i] = max(maximos[i], v)
    if minimos is None:
        return None
    return DB.XYZ(*minimos), DB.XYZ(*maximos)


class FilaElemento(object):
    """Fila de verificacion para un elemento seleccionado."""

    def __init__(self, elemento, marcado=False):
        self.elemento = elemento
        self.punto = _punto_insercion(elemento)
        self.Valido = self.punto is not None
        self.Marcado = bool(marcado and self.Valido)
        self.Id = u"{0}".format(elemento.Id.IntegerValue)
        self.Categoria, self.Familia, self.Tipo = revit_elements.describir(
            DB, doc, elemento)
        self.Punto = _texto_punto(self.punto)
        self.Estado = u"Valido" if self.Valido else u"No valido"
        self.Conectores = _texto_conectores(elemento)


def _texto_conectores(elemento):
    """Resumen de conectores libres del elemento, para la tabla:
    'C' conduit, 'E' electrico, 'O' otro. Vacio si no tiene."""
    etiquetas = {0: u"C", 1: u"E", 2: u"O"}
    try:
        libres = _conectores_libres(elemento)
    except Exception:
        return u"?"
    if not libres:
        return u"-"
    conteo = {}
    for conector in libres:
        clave = etiquetas[_prioridad_conector(conector)]
        conteo[clave] = conteo.get(clave, 0) + 1
    return u" ".join(u"{0}{1}".format(conteo[k], k)
                     for k in (u"C", u"E", u"O") if k in conteo)


class Ventana(forms.WPFWindow):
    """Ventana persistente para seleccionar, verificar y crear la tuberia."""

    def __init__(self, registro):
        self._cargando = True
        self._cerrada = False
        self._permitir_cierre = False
        forms.WPFWindow.__init__(
            self, os.path.join(os.path.dirname(__file__), "ui.xaml"))

        self.registro = registro
        # NWD/IFC aislado de la apertura de la ventana y de la creacion: se
        # resuelve solo si se pulsa "Verificar NWD/IFC..." (bloqueaba varios
        # segundos y abria una ventana de registro de pyRevit en cada uso).
        self.modelo = None
        self.origen = None
        self.transformacion = None
        self.diagnostico = None
        self.elementos = []
        self.filas = []
        self.tipo_tuberia = None
        # Continuidad entre creaciones sucesivas dentro de esta ventana:
        # el siguiente "Crear tuberia" arranca donde termino el anterior.
        self._ultimo_punto = None
        self._ultima_tuberia = None
        self._ultimo_elemento_id = None
        # CAD vinculado: lista de archivos, capas del CAD elegido y
        # consultores de terreno (uno por zona) para trazar bajo el NWD.
        self.cads = []
        self.capas = []
        self._cargando_cads = False
        self._consultores = {}

        self.acciones = AccionesRevit(self)
        self.Closing += self._al_cerrar
        self.Closed += self._al_cerrarse
        self._aplicacion = doc.Application
        self._aplicacion.DocumentChanged += self._documento_cambiado
        self._aplicacion.DocumentClosed += self._documento_cerrado

        self.txt_cabecera.Text = self._resumen_deteccion()
        self._cargando_tipos = False
        self._cargar_tipos_tuberia()
        self._detectar_cads(analizar=False)
        self._fijar_elementos(_seleccion_actual(), u"la seleccion actual")
        self._cargando = False

        if self.elementos:
            self._estado(
                u"Seleccion cargada. Marca dos o mas elementos, en orden, "
                u"para definir la ruta.")
        else:
            self._estado(u"Listo. Selecciona elementos sin cerrar esta ventana.")

    def _resumen_deteccion(self):
        if self.modelo is None:
            return u"Sin verificar (opcional; no bloquea la creacion)."
        origen = self.origen.etiqueta if self.origen is not None else u"sin origen"
        estado = (u"verificada" if self.diagnostico is not None and
                  self.diagnostico.valida else u"no verificada")
        return u"{0} · via {1} · transformacion {2}".format(
            _texto_modelo(self.modelo), origen, estado)

    def _estado(self, texto):
        try:
            self.txt_estado.Text = texto
        except Exception:
            pass

    def _refrescar(self):
        self.lv_elementos.ItemsSource = None
        self.lv_elementos.ItemsSource = self.filas
        self._actualizar_conteo()

    def _actualizar_conteo(self):
        marcados = len([fila for fila in self.filas if fila.Marcado])
        validos = len([fila for fila in self.filas if fila.Valido])
        self.txt_conteo.Text = (
            u"{0} elemento(s) · {1} valido(s) · {2} marcado(s)".format(
                len(self.filas), validos, marcados))

    def _anclaje_continuidad(self, elementos, solo_primero=False):
        """True solo si el final de la ultima tuberia creada queda cerca
        (< _MAX_DIST_CONTINUAR_FT) de algun elemento de la nueva tanda.
        Encadenar siempre cruzaba el sitio con un tramo ilogico cuando la
        nueva tanda esta lejos de la anterior.

        solo_primero=True: solo cuenta el primer elemento de la ruta. Al
        crear, si el cercano es el ultimo (misma tabla otra vez), empalmar
        desde el final anterior generaba un tramo de regreso hasta el
        primer elemento y la tuberia se repetia entera."""
        if self._ultimo_punto is None:
            return False
        for elemento in (elementos[:1] if solo_primero else elementos):
            punto = _punto_insercion(elemento)
            if punto is None:
                continue
            if punto.DistanceTo(self._ultimo_punto) <= _MAX_DIST_CONTINUAR_FT:
                return True
        return False

    def _fijar_elementos(self, elementos, origen, orden_de_click=False):
        """La lista se ordena por POSICION de los elementos
        (_ordenar_secuencia) y ese orden es el recorrido de la tuberia,
        sin depender del orden de click ni del de GetElementIds.
        orden_de_click=True solo conserva un orden ya decidido (p.ej. la
        seleccion conservada tras un cambio del modelo)."""
        unicos = []
        vistos = set()
        for elemento in elementos or []:
            if elemento is None:
                continue
            try:
                clave = elemento.Id.IntegerValue
            except Exception:
                continue
            if clave in vistos:
                continue
            vistos.add(clave)
            unicos.append(elemento)

        if not orden_de_click and len(unicos) >= 2:
            anclaje = (self._ultimo_punto
                       if self._anclaje_continuidad(unicos) else None)
            # Si se vuelve a escoger el elemento donde termino la ultima
            # tuberia, va primero: la ruta sigue desde el, sin volver a
            # pasar por el tramo ya creado (ida y vuelta duplicada).
            inicio = [e for e in unicos
                      if e.Id.IntegerValue == self._ultimo_elemento_id]
            if anclaje is not None and inicio:
                resto = [e for e in unicos if e is not inicio[0]]
                unicos = inicio + (_ordenar_secuencia(resto, anclaje)
                                   if len(resto) >= 2 else resto)
            else:
                unicos = _ordenar_secuencia(unicos, anclaje)

        marcar_validos = len(unicos) >= 2
        self.elementos = unicos
        self.filas = [FilaElemento(elemento, marcar_validos)
                      for elemento in unicos]
        for numero, fila in enumerate(self.filas, 1):
            fila.Orden = u"{0}".format(numero)
        self._refrescar()
        self.txt_seleccion.Text = u"{0} elemento(s) desde {1}.".format(
            len(unicos), origen)
        self.registro.info(
            u"Seleccion actualizada: origen={0}; elementos={1}",
            origen, u", ".join(fila.Id for fila in self.filas) or u"ninguno")
        self._estado(self.txt_seleccion.Text)

    def _fijar_asignacion(self, txt_nombre, txt_id, tipo):
        if tipo is None:
            txt_nombre.Text = u"Sin asignar"
            txt_id.Text = u""
            return
        txt_nombre.Text = _nombre_familia_tipo(tipo)
        txt_id.Text = u"ElementId {0}".format(tipo.Id.IntegerValue)

    def _actualizar_asignaciones(self):
        self._fijar_asignacion(
            self.txt_tipo_tuberia_nombre, self.txt_tipo_tuberia_id,
            self.tipo_tuberia)

    def _filas_marcadas(self):
        return [fila for fila in self.filas if fila.Marcado]

    @safe_handler
    def on_marcar_fila(self, sender, args):
        fila = getattr(sender, "DataContext", None)
        if fila is not None and hasattr(fila, "Marcado"):
            fila.Marcado = bool(sender.IsChecked)
        self._actualizar_conteo()

    @revit_handler
    def on_usar_seleccion(self, sender, args):
        self._fijar_elementos(_seleccion_actual(), u"la seleccion de Revit")

    @revit_handler
    def on_escoger(self, sender, args):
        """Escoge elemento a elemento (PickObject). Cada elemento escogido
        se anade a la seleccion de Revit para que quede resaltado mientras
        se sigue escogiendo. Termina con Esc, Espacio o Enter (las dos
        ultimas via _TeclasFinalizar), conservando lo escogido. Volver a
        clicar un elemento ya escogido lo quita.

        PickObjects exigia el boton 'Finalizar' de la barra de opciones y
        la API no expone tecla alguna para cerrarlo; Esc alli descartaba
        todo."""
        self.Hide()
        elementos = []
        teclas = _TeclasFinalizar(self.registro)
        con_atajo = teclas.instalar()
        resaltado = _ResaltadoTemporal(self.registro)
        try:
            while True:
                aviso = (u"Clic en cada elemento de la ruta ({0} escogido(s)). "
                         u"{1} = terminar; clic repetido = quitar."
                         .format(len(elementos),
                                 u"Espacio, Enter o Esc" if con_atajo
                                 else u"Esc"))
                try:
                    referencia = uidoc.Selection.PickObject(
                        UI.Selection.ObjectType.Element, aviso)
                except OperationCanceledException:
                    break
                except Exception as error:
                    if u"aborted the pick" in u"{0}".format(error):
                        break
                    raise
                elemento = doc.GetElement(referencia.ElementId)
                if elemento is None:
                    continue
                repetido = [e for e in elementos if e.Id == elemento.Id]
                if repetido:
                    elementos.remove(repetido[0])
                else:
                    elementos.append(elemento)
                resaltado.marcar(elementos)
            resaltado.terminar()
            if elementos:
                self._fijar_elementos(elementos, u"la pantalla")
                try:
                    ids = List[DB.ElementId]()
                    for elemento in elementos:
                        ids.Add(elemento.Id)
                    uidoc.Selection.SetElementIds(ids)
                except Exception:
                    pass
        finally:
            resaltado.terminar()
            teclas.quitar()
            if not self._cerrada:
                self.Show()

    @revit_handler
    def on_verificar_nwd(self, sender, args):
        """Accion aislada y opcional: no se ejecuta al abrir la ventana ni
        bloquea la creacion. Puede tardar (lanza NwdExtractor.exe) por eso
        se deja a criterio del usuario, no automatica."""
        self._estado(u"Verificando NWD/IFC... puede tardar.")
        try:
            self.modelo, self.origen, self.transformacion, self.diagnostico, \
                _superficies, _puentes, error_deteccion = \
                _detectar_origen(self.registro)
        except Exception as error:
            self.registro.excepcion(u"Deteccion NWD/IFC", error)
            self.modelo = None
            error_deteccion = u"{0}".format(error)

        self.txt_cabecera.Text = self._resumen_deteccion()
        self._consultores = {}
        if self.modelo is None:
            self._estado(error_deteccion or u"No se detecto ningun NWD/IFC.")
        elif error_deteccion:
            self._estado(u"NWD/IFC detectado, con aviso: {0}".format(
                error_deteccion))
        else:
            self._estado(u"NWD/IFC verificado.")

    def _cargar_tipos_tuberia(self):
        """Llena la lista desplegable con las familias de tuberia del
        proyecto. MEP electricidad: 'tuberia' aqui es Tubo/Conduit
        (categoria OST_Conduit), no Pipe de Plumbing; Electrical solo
        expone ConduitType (no existe 'FlexConduitType' en la API).
        Conserva la seleccion actual si el tipo sigue existiendo."""
        tipos = _catalogo(
            DB.BuiltInCategory.OST_Conduit, (DB.Electrical.ConduitType,))
        tipos.sort(key=lambda t: (_etiqueta_catalogo(t).lower(),
                                  t.Id.IntegerValue))
        opciones = [OpcionTipo(tipo) for tipo in tipos]
        actual = (self.tipo_tuberia.Id.IntegerValue
                  if self.tipo_tuberia is not None else None)
        self._cargando_tipos = True
        try:
            self.cmb_tipo_tuberia.ItemsSource = opciones
            elegida = None
            for opcion in opciones:
                if opcion.tipo.Id.IntegerValue == actual:
                    elegida = opcion
                    break
            if elegida is None and len(opciones) == 1:
                elegida = opciones[0]
            self.cmb_tipo_tuberia.SelectedItem = elegida
            self.tipo_tuberia = elegida.tipo if elegida is not None else None
        finally:
            self._cargando_tipos = False
        self._actualizar_asignaciones()
        if not opciones:
            self._estado(
                u"El proyecto no contiene familias de tuberia (Tubos) "
                u"cargadas ni creadas.")

    @safe_handler
    def on_tipo_tuberia_cambiado(self, sender, args):
        if getattr(self, "_cargando_tipos", False) or self._cargando:
            return
        opcion = self.cmb_tipo_tuberia.SelectedItem
        self.tipo_tuberia = opcion.tipo if opcion is not None else None
        self._actualizar_asignaciones()
        if self.tipo_tuberia is not None:
            self._estado(u"Familia de tuberia asignada.")

    @revit_handler
    def on_actualizar_tipos_tuberia(self, sender, args):
        self._cargar_tipos_tuberia()
        self._estado(u"Lista de familias de tuberia actualizada.")

    # ---- CAD vinculado -------------------------------------------------

    def _detectar_cads(self, analizar=True):
        """Llena la lista de CAD (vinculos e importaciones) conservando el
        elegido si sigue en el documento. analizar=False (al abrir la
        ventana) solo lista: leer la geometria de un CAD grande tarda y
        se hace cuando el usuario lo elige."""
        actual = None
        try:
            fila = self.cmb_cad.SelectedItem
            actual = fila.Id if fila is not None else None
        except Exception:
            pass
        self.cads = _cads_del_documento()
        self._cargando_cads = True
        try:
            self.cmb_cad.ItemsSource = self.cads
            elegida = None
            for fila in self.cads:
                if fila.Id == actual:
                    elegida = fila
                    break
            if elegida is None and len(self.cads) == 1 and analizar:
                elegida = self.cads[0]
            self.cmb_cad.SelectedItem = elegida
        finally:
            self._cargando_cads = False
        self.txt_cad_resumen.Text = (
            u"{0} archivo(s) CAD detectado(s). Elige uno para ver sus capas."
            .format(len(self.cads))
            if self.cads else u"Sin archivos CAD vinculados ni importados.")
        if analizar:
            self._analizar_capas()

    def _analizar_capas(self):
        fila = self.cmb_cad.SelectedItem
        self.capas = _capas_de_cad(fila.elemento) if fila is not None else []
        self.lv_capas.ItemsSource = None
        self.lv_capas.ItemsSource = self.capas
        if fila is None:
            return
        total = sum(c.Segmentos for c in self.capas)
        self.txt_cad_resumen.Text = (
            u"{0}: {1} capa(s), {2} segmento(s). Marca las capas a trazar."
            .format(fila.Nombre, len(self.capas), total))

    @revit_handler
    def on_detectar_cad(self, sender, args):
        self._detectar_cads()
        self._estado(self.txt_cad_resumen.Text)

    @revit_handler
    def on_cad_cambiado(self, sender, args):
        if self._cargando_cads:
            return
        self._analizar_capas()

    @safe_handler
    def on_marcar_capa(self, sender, args):
        fila = getattr(sender, "DataContext", None)
        if fila is not None and hasattr(fila, "Marcada"):
            fila.Marcada = bool(sender.IsChecked)

    def _consultor_terreno(self, caja_ft):
        """Consultor de terreno para la zona (cacheado por caja redondeada).
        None si el NWD/IFC no esta verificado: entonces la cota sale del
        propio CAD menos la profundidad."""
        if (self.modelo is None or self.transformacion is None
                or caja_ft is None):
            return None
        clave = tuple(int(round(v)) for v in (
            caja_ft[0].X, caja_ft[0].Y, caja_ft[1].X, caja_ft[1].Y))
        if clave in self._consultores:
            return self._consultores[clave]
        with forms.ProgressBar(title=u"Leyendo terreno del NWD/IFC...",
                               indeterminate=True):
            consultor = ConsultorTerreno(
                self.modelo, self.transformacion, self.origen, caja_ft,
                self.registro)
        self.registro.info(u"Terreno para tuberia CAD: {0}", consultor.detalle)
        self._consultores[clave] = consultor
        return consultor

    @revit_handler
    def on_crear_desde_cad(self, sender, args):
        """Traza tuberia sobre las capas marcadas del CAD elegido. Con el
        NWD/IFC verificado, cada tramo va a la profundidad indicada bajo
        el terreno a lo largo de toda la superficie; sin NWD, a la
        profundidad bajo la cota del propio CAD."""
        capas = [c for c in self.capas if c.Marcada]
        if not capas:
            forms.alert(u"Marca al menos una capa del CAD.",
                        title=TITULO, warn_icon=True)
            return
        if self.tipo_tuberia is None:
            forms.alert(
                u"Elige primero la familia de tuberia en la lista.",
                title=TITULO, warn_icon=True)
            return
        profundidad_m, error_profundidad = _leer_profundidad_m(
            self.txt_profundidad.Text)
        if error_profundidad:
            forms.alert(error_profundidad, title=TITULO, warn_icon=True)
            return
        profundidad_ft = profundidad_m / 0.3048 if profundidad_m else 0.0

        segmentos = []
        for capa in capas:
            segmentos.extend(capa.segmentos)
        if len(segmentos) > _LIMITE_SEGMENTOS_AVISO:
            if not forms.alert(
                    u"Las capas marcadas suman {0} segmentos. Crear "
                    u"tuberia para todos puede tardar varios minutos. "
                    u"Continuar?".format(len(segmentos)),
                    title=TITULO, yes=True, no=True):
                return

        caja = _caja_de_segmentos(segmentos)
        consultor = self._consultor_terreno(caja)
        if consultor is not None and consultor.superficie is None:
            self.registro.aviso(u"Terreno no disponible: {0}",
                                consultor.detalle)
            consultor = None

        rutas = _encadenar(segmentos)
        self.registro.info(
            u"Tuberia desde CAD: {0} capa(s), {1} segmento(s), {2} "
            u"polilinea(s), terreno={3}, profundidad={4:.3f} m",
            len(capas), len(segmentos), len(rutas),
            u"NWD/IFC" if consultor else u"cota del CAD", profundidad_m)

        creadas = []
        sin_terreno = 0
        fallidas = []
        grupo = DB.TransactionGroup(doc, u"Crear tuberia desde CAD")
        grupo.Start()
        try:
            with forms.ProgressBar(title=u"Creando tuberia desde CAD "
                                         u"({value} de {max_value})",
                                   cancellable=True) as barra:
                barra.max_value = len(rutas)
                for numero, ruta in enumerate(rutas, 1):
                    if barra.cancelled:
                        break
                    barra.update_progress(numero, len(rutas))
                    puntos, faltan = _ruta_bajo_terreno(
                        ruta, consultor, profundidad_ft)
                    sin_terreno += faltan
                    puntos = _con_pendientes(puntos)
                    if len(puntos) < 2:
                        continue
                    tuberias, error = _crear_ruta(
                        [], puntos, self.tipo_tuberia, profundidad_m,
                        self.registro)
                    if error:
                        fallidas.append(error)
                        continue
                    creadas.extend(tuberias)
            grupo.Assimilate()
        except Exception as error:
            self.registro.excepcion(u"Tuberia desde CAD", error)
            try:
                grupo.RollBack()
            except Exception:
                pass
            forms.alert(u"No se pudo crear la tuberia desde el CAD: {0}"
                        .format(error), title=TITULO, warn_icon=True)
            return

        self.registro.guardar()
        if creadas:
            _seleccionar_creadas(creadas)
        aviso = u""
        if sin_terreno:
            aviso = (u" {0} vertice(s) sin terreno bajo ellos: se uso la "
                     u"cota del CAD.".format(sin_terreno))
        if fallidas:
            aviso += u" {0} polilinea(s) fallaron (ver registro).".format(
                len(fallidas))
        self._estado(
            u"{0} tramo(s) de tuberia creado(s) desde {1} polilinea(s) del "
            u"CAD ({2}).{3}".format(
                len(creadas), len(rutas),
                u"bajo terreno NWD/IFC" if consultor else u"cota del CAD",
                aviso))

    @revit_handler
    def on_crear(self, sender, args):
        filas = self._filas_marcadas()
        if len(filas) < 2:
            forms.alert(
                u"Marca al menos dos filas para definir la ruta de la "
                u"tuberia. Actualmente hay {0}.".format(len(filas)),
                title=TITULO, warn_icon=True)
            return
        if any(not fila.Valido for fila in filas):
            forms.alert(
                u"Todos los elementos marcados deben tener LocationPoint.",
                title=TITULO, warn_icon=True)
            return
        if self.tipo_tuberia is None:
            forms.alert(
                u"Elige primero la familia de tuberia en la lista antes de "
                u"crear el tramo.", title=TITULO, warn_icon=True)
            return

        profundidad_m, error_profundidad = _leer_profundidad_m(
            self.txt_profundidad.Text)
        if error_profundidad:
            forms.alert(error_profundidad, title=TITULO, warn_icon=True)
            return

        # Orden de la ruta = orden visible de la tabla (orden de click, o
        # ya ordenado al cargar). No se reordena aqui: reordenar en este
        # punto producia trayectorias ilogicas.
        elementos = [fila.elemento for fila in filas]
        profundidad_ft = profundidad_m / 0.3048 if profundidad_m else 0.0
        continuar = self._anclaje_continuidad(elementos, solo_primero=True)
        # Ruta que arranca en el elemento final de la anterior: se empalma
        # con la ultima tuberia pero sin anteponer su punto (seria un tramo
        # duplicado de vuelta sobre el ya creado).
        repite_final = (elementos[0].Id.IntegerValue ==
                        self._ultimo_elemento_id)
        puntos, error_puntos = _validar_ruta(
            elementos, profundidad_ft,
            punto_previo=(self._ultimo_punto
                          if continuar and not repite_final else None))
        if error_puntos:
            self.registro.error(error_puntos)
            forms.alert(error_puntos, title=TITULO, warn_icon=True)
            return

        tuberias, error_creacion = _crear_ruta(
            elementos, puntos, self.tipo_tuberia, profundidad_m,
            self.registro,
            tuberia_previa=self._ultima_tuberia if continuar else None)
        if error_creacion:
            forms.alert(error_creacion, title=TITULO, warn_icon=True)
            return
        if not tuberias:
            return

        self._ultima_tuberia = tuberias[-1]
        self._ultimo_punto = puntos[-1]
        self._ultimo_elemento_id = elementos[-1].Id.IntegerValue

        # Las filas ya usadas se desmarcan: pulsar 'Crear' de nuevo con la
        # misma tabla no vuelve a generar la misma tuberia.
        for fila in filas:
            fila.Marcado = False
            fila.Estado = u"Creada"
        self._refrescar()

        _seleccionar_creadas(tuberias)
        self.registro.guardar()
        self._estado(
            u"{0} tramo(s) de tuberia creado(s): ElementId {1}. Sigue "
            u"marcando elementos: el proximo tramo continua desde aqui."
            .format(len(tuberias),
                    u", ".join(unicode(t.Id.IntegerValue) for t in tuberias)))

    def _documento_activo(self, app):
        if not doc.IsValidObject:
            self._permitir_cierre = True
            self.Close()
            return False
        activo = app.ActiveUIDocument
        if activo is None or not activo.Document.Equals(doc):
            self._estado(
                u"Activa el documento donde abriste Generar Tuberia NWD "
                u"para continuar, o cierra la ventana y abrela en el nuevo.")
            return False
        return True

    @safe_handler
    def _documento_cambiado(self, sender, args):
        if self._cerrada or self.acciones.ejecutando:
            return
        if not args.GetDocument().Equals(doc):
            return

        elementos_validos = []
        for elemento in self.elementos:
            try:
                if elemento.IsValidObject:
                    elementos_validos.append(elemento)
            except Exception:
                pass
        # Conservar marca y estado por fila: al reconstruir la tabla se
        # volvian a marcar las filas ya usadas ('Creada') y el siguiente
        # 'Crear' repetia la tuberia completa.
        previas = dict((fila.Id, fila) for fila in self.filas)
        self._fijar_elementos(
            elementos_validos, u"la seleccion conservada", orden_de_click=True)
        for fila in self.filas:
            previa = previas.get(fila.Id)
            if previa is not None and fila.Valido:
                fila.Marcado = previa.Marcado
                fila.Estado = previa.Estado
        self._refrescar()

        try:
            vigente = (self.tipo_tuberia is not None and
                      self.tipo_tuberia.IsValidObject)
        except Exception:
            vigente = False
        if not vigente:
            self.tipo_tuberia = None
        self._cargar_tipos_tuberia()

        try:
            if (self._ultima_tuberia is not None and
                    not self._ultima_tuberia.IsValidObject):
                self._ultima_tuberia = None
        except Exception:
            self._ultima_tuberia = None

        self._actualizar_asignaciones()
        self._estado(
            u"El modelo cambio. Verifica los extremos y la familia de "
            u"tuberia antes de crear.")

    @safe_handler
    def _documento_cerrado(self, sender, args):
        if not doc.IsValidObject:
            self._permitir_cierre = True
            self.Close()

    @safe_handler
    def _al_cerrar(self, sender, args):
        if not self._permitir_cierre:
            args.Cancel = True
            self.on_cerrar(sender, args)

    @safe_handler
    def _al_cerrarse(self, sender, args):
        self._cerrada = True
        self._aplicacion.DocumentChanged -= self._documento_cambiado
        self._aplicacion.DocumentClosed -= self._documento_cerrado
        AppDomain.CurrentDomain.SetData(_VENTANA_KEY, None)
        if not self.acciones.ejecutando:
            self.acciones.liberar()
        self.elementos = []
        self.filas = []
        self.tipo_tuberia = None

    @safe_handler
    def on_guardar_registro(self, sender, args):
        ruta = self.registro.guardar()
        if ruta:
            self._estado(u"Registro guardado en {0}".format(ruta))
            try:
                os.startfile(ruta)
            except Exception:
                pass
        else:
            self._estado(u"No se pudo guardar el registro.")

    @safe_handler
    def on_cerrar(self, sender, args):
        self.acciones.solicitar(self._cerrar_en_revit, cierre=True)

    def _cerrar_en_revit(self):
        self._permitir_cierre = True
        self.Close()


def main():
    """Abre la ventana directo: la verificacion NWD/IFC ya no es requisito
    de apertura ni de creacion, queda aislada en 'Verificar NWD/IFC...'."""
    registro = Registro("generar_tuberia_nwd")
    registro.info(
        u"Generar Tuberia NWD; modulos nwdcoord recargados={0}",
        _NWD_MODULOS_RECARGADOS)

    ventana = Ventana(registro)
    AppDomain.CurrentDomain.SetData(_VENTANA_KEY, ventana)
    ventana.Show()


if __name__ == "__main__":
    main()
