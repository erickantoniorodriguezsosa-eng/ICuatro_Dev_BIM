# -*- coding: utf-8 -*-
"""Indice espacial XY sobre la malla del NWD.

Sin indice, resolver la cota de un punto obliga a recorrer los ~900.000
triangulos de la zona; con mil elementos serian 900 millones de pruebas
(seccion 11 del prompt maestro: "NO recorrer todos los triangulos para
cada elemento Revit").

Se usa una REJILLA XY uniforme, que es la preferencia que marca el
prompt. Frente a un quadtree o un BVH tiene tres ventajas que aqui
pesan mas que su teorica peor adaptacion a densidades irregulares:

  - se construye en una sola pasada, sin recursion;
  - la consulta es aritmetica pura, sin recorrer arboles;
  - la topografia reparte los triangulos de forma bastante uniforme,
    que es justo el caso en que una rejilla se comporta bien.

Solo se indexa en XY: la consulta es siempre "que hay en esta vertical",
asi que la Z no discrimina nada y anadirla multiplicaria las celdas.
"""

import math


class RejillaXY(object):
    """Rejilla uniforme que reparte los triangulos por celdas XY.

    Un triangulo se registra en TODAS las celdas que toca su caja
    envolvente, no solo en la de su centro: si no, un triangulo grande
    desaparecería al consultar por sus bordes."""

    def __init__(self, malla, tamano_celda=None):
        self.malla = malla
        self.celdas = {}
        self.tamano_celda = tamano_celda or self._tamano_recomendado(malla)
        self.construida = False
        self.max_por_celda = 0
        self.celdas_ocupadas = 0

    # ------------------------------------------------------------------

    def _tamano_recomendado(self, malla):
        """Lado de celda para que caigan unos pocos triangulos en cada una.

        Se parte del area por triangulo: si la celda mide como cuatro
        triangulos de lado, cada consulta revisa un punado y no cientos.
        Los limites evitan los dos extremos malos: celdas tan pequenas
        que la rejilla ocupe mas que la malla, o tan grandes que no
        filtren nada."""
        if malla.num_triangulos == 0:
            return 10.0

        ancho = max(malla.maximos[0] - malla.minimos[0], 1.0)
        fondo = max(malla.maximos[1] - malla.minimos[1], 1.0)

        area_por_triangulo = (ancho * fondo) / malla.num_triangulos
        lado = math.sqrt(area_por_triangulo) * 4.0

        return min(max(lado, 0.5), 50.0)

    # ------------------------------------------------------------------

    def construir(self, progreso=None):
        """Reparte todos los triangulos por celdas.

        El bucle esta escrito plano a proposito: es el punto mas caliente
        del modulo y cada llamada a funcion dentro de el se paga un
        millon de veces."""
        malla = self.malla
        vertices = malla.vertices
        triangulos = malla.triangulos
        celdas = self.celdas
        lado = self.tamano_celda

        total = malla.num_triangulos
        aviso_cada = max(total // 20, 1)

        for t in range(total):
            base = t * 3

            a = triangulos[base] * 3
            b = triangulos[base + 1] * 3
            c = triangulos[base + 2] * 3

            ax = vertices[a]
            bx = vertices[b]
            cx = vertices[c]
            ay = vertices[a + 1]
            by = vertices[b + 1]
            cy = vertices[c + 1]

            min_x = ax if ax < bx else bx
            if cx < min_x:
                min_x = cx
            max_x = ax if ax > bx else bx
            if cx > max_x:
                max_x = cx

            min_y = ay if ay < by else by
            if cy < min_y:
                min_y = cy
            max_y = ay if ay > by else by
            if cy > max_y:
                max_y = cy

            for celda_x in range(int(math.floor(min_x / lado)),
                                 int(math.floor(max_x / lado)) + 1):
                for celda_y in range(int(math.floor(min_y / lado)),
                                     int(math.floor(max_y / lado)) + 1):
                    clave = (celda_x, celda_y)

                    if clave in celdas:
                        celdas[clave].append(t)
                    else:
                        celdas[clave] = [t]

            if progreso is not None and t % aviso_cada == 0:
                progreso(t, total)

        self.celdas_ocupadas = len(celdas)

        # Sin default=, que IronPython 2.7 no admite en max().
        ocupaciones = [len(v) for v in celdas.values()]
        self.max_por_celda = max(ocupaciones) if ocupaciones else 0

        self.construida = True

        return self

    # ------------------------------------------------------------------

    def candidatos(self, x, y):
        """Indices de los triangulos que podrian cubrir el punto (x, y)."""
        clave = (int(math.floor(x / self.tamano_celda)),
                 int(math.floor(y / self.tamano_celda)))

        return self.celdas.get(clave, ())

    def candidatos_en_radio(self, x, y, radio):
        """Triangulos cercanos a (x, y), sin recorrer toda la malla.

        Se usa unicamente como rescate cuando la consulta exacta no
        encontro superficie. El conjunto puede contener duplicados porque
        un triangulo puede ocupar varias celdas; se eliminan aqui para no
        repetir calculos geometricos.
        """
        lado = self.tamano_celda
        centro_x = int(math.floor(x / lado))
        centro_y = int(math.floor(y / lado))
        paso = int(math.ceil(max(radio, 0.0) / lado))

        vistos = set()
        salida = []

        for celda_x in range(centro_x - paso, centro_x + paso + 1):
            for celda_y in range(centro_y - paso, centro_y + paso + 1):
                for triangulo in self.celdas.get((celda_x, celda_y), ()):
                    if triangulo not in vistos:
                        vistos.add(triangulo)
                        salida.append(triangulo)

        return salida

    def estadisticas(self):
        if not self.celdas:
            return {}

        ocupaciones = [len(v) for v in self.celdas.values()]

        return {
            "tamano_celda": self.tamano_celda,
            "celdas": len(self.celdas),
            "triangulos": self.malla.num_triangulos,
            "media_por_celda": float(sum(ocupaciones)) / len(ocupaciones),
            "maximo_por_celda": max(ocupaciones),
        }

    def __repr__(self):
        return "<RejillaXY celda={0:.2f} m, {1} celdas>".format(
            self.tamano_celda, len(self.celdas))
