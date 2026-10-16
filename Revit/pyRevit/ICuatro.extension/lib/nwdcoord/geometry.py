# -*- coding: utf-8 -*-
"""Lectura del cache geometrico que produce el extractor de Navisworks.

DECISION DE REPRESENTACION
    La seccion 9 del prompt maestro propone una clase Triangle(p1, p2,
    p3, normal, bbox) "o una estructura equivalente eficiente". Aqui se
    toma la segunda opcion, y no por gusto: una zona de trabajo normal
    trae del orden de 900.000 triangulos, y crear 900.000 objetos de
    Python (mas 2,7 millones de puntos) agota la memoria y tarda
    minutos en IronPython.

    En su lugar se usan ARRAYS PLANOS de .NET:

        vertices[i*3 + 0..2]    coordenadas X, Y, Z del vertice i
        triangulos[t*3 + 0..2]  indices de los tres vertices del triangulo t

    Los bloques se leen con Buffer.BlockCopy, que copia memoria a
    memoria: leer 900.000 triangulos tarda menos de un segundo, frente a
    los minutos que costaria un bucle de ReadDouble().
"""

import os

from System import Array, Buffer, Double, Int32
from System.IO import BinaryReader, FileStream, FileMode, FileAccess

FIRMA = "NWCACHE2"


class ItemNWD(object):
    """Un elemento del NWD dentro de la malla combinada.

    No guarda geometria propia: apunta al tramo del array global que le
    corresponde, para poder decir de que item salio un triangulo sin
    duplicar datos."""

    def __init__(self, nombre, ruta, clase, primer_triangulo, num_triangulos):
        self.nombre = nombre
        self.ruta = ruta
        self.clase = clase
        self.primer_triangulo = primer_triangulo
        self.num_triangulos = num_triangulos

    def contiene(self, indice_triangulo):
        return (self.primer_triangulo <= indice_triangulo <
                self.primer_triangulo + self.num_triangulos)

    def __repr__(self):
        return "<ItemNWD '{0}' {1} tri>".format(
            self.nombre or u"(sin nombre)", self.num_triangulos)


class MallaNWD(object):
    """Los triangulos de una zona del NWD, en metros y coordenadas NWD."""

    def __init__(self):
        self.vertices = None        # Array[Double], 3 por vertice
        self.triangulos = None      # Array[Int32], 3 por triangulo
        self.items = []
        self.minimos = [0.0, 0.0, 0.0]
        self.maximos = [0.0, 0.0, 0.0]
        self.unidades = u"Meters"
        self.archivo_origen = u""
        self.ruta_cache = None

    @property
    def num_triangulos(self):
        return 0 if self.triangulos is None else self.triangulos.Length // 3

    @property
    def num_vertices(self):
        return 0 if self.vertices is None else self.vertices.Length // 3

    def caja(self):
        return (list(self.minimos), list(self.maximos))

    def tamano(self):
        return [self.maximos[i] - self.minimos[i] for i in range(3)]

    def vertices_de(self, indice_triangulo):
        """Los tres vertices de un triangulo, como tuplas (x, y, z)."""
        base = indice_triangulo * 3
        salida = []

        for esquina in range(3):
            v = self.triangulos[base + esquina] * 3
            salida.append((self.vertices[v],
                           self.vertices[v + 1],
                           self.vertices[v + 2]))

        return salida

    def item_de(self, indice_triangulo):
        for item in self.items:
            if item.contiene(indice_triangulo):
                return item
        return None

    def __repr__(self):
        return "<MallaNWD {0} tri, {1} items>".format(
            self.num_triangulos, len(self.items))


def _leer_bloque_double(lector, cantidad):
    """Lee cantidad*3 dobles de golpe."""
    total = cantidad * 3
    crudo = lector.ReadBytes(total * 8)

    destino = Array.CreateInstance(Double, total)
    Buffer.BlockCopy(crudo, 0, destino, 0, crudo.Length)

    return destino


def _leer_bloque_int(lector, cantidad):
    """Lee cantidad*3 enteros de golpe."""
    total = cantidad * 3
    crudo = lector.ReadBytes(total * 4)

    destino = Array.CreateInstance(Int32, total)
    Buffer.BlockCopy(crudo, 0, destino, 0, crudo.Length)

    return destino


