# -*- coding: utf-8 -*-
"""Orquestación: del vínculo de Revit a una superficie consultable.

    vínculo NWD en Revit
        -> origen de la geometría (IFC si está, si no NWD)
        -> transformación origen <-> Revit  (derivada y cacheada)
        -> zona de interés
        -> extracción por zona              (cache binario, invalidable)
        -> malla + índice espacial
        -> SuperficieNWD lista para consultar

DOS ORÍGENES, UN SOLO CAMINO

El vínculo de Revit siempre apunta a un NWD, pero la geometría puede
salir de dos sitios:

  - del IFC del que se generó ese NWD, si está en disco: es el camino
    preferente, porque no necesita Navisworks ni licencia y lee la
    triangulación ya hecha;
  - del propio NWD a través de Navisworks, como respaldo.

Ambos extractores escriben el mismo cache, así que a partir de aquí no
se distingue cuál se usó.
"""

import os
import hashlib
import re
import tempfile

from . import geometry
from . import ifc_bridge
from . import navisworks_bridge
from . import proceso
from . import surface as superficie_mod
from . import transform as transform_mod


IFC = "ifc"
NWD = "nwd"


def carpeta_trabajo():
    """Donde viven los caches. Configurable por si el temporal del
    usuario está en un disco lento o lleno."""
    ruta = os.environ.get("ICUATRO_NWD_CACHE")

    if not ruta:
        ruta = os.path.join(tempfile.gettempdir(), "nwdcoord")

    if not os.path.isdir(ruta):
        try:
            os.makedirs(ruta)
        except Exception:
            pass

    return ruta


def _clave(texto):
    return hashlib.md5(texto.encode("utf-8")).hexdigest()[:12]


def _texto_caja(caja):
    """Representacion compacta de una caja para el registro."""
    if not caja or len(caja) != 2:
        return u"(sin caja)"

    try:
        minimos, maximos = caja
        tamano = [maximos[i] - minimos[i] for i in range(3)]
        return (u"min=({0:.3f},{1:.3f},{2:.3f}) "
                u"max=({3:.3f},{4:.3f},{5:.3f}) "
                u"tam=({6:.3f},{7:.3f},{8:.3f}) m".format(
                    minimos[0], minimos[1], minimos[2],
                    maximos[0], maximos[1], maximos[2],
                    tamano[0], tamano[1], tamano[2]))
    except Exception:
        return u"(caja invalida: {0})".format(caja)


def _cajas_coinciden(primera, segunda, tolerancia_m=0.01):
    """Comprueba que dos cajas describen la misma posicion en metros."""
    if not primera or not segunda:
        return False

    try:
        valores = list(primera[0]) + list(primera[1])
        actuales = list(segunda[0]) + list(segunda[1])
        return max(abs(valores[i] - actuales[i])
                   for i in range(6)) <= tolerancia_m
    except Exception:
        return False


def _registrar_diagnostico(registro, diagnostico):
    """Escribe el diagnostico completo, no solo el motivo final."""
    if registro is None or diagnostico is None:
        return

    registro.info(
        u"Diagnostico transformacion: valida={0}, traslacion_pura={1}, "
        u"dZ_cero={2}, discrepancia_max={3:.6f} m, "
        u"desviacion_tamano={4:.6f}",
        diagnostico.valida, diagnostico.traslacion_pura,
        diagnostico.dz_es_cero, diagnostico.discrepancia_maxima,
        diagnostico.desviacion_tamano)

    for indice, eje in enumerate((u"X", u"Y", u"Z")):
        desde_min, desde_max, discrepancia = \
            diagnostico.desplazamientos[indice]
        registro.info(
            u"  Eje {0}: d_min={1:.6f} m, d_max={2:.6f} m, "
            u"diferencia={3:.6f} m, tam_origen={4:.6f} m, "
            u"tam_revit={5:.6f} m",
            eje, desde_min, desde_max, discrepancia,
            diagnostico.tamano_nwd[indice],
            diagnostico.tamano_revit[indice])


# ==========================================================================
# ORIGEN DE LA GEOMETRÍA
# ==========================================================================

