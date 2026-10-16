# -*- coding: utf-8 -*-
"""Lanzar un extractor externo y leer su informe.

Lo comparten los dos puentes (IFC y Navisworks) porque el mecanismo es el
mismo: se ejecuta un .exe, se espera, y se lee un .txt de pares
clave-valor separados por tabuladores.
"""

import os

from System.Diagnostics import Process, ProcessStartInfo


def localizar(nombre_exe, raiz_extension, subcarpetas=()):
    """Busca un ejecutable junto a la extension o en carpetas hermanas.

    Devuelve la primera ruta que exista, o None."""
    candidatos = [os.path.join(raiz_extension, "bin", nombre_exe)]

    padre = os.path.dirname(raiz_extension)

    for subcarpeta in subcarpetas:
        candidatos.append(os.path.join(padre, subcarpeta, nombre_exe))

    candidatos.append(os.path.join(padre, "bin", nombre_exe))

    for ruta in candidatos:
        if os.path.isfile(ruta):
            return ruta

    return None


def ejecutar(ruta_exe, argumentos, tiempo_maximo_s=1800, registro=None):
    """Lanza el proceso y espera. Devuelve (codigo, salida, error).

    Se usa System.Diagnostics.Process en lugar de subprocess porque en
    IronPython es mas predecible redirigiendo la salida."""
    if not ruta_exe or not os.path.isfile(ruta_exe):
        raise IOError(u"No se encontro el ejecutable: {0}".format(ruta_exe))

    info = ProcessStartInfo()
    info.FileName = ruta_exe
    info.Arguments = u" ".join(
        u'"{0}"'.format(a) if u" " in a else a for a in argumentos)
    info.UseShellExecute = False
    info.RedirectStandardOutput = True
    info.RedirectStandardError = True
    info.CreateNoWindow = True
    info.WorkingDirectory = os.path.dirname(ruta_exe)

    if registro:
        registro.info(u"Lanzando: {0} {1}",
                      os.path.basename(ruta_exe), info.Arguments)

    proceso = Process()
    proceso.StartInfo = info
    proceso.Start()

    # Se leen ambos flujos ANTES de esperar: si el proceso llena el buffer
    # de una tuberia que nadie vacia, se queda bloqueado para siempre.
    salida = proceso.StandardOutput.ReadToEnd()
    error = proceso.StandardError.ReadToEnd()

    if not proceso.WaitForExit(int(tiempo_maximo_s * 1000)):
        try:
            proceso.Kill()
        except Exception:
            pass

        raise IOError(
            u"El extractor no termino en {0} s.".format(tiempo_maximo_s))

    return proceso.ExitCode, salida, error


def leer_informe(ruta):
    """Convierte el informe de texto del extractor en un diccionario.

    Una linea por dato, con tabuladores. Las claves repetidas (como
    'item' o 'sup') se acumulan en una lista."""
    if not os.path.isfile(ruta):
        return {}

    try:
        import codecs
        with codecs.open(ruta, "r", "utf-8") as archivo:
            contenido = archivo.read()
    except Exception:
        return {}

    datos = {}

    for linea in contenido.splitlines():
        linea = linea.lstrip(u"﻿").rstrip()

        if not linea:
            continue

        partes = linea.split(u"\t")
        clave = partes[0]

        if len(partes) > 2:
            valor = partes[1:]
        elif len(partes) == 2:
            valor = partes[1]
        else:
            valor = u""

        if clave in (u"item", u"sup", u"superficie"):
            datos.setdefault(clave + u"s", []).append(valor)
        else:
            datos[clave] = valor

    return datos


def caja_de(datos, clave=u"bbox_m"):
    """La caja envolvente del informe como (minimos, maximos), o None."""
    valores = datos.get(clave)

    if not isinstance(valores, list) or len(valores) != 6:
        return None

    try:
        numeros = [float(v) for v in valores]
    except Exception:
        return None

    return (numeros[:3], numeros[3:])