def leer_cache(ruta, registro=None, progreso=None):
    """Carga un .nwcache completo en una MallaNWD.

    Los bloques de cada item se concatenan tal cual: sus indices ya
    vienen referidos al array global desde el extractor."""
    if not os.path.isfile(ruta):
        raise IOError(u"No existe el cache: {0}".format(ruta))

    flujo = FileStream(ruta, FileMode.Open, FileAccess.Read)
    lector = BinaryReader(flujo)

    malla = MallaNWD()
    malla.ruta_cache = ruta

    try:
        firma = u"".join(lector.ReadChars(8))

        if firma != FIRMA:
            raise ValueError(
                u"El archivo no es un cache valido (firma '{0}').".format(firma))

        malla.unidades = lector.ReadString()
        lector.ReadDouble()                       # factor, ya aplicado
        malla.archivo_origen = lector.ReadString()

        total_items = lector.ReadInt32()
        malla.minimos = [lector.ReadDouble() for _ in range(3)]
        malla.maximos = [lector.ReadDouble() for _ in range(3)]

        bloques_vertices = []
        bloques_triangulos = []

        vertices_acumulados = 0
        triangulos_acumulados = 0

        for indice in range(total_items):
            if progreso is not None:
                progreso(indice + 1, total_items)

            nombre = lector.ReadString()
            ruta_item = lector.ReadString()
            clase = lector.ReadString()

            num_vertices = lector.ReadInt32()
            vertices = _leer_bloque_double(lector, num_vertices)

            num_triangulos = lector.ReadInt32()
            indices = _leer_bloque_int(lector, num_triangulos)

            # Los indices ya vienen referidos al array global: el
            # extractor los desplaza al escribir (firma NWCACHE2). Hacerlo
            # aqui costaria millones de iteraciones en IronPython.
            bloques_vertices.append(vertices)
            bloques_triangulos.append(indices)

            malla.items.append(ItemNWD(
                nombre, ruta_item, clase,
                triangulos_acumulados, num_triangulos))

            vertices_acumulados += num_vertices
            triangulos_acumulados += num_triangulos
    finally:
        lector.Close()
        flujo.Close()

    malla.vertices = _unir(bloques_vertices, Double, vertices_acumulados * 3)
    malla.triangulos = _unir(bloques_triangulos, Int32, triangulos_acumulados * 3)

    if registro:
        registro.info(
            u"Cache leido: {0} triangulos, {1} vertices, {2} items",
            malla.num_triangulos, malla.num_vertices, len(malla.items))

    return malla


def _unir(bloques, tipo, longitud_total):
    """Concatena los bloques en un unico array.

    Se usa Array.Copy y no un bucle: son millones de valores."""
    destino = Array.CreateInstance(tipo, longitud_total)
    posicion = 0

    for bloque in bloques:
        Array.Copy(bloque, 0, destino, posicion, bloque.Length)
        posicion += bloque.Length

    return destino


def es_cache_valido(ruta_cache, ruta_nwd, caja=None):
    """Si el cache sigue sirviendo para ese NWD y esa zona.

    Invalidacion (seccion 26 del prompt maestro): cambia la fecha o el
    tamano del NWD, o se pide una zona distinta de la cacheada."""
    if not os.path.isfile(ruta_cache) or not os.path.isfile(ruta_nwd):
        return False, u"Falta el cache o el NWD."

    metadatos = ruta_cache + ".meta"

    if not os.path.isfile(metadatos):
        return False, u"El cache no tiene metadatos de validez."

    try:
        with open(metadatos, "r") as archivo:
            guardado = {}
            for linea in archivo.read().splitlines():
                if "=" in linea:
                    clave, valor = linea.split("=", 1)
                    guardado[clave.strip()] = valor.strip()
    except Exception as error:
        return False, u"No se pudieron leer los metadatos: {0}".format(error)

    try:
        if guardado.get("nwd", "").lower() != os.path.abspath(ruta_nwd).lower():
            return False, u"El cache es de otro NWD."

        if int(guardado.get("tamano", "-1")) != os.path.getsize(ruta_nwd):
            return False, u"El NWD ha cambiado de tamano."

        if abs(float(guardado.get("fecha", "-1")) -
               os.path.getmtime(ruta_nwd)) > 1.0:
            return False, u"El NWD se ha modificado."
    except Exception as error:
        return False, u"Metadatos ilegibles: {0}".format(error)

    if caja:
        try:
            guardada = [float(v) for v in guardado.get("caja", "").split(",")]
        except Exception:
            return False, u"El cache no registra la zona extraida."

        if len(guardada) != 4:
            return False, u"El cache no registra la zona extraida."

        # Solo sirve si la zona cacheada CONTIENE la pedida.
        if (caja[0] < guardada[0] - 1e-6 or caja[1] < guardada[1] - 1e-6 or
                caja[2] > guardada[2] + 1e-6 or caja[3] > guardada[3] + 1e-6):
            return False, u"La zona pedida no esta contenida en el cache."

    return True, u"Cache valido."


def escribir_metadatos(ruta_cache, ruta_nwd, caja=None):
    """Deja junto al cache lo necesario para poder invalidarlo despues."""
    try:
        lineas = [
            "nwd={0}".format(os.path.abspath(ruta_nwd)),
            "tamano={0}".format(os.path.getsize(ruta_nwd)),
            "fecha={0}".format(os.path.getmtime(ruta_nwd)),
        ]

        if caja:
            lineas.append("caja={0},{1},{2},{3}".format(*caja))

        with open(ruta_cache + ".meta", "w") as archivo:
            archivo.write("\n".join(lineas))

        return True
    except Exception:
        return False