class Origen(object):
    """De dónde se saca la geometría de un vínculo."""

    def __init__(self, tipo, ruta, motivo=u""):
        self.tipo = tipo          # IFC o NWD
        self.ruta = ruta
        self.motivo = motivo

    @property
    def es_ifc(self):
        return self.tipo == IFC

    @property
    def nombre(self):
        return os.path.basename(self.ruta) if self.ruta else u"—"

    @property
    def etiqueta(self):
        return u"IFC (lectura directa)" if self.es_ifc else u"NWD (Navisworks)"

    def __repr__(self):
        return "<Origen {0} {1}>".format(self.tipo, self.nombre)


def resolver_origen(modelo, preferir=None, registro=None):
    """Elige de dónde leer la geometría del vínculo.

    Se prefiere el IFC cuando existe y su lector está compilado: es más
    rápido y no consume licencia de Navisworks. 'preferir' fuerza uno de
    los dos.

    Devuelve un Origen, o None si no hay ninguno disponible."""
    if registro:
        registro.info(u"Archivo NWD vinculado por Revit: ruta={0}, existe={1}",
                      getattr(modelo, "ruta", None),
                      getattr(modelo, "existe", False))

    if not modelo.ruta:
        if registro:
            registro.aviso(u"El modelo de coordinacion no expuso una ruta "
                           u"NWD en sus parametros.")
        return None

    ifc = ifc_bridge.ifc_asociado(modelo.ruta)
    hay_lector_ifc = ifc_bridge.localizar_extractor() is not None
    inventario_fuentes = None

    # Si Navisworks esta disponible, se contrasta el IFC vecino con la
    # procedencia real declarada dentro del NWD. Asi un IFC de otra revision
    # no puede ganar solo por tener un nombre parecido o una caja similar.
    if modelo.existe and navisworks_bridge.estado()[0]:
        try:
            inventario_fuentes = navisworks_bridge.fuentes(
                modelo.ruta, _ruta_fuentes(modelo.ruta), registro)
            _registrar_fuentes_nwd(registro, inventario_fuentes)
        except Exception as error:
            if registro:
                registro.aviso(u"No se pudo inventariar el contenido del "
                               u"NWD: {0}", error)

    if ifc and not _ifc_coincide_con_fuentes(ifc, inventario_fuentes):
        if registro:
            registro.aviso(
                u"El IFC candidato no coincide con el SourceFileName del "
                u"NWD; se descarta para evitar una superficie equivocada.")
        ifc = None

    if registro:
        registro.info(u"IFC asociado candidato: {0}; lector disponible={1}",
                      ifc or u"ninguno", hay_lector_ifc)

    if preferir == NWD:
        if modelo.existe:
            return Origen(NWD, modelo.ruta, u"Forzado por el usuario.")
        return None

    if preferir == IFC or (ifc and hay_lector_ifc):
        if ifc and hay_lector_ifc:
            if registro:
                registro.info(u"Origen IFC: {0}", ifc)

            return Origen(IFC, ifc,
                          u"Se encontro el IFC del que salio el NWD: no hace "
                          u"falta Navisworks.")

        if preferir == IFC:
            return None

    if modelo.existe:
        motivo = (u"No se encontro un IFC asociado."
                  if not ifc else
                  u"Hay IFC pero falta IfcExtractor.exe.")

        if registro:
            registro.info(u"Origen NWD: {0}", motivo)

        return Origen(NWD, modelo.ruta, motivo)

    return None


def _puente(origen):
    return ifc_bridge if origen.es_ifc else navisworks_bridge


def estado_puentes():
    """Disponibilidad de los dos lectores, para el diagnóstico."""
    ifc_ok, ifc_msg, ifc_det = ifc_bridge.estado()
    nwd_ok, nwd_msg, nwd_det = navisworks_bridge.estado()

    return {
        "ifc": (ifc_ok, ifc_msg, ifc_det),
        "nwd": (nwd_ok, nwd_msg, nwd_det),
    }


# ==========================================================================
# TRANSFORMACIÓN
# ==========================================================================

def _ruta_sondeo(ruta_origen):
    """Informe del sondeo. El índice de facesets cuelga de esta ruta."""
    return os.path.join(
        carpeta_trabajo(), "probe_{0}.txt".format(_clave(ruta_origen)))


