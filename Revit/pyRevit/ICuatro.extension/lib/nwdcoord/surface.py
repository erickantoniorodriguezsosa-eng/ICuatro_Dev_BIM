# -*- coding: utf-8 -*-
"""Superficie de consulta: dado un XY, que cotas hay.

Implementa la abstraccion SurfaceProvider de la seccion 33 del prompt
maestro, para que mas adelante se puedan anadir superficies de CAD, IFC,
topografia de Revit o Civil 3D sin tocar el analisis vertical.

La superficie NO se convierte en geometria de Revit: vive en memoria
solo mientras dura el analisis (seccion 10).
"""

from . import spatial_index


# Estrategias para elegir una cota cuando la vertical corta la superficie
# en varios sitios: terreno, puente, tunel, cubierta... (seccion 13).
MAS_ALTA = "mas_alta"
MAS_BAJA = "mas_baja"
MAS_BAJA_DEBAJO = "mas_baja_debajo"
MAS_CERCANA = "mas_cercana"
PRIMERA_DEBAJO = "primera_debajo"
PRIMERA_ENCIMA = "primera_encima"

ESTRATEGIAS = (MAS_ALTA, MAS_BAJA, MAS_BAJA_DEBAJO, MAS_CERCANA,
               PRIMERA_DEBAJO, PRIMERA_ENCIMA)

# Solo se usa cuando el punto no cae en ningun triangulo. Se prueban radios
# progresivos para cubrir grietas y separaciones entre teselas del corredor;
# nunca se recorre toda la malla ni se acepta una cota fuera de 8 m.
RADIOS_RESCATE_M = (0.75, 2.0, 4.0, 8.0)

# Una muestra de la huella que queda demasiado lejos del centro no debe
# presentarse como una interseccion exacta: puede pertenecer a otra cota
# local de un elemento grande.
DISTANCIA_HUELLA_APROXIMADA_M = 2.0

ETIQUETAS = {
    MAS_ALTA: u"La mas alta",
    MAS_BAJA: u"La mas baja",
    MAS_BAJA_DEBAJO: u"La mas baja por debajo del punto",
    MAS_CERCANA: u"La mas cercana al elemento",
    PRIMERA_DEBAJO: u"La primera por debajo del elemento",
    PRIMERA_ENCIMA: u"La primera por encima del elemento",
}


class ProveedorSuperficie(object):
    """Interfaz comun a toda fuente de superficie.

    Quien la use no debe saber si detras hay un NWD, un CAD o una
    topografia de Revit."""

    def cargar(self):
        raise NotImplementedError

    def caja(self):
        """(minimos, maximos) en metros y coordenadas de la fuente."""
        raise NotImplementedError

    def intersecciones(self, x, y):
        """Todas las cotas de la superficie en esa vertical, ordenadas."""
        raise NotImplementedError

    def altura(self, x, y, referencia=None, estrategia=MAS_ALTA):
        """Una sola cota, segun la estrategia. None si no hay superficie."""
        raise NotImplementedError

    def liberar(self):
        pass


