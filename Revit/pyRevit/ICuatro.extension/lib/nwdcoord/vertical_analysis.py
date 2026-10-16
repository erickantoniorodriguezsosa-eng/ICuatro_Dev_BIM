# -*- coding: utf-8 -*-
"""Analisis vertical: distancia de cada elemento a la superficie del NWD.

Secciones 17 a 19, 22 y 46 del prompt maestro. Este modulo NO modifica
nada: solo mide y clasifica. La separacion entre ANALIZAR y APLICAR es
un requisito explicito (seccion 23) y aqui se respeta por construccion,
porque no se abre ninguna transaccion.

CONVENIO DE SIGNOS (seccion 17)

    DZ = Z_objetivo - Z_elemento

    DZ negativo -> hay que BAJAR el elemento
    DZ positivo -> hay que SUBIR el elemento

    Z_objetivo = Z_superficie + offset
"""

from . import revit_elements
from . import surface as superficie_mod
from . import units
from . import slope


# Estados posibles (seccion 18).
OK = "ok"
SUBIR = "subir"
BAJAR = "bajar"
PENDIENTE = "pendiente"
SIN_SUPERFICIE = "sin_superficie"
FUERA_DE_RANGO = "fuera_de_rango"
ERROR = "error"

ETIQUETAS_ESTADO = {
    OK: u"OK",
    SUBIR: u"Subir",
    BAJAR: u"Bajar",
    PENDIENTE: u"Adaptar pendiente",
    SIN_SUPERFICIE: u"Sin superficie",
    FUERA_DE_RANGO: u"Fuera de rango",
    ERROR: u"Error",
}

# Un salto mayor que esto casi siempre significa que el elemento no
# pertenece a esa zona del terreno, no que haya que moverlo 200 m.
LIMITE_RAZONABLE_M = 200.0
DISTANCIA_HUELLA_APROXIMADA_M = \
    superficie_mod.DISTANCIA_HUELLA_APROXIMADA_M


class ResultadoCoordinacion(object):
    """Todo lo que hay que saber de un elemento analizado (seccion 46).

    Se conservan las cotas en METROS ademas de en pies porque es lo que
    el usuario lee en la tabla, y recalcularlas en cada refresco de la
    interfaz seria absurdo."""

    def __init__(self, elemento):
        self.elemento = elemento
        self.element_id = None
        self.unique_id = None

        self.categoria = u"—"
        self.familia = u"—"
        self.tipo = u"—"

        self.punto = None             # PuntoReferencia, en pies
        self.z_original_m = None      # cota actual del elemento
        self.z_superficie_m = None    # cota del terreno bajo el
        self.offset_m = 0.0
        self.z_objetivo_m = None
        self.delta_m = None           # DZ

        self.estado = ERROR
        self.movible = False
        self.motivo_inmovil = u""
        self.error = u""
        self.aplicado = False
        self.superficie_aproximada = False
        self.distancia_superficie_m = None
        self.distancia_huella_m = None
        self.modo_referencia = None
        self.pendiente = None
        self.nota_pendiente = u""

        try:
            self.element_id = elemento.Id
            self.unique_id = elemento.UniqueId
        except Exception:
            pass

    # ------------------------------------------------------------------

    @property
    def id_valor(self):
        try:
            return self.element_id.IntegerValue
        except Exception:
            return 0

    @property
    def delta_pies(self):
        """El desplazamiento en la unidad interna de Revit."""
        if self.delta_m is None:
            return 0.0

        return units.metros_a_pies(self.delta_m)

    @property
    def etiqueta_estado(self):
        return ETIQUETAS_ESTADO.get(self.estado, self.estado)

    def necesita_movimiento(self):
        return self.estado in (SUBIR, BAJAR, PENDIENTE)

    def __repr__(self):
        return "<Resultado {0} {1} DZ={2}>".format(
            self.id_valor, self.estado,
            u"—" if self.delta_m is None else "{0:.3f}".format(self.delta_m))


class OpcionesAnalisis(object):
    """Parametros del analisis, agrupados para no arrastrar ocho argumentos."""

    def __init__(self):
        # La posicion usada para colocar la familia o curva es la referencia
        # mas estable para intersectar la superficie y calcular el DZ.
        self.modo_referencia = revit_elements.INSERCION
        # El rayo parte del punto de insercion y desciende hasta la cota mas
        # baja disponible debajo de el, para nivelar contra terreno irregular.
        self.estrategia = superficie_mod.MAS_BAJA_DEBAJO
        self.offset_m = 0.0                 # seccion 22
        self.tolerancia_m = 0.02            # seccion 19
        self.limite_razonable_m = LIMITE_RAZONABLE_M
        self.detectar_pendiente = False