def _ruta_fuentes(ruta_origen):
    return os.path.join(
        carpeta_trabajo(), "sources_{0}.txt".format(_clave(ruta_origen)))


def _ruta_transformacion(ruta_origen):
    return os.path.join(
        carpeta_trabajo(), "trans_{0}.txt".format(_clave(ruta_origen)))


def _registrar_fuentes_nwd(registro, datos):
    """Resume en el registro la procedencia interna declarada por NWD."""
    if registro is None or not datos:
        return

    contenido = datos.get("contenido", u"")
    interesantes = []

    for linea in contenido.splitlines():
        texto = linea.strip()
        bajo = texto.lower()
        if (bajo.startswith("creator\t") or
                bajo.startswith("filename\t") or
                bajo.startswith("sourcefilename\t") or
                bajo.startswith("unidades\t") or
                bajo.startswith("guid\t") or
                bajo.startswith("sourceguid\t")):
            interesantes.append(texto)

    registro.info(u"Inventario de fuentes NWD: archivo={0}, codigo={1}",
                  datos.get("ruta"), datos.get("codigo"))

    for linea in interesantes:
        registro.info(u"  NWD {0}", linea)

    if datos.get("error"):
        registro.aviso(u"Inventario de fuentes NWD con error: {0}",
                       datos.get("error"))


def _ifc_coincide_con_fuentes(ifc, datos):
    """Evita usar un IFC vecino que no es el que compone el NWD.

    Navisworks no siempre escribe el IFC en SourceFileName: cuando el NWD
    es un contenedor compuesto, el nombre aparece en la ruta de ancestros
    (`ruta_arbol`). Se revisan ambos formatos antes de aceptar un IFC local.
    """
    if not ifc or not datos:
        return True

    nombres = []
    for linea in datos.get("contenido", u"").splitlines():
        texto = linea.strip()
        partes = texto.split(u"\t", 1)
        clave = partes[0].lower() if partes else u""

        if clave == u"sourcefilename" and len(partes) == 2:
            candidatos = [partes[1].strip()]
        elif clave == u"ruta_arbol" and len(partes) == 2:
            candidatos = re.findall(
                r"[^>\t\r\n]+\.ifc\b", partes[1], re.IGNORECASE)
        else:
            candidatos = []

        for candidato in candidatos:
            nombre = os.path.basename(candidato.strip()).lower()
            if nombre.endswith(u".ifc") and nombre not in nombres:
                nombres.append(nombre)

    if not nombres:
        # Algunos NWD no conservan el SourceFileName; en ese caso no hay
        # evidencia suficiente para descartar el candidato local.
        return True

    coincide = os.path.basename(ifc).lower() in nombres

    return coincide


def _guardar_transformacion(ruta_origen, transformacion,
                            caja_origen, caja_revit):
    try:
        with open(_ruta_transformacion(ruta_origen), "w") as archivo:
            archivo.write("dx={0}\ndy={1}\ndz={2}\n".format(
                transformacion.dx, transformacion.dy, transformacion.dz))
            archivo.write("caja_origen={0}\n".format(
                ",".join(str(v) for v in
                          list(caja_origen[0]) + list(caja_origen[1]))))
            archivo.write("caja_revit={0}\n".format(
                ",".join(str(v) for v in
                          list(caja_revit[0]) + list(caja_revit[1]))))
            archivo.write("tamano={0}\nfecha={1}\n".format(
                os.path.getsize(ruta_origen), os.path.getmtime(ruta_origen)))
    except Exception:
        pass