class SuperficieNWD(ProveedorSuperficie):
    """Superficie construida con los triangulos extraidos del NWD.

    Trabaja en METROS y en coordenadas del NWD. La conversion a Revit es
    responsabilidad de transform.py: mezclarla aqui obligaria a arrastrar
    la transformacion hasta el bucle interno de la interpolacion."""

    def __init__(self, malla, tamano_celda=None, registro=None):
        self.malla = malla
        self.registro = registro
        self.rejilla = spatial_index.RejillaXY(malla, tamano_celda)
        self.cargada = False

        # Contadores para el informe de rendimiento.
        self.consultas = 0
        self.consultas_sin_superficie = 0
        self.consultas_aproximadas = 0
        self.ultima_aproximada = False
        self.ultima_distancia_xy_m = None
        self.ultimas_cotas = []
        self.ultimo_triangulo = None
        self._impactos = []

    # ------------------------------------------------------------------

    def cargar(self, progreso=None):
        if self.cargada:
            return self

        if self.registro:
            self.registro.empezar(u"indice espacial")

        self.rejilla.construir(progreso)
        self.cargada = True

        if self.registro:
            self.registro.terminar(
                u"indice espacial",
                u"{0} celdas de {1:.2f} m".format(
                    self.rejilla.celdas_ocupadas, self.rejilla.tamano_celda))

        return self

    def caja(self):
        return self.malla.caja()

    def liberar(self):
        self.rejilla.celdas = {}
        self.cargada = False

    # ------------------------------------------------------------------
    # CONSULTA
    # ------------------------------------------------------------------

    def intersecciones(self, x, y):
        """Cotas de la superficie en la vertical de (x, y).

        Para cada triangulo candidato se comprueba si el punto cae dentro
        en proyeccion XY, y si cae, se interpola su Z con las coordenadas
        baricentricas. Es exacto sobre la propia malla: no aproxima ni
        promedia vecinos."""
        vertices = self.malla.vertices
        triangulos = self.malla.triangulos

        alturas = []
        self._impactos = []

        for t in self.rejilla.candidatos(x, y):
            base = t * 3

            a = triangulos[base] * 3
            b = triangulos[base + 1] * 3
            c = triangulos[base + 2] * 3

            ax = vertices[a]
            ay = vertices[a + 1]
            bx = vertices[b]
            by = vertices[b + 1]
            cx = vertices[c]
            cy = vertices[c + 1]

            # Denominador baricentrico: dos veces el area con signo del
            # triangulo proyectado. Si es cero, el triangulo es vertical
            # o degenerado y no define ninguna cota en esta vertical.
            denominador = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)

            if -1e-12 < denominador < 1e-12:
                continue

            l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / denominador

            if l1 < -1e-9 or l1 > 1.0 + 1e-9:
                continue

            l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / denominador

            if l2 < -1e-9:
                continue

            l3 = 1.0 - l1 - l2

            if l3 < -1e-9:
                continue

            z = (l1 * vertices[a + 2] + l2 * vertices[b + 2] +
                 l3 * vertices[c + 2])
            alturas.append(z)
            self._impactos.append((z, t))

        if not alturas:
            return []

        # Cotas casi iguales son el mismo punto visto desde dos
        # triangulos que comparten arista: se colapsan a una.
        alturas.sort()
        unicas = [alturas[0]]

        for altura in alturas[1:]:
            if altura - unicas[-1] > 0.001:
                unicas.append(altura)

        return unicas

    def altura(self, x, y, referencia=None, estrategia=MAS_ALTA):
        """Una cota segun la estrategia, o None si no hay superficie."""
        self.consultas += 1
        self.ultima_aproximada = False
        self.ultima_distancia_xy_m = None

        self.ultimas_cotas = []
        self.ultimo_triangulo = None
        estricta = estrategia == MAS_BAJA_DEBAJO
        self._impactos = []
        if estricta and referencia is None:
            self.consultas_sin_superficie += 1
            return None

        # Evita consultas de rescate innecesarias cuando el punto esta
        # claramente fuera de la malla cargada.
        minimos, maximos = self.caja()
        radio_maximo = 0.0 if estricta else RADIOS_RESCATE_M[-1]
        if (x < minimos[0] - radio_maximo or
                x > maximos[0] + radio_maximo or
                y < minimos[1] - radio_maximo or
                y > maximos[1] + radio_maximo):
            self.consultas_sin_superficie += 1
            return None

        alturas = self.intersecciones(x, y)
        self.ultimas_cotas = alturas

        # Implementacion desarrollada con Claude: el criterio descendente
        # estricto solo acepta triangulos reales en el XY de insercion.
        # Prolongar el plano de un talud vecino no es una interseccion.
        if estricta:
            elegida = elegir(alturas, referencia, estrategia)
            if elegida is None:
                self.consultas_sin_superficie += 1
            else:
                self.ultimo_triangulo = next(
                    (t for z, t in self._impactos if abs(z - elegida) < 1e-9),
                    None)
            return elegida

        if alturas:
            return elegir(alturas, referencia, estrategia)

        rescate = None

        for radio in RADIOS_RESCATE_M:
            candidato = self._altura_cercana(x, y, radio)

            if candidato is None:
                continue

            # El rescate tambien representa una interseccion del rayo. No
            # aceptar una extrapolacion que quede sobre el punto de origen
            # cuando el criterio solicitado es estrictamente descendente.
            if (estrategia in (MAS_BAJA_DEBAJO, PRIMERA_DEBAJO) and
                    referencia is not None and
                    candidato[0] > referencia + 0.001):
                continue

            if (estrategia == PRIMERA_ENCIMA and
                    referencia is not None and
                    candidato[0] < referencia - 0.001):
                continue

            rescate = candidato

            if rescate is not None:
                break

        if rescate is not None:
            self.consultas_aproximadas += 1
            self.ultima_aproximada = True
            self.ultima_distancia_xy_m = rescate[1]
            return rescate[0]

        self.consultas_sin_superficie += 1
        return None

    def _altura_cercana(self, x, y, radio):
        """Devuelve (Z, distancia XY) del triangulo cercano.

        La distancia se mide contra el borde, pero la cota se calcula en el
        plano del triangulo para conservar su pendiente local. Usar solo la
        cota del borde producia un salto en superficies inclinadas cuando el
        XY caia en una grieta entre teselas.
        """
        vertices = self.malla.vertices
        triangulos = self.malla.triangulos
        mejor_distancia_2 = radio * radio
        mejor_altura = None

        for t in self.rejilla.candidatos_en_radio(x, y, radio):
            base = t * 3
            indices = (triangulos[base], triangulos[base + 1],
                       triangulos[base + 2])

            puntos = []
            for indice in indices:
                offset = indice * 3
                puntos.append((vertices[offset], vertices[offset + 1],
                               vertices[offset + 2]))

            distancia_2 = None

            for inicio, fin in ((puntos[0], puntos[1]),
                                (puntos[1], puntos[2]),
                                (puntos[2], puntos[0])):
                distancia_borde_2, _altura = _segmento_mas_cercano(
                    x, y, inicio, fin)

                if distancia_2 is None or distancia_borde_2 < distancia_2:
                    distancia_2 = distancia_borde_2

            if distancia_2 is not None and distancia_2 < mejor_distancia_2:
                mejor_distancia_2 = distancia_2
                mejor_altura = _altura_en_plano(x, y, puntos)

                if mejor_altura is None:
                    # Degeneracion defensiva: si el plano no se puede
                    # resolver, conserva la cota del borde mas cercano.
                    mejor_altura = min(
                        _segmento_mas_cercano(x, y, puntos[0], puntos[1]),
                        _segmento_mas_cercano(x, y, puntos[1], puntos[2]),
                        _segmento_mas_cercano(x, y, puntos[2], puntos[0]),
                        key=lambda valor: valor[0])[1]

        if mejor_altura is None:
            return None

        return mejor_altura, mejor_distancia_2 ** 0.5

    # ------------------------------------------------------------------

    def estadisticas(self):
        datos = self.rejilla.estadisticas()
        datos["consultas"] = self.consultas
        datos["aproximadas"] = self.consultas_aproximadas
        datos["sin_superficie"] = self.consultas_sin_superficie
        return datos