def analizar(DB, doc, elementos, superficie, transformacion,
             opciones=None, registro=None, progreso=None):
    """Analiza los elementos contra la superficie. No modifica nada.

    Devuelve una lista de ResultadoCoordinacion en el mismo orden en que
    llegaron los elementos."""
    opciones = opciones or OpcionesAnalisis()
    resultados = []

    total = len(elementos)

    if registro:
        registro.empezar(u"analisis vertical")

    for indice, elemento in enumerate(elementos):
        if progreso is not None:
            progreso(indice + 1, total)

        resultados.append(_analizar_uno(
            DB, doc, elemento, superficie, transformacion, opciones, registro))

    if registro:
        registro.terminar(
            u"analisis vertical", u"{0} elementos".format(total))

    return resultados


def _analizar_uno(DB, doc, elemento, superficie, transformacion,
                  opciones, registro):
    resultado = ResultadoCoordinacion(elemento)
    resultado.modo_referencia = opciones.modo_referencia

    try:
        resultado.categoria, resultado.familia, resultado.tipo = \
            revit_elements.describir(DB, doc, elemento)
    except Exception:
        pass

    try:
        resultado.movible, resultado.motivo_inmovil = \
            revit_elements.comprobar_movilidad(DB, doc, elemento)
    except Exception as error:
        resultado.movible = False
        resultado.motivo_inmovil = u"{0}".format(error)

    # --- punto de referencia ---
    try:
        punto = revit_elements.resolver_punto(
            doc, elemento, opciones.modo_referencia)
    except Exception as error:
        if registro:
            registro.excepcion(u"Punto de referencia", error, resultado.id_valor)
        resultado.error = u"No se pudo situar el elemento: {0}".format(error)
        resultado.estado = ERROR
        return resultado

    if punto is None:
        resultado.error = u"El elemento no tiene ni caja envolvente ni " \
                          u"punto de insercion."
        if opciones.modo_referencia == revit_elements.INSERCION:
            resultado.error = u"El elemento no expone un punto de insercion."
        resultado.estado = ERROR
        return resultado

    resultado.punto = punto

    # --- cota del terreno bajo el elemento ---
    try:
        x_nwd, y_nwd, z_nwd = transformacion.revit_a_nwd(
            punto.x, punto.y, punto.z)

        resultado.z_original_m = z_nwd

        if registro:
            registro.info(
                u"Consulta superficie ElementId={0}: "
                u"punto_nwd=({1:.3f},{2:.3f},{3:.3f}) m, "
                u"origen={4}, caja_malla={5}",
                resultado.id_valor, x_nwd, y_nwd, z_nwd,
                punto.origen, superficie.caja())

        puntos = revit_elements.puntos_para_superficie(
            doc, elemento, punto, opciones.modo_referencia)
        candidatos = []

        for indice, prueba in enumerate(puntos):
            px_nwd, py_nwd, pz_nwd = transformacion.revit_a_nwd(
                prueba[0], prueba[1], prueba[2])
            candidata = superficie.altura(
                px_nwd, py_nwd, pz_nwd, opciones.estrategia)

            if registro:
                triangulo = getattr(superficie, "ultimo_triangulo", None)
                item = (superficie.malla.item_de(triangulo)
                        if triangulo is not None else None)
                registro.info(
                    u"Rayo XY=({0:.6f},{1:.6f}) desde Z={2:.6f}: "
                    u"estrategia={3}, cotas={4}, elegida={5}, item={6}",
                    px_nwd, py_nwd, pz_nwd, opciones.estrategia,
                    getattr(superficie, "ultimas_cotas", []), candidata,
                    getattr(item, "ruta", u"(sin impacto)"))

            if candidata is None:
                continue

            aproximada = bool(getattr(superficie, "ultima_aproximada", False))
            distancia = getattr(superficie, "ultima_distancia_xy_m", None)
            dx = px_nwd - x_nwd
            dy = py_nwd - y_nwd
            distancia_punto = (dx * dx + dy * dy) ** 0.5

            # Se prefiere una interseccion exacta y, entre ellas, la mas
            # cercana al centro. El rescate aproximado solo gana si no hay
            # ningun punto exacto en la huella.
            candidatos.append((
                1 if aproximada else 0,
                distancia_punto,
                indice,
                candidata,
                aproximada,
                distancia))

        if candidatos:
            # El centro representa la posicion del elemento. Si su rescate
            # esta cerca, conservarlo respeta la forma local de la superficie
            # mejor que elegir una esquina exacta con otra cota. Las muestras
            # de la huella solo sustituyen al centro cuando este esta fuera
            # de la malla o demasiado lejos.
            centro_cercano = [
                valor for valor in candidatos
                if valor[2] == 0 and
                (not valor[4] or
                 (valor[5] is not None and
                  valor[5] <= DISTANCIA_HUELLA_APROXIMADA_M))]

            if centro_cercano:
                centro_cercano.sort(
                    key=lambda valor: (valor[0], valor[1], valor[2]))
                elegido = centro_cercano[0]
            else:
                candidatos.sort(
                    key=lambda valor: (valor[0], valor[1], valor[2]))
                elegido = candidatos[0]

            _aproximacion, _distancia, _indice, cota, aproximada, distancia = \
                elegido
            resultado.superficie_aproximada = aproximada
            resultado.distancia_superficie_m = distancia
            resultado.distancia_huella_m = _distancia

            # El punto interior que resolvio la cota puede ser una esquina
            # de una caja grande. Aunque la vertical haya cortado un
            # triangulo, se informa como aproximacion si quedo lejos del
            # centro del elemento y no se permite aplicarlo por defecto.
            if (_distancia > DISTANCIA_HUELLA_APROXIMADA_M and
                    not resultado.superficie_aproximada):
                resultado.superficie_aproximada = True
        else:
            cota = None

        if registro:
            registro.info(
                u"Puntos de huella consultados: {0}; validos={1}; "
                u"seleccionado={2}",
                len(puntos), len(candidatos),
                u"ninguno" if not candidatos else _indice)
    except Exception as error:
        if registro:
            registro.excepcion(u"Consulta de superficie", error,
                               resultado.id_valor)
        resultado.error = u"Fallo la consulta de superficie: {0}".format(error)
        resultado.estado = ERROR
        return resultado

    if cota is None:
        if registro:
            registro.aviso(
                u"Sin superficie en ElementId={0}: el punto NWD "
                u"no intersecta ningun triangulo de la malla cargada.",
                resultado.id_valor)
        resultado.estado = SIN_SUPERFICIE
        return resultado

    # --- DZ ---
    resultado.z_superficie_m = cota
    resultado.offset_m = opciones.offset_m
    resultado.z_objetivo_m = cota + opciones.offset_m
    resultado.delta_m = resultado.z_objetivo_m - resultado.z_original_m

    if abs(resultado.delta_m) > opciones.limite_razonable_m:
        resultado.estado = FUERA_DE_RANGO
        resultado.error = (
            u"Salto de {0:.1f} m: revisa que el elemento pertenezca a esta "
            u"zona del terreno.".format(resultado.delta_m))
        return resultado

    if abs(resultado.delta_m) <= opciones.tolerancia_m:
        resultado.estado = OK
    elif resultado.delta_m > 0:
        resultado.estado = SUBIR
    else:
        resultado.estado = BAJAR

    if opciones.detectar_pendiente:
        try:
            resultado.pendiente, resultado.nota_pendiente = slope.evaluar(
                DB, doc, resultado, superficie, transformacion, opciones)
            if resultado.pendiente is not None and resultado.estado == OK:
                resultado.estado = PENDIENTE
            if registro:
                registro.info(u"Perfil {0}: {1}", resultado.id_valor,
                              resultado.nota_pendiente)
        except Exception as error:
            # La mejora opcional no invalida una nivelacion vertical ya valida.
            resultado.nota_pendiente = u"No se pudo evaluar pendiente: {0}".format(error)

    return resultado