def _cargar_transformacion(ruta_origen):
    """Transformación guardada, si el archivo no ha cambiado."""
    ruta = _ruta_transformacion(ruta_origen)

    if not os.path.isfile(ruta):
        return None, None, None

    try:
        datos = {}

        with open(ruta, "r") as archivo:
            for linea in archivo.read().splitlines():
                if "=" in linea:
                    clave, valor = linea.split("=", 1)
                    datos[clave.strip()] = valor.strip()

        if int(datos["tamano"]) != os.path.getsize(ruta_origen):
            return None, None, None

        if abs(float(datos["fecha"]) - os.path.getmtime(ruta_origen)) > 1.0:
            return None, None, None

        texto_origen = datos.get("caja_origen", datos.get("caja"))
        texto_revit = datos.get("caja_revit")

        # Los caches anteriores no guardaban la caja de Revit. Se descartan
        # una vez para impedir que sobreviva una transformacion obsoleta si
        # el usuario reposiciono el vinculo.
        if not texto_origen or not texto_revit:
            return None, None, None

        valores_origen = [float(v) for v in texto_origen.split(",")]
        valores_revit = [float(v) for v in texto_revit.split(",")]

        if len(valores_origen) != 6 or len(valores_revit) != 6:
            return None, None, None

        return (
            transform_mod.Transformacion(
                float(datos["dx"]), float(datos["dy"]), float(datos["dz"])),
            (valores_origen[:3], valores_origen[3:]),
            (valores_revit[:3], valores_revit[3:]),
        )
    except Exception:
        return None, None, None


def resolver_transformacion(modelo, registro=None, forzar=False, origen=None):
    """Transformación origen -> Revit para este vínculo.

    Requiere sondear el archivo para conocer su caja real, así que el
    resultado se guarda: repetirlo en cada análisis costaría segundos (y
    una licencia de Navisworks, si el origen fuera el NWD).

    Devuelve (transformacion, diagnostico, origen)."""
    diagnostico = transform_mod.DiagnosticoTransformacion()

    if origen is None:
        origen = resolver_origen(modelo, registro=registro)

    if origen is None:
        diagnostico.motivo = (
            u"No hay ningun origen de geometria accesible: ni IFC junto al "
            u"NWD, ni el propio NWD.")
        return None, diagnostico, None

    caja_revit = modelo.caja_en_metros()

    if caja_revit is None:
        diagnostico.motivo = u"El vinculo no tiene caja envolvente en Revit."
        return None, diagnostico, origen

    if registro:
        registro.info(u"Vinculo Revit: ElementId {0}, nombre={1}, ruta={2}, "
                      u"existe={3}",
                      getattr(modelo, "id_valor", u"?"),
                      getattr(modelo, "nombre", u"?"),
                      getattr(modelo, "ruta", u"?"),
                      getattr(modelo, "existe", False))
        registro.info(u"Origen geometrico elegido: {0} | {1}",
                      origen.etiqueta, origen.ruta)
        registro.info(u"Caja actual del vinculo Revit: {0}",
                      _texto_caja(caja_revit))

    if not forzar:
        guardada, caja_origen, caja_revit_guardada = \
            _cargar_transformacion(origen.ruta)

        if guardada is not None and _cajas_coinciden(
                caja_revit_guardada, caja_revit):
            _, diagnostico = transform_mod.derivar(caja_origen, caja_revit)

            if registro:
                registro.info(u"Transformacion reutilizada: {0}", guardada)
                registro.info(u"Caja origen cacheada: {0}",
                              _texto_caja(caja_origen))
                _registrar_diagnostico(registro, diagnostico)

            return guardada, diagnostico, origen

        if registro and guardada is not None:
            registro.aviso(
                u"Cache de transformacion descartada: la caja actual de "
                u"Revit no coincide con la caja guardada. Se vuelve a "
                u"sondear el origen.")

    destino = _ruta_sondeo(origen.ruta)

    if registro:
        registro.info(u"Archivo auxiliar de sondeo: {0}", destino)
        registro.info(u"Archivo auxiliar de transformacion: {0}",
                      _ruta_transformacion(origen.ruta))
        registro.empezar(u"sondeo del origen")

    try:
        if origen.es_ifc:
            # El modo bbox no devuelve facesets. Para que la interfaz pueda
            # detectar y listar superficies, el primer sondeo IFC debe ser
            # completo; el extractor solo lee la estructura y no extrae la
            # malla de la zona en este paso.
            datos = ifc_bridge.sondear(
                origen.ruta, destino, registro, completo=True)
        else:
            datos = navisworks_bridge.sondear(
                # Un NWD de topografia suele estar dividido en muchas
                # teselas. Una muestra no representa el inventario real.
                origen.ruta, destino, muestras=25, registro=registro)
    except Exception as error:
        if registro:
            registro.excepcion(u"Sondeo del origen", error)

        diagnostico.motivo = u"Fallo el sondeo: {0}".format(error)
        return None, diagnostico, origen

    if registro:
        registro.terminar(u"sondeo del origen")

    caja_origen = proceso.caja_de(datos, u"bbox_m")

    if caja_origen is None:
        caja_origen = proceso.caja_de(datos, u"bbox_modelo_m")

    if caja_origen is None:
        diagnostico.motivo = (
            u"El sondeo no devolvio la caja del modelo. Codigo {0}. {1}".format(
                datos.get("_codigo"), datos.get("_error", u"")))
        return None, diagnostico, origen

    if registro:
        registro.info(u"Caja del origen sondeado: {0}",
                      _texto_caja(caja_origen))

    transformacion, diagnostico = transform_mod.derivar(caja_origen, caja_revit)

    if diagnostico.valida:
        _guardar_transformacion(origen.ruta, transformacion,
                                 caja_origen, caja_revit)

    if registro:
        registro.info(u"Transformacion derivada desde {0}: {1} | {2}",
                      origen.etiqueta, transformacion, diagnostico.motivo)
        _registrar_diagnostico(registro, diagnostico)

    return transformacion, diagnostico, origen


