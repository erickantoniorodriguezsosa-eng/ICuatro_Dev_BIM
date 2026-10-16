# -*- coding: utf-8 -*-
"""Puente con Navisworks a traves del proceso externo NwdExtractor.exe.

Revit no da acceso a la geometria del NWD (ver NWD_CAPABILITY_REPORT.md
secciones 2 y 3). La unica via son los triangulos que entrega el puente
COM de Navisworks, y ese puente solo funciona dentro del proceso de
Navisworks. De ahi el proceso externo:

    pyRevit -> NwdExtractor.exe -> NavisworksApplication (headless)
                                        AddPluginAssembly
                                   plugin in-process -> puente COM
                                        cache binario -> pyRevit

Ventajas frente a instalar un plugin en Navisworks (seccion 42 del
prompt maestro): no requiere permisos de administrador, y si Navisworks o
el NWD fallan se cae el proceso auxiliar y no la sesion de Revit.

Este modulo NO interpreta geometria: solo lanza el proceso y lee su
informe. La lectura del cache vive en geometry.py.
"""

import os

from . import proceso


# Nombres de los artefactos, tal cual los produce la compilacion.
EJECUTABLE = "NwdExtractor.exe"
PLUGIN = "ICuatroNwdExtractorPlugin.dll"


def _raiz_extension():
    """Carpeta que contiene la extension, subiendo desde lib/nwdcoord."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))


def localizar_extractor():
    """Ruta de NwdExtractor.exe, o None."""
    forzado = os.environ.get("ICUATRO_NWDEXTRACTOR")

    if forzado and os.path.isfile(forzado):
        return forzado

    return proceso.localizar(EJECUTABLE, _raiz_extension(), ("NwdExtractor",))


def estado():
    """Disponibilidad del puente, para el diagnostico.

    Devuelve (disponible, mensaje, detalles) sin lanzar nada: sirve para
    que TEST NWD informe en lugar de fallar."""
    detalles = {}

    ruta = localizar_extractor()
    detalles["ejecutable"] = ruta

    if ruta is None:
        return False, u"No se encontro {0}. Compila el proyecto " \
                      u"NwdExtractor.".format(EJECUTABLE), detalles

    plugin = os.path.join(os.path.dirname(ruta), PLUGIN)
    detalles["plugin"] = plugin if os.path.isfile(plugin) else None

    if detalles["plugin"] is None:
        return False, u"Falta {0} junto al ejecutable.".format(PLUGIN), detalles

    navisworks = localizar_navisworks()
    detalles["navisworks"] = navisworks

    if navisworks is None:
        return False, u"No hay ninguna instalacion de Navisworks con API.", \
               detalles

    return True, u"Puente disponible ({0}).".format(
        os.path.basename(navisworks)), detalles


def localizar_navisworks():
    """Instalacion de Navisworks mas reciente que tenga la API."""
    forzado = os.environ.get("ICUATRO_NAVISWORKS_DIR")

    if forzado and os.path.isdir(forzado):
        return forzado

    base = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Autodesk"
    )

    if not os.path.isdir(base):
        return None

    encontradas = []

    try:
        for nombre in os.listdir(base):
            if not nombre.startswith("Navisworks"):
                continue

            directorio = os.path.join(base, nombre)

            if os.path.isfile(os.path.join(
                    directorio, "Autodesk.Navisworks.Api.dll")):
                encontradas.append(directorio)
    except Exception:
        return None

    if not encontradas:
        return None

    return sorted(encontradas)[-1]


def sondear(nwd, salida, muestras=25, filtro=None, registro=None):
    """Inventario del NWD sin extraer geometria completa.

    Devuelve el diccionario del informe. La clave 'com_bridge' dice si el
    puente COM entrego triangulos de verdad."""
    argumentos = ["probe", nwd, salida, "muestras={0}".format(muestras)]

    if filtro:
        argumentos.append("filtro={0}".format(filtro))

    codigo, texto, error = proceso.ejecutar(
        localizar_extractor(), argumentos, 600, registro)
    datos = proceso.leer_informe(salida)

    datos["_codigo"] = codigo
    datos["_consola"] = texto
    datos["_error"] = error

    return datos


def fuentes(nwd, salida, registro=None):
    """Identifica los archivos que Navisworks agrego dentro del NWD.

    El NWD es un contenedor binario: si el IFC original no esta disponible
    en disco no se puede abrir con IfcExtractor, pero Navisworks si puede
    entregar sus triangulos mediante el puente COM. Este inventario deja
    esa diferencia explicita en el registro.
    """
    codigo, texto, error = proceso.ejecutar(
        localizar_extractor(), ["sources", nwd, salida], 600, registro)

    try:
        import codecs
        with codecs.open(salida, "r", "utf-8") as archivo:
            contenido = archivo.read()
    except Exception:
        contenido = u""

    return {
        "codigo": codigo,
        "contenido": contenido,
        "consola": texto,
        "error": error,
        "ruta": salida,
    }


def extraer(nwd, cache, caja=None, filtro=None, maximo=0,
            tiempo_maximo_s=1800, registro=None):
    """Vuelca a 'cache' la geometria de la zona pedida.

    'caja' es (minX, minY, maxX, maxY) en METROS y en coordenadas del
    NWD. Sin caja se extrae todo, que en un modelo de corredor no es
    viable: ver la seccion 5 del informe de capacidades."""
    argumentos = ["extract", nwd, cache]

    if caja:
        argumentos.append("caja={0},{1},{2},{3}".format(*caja))

    if filtro:
        argumentos.append("filtro={0}".format(filtro))

    if maximo:
        argumentos.append("max={0}".format(maximo))

    codigo, texto, error = proceso.ejecutar(
        localizar_extractor(), argumentos, tiempo_maximo_s, registro)
    datos = proceso.leer_informe(cache + ".txt")

    datos["_codigo"] = codigo
    datos["_consola"] = texto
    datos["_error"] = error
    datos["_cache"] = cache if os.path.isfile(cache) else None

    return datos
