# -*- coding: utf-8 -*-
"""Historial por ejemplar, almacenado con el movimiento en el documento.

Extensible Storage se confirma/deshace con la transaccion de Revit. El UID
evita que una copia herede el estado de movimiento del ejemplar original.
"""

SCHEMA_ID = "43ba4179-a517-4e25-92c0-c9ed8fbc4c81"
CAMPO_UID = "MovedElementUniqueId"


def _schema(crear=False):
    from System import Guid, String
    from Autodesk.Revit.DB.ExtensibleStorage import Schema, SchemaBuilder, AccessLevel
    schema = Schema.Lookup(Guid(SCHEMA_ID))
    if schema is not None or not crear:
        return schema
    builder = SchemaBuilder(Guid(SCHEMA_ID))
    try:
        builder.SetSchemaName("ICuatroNwdMovementV1")
        builder.SetReadAccessLevel(AccessLevel.Public)
        builder.SetWriteAccessLevel(AccessLevel.Public)
        builder.AddSimpleField(CAMPO_UID, String)
        return builder.Finish()
    finally:
        builder.Dispose()


def registrar(elemento, registro=None):
    """Se llama SOLO tras mover, dentro de la transaccion del movimiento."""
    try:
        from System import String
        from Autodesk.Revit.DB.ExtensibleStorage import Entity
        schema = _schema(crear=True)
        entity = Entity(schema)
        entity.Set[String](schema.GetField(CAMPO_UID), elemento.UniqueId)
        elemento.SetEntity(entity)
        return True
    except Exception as error:
        if registro:
            registro.aviso(u"Movimiento realizado, pero no se pudo guardar "
                           u"el indicador de familia: {0}", error)
        return False


def _esta_movido(elemento, schema):
    if schema is None:
        return False
    from System import String
    entity = elemento.GetEntity(schema)
    return (entity.IsValid() and
            entity.Get[String](schema.GetField(CAMPO_UID)) == elemento.UniqueId)


class EstadoFamilia(object):
    """Datos para la fila coloreada; el nombre real se conserva intacto."""

    def __init__(self, nombre, total=0, movidos=0):
        self.nombre = nombre
        self.total = total
        self.movidos = movidos

    @property
    def name(self):
        # SelectFromList utiliza name para buscar, incluso por estado.
        return u"{0} - {1}".format(self.nombre, self.estado)

    @property
    def estado(self):
        if self.total == 0:
            return u"Sin ejemplares"
        if self.movidos == 0:
            return u"Sin movimientos registrados (0/{0})".format(self.total)
        if self.movidos == self.total:
            return u"Movilizada ({0}/{0})".format(self.total)
        return u"Parcial: {0}/{1} movilizados".format(self.movidos, self.total)

    @property
    def color(self):
        if self.movidos and self.movidos == self.total:
            return "#15803D"
        return "#A16207" if self.movidos else "#64748B"

    @property
    def fondo(self):
        if self.movidos and self.movidos == self.total:
            return "#DCFCE7"
        return "#FEF3C7" if self.movidos else "#F1F5F9"


def listar(DB, doc, nombres):
    """Cuenta los ejemplares actuales una sola vez, al abrir el selector."""
    estados = dict((n, EstadoFamilia(n)) for n in nombres)
    schema = _schema()
    tipos = {}
    for elemento in (DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance)
                     .WhereElementIsNotElementType()):
        tipo_id = elemento.GetTypeId()
        clave = tipo_id.IntegerValue
        if clave not in tipos:
            tipo = doc.GetElement(tipo_id)
            tipos[clave] = u"{0}".format(tipo.FamilyName) if tipo else None
        nombre = tipos[clave]
        if nombre not in estados:
            continue
        fila = estados[nombre]
        fila.total += 1
        if _esta_movido(elemento, schema):
            fila.movidos += 1
    return [estados[n] for n in nombres]