def _segmento_mas_cercano(x, y, inicio, fin):
    """Distancia XY y Z interpolada del punto mas cercano de un segmento."""
    ax, ay, az = inicio
    bx, by, bz = fin
    dx = bx - ax
    dy = by - ay
    denominador = dx * dx + dy * dy

    if denominador <= 1e-20:
        u = 0.0
    else:
        u = ((x - ax) * dx + (y - ay) * dy) / denominador
        u = max(0.0, min(1.0, u))

    px = ax + u * dx
    py = ay + u * dy
    pz = az + u * (bz - az)
    distancia_x = x - px
    distancia_y = y - py

    return distancia_x * distancia_x + distancia_y * distancia_y, pz


def _altura_en_plano(x, y, puntos):
    """Interpela Z en el plano XY del triangulo, incluso fuera del borde."""
    a, b, c = puntos
    ax, ay, az = a
    bx, by, bz = b
    cx, cy, cz = c

    denominador = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)

    if -1e-12 < denominador < 1e-12:
        return None

    l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / denominador
    l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / denominador
    l3 = 1.0 - l1 - l2

    return l1 * az + l2 * bz + l3 * cz


def elegir(alturas, referencia=None, estrategia=MAS_ALTA):
    """Elige una cota de entre las que corta la vertical.

    'alturas' llega ordenada de menor a mayor. 'referencia' es la cota
    del elemento de Revit, necesaria para las estrategias relativas: sin
    ella se cae a la mas alta, que es el criterio util para terreno."""
    if not alturas:
        return None

    if estrategia == MAS_BAJA:
        return alturas[0]

    if estrategia == MAS_BAJA_DEBAJO:
        if referencia is None:
            return None

        debajo = [altura for altura in alturas
                  if altura <= referencia + 0.001]
        return min(debajo) if debajo else None

    if estrategia == MAS_ALTA or referencia is None:
        return alturas[-1]

    if estrategia == MAS_CERCANA:
        mejor = alturas[0]
        distancia = abs(mejor - referencia)

        for altura in alturas[1:]:
            candidata = abs(altura - referencia)
            if candidata < distancia:
                mejor = altura
                distancia = candidata

        return mejor

    if estrategia == PRIMERA_DEBAJO:
        debajo = [a for a in alturas if a <= referencia]
        return debajo[-1] if debajo else None

    if estrategia == PRIMERA_ENCIMA:
        encima = [a for a in alturas if a >= referencia]
        return encima[0] if encima else None

    return alturas[-1]
