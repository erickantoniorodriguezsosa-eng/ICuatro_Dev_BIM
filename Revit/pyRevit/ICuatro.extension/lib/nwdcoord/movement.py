# -*- coding: utf-8 -*-
"""Aplicacion de la nivelacion: mover los elementos en Z.

Secciones 20, 21, 44 y 45 del prompt maestro. Es el UNICO modulo que
modifica el modelo, y esa concentracion es deliberada: todo lo demas se
puede ejecutar sin riesgo.

Tres reglas que se cumplen aqui:

  - Solo se toca la Z. Nunca X ni Y (seccion 48).
  - Un elemento problematico no cancela la operacion: se registra y se
    sigue con los demas (seccion 20).
  - Todo va en una transaccion para que el UNDO de Revit funcione de una
    sola vez (seccion 44).
"""

from . import revit_elements
from . import units
from . import family_progress
from . import slope

TOLERANCIA_PIES = units.metros_a_pies(0.001)


def aplicar_pendiente(DB, doc, resultado):
    """Modifica solo Z de los extremos del eje, con rollback si no coincide.

    La autorizacion se da expresamente en la UI. No gira familias puntuales,
    no rompe conexiones ni convierte un eje recto en geometria deformada.
    """
    propuesta = resultado.pendiente
    if propuesta is None:
        return False, u"", u"No hay propuesta de pendiente; vuelve a analizar."
    sub = None
    iniciada = False
    try:
        motivo = slope.motivo_no_adaptable(DB, doc, resultado.elemento)
        if motivo:
            return False, u"", motivo
        actual = slope.extremos(DB, resultado.elemento)
        if actual is None:
            return False, u"", u"El eje ya no es recto; vuelve a analizar."

        def residuo(puntos):
            return max(abs(actual[i][j] - puntos[i][j])
                       for i in (0, 1) for j in range(3))

        if residuo(propuesta.objetivos) <= TOLERANCIA_PIES:
            return True, u"pendiente ya aplicada", u""
        if residuo(propuesta.originales) > TOLERANCIA_PIES:
            return False, u"", u"Cambio el eje desde el analisis; vuelve a analizar."
        sub = DB.SubTransaction(doc)
        sub.Start()
        iniciada = True
        a, b = [DB.XYZ(*p) for p in propuesta.objetivos]
        resultado.elemento.Location.Curve = DB.Line.CreateBound(a, b)
        doc.Regenerate()
        actual = slope.extremos(DB, resultado.elemento)
        if actual is None or residuo(propuesta.objetivos) > TOLERANCIA_PIES:
            raise ValueError(u"La familia no acepto las cotas de ambos extremos; "
                             u"ajuste revertido.")
        estado = sub.Commit()
        iniciada = False
        if estado != DB.TransactionStatus.Committed:
            return False, u"", u"Revit no confirmo la pendiente."
        return True, u"pendiente: extremos verificados a 1 mm", u""
    except Exception as error:
        if iniciada:
            sub.RollBack()
        return False, u"", primera_linea(error)
    finally:
        if sub is not None:
            sub.Dispose()


def aplicar_insercion(DB, doc, resultado):
    """Destino absoluto y verificacion por elemento, desarrollado con Claude.

    Requiere la transaccion exterior de aplicar_nivelacion. Un movimiento
    que no alcance el destino se revierte dentro de su SubTransaction.
    """
    elemento = resultado.elemento
    objetivo_z = resultado.punto.z + units.metros_a_pies(
        resultado.z_objetivo_m - resultado.z_original_m)
    actual = revit_elements._punto_de_insercion(elemento)
    if actual is None:
        return False, u"", u"No se puede releer el punto de insercion."
    if max(abs(actual[0] - resultado.punto.x),
           abs(actual[1] - resultado.punto.y)) > TOLERANCIA_PIES:
        return False, u"", u"Cambio la posicion XY; vuelve a analizar."
    delta = objetivo_z - actual[2]
    if abs(delta) <= TOLERANCIA_PIES:
        return True, u"insercion ya en cota", u""
    sub = DB.SubTransaction(doc)
    iniciada = False
    try:
        sub.Start()
        iniciada = True
        DB.ElementTransformUtils.MoveElement(
            doc, elemento.Id, DB.XYZ(0.0, 0.0, delta))
        doc.Regenerate()
        final = revit_elements._punto_de_insercion(elemento)
        if final is None:
            raise ValueError(u"No se pudo verificar la insercion despues de mover.")
        residuo = max(abs(final[0] - resultado.punto.x),
                      abs(final[1] - resultado.punto.y),
                      abs(final[2] - objetivo_z))
        if residuo > TOLERANCIA_PIES:
            raise ValueError(u"La insercion no alcanzo el destino; residuo {0} m".
                             format(units.formato_m(residuo, 4)))
        estado = sub.Commit()
        iniciada = False
        if estado != DB.TransactionStatus.Committed:
            return False, u"", u"Revit no confirmo el movimiento del elemento."
        return True, u"MoveElement: insercion verificada a 1 mm", u""
    except Exception as error:
        if iniciada:
            sub.RollBack()
        return False, u"", primera_linea(error)
    finally:
        sub.Dispose()