# ==========================================================================
# SUPERFICIE
# ==========================================================================

def superficies_del_origen(origen, registro=None):
    """Las superficies que contiene el origen, ya listadas por el sondeo.

    Cada entrada es (nombre, tipo, facesets, triangulos). En el IFC de este
    proyecto salen los tramos del levantamiento (IfcGeographicElement con
    ObjectType TRIANGULACIÓN), y poder acotar a uno reduce mucho lo que hay
    que leer.

    No relanza nada: lee el informe que dejó el sondeo del arranque."""
    if origen is None:
        return []

    informe = _ruta_sondeo(origen.ruta)

    if not os.path.isfile(informe):
        return []

    datos = proceso.leer_informe(informe)
    salida = []

    for fila in datos.get(u"superficies", []):
        if not isinstance(fila, list) or len(fila) < 4:
            continue

        try:
            salida.append((fila[0], fila[1], int(fila[2]), int(fila[3])))
        except Exception:
            continue

    if registro:
        registro.info(u"Superficies en el origen: {0}", len(salida))

        for nombre, tipo, facesets, triangulos in salida:
            registro.info(u"  {0} [{1}] {2} faceset(s), {3} triangulos",
                          nombre, tipo, facesets, triangulos)

    return salida


def _ruta_cache(ruta_origen, caja, filtro=None):
    """Un cache por archivo, zona y superficie: no deben pisarse entre sí."""
    firma = "{0}|{1:.1f},{2:.1f},{3:.1f},{4:.1f}|{5}".format(
        ruta_origen, caja[0], caja[1], caja[2], caja[3], filtro or "")

    return os.path.join(
        carpeta_trabajo(), "zona_{0}.nwcache".format(_clave(firma)))


def _extraer_zona(origen, cache, caja_zona, filtro, maximo,
                  tiempo_maximo_s, registro):
    """Extrae una zona con el puente y argumentos propios de su origen."""
    argumentos = {
        "caja": caja_zona,
        "filtro": filtro,
        "maximo": maximo,
        "tiempo_maximo_s": tiempo_maximo_s,
        "registro": registro,
    }

    if origen.es_ifc:
        # El sondeo dejó un índice de cajas por faceset junto a su informe:
        # con él la extracción se salta lo que no toca.
        argumentos["indice"] = ifc_bridge.ruta_indice(
            _ruta_sondeo(origen.ruta))

    return _puente(origen).extraer(
        origen.ruta, cache, **argumentos)


