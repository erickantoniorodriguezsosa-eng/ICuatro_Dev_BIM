# -*- coding: utf-8 -*-
"""Puente con el IFC: lectura directa de la triangulación.

POR QUE ES EL CAMINO PREFERENTE

El NWD de superficies se genera anexando un IFC, y ese IFC suele estar
en disco junto al NWD. Se comprobó en este proyecto (ver
NWD_CAPABILITY_REPORT.md §11):

    Archivo             1,08 GB, IFC4 de ISTRAM vía IfcOpenShell
    Geometría           IFCTRIANGULATEDFACESET + IFCCARTESIANPOINTLIST3D
    Lectura completa    24.468.130 triángulos en 7,3 s

Frente al puente de Navisworks, leer el IFC:

  - no necesita Navisworks instalado ni consume licencia;
  - no depende del puente COM ni de la versión de la API;
  - es mucho más rápido, porque no hay que teselar nada: la malla ya
    está triangulada en el archivo;
  - y da exactamente la misma geometría (las cajas coinciden a 0,0001 m
    sobre 22 km).

El extractor produce el mismo cache NWCACHE2 que el de Navisworks, así
que todo lo que va después no distingue de dónde vino la superficie.
"""

import os
import re

from . import proceso

EJECUTABLE = "IfcExtractor.exe"


def _raiz_extension():
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def localizar_extractor():
    forzado = os.environ.get("ICUATRO_IFCEXTRACTOR")

    if forzado and os.path.isfile(forzado):
        return forzado

    return proceso.localizar(EJECUTABLE, _raiz_extension(), ("IfcExtractor",))


def ifc_asociado(ruta_nwd):
    """Busca el IFC del que salió un NWD.

    Primero respeta ICUATRO_IFC, luego prueba el mismo nombre y finalmente
    busca IFC vecinos. Esto cubre el caso habitual en que Navisworks guardo
    el NWD con un nombre de coordinacion distinto al del IFC original.

    Si hay varios IFC vecinos, solo se elige uno cuando la puntuacion del
    nombre es claramente superior; asi no se usa silenciosamente un modelo
    equivocado. Devuelve la ruta o None."""
    if not ruta_nwd:
        return None

    forzado = os.environ.get("ICUATRO_IFC")

    if forzado and os.path.isfile(forzado):
        return os.path.normpath(forzado)

    ruta_nwd = os.path.normpath(ruta_nwd)
    base, _ = os.path.splitext(ruta_nwd)
    candidato = base + ".ifc"

    if os.path.isfile(candidato):
        return os.path.normpath(candidato)

    carpeta = os.path.dirname(ruta_nwd)
    nombre = os.path.basename(base) + ".ifc"
    candidatos = []

    def agregar(ruta):
        ruta = os.path.normpath(ruta)

        if os.path.isfile(ruta) and ruta.lower().endswith(".ifc"):
            clave = os.path.normcase(os.path.abspath(ruta))

            if clave not in [os.path.normcase(os.path.abspath(x))
                             for x in candidatos]:
                candidatos.append(ruta)

    # Variantes de carpetas usadas por los entregables de coordinacion.
    for vecina in ("IFC", "ifc", "IFC-SUPERFICIES_EXISTENTES", ".."):
        agregar(os.path.join(carpeta, vecina, nombre))

    try:
        for entrada in os.listdir(carpeta):
            if entrada.lower().endswith(".ifc"):
                agregar(os.path.join(carpeta, entrada))
    except Exception:
        pass

    if len(candidatos) == 0:
        return None

    if len(candidatos) == 1:
        return candidatos[0]

    palabras_nwd = _palabras(os.path.splitext(os.path.basename(ruta_nwd))[0])
    mejores = []

    for candidato in candidatos:
        nombre_ifc = os.path.splitext(os.path.basename(candidato))[0]
        palabras_ifc = _palabras(nombre_ifc)
        comunes = palabras_nwd.intersection(palabras_ifc)
        puntuacion = len(comunes) * 25

        if any(p in palabras_ifc for p in (
                "surface", "surfaces", "superficie", "superficies",
                "terrain", "terreno", "topo", "topografia", "existente",
                "existentes", "dem", "tin")):
            puntuacion += 100

        mejores.append((puntuacion, candidato))

    mejores.sort(key=lambda par: par[0], reverse=True)

    if mejores[0][0] > mejores[1][0]:
        return mejores[0][1]

    return None


def _palabras(nombre):
    """Tokens comparables de un nombre de NWD/IFC, sin depender de tildes."""
    nombre = nombre.lower()
    tokens = re.split(r"[^a-z0-9]+", nombre)
    return set(token for token in tokens if len(token) >= 3)


def estado():
    """Disponibilidad del puente. Devuelve (disponible, mensaje, detalles)."""
    detalles = {}

    ruta = localizar_extractor()
    detalles["ejecutable"] = ruta

    if ruta is None:
        return False, (u"No se encontro {0}. Compila el proyecto "
                       u"IfcExtractor.".format(EJECUTABLE)), detalles

    return True, u"Lector de IFC disponible.", detalles


def sondear(ifc, salida, registro=None, completo=False):
    """Caja envolvente del IFC, que es lo que necesita la transformación.

    Por defecto usa el modo rápido, que lee solo las listas de puntos y se
    salta la construcción de triángulos: abrir la herramienta no debe
    costar lo mismo que extraer la geometría. Con completo=True hace el
    inventario entero (elementos, facesets, triángulos)."""
    ruta = localizar_extractor()

    codigo, texto, error = proceso.ejecutar(
        ruta, ["probe" if completo else "bbox", ifc, salida], 900, registro)

    datos = proceso.leer_informe(salida)
    datos["_codigo"] = codigo
    datos["_consola"] = texto
    datos["_error"] = error

    return datos


def ruta_indice(destino_sondeo):
    """El índice que deja el sondeo junto a su informe."""
    return destino_sondeo + ".idx"


def extraer(ifc, cache, caja=None, maximo=0, tiempo_maximo_s=1800,
            registro=None, indice=None, filtro=None):
    """Vuelca la triangulación de la zona pedida al cache binario.

    'caja' es (minX, minY, maxX, maxY) en metros y en las coordenadas del
    propio IFC.

    'indice' es el archivo de cajas por faceset que genera el sondeo. Con
    él, los facesets que no tocan la zona se saltan sin convertir sus
    coordenadas: en el modelo de prueba baja la extracción de 12,3 s a
    1,6 s."""
    ruta = localizar_extractor()
    argumentos = ["extract", ifc, cache]

    if caja:
        argumentos.append("caja={0},{1},{2},{3}".format(*caja))

    if maximo:
        argumentos.append("max={0}".format(maximo))

    if indice and os.path.isfile(indice):
        argumentos.append("idx={0}".format(indice))

    if filtro:
        argumentos.append("sup={0}".format(filtro))

    codigo, texto, error = proceso.ejecutar(
        ruta, argumentos, tiempo_maximo_s, registro)

    datos = proceso.leer_informe(cache + ".txt")
    datos["_codigo"] = codigo
    datos["_consola"] = texto
    datos["_error"] = error
    datos["_cache"] = cache if os.path.isfile(cache) else None

    return datos
