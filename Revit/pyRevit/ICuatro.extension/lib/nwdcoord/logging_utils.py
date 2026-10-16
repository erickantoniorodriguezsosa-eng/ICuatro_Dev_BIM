# -*- coding: utf-8 -*-
"""Registro resumido de la actividad.

Requisito de la seccion 29 del prompt maestro: registrar lo que importa
sin imprimir millones de lineas, y de la 30: no ocultar excepciones en
silencio.
"""

import os
import datetime
import tempfile
import traceback


class Registro(object):
    """Acumula lineas en memoria y las vuelca a archivo cuando se pide.

    No escribe en cada llamada a proposito: durante un analisis con miles
    de elementos, abrir el archivo cada vez domina el tiempo total."""

    def __init__(self, nombre="nwdcoord"):
        self.nombre = nombre
        self.lineas = []
        self.errores = 0
        self._inicio = datetime.datetime.now()
        self._marcas = {}

    # --------------------------------------------------------------------
    # ESCRITURA
    # --------------------------------------------------------------------

    def info(self, texto, *args):
        self._añadir(u"INFO", texto, args)

    def aviso(self, texto, *args):
        self._añadir(u"AVISO", texto, args)

    def error(self, texto, *args):
        self.errores += 1
        self._añadir(u"ERROR", texto, args)

    def excepcion(self, contexto, error, element_id=None):
        """Una excepcion con su contexto.

        Se registra el tipo, el mensaje, donde paso y sobre que elemento:
        sin esos cuatro datos, un error en el elemento 700 de 3000 es
        imposible de reproducir."""
        self.errores += 1

        self._añadir(u"ERROR", u"{0} | {1}: {2}{3}", (
            contexto,
            type(error).__name__,
            error,
            u" | ElementId {0}".format(element_id) if element_id else u"",
        ))

        for linea in traceback.format_exc().splitlines():
            self.lineas.append(u"        {0}".format(linea))

    def _añadir(self, nivel, texto, args):
        try:
            mensaje = texto.format(*args) if args else texto
        except Exception:
            mensaje = u"{0} {1}".format(texto, args)

        marca = (datetime.datetime.now() - self._inicio).total_seconds()
        self.lineas.append(u"[{0:8.3f}s] {1:5} {2}".format(marca, nivel, mensaje))

    # --------------------------------------------------------------------
    # TIEMPOS
    # --------------------------------------------------------------------

    def empezar(self, clave):
        self._marcas[clave] = datetime.datetime.now()

    def terminar(self, clave, extra=u""):
        """Cierra un cronometro abierto con empezar() y lo registra."""
        inicio = self._marcas.pop(clave, None)

        if inicio is None:
            return 0.0

        segundos = (datetime.datetime.now() - inicio).total_seconds()
        self.info(u"{0}: {1:.2f} s{2}", clave, segundos,
                  u" ({0})".format(extra) if extra else u"")

        return segundos

    # --------------------------------------------------------------------
    # VOLCADO
    # --------------------------------------------------------------------

    def guardar(self, ruta=None):
        """Vuelca el registro a un .txt y devuelve su ruta."""
        if ruta is None:
            marca = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            ruta = os.path.join(
                tempfile.gettempdir(),
                "{0}_{1}.txt".format(self.nombre, marca)
            )

        try:
            import codecs
            with codecs.open(ruta, "w", "utf-8") as archivo:
                archivo.write(u"\n".join(self.lineas))
            return ruta
        except Exception:
            return None

    def resumen(self):
        return u"{0} linea(s) de registro, {1} error(es)".format(
            len(self.lineas), self.errores
        )