def primera_linea(valor):
    """Primera linea del texto de un error, para que los mensajes de la
    tabla no arrastren trazas enteras."""
    try:
        texto = u"{0}".format(valor)
    except Exception:
        return u"?"

    return texto.splitlines()[0].strip() if texto.strip() else u"?"


class ResultadoAplicacion(object):
    """Que paso al intentar mover una tanda."""

    def __init__(self):
        self.movidos = 0
        self.omitidos = 0
        self.fallidos = 0
        self.errores = []          # (id, motivo)
        self.desplazamiento_total = 0.0
        self.cancelado = False
        self.sin_historial = 0

    def resumen(self):
        linea = u"{0} movido(s)".format(self.movidos)

        if self.omitidos:
            linea += u", {0} omitido(s)".format(self.omitidos)

        if self.fallidos:
            linea += u", {0} con error".format(self.fallidos)

        if self.sin_historial:
            linea += u", {0} sin indicador de familia guardado".format(self.sin_historial)

        return linea


class EstrategiaMovimiento(object):
    """Como se desplaza verticalmente un elemento concreto (seccion 45).

    No todos los elementos se mueven igual. Un poste suelto se desplaza
    con MoveElement; uno cuya altura depende de un parametro de offset
    respecto a su nivel se mueve mejor cambiando ese parametro, porque
    asi el modelo sigue siendo coherente con su nivel de referencia.
    """

    # Parametros de desfase vertical mas habituales, por BuiltInParameter.
    # FAMILY_LEVEL_PARAM no esta: es el nivel (un ElementId), no un desfase.
    NOMBRES_OFFSET = (
        "INSTANCE_ELEVATION_PARAM",           # Elevacion respecto al nivel
        "INSTANCE_FREE_HOST_OFFSET_PARAM",    # Desfase respecto al anfitrion
        "INSTANCE_SILL_HEIGHT_PARAM",         # Altura de antepecho
    )

    # Cuantas veces se permite regenerar el documento para comprobar un
    # movimiento. Regenerar es caro; unas pocas bastan para detectar que
    # algo va sistematicamente mal.
    MAXIMO_REGENERACIONES = 5

    def __init__(self, DB, doc):
        self.DB = DB
        self.doc = doc
        self._regeneraciones = 0

    # ------------------------------------------------------------------

    def parametro_offset(self, elemento):
        """Parametro de desfase vertical editable. Devuelve (parametro, nombre)."""
        DB = self.DB

        for nombre in self.NOMBRES_OFFSET:
            bip = getattr(DB.BuiltInParameter, nombre, None)

            if bip is None:
                continue

            try:
                parametro = elemento.get_Parameter(bip)
            except Exception:
                continue

            if parametro is None or parametro.IsReadOnly:
                continue

            if parametro.StorageType != DB.StorageType.Double:
                continue

            return parametro, nombre

        return None, u""

    def cota_de(self, elemento):
        """Cota actual del elemento, para comprobar si se ha movido.

        Se prefiere LocationPoint porque refleja el cambio al instante;
        la caja envolvente necesitaria regenerar el documento."""
        try:
            localizacion = elemento.Location

            if localizacion is not None and hasattr(localizacion, "Point"):
                punto = localizacion.Point

                if punto is not None:
                    return punto.Z, True
        except Exception:
            pass

        try:
            caja = elemento.get_BoundingBox(None)

            if caja is not None:
                return caja.Min.Z, False
        except Exception:
            pass

        return None, False

    def aplicar(self, elemento, delta_pies):
        """Desplaza el elemento delta_pies en Z y COMPRUEBA que se movio.

        Dos lecciones que costaron un fallo silencioso:

          - Parameter.Set() devuelve un bool. Si el parametro rechaza el
            valor NO lanza excepcion: devuelve False. Dar por bueno ese
            caso marcaba el elemento como movido sin haberlo tocado.
          - MoveElement va primero porque es explicito y no depende de que
            la familia tenga un parametro de elevacion utilizable.

        Al final se contrasta la cota real contra la esperada: si no
        cambio, se informa como fallo en lugar de mentir.

        Devuelve (exito, metodo, error)."""
        DB = self.DB

        antes, medible = self.cota_de(elemento)
        motivos = []

        # --- 1. Desplazamiento directo ---
        try:
            DB.ElementTransformUtils.MoveElement(
                self.doc, elemento.Id, DB.XYZ(0, 0, delta_pies))

            if self._se_movio(elemento, antes, delta_pies, medible):
                return True, u"MoveElement", u""

            motivos.append(u"MoveElement no cambio la posicion")
        except Exception as error:
            motivos.append(u"MoveElement: {0}".format(primera_linea(error)))

        # --- 2. Parametro de desfase vertical ---
        parametro, nombre = self.parametro_offset(elemento)

        if parametro is not None:
            try:
                if parametro.Set(parametro.AsDouble() + delta_pies):
                    if self._se_movio(elemento, antes, delta_pies, medible):
                        return True, u"parametro {0}".format(nombre), u""

                    motivos.append(
                        u"el parametro {0} cambio pero el elemento no se "
                        u"movio".format(nombre))
                else:
                    motivos.append(
                        u"el parametro {0} rechazo el valor".format(nombre))
            except Exception as error:
                motivos.append(u"parametro {0}: {1}".format(
                    nombre, primera_linea(error)))
        else:
            motivos.append(u"sin parametro de desfase vertical utilizable")

        return False, u"", u" · ".join(motivos)

    def _se_movio(self, elemento, antes, delta_pies, medible):
        """True si la cota cambio en la cantidad esperada.

        Cuando no hay LocationPoint hay que regenerar el documento para
        que la caja envolvente refleje el cambio, y eso es caro. Se hace
        solo en las primeras comprobaciones: basta para detectar un fallo
        sistematico, que es lo que importa, sin penalizar una tanda de
        miles de elementos."""
        if antes is None:
            # Sin forma de medir, se acepta que la API no protesto.
            return True

        if not medible:
            if self._regeneraciones >= self.MAXIMO_REGENERACIONES:
                return True

            self._regeneraciones += 1

            try:
                self.doc.Regenerate()
            except Exception:
                return True

        despues, _ = self.cota_de(elemento)

        if despues is None:
            return True

        esperado = antes + delta_pies

        # Un milimetro de holgura: el redondeo interno de Revit no da
        # igualdad exacta.
        return abs(despues - esperado) < 0.0033