# ==========================================================================
# RESUMEN
# ==========================================================================

def resumir(resultados):
    """Conteo por estado y estadisticas de DZ, para la cabecera de la UI."""
    resumen = {
        "total": len(resultados),
        "por_estado": {},
        "movibles": 0,
        "bloqueados": 0,
        "aproximadas": 0,
        "delta_maximo": 0.0,
        "delta_medio": 0.0,
        "a_mover": 0,
        "pendientes_propuestas": 0,
    }

    deltas = []

    for resultado in resultados:
        clave = resultado.estado
        if resultado.pendiente is not None:
            resumen["pendientes_propuestas"] += 1
        resumen["por_estado"][clave] = resumen["por_estado"].get(clave, 0) + 1

        if getattr(resultado, "superficie_aproximada", False):
            resumen["aproximadas"] += 1

        if resultado.necesita_movimiento():
            resumen["a_mover"] += 1

            if resultado.movible:
                resumen["movibles"] += 1
            else:
                resumen["bloqueados"] += 1

        if resultado.delta_m is not None:
            deltas.append(resultado.delta_m)

    if deltas:
        resumen["delta_maximo"] = max(deltas, key=abs)
        resumen["delta_medio"] = sum(deltas) / len(deltas)

    return resumen


def texto_resumen(resumen):
    """El resumen en una linea, para la barra de estado."""
    partes = []

    for estado in (OK, SUBIR, BAJAR, PENDIENTE, SIN_SUPERFICIE, FUERA_DE_RANGO, ERROR):
        cuenta = resumen["por_estado"].get(estado, 0)

        if cuenta:
            partes.append(u"{0}: {1}".format(ETIQUETAS_ESTADO[estado], cuenta))

    linea = u"{0} elemento(s). {1}".format(
        resumen["total"], u" · ".join(partes) if partes else u"sin resultados")

    if resumen["bloqueados"]:
        linea += u" · {0} no se pueden mover".format(resumen["bloqueados"])

    if resumen["aproximadas"]:
        linea += u" · {0} con superficie aproximada".format(
            resumen["aproximadas"])

    if resumen["pendientes_propuestas"]:
        linea += u" · {0} pendiente(s) propuesta(s): se preguntara al aplicar".format(
            resumen["pendientes_propuestas"])

    return linea
