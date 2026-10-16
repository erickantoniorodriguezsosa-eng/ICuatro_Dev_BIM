# -*- coding: utf-8 -*-
"""Seleccion de elementos de Revit y su punto vertical de referencia.

Cubre las secciones 14 y 16 del prompt maestro. Dos responsabilidades que
van juntas porque comparten el mismo problema: decidir QUE elemento se
analiza y COMO se mide su altura.

La eleccion del punto de referencia no es un detalle: para apoyar un
poste contra el terreno interesa la base, pero para una luminaria colgada
o una tapa de arqueta puede interesar el punto de insercion. Un modo
equivocado desplaza todo de forma sistematica.
"""

# Modos de referencia vertical (seccion 16).
BASE = "base"
CENTRO = "centro"
SUPERIOR = "superior"
INSERCION = "insercion"

MODOS = (BASE, INSERCION, CENTRO, SUPERIOR)

ETIQUETAS_MODO = {
    BASE: u"Base del elemento (parte inferior)",
    INSERCION: u"Punto de insercion",
    CENTRO: u"Centro geometrico",
    SUPERIOR: u"Parte superior",
}


class PuntoReferencia(object):
    """El punto que representa al elemento en el analisis vertical."""

    def __init__(self, x, y, z, origen):
        self.x = x
        self.y = y
        self.z = z
        self.origen = origen      # de donde salio, para poder explicarlo

    def __repr__(self):
        return "<PuntoReferencia ({0:.3f}, {1:.3f}, {2:.3f}) {3}>".format(
            self.x, self.y, self.z, self.origen)


def _caja(doc, elemento):
    """Caja envolvente del elemento, en pies.

    Se pide primero sin vista (la del modelo) y solo si falla contra la
    vista activa: la de la vista puede venir recortada por el encuadre."""
    for vista in (None, doc.ActiveView):
        try:
            caja = elemento.get_BoundingBox(vista)
        except Exception:
            caja = None

        if caja is not None:
            return caja

    return None


def _punto_de_insercion(elemento):
    """LocationPoint, o el punto medio de una LocationCurve."""
    try:
        localizacion = elemento.Location
    except Exception:
        return None

    if localizacion is None:
        return None

    try:
        if hasattr(localizacion, "Point") and localizacion.Point is not None:
            punto = localizacion.Point
            return (punto.X, punto.Y, punto.Z)
    except Exception:
        pass

    try:
        if hasattr(localizacion, "Curve") and localizacion.Curve is not None:
            curva = localizacion.Curve
            medio = curva.Evaluate(0.5, True)
            return (medio.X, medio.Y, medio.Z)
    except Exception:
        pass

    return None


def resolver_punto(doc, elemento, modo=BASE):
    """Punto de referencia del elemento, en pies. None si no se puede.

    XY sale siempre del centro de la caja (es donde cae el elemento en
    planta); lo que cambia con el modo es la Z."""
    insercion = _punto_de_insercion(elemento)

    if modo == INSERCION:
        if insercion is None:
            return None
        return PuntoReferencia(insercion[0], insercion[1], insercion[2],
                               u"punto de insercion")

    caja = _caja(doc, elemento)
    if caja is not None:
        centro_x = (caja.Min.X + caja.Max.X) / 2.0
        centro_y = (caja.Min.Y + caja.Max.Y) / 2.0

        if modo == SUPERIOR:
            return PuntoReferencia(centro_x, centro_y, caja.Max.Z,
                                   u"caja: superior")

        if modo == CENTRO:
            return PuntoReferencia(centro_x, centro_y,
                                   (caja.Min.Z + caja.Max.Z) / 2.0,
                                   u"caja: centro")

        if modo == INSERCION and insercion is not None:
            return PuntoReferencia(insercion[0], insercion[1], insercion[2],
                                   u"punto de insercion")

        return PuntoReferencia(centro_x, centro_y, caja.Min.Z, u"caja: base")

    # Sin caja envolvente solo queda la insercion.
    if insercion is not None:
        return PuntoReferencia(insercion[0], insercion[1], insercion[2],
                               u"punto de insercion (sin caja)")

    return None