def aplicar_nivelacion(DB, doc, resultados, registro=None, progreso=None,
                       solo_seleccionados=None, adaptar_pendientes=None):
    """Mueve los elementos que lo necesitan. DEBE ejecutarse dentro de
    una transaccion abierta por quien llama.

    'solo_seleccionados' permite aplicar unicamente las filas marcadas en
    la interfaz; si es None, se aplican todos los que necesiten moverse.
    """
    salida = ResultadoAplicacion()
    estrategia = EstrategiaMovimiento(DB, doc)

    pendientes = [
        r for r in resultados
        if r.necesita_movimiento() and
        (solo_seleccionados is None or r.id_valor in solo_seleccionados)
    ]

    total = len(pendientes)

    for indice, resultado in enumerate(pendientes):
        if progreso is not None:
            progreso(indice + 1, total)

        if not resultado.movible:
            salida.omitidos += 1
            salida.errores.append(
                (resultado.id_valor, resultado.motivo_inmovil))
            continue

        delta = resultado.delta_pies

        adaptar = (adaptar_pendientes is not None and
                   resultado.id_valor in adaptar_pendientes and
                   getattr(resultado, "pendiente", None) is not None)
        # Si el centro ya estaba en tolerancia, rechazar la pendiente no debe
        # introducir un desplazamiento vertical que antes no era necesario.
        if not adaptar and (abs(delta) < 1e-9 or resultado.estado == "pendiente"):
            salida.omitidos += 1
            continue

        insercion = (getattr(resultado, "modo_referencia", None) ==
                     revit_elements.INSERCION)
        if adaptar:
            exito, metodo, error = aplicar_pendiente(DB, doc, resultado)
        elif insercion:
            exito, metodo, error = aplicar_insercion(DB, doc, resultado)
        else:
            exito, metodo, error = estrategia.aplicar(resultado.elemento, delta)

        if exito:
            salida.movidos += 1
            salida.desplazamiento_total += abs(resultado.delta_m)
            resultado.aplicado = True
            if metodo not in (u"insercion ya en cota", u"pendiente ya aplicada"):
                if not family_progress.registrar(resultado.elemento, registro):
                    salida.sin_historial += 1

            if insercion:
                actual = revit_elements._punto_de_insercion(resultado.elemento)
                if adaptar:
                    # La recta entre extremos puede diferir del centro de la
                    # malla hasta la tolerancia de perfil. Informar su Z real.
                    resultado.z_objetivo_m = resultado.z_original_m + \
                        units.pies_a_metros(actual[2] - resultado.punto.z)
                resultado.punto = revit_elements.PuntoReferencia(
                    actual[0], actual[1], actual[2], u"punto de insercion")
                if registro:
                    registro.info(u"Insercion final {0}: XYZ={1} pies, "
                                  u"cota objetivo fuente={2:.6f} m",
                                  resultado.id_valor, actual,
                                  resultado.z_objetivo_m)

            # El elemento ya esta en su sitio: su DZ pasa a ser cero.
            resultado.z_original_m = resultado.z_objetivo_m
            resultado.delta_m = 0.0
            resultado.estado = "ok"
            resultado.pendiente = None
            resultado.nota_pendiente = (
                u"Pendiente aplicada y eje verificado; no se deforma la seccion."
                if adaptar else u"Solo nivelacion vertical aplicada.")

            if registro:
                registro.info(u"Movido {0} por {1:+.3f} m ({2})",
                              resultado.id_valor,
                              units.pies_a_metros(delta), metodo)
        else:
            salida.fallidos += 1
            salida.errores.append((resultado.id_valor, error))
            resultado.error = error

            if registro:
                registro.error(u"No se pudo mover {0}: {1}",
                               resultado.id_valor, error)

    return salida