def preparar_superficie(modelo, caja_zona, origen=None, filtro=None, maximo=0,
                        registro=None, progreso_lectura=None,
                        progreso_indice=None, reutilizar_cache=True,
                        tiempo_maximo_s=1800):
    """Deja lista una SuperficieNWD para la zona pedida.

    'caja_zona' es (minX, minY, maxX, maxY) en metros y en las coordenadas
    del ORIGEN; normalmente sale de transform.caja_revit_a_nwd().

    Devuelve (superficie, informe)."""
    informe = {
        "cache": None,
        "reutilizado": False,
        "triangulos": 0,
        "items": 0,
        "extraccion_s": 0.0,
        "origen": None,
        "filtro": filtro,
        "filtro_replegado": False,
        "error": None,
    }

    if origen is None:
        origen = resolver_origen(modelo, registro=registro)

    if origen is None:
        informe["error"] = u"No hay ningun origen de geometria accesible."
        return None, informe

    informe["origen"] = origen

    cache = _ruta_cache(origen.ruta, caja_zona, filtro)
    informe["cache"] = cache

    valido = False

    if reutilizar_cache:
        valido, motivo = geometry.es_cache_valido(cache, origen.ruta, caja_zona)

        if registro:
            registro.info(u"Cache de zona: {0}", motivo)

    if not valido:
        if registro:
            registro.empezar(u"extraccion")

        try:
            datos = _extraer_zona(
                origen, cache, caja_zona, filtro, maximo,
                tiempo_maximo_s, registro)
        except Exception as error:
            if registro:
                registro.excepcion(u"Extraccion de la zona", error)

            informe["error"] = u"{0}".format(error)
            return None, informe

        if registro:
            informe["extraccion_s"] = registro.terminar(u"extraccion")

        if registro:
            registro.info(
                u"Resultado de extraccion: filtro={0}, items={1}, "
                u"triangulos={2}, bbox={3}",
                filtro or u"(todos)", datos.get(u"items", u"?"),
                datos.get(u"triangulos", u"?"),
                datos.get(u"bbox_m", u"?"))

        # Un filtro nominal puede identificar un tramo que no solapa la
        # caja de trabajo del elemento. En ese caso no significa que el NWD
        # carezca de superficie: se repite la extraccion espacial sin filtro.
        try:
            triangulos_extraidos = int(datos.get(u"triangulos", 0))
        except Exception:
            triangulos_extraidos = 0

        if (filtro and datos.get("_codigo") == 0 and
                datos.get("_cache") and triangulos_extraidos == 0):
            if registro:
                registro.aviso(
                    u"El filtro '{0}' no intersecta la zona solicitada. "
                    u"Se reintenta con todas las superficies de la zona.",
                    filtro)

            cache_replegado = _ruta_cache(origen.ruta, caja_zona, None)

            try:
                datos = _extraer_zona(
                    origen, cache_replegado, caja_zona, None, maximo,
                    tiempo_maximo_s, registro)

                cache = cache_replegado
                informe["cache"] = cache
                informe["filtro_replegado"] = True
                if registro:
                    registro.info(
                        u"Reintento sin filtro: items={0}, triangulos={1}, "
                        u"bbox={2}",
                        datos.get(u"items", u"?"),
                        datos.get(u"triangulos", u"?"),
                        datos.get(u"bbox_m", u"?"))
            except Exception as error:
                if registro:
                    registro.excepcion(u"Reintento sin filtro", error)

                informe["error"] = \
                    u"Fallo el reintento sin filtro: {0}".format(error)
                return None, informe

        if datos.get("_codigo") != 0 or not datos.get("_cache"):
            informe["error"] = u"El extractor fallo (codigo {0}). {1}".format(
                datos.get("_codigo"), datos.get("_error", u""))
            return None, informe

        geometry.escribir_metadatos(cache, origen.ruta, caja_zona)
    else:
        informe["reutilizado"] = True

    try:
        malla = geometry.leer_cache(cache, registro, progreso_lectura)
    except Exception as error:
        if registro:
            registro.excepcion(u"Lectura del cache", error)

        informe["error"] = u"{0}".format(error)
        return None, informe

    informe["triangulos"] = malla.num_triangulos
    informe["items"] = len(malla.items)

    if registro and informe["filtro_replegado"]:
        registro.aviso(
            u"La superficie nominal no cubria el elemento; se utilizo la "
            u"union espacial de superficies cercanas.")

    if malla.num_triangulos == 0:
        informe["error"] = (
            u"La zona no contiene ningun triangulo. Puede que los elementos "
            u"esten fuera de la superficie del modelo.")
        return None, informe

    superficie = superficie_mod.SuperficieNWD(malla, registro=registro)
    superficie.cargar(progreso_indice)

    return superficie, informe