def puntos_para_superficie(doc, elemento, punto=None, modo=BASE):
    """Puntos XY de prueba para localizar la superficie bajo un elemento.

    El centro de la caja sigue siendo el punto principal. Se añaden cuatro
    puntos interiores al 25%%/75%% del footprint para cubrir los casos en
    que el centro cae en una grieta entre teselas o triangulos. Las muestras
    nunca salen de la caja del elemento y se devuelven en pies de Revit.
    """
    punto = punto or resolver_punto(doc, elemento, modo)

    if punto is None:
        return []

    # Cuando se solicita la referencia de insercion, la interseccion debe
    # hacerse en el origen real de la familia o en el punto medio de la
    # curva. No se reemplaza por el centro de la caja envolvente.
    if modo == INSERCION:
        return [(punto.x, punto.y, punto.z)]

    caja = _caja(doc, elemento)

    if caja is None:
        return [(punto.x, punto.y, punto.z)]

    min_x = caja.Min.X
    max_x = caja.Max.X
    min_y = caja.Min.Y
    max_y = caja.Max.Y
    centro_x = (min_x + max_x) / 2.0
    centro_y = (min_y + max_y) / 2.0
    ancho = max_x - min_x
    fondo = max_y - min_y

    puntos = [(centro_x, centro_y, punto.z)]

    if ancho > 1e-9 and fondo > 1e-9:
        for proporcion_x, proporcion_y in (
                (0.25, 0.25), (0.25, 0.75),
                (0.75, 0.25), (0.75, 0.75)):
            puntos.append((
                min_x + ancho * proporcion_x,
                min_y + fondo * proporcion_y,
                punto.z))

    # Si el punto de insercion cae dentro de la caja y es diferente, tambien
    # es una referencia util para familias cuyo origen no coincide con el
    # centro geometrico.
    insercion = _punto_de_insercion(elemento)
    if insercion is not None:
        if (min_x - 1e-9 <= insercion[0] <= max_x + 1e-9 and
                min_y - 1e-9 <= insercion[1] <= max_y + 1e-9):
            candidato = (insercion[0], insercion[1], punto.z)
            if candidato[:2] not in [p[:2] for p in puntos]:
                puntos.append(candidato)

    return puntos


# ==========================================================================
# SELECCION (seccion 14)
# ==========================================================================

def de_la_seleccion(uidoc, doc):
    """Los elementos seleccionados ahora mismo en Revit."""
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        return []

    elementos = []

    for element_id in ids:
        elemento = doc.GetElement(element_id)

        if elemento is not None:
            elementos.append(elemento)

    return elementos


def por_ids(DB, doc, texto):
    """Elementos a partir de una lista de ids escrita a mano.

    La busqueda es EXACTA (requisito de la seccion 14): un id que no
    existe se informa, no se aproxima. Acepta separacion por comas,
    espacios o saltos de linea.

    Devuelve (elementos, no_encontrados)."""
    elementos = []
    fallidos = []

    if not texto:
        return elementos, fallidos

    crudos = texto.replace(",", " ").replace(";", " ").split()

    for crudo in crudos:
        crudo = crudo.strip()

        if not crudo:
            continue

        try:
            valor = int(crudo)
        except ValueError:
            fallidos.append(crudo)
            continue

        try:
            elemento = doc.GetElement(DB.ElementId(valor))
        except Exception:
            elemento = None

        if elemento is None:
            fallidos.append(crudo)
        else:
            elementos.append(elemento)

    return elementos, fallidos