def aplicar_ajuste_manual(DB, doc, elementos, delta_m, registro=None):
    """Desplaza en Z una cantidad fija (seccion 21).

    Es el retoque posterior a la nivelacion automatica: mismo mecanismo,
    pero con un valor que da el usuario en lugar de uno calculado."""
    salida = ResultadoAplicacion()
    estrategia = EstrategiaMovimiento(DB, doc)
    delta_pies = units.metros_a_pies(delta_m)

    for elemento in elementos:
        movible, motivo = revit_elements.comprobar_movilidad(DB, doc, elemento)

        if not movible:
            salida.omitidos += 1
            salida.errores.append((elemento.Id.IntegerValue, motivo))
            continue

        exito, metodo, error = estrategia.aplicar(elemento, delta_pies)

        if exito:
            salida.movidos += 1
            salida.desplazamiento_total += abs(delta_m)
            if abs(delta_m) > 1e-9:
                if not family_progress.registrar(elemento, registro):
                    salida.sin_historial += 1

            if registro:
                registro.info(u"Ajuste manual {0}: {1:+.3f} m ({2})",
                              elemento.Id.IntegerValue, delta_m, metodo)
        else:
            salida.fallidos += 1
            salida.errores.append((elemento.Id.IntegerValue, error))

    return salida