def por_categoria(DB, doc, id_categoria, vista=None):
    """Todos los elementos de una categoria, opcionalmente de una vista."""
    try:
        if vista is not None:
            recolector = DB.FilteredElementCollector(doc, vista.Id)
        else:
            recolector = DB.FilteredElementCollector(doc)

        return list(
            recolector.OfCategoryId(id_categoria)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []


def nombres_familias(DB, doc):
    """Nombres de familias cargadas en el proyecto, ordenados y sin repetir."""
    nombres = set()

    try:
        familias = DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements()

        for familia in familias:
            try:
                nombre = u"{0}".format(familia.Name).strip()
            except Exception:
                nombre = u""

            if nombre:
                nombres.add(nombre)
    except Exception:
        pass

    return sorted(nombres, key=lambda valor: valor.lower())


def por_familia(DB, doc, nombre_familia, vista=None):
    """Todos los ejemplares de una familia cargada en el documento.

    La consulta se hace por FamilyInstance y compara FamilyName del tipo,
    no el texto mostrado en la tabla. Asi se incluyen todos los tipos de la
    familia y se evita confundir una familia con otra que comparte un nombre
    de tipo.
    """
    if not nombre_familia:
        return []

    buscada = u"{0}".format(nombre_familia).strip().lower()

    try:
        recolector = (DB.FilteredElementCollector(doc, vista.Id)
                      if vista is not None else
                      DB.FilteredElementCollector(doc))

        recolector = recolector.OfClass(DB.FamilyInstance) \
                               .WhereElementIsNotElementType()
        candidatos = recolector.ToElements()
    except Exception:
        return []

    salida = []
    vistos = set()

    for elemento in candidatos:
        try:
            tipo = doc.GetElement(elemento.GetTypeId())
            familia = u"{0}".format(tipo.FamilyName).strip().lower()
        except Exception:
            continue

        if familia != buscada:
            continue

        try:
            clave = elemento.Id.IntegerValue
        except Exception:
            clave = id(elemento)

        if clave in vistos:
            continue

        vistos.add(clave)
        salida.append(elemento)

    try:
        salida.sort(key=lambda elemento: elemento.Id.IntegerValue)
    except Exception:
        pass

    return salida


def describir(DB, doc, elemento):
    """(categoria, familia, tipo) del elemento, para las tablas."""
    categoria = u"—"
    familia = u"—"
    tipo = u"—"

    try:
        if elemento.Category is not None:
            categoria = elemento.Category.Name
    except Exception:
        pass

    try:
        type_elem = doc.GetElement(elemento.GetTypeId())
    except Exception:
        type_elem = None

    if type_elem is not None:
        try:
            tipo = DB.Element.Name.GetValue(type_elem)
        except Exception:
            pass

        try:
            familia = type_elem.FamilyName
        except Exception:
            pass

    return categoria, familia, tipo


# ==========================================================================
# APTITUD PARA SER MOVIDO (seccion 45)
# ==========================================================================

def comprobar_movilidad(DB, doc, elemento):
    """Si el elemento se puede desplazar verticalmente, y por que no.

    Se comprueba ANTES de abrir ninguna transaccion: es mucho mejor
    avisar en la tabla de resultados que fallar a mitad de la operacion.

    Devuelve (puede_moverse, motivo)."""
    try:
        if elemento.Pinned:
            return False, u"Elemento anclado (pinned)"
    except Exception:
        pass

    try:
        if (elemento.GroupId is not None and
                elemento.GroupId != DB.ElementId.InvalidElementId):
            return False, u"Pertenece a un grupo"
    except Exception:
        pass

    try:
        if elemento.Document.IsLinked:
            return False, u"Pertenece a un vinculo"
    except Exception:
        pass

    # Familias alojadas en una cara o en un anfitrion: moverlas en Z sin
    # mover el anfitrion las descoloca o Revit lo impide.
    try:
        anfitrion = getattr(elemento, "Host", None)

        if anfitrion is not None:
            return False, u"Alojada en {0}".format(
                DB.Element.Name.GetValue(anfitrion))
    except Exception:
        pass

    try:
        if not elemento.IsValidObject:
            return False, u"El elemento ya no es valido"
    except Exception:
        pass

    try:
        if doc.IsWorkshared:
            estado = DB.WorksharingUtils.GetCheckoutStatus(doc, elemento.Id)

            if estado == DB.CheckoutStatus.OwnedByOtherUser:
                return False, u"Otro usuario tiene el elemento"
    except Exception:
        pass

    return True, u""
