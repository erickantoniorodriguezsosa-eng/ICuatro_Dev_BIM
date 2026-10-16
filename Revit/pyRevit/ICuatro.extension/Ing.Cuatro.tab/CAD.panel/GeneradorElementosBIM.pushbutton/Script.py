# -*- coding: utf-8 -*-
"""Convierte bloques y curvas por capas de un CAD en familias de Revit.

1. Detecta los CAD vinculados/importados del modelo y analiza sus
   BLOQUES: nombre, capa, punto de insercion y giro de cada insercion.
2. A cada bloque se le asigna la familia y el tipo que le corresponde.
   Se pueden usar familias y tipos distintos para bloques distintos, o
   resolverlos de golpe por nombre (tipo = nombre del bloque).
3. Coloca un ejemplar por cada insercion, en su punto (con la cota Z
   que trae el CAD) y con el giro del bloque.

La pestana Capas permite asignar familias y tipos cargados a lineas,
polilineas por tramos y curvas, con colocacion lineal o puntual.
"""

# Este texto se muestra como tooltip del boton en la cinta de Revit;
# pyRevit lo toma del docstring del modulo, junto con las variables
# __title__ / __author__ de abajo.

__title__ = "Elementos\nBIM"
__author__ = "Ingenieria Cuatro S.A."

# ------------------------------------------------------------------------------
# NOTAS DE IMPLEMENTACION
#
#   - Este boton es la version nativa del grafo de Dynamo
#     GENERADOR-BLOQUES-BIM-I4.dyn. Aquel encadenaba:
#         Selecionar archivo DWG -> CAD_Block_BIMI4 (nodo personalizado:
#         BlockName / OriginPoint / Rotation / LayerName)
#         -> FamilyType.ByFamilyNameAndTypeName(familia, BlockName)
#         -> FamilyInstance.ByPoint -> SetRotation(Rotation * -1)
#     El grafo trabajaba con UNA sola familia y el nombre del bloque como
#     nombre de tipo. Aqui la asignacion es POR BLOQUE (cada fila lleva su
#     propio FamilySymbol), y ese criterio del grafo queda disponible como
#     el boton "Asignar por nombre".
#   - El nodo personalizado CAD_Block_BIMI4.dyf no esta en el equipo, asi
#     que su logica se reimplementa con la API de Revit.
#
#   RECORRIDO DE LA GEOMETRIA (lo delicado de todo el boton)
#
#   - Revit envuelve el DWG completo en un GeometryInstance. Dentro de
#     el, cada referencia de bloque es otro GeometryInstance anidado.
#   - Se recorre con GetSymbolGeometry() y NO con GetInstanceGeometry():
#     GetSymbolGeometry devuelve la geometria del simbolo sin transformar,
#     de modo que el Transform de cada hijo es RELATIVO a su padre y las
#     matrices se pueden componer (padre.Multiply(hijo)) para llegar a
#     coordenadas de modelo. Tomar obj.Transform tal cual, sin componer,
#     deja los puntos de insercion referidos al origen del propio DWG y
#     no al del modelo: los bloques salen desplazados.
#     GetInstanceGeometry() queda solo como respaldo, y en ese caso la
#     geometria ya viene en coordenadas de modelo y NO se compone.
#   - Un DWG no siempre entrega geometria con las mismas Options. Se
#     prueban varias configuraciones por archivo (solo visible, incluir
#     no visibles, y contra la vista activa) y se usa la primera que
#     devuelva bloques. La configuracion que funciono se anota.
#   - El envoltorio del propio DWG (profundidad 0) no es un bloque: se
#     recorre pero no se registra. Si con ese criterio no aparece ningun
#     bloque, se repite el analisis registrando tambien desde la
#     profundidad 0, por si el DWG no trae envoltorio.
#   - Si la deteccion falla, el boton "Guardar Diagnostico" vuelca el
#     arbol de geometria real (clase, simbolo, categoria, transformada)
#     a un .txt. Es la forma de ver que devuelve Revit para ESE DWG.
#
#   COLOCACION
#
#   - El giro se calcula de la matriz (atan2 de BasisX), por lo que NO se
#     replica el "* -1" del grafo: aquel compensaba el convenio de signo
#     del nodo personalizado.
#   - Un bloque REFLEJADO (determinante negativo) o ESCALADO no se puede
#     reproducir girando un ejemplar de familia: se coloca igualmente y
#     se avisa en la columna 'Aviso'.
#   - La cota se toma del propio CAD. Si la familia obliga a un nivel, se
#     coloca contra el nivel mas cercano y despues se corrige el
#     desplazamiento vertical para caer en la Z pedida.
#
#   DUPLICADOS (se atacan en los dos puntos donde aparecen)
#
#   - AL LEER: la misma insercion puede llegar por varios caminos del
#     arbol de geometria, o desde dos ImportInstance que sean el mismo
#     DWG vinculado dos veces. Se descarta por punto+giro y la cuenta se
#     muestra en la columna 'Aviso'. Los bloques de varios CAD se FUSIONAN
#     por clave en vez de reemplazarse, que es lo que perdia los del
#     primer archivo.
#   - AL COLOCAR: antes de generar se indexan los ejemplares que ya hay en
#     el modelo de los tipos implicados (IndiceExistentes) y no se coloca
#     donde ya hay uno del mismo tipo. El indice se alimenta con lo que se
#     va creando, asi que tambien frena los choques dentro de la misma
#     tanda. Es lo que hace que volver a pulsar 'Generar' no apile
#     elementos invisibles unos sobre otros.
#   - El aviso se da ANTES de abrir la transaccion, con el numero exacto:
#     la cuenta previa se hace sobre una copia del indice, simulando la
#     colocacion. Si todo esta ya colocado no se abre transaccion ninguna.
#
#   WPF
#
#   - Todo manejador de evento WPF va envuelto en @safe_handler: una
#     excepcion que escape hacia el dispatcher de WPF puede cerrar Revit.
#   - La ventana se abre con ShowDialog (modal). Por eso la seleccion
#     final de los elementos creados se aplica DESPUES de cerrarla, y el
#     diagnostico se escribe a un archivo en lugar de abrir la ventana de
#     salida de pyRevit.
# ------------------------------------------------------------------------------

import os
import math
import codecs
import datetime
import tempfile
import traceback
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List

from pyrevit import revit, DB, forms
from cad_curvas import CurvasCADMixin

doc = revit.doc
uidoc = revit.uidoc

PIES_A_METROS = 0.3048

# Profundidad maxima del recorrido de bloques anidados dentro del DWG.
PROFUNDIDAD_MAXIMA = 10

# Cuantas lineas de arbol se guardan en el diagnostico. Un DWG grande
# tiene decenas de miles de objetos y el archivo se volveria inutil.
LIMITE_LINEAS_DIAGNOSTICO = 800

# Aviso cuando se van a crear tantos ejemplares que Revit puede tardar.
LIMITE_INSTANCIAS_AVISO = 500

# Dos inserciones mas cercanas que esto (en pies) se consideran la misma
# lectura repetida del mismo bloque y solo se conserva una.
TOLERANCIA_DUPLICADOS_FT = 0.001

# Distancia por debajo de la cual se considera que en ese punto YA hay
# colocado un ejemplar del mismo tipo, y por tanto no hay que volver a
# colocarlo. Es mas holgada que la anterior: no compara dos lecturas del
# mismo DWG, sino una insercion del CAD contra un elemento ya existente
# en el modelo, que pudo colocarse con otro redondeo.
TOLERANCIA_EXISTENTE_M = 0.01
TOLERANCIA_EXISTENTE_FT = TOLERANCIA_EXISTENTE_M / PIES_A_METROS

# Por debajo de esta diferencia (en pies) no se corrige la cota del
# ejemplar recien colocado.
TOLERANCIA_Z_FT = 1e-6

# Margen respecto a 1.0 a partir del cual se considera que el bloque
# viene escalado y hay que avisarlo.
TOLERANCIA_ESCALA = 0.001


def safe_handler(func):
    """Decorador para los manejadores de eventos WPF (Click, etc.).

    Las excepciones lanzadas dentro de un event handler de WPF NO
    pasan por el manejo de errores de pyRevit: si se dejan escapar,
    pueden cerrar Revit por completo en lugar de solo mostrar un
    error. Cada manejador debe atrapar sus propias excepciones.
    """
    def wrapper(self, sender, args):
        try:
            return func(self, sender, args)
        except Exception as error:
            try:
                # La ventana es modal: solo se vuelve a mostrar si
                # algo la oculto. Llamar a Show() sobre un dialogo
                # modal ya visible es innecesario y puede lanzar
                # InvalidOperationException desde el dispatcher.
                if not self.IsVisible:
                    self.Show()
            except Exception:
                pass
            try:
                self.txt_status_bar.Text = "Error: {0}".format(error)
            except Exception:
                pass
            forms.alert(
                "Ocurrio un error inesperado:\n\n{0}\n\n{1}".format(
                    error, traceback.format_exc()
                ),
                title="Error"
            )
    return wrapper


def nombre_elemento(elem, defecto="N/D"):
    """Nombre de un elemento, resuelto de forma fiable en IronPython.

    En las clases que redefinen la propiedad Name (ElementType y sus
    derivadas: FamilySymbol, Family...) el acceso directo 'elem.Name'
    puede fallar y dejar todos los nombres en 'N/D'.
    Element.Name.GetValue() resuelve la propiedad declarada en la clase
    base y funciona en todos los casos; el resto son respaldos.

    NO sirve para los GraphicsStyle de las capas de un CAD: ahi el Name
    de la clase base devuelve el nombre del archivo importado y no el de
    la capa (ver ElementosBIMWPF._nombre_capa)."""
    if elem is None:
        return defecto

    try:
        valor = DB.Element.Name.GetValue(elem)
        if valor:
            return valor
    except Exception:
        pass

    try:
        valor = DB.Element.Name.__get__(elem, type(elem))
        if valor:
            return valor
    except Exception:
        pass

    try:
        valor = elem.Name
        if valor:
            return valor
    except Exception:
        pass

    for nombre_bip in ("SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME"):
        bip = getattr(DB.BuiltInParameter, nombre_bip, None)
        if bip is None:
            continue
        try:
            param = elem.get_Parameter(bip)
            if param is not None:
                valor = param.AsString()
                if valor:
                    return valor
        except Exception:
            continue

    return defecto


def texto(valor):
    """Convierte cualquier valor a unicode sin romperse con los nombres
    de bloque o de capa que traen acentos desde AutoCAD."""
    if valor is None:
        return u"-"
    try:
        if isinstance(valor, unicode):        # noqa: F821 (IronPython 2.7)
            return valor
    except NameError:
        pass
    try:
        return u"{0}".format(valor)
    except Exception:
        try:
            return str(valor).decode("utf-8", "replace")
        except Exception:
            return u"(ilegible)"


# ==============================================================================
# MODELOS DE DATOS PARA WPF
# ==============================================================================

class CadRow(object):
    """Representa un archivo CAD vinculado o importado en el documento,
    con su identificacion completa (no solo el Id)."""

    def __init__(self, elem):
        self.element = elem
        self.IsSelected = False
        self.Id = elem.Id.IntegerValue

        try:
            self.Categoria = elem.Category.Name if elem.Category else "N/D"
        except Exception:
            self.Categoria = "N/D"

        self.IsLinked = getattr(elem, "IsLinked", False)
        self.Tipo = "Vinculo CAD" if self.IsLinked else "CAD Importado"

        self.Nombre = "CAD_{0}".format(self.Id)
        self.Ruta = "-"
        self.Estado = "-"

        self._resolver_identificacion(elem)

    def _resolver_identificacion(self, elem):
        try:
            type_elem = doc.GetElement(elem.GetTypeId())
        except Exception:
            type_elem = None

        if type_elem is None:
            return

        try:
            param = type_elem.get_Parameter(
                DB.BuiltInParameter.IMPORT_SYMBOL_NAME
            )
            if param and param.AsString():
                self.Nombre = param.AsString()
            elif hasattr(type_elem, "Name") and type_elem.Name:
                self.Nombre = type_elem.Name
        except Exception:
            pass

        if not self.IsLinked:
            self.Ruta = "(CAD importado: geometria incrustada)"
            self.Estado = "Incrustado"
            return

        try:
            ext_ref = type_elem.GetExternalFileReference()
            if ext_ref is not None:
                model_path = ext_ref.GetAbsolutePath()
                self.Ruta = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(
                    model_path
                )
                self.Estado = str(ext_ref.GetLinkedFileStatus())
        except Exception:
            pass


class Insercion(object):
    """Una referencia de bloque concreta dentro del DWG: donde esta y
    como esta girada."""

    def __init__(self, punto, angulo, escala, reflejado):
        self.punto = punto            # DB.XYZ en coordenadas de modelo (pies)
        self.angulo = angulo          # radianes sobre el eje Z
        self.escala = escala          # 1.0 = sin escalar
        self.reflejado = reflejado    # True si el bloque viene reflejado


class BloqueRow(object):
    """Agrupa todas las inserciones de un mismo bloque del DWG.

    Es la fila que ve el usuario: un bloque, cuantas veces aparece y
    que familia y tipo se le han asignado."""

    def __init__(self, nombre, capa, cad_nombre, id_simbolo):
        self.Nombre = nombre
        self.Capa = capa
        self.Cad = cad_nombre
        self.IdSimbolo = id_simbolo
        self.IsSelected = False

        self.inserciones = []

        # Lecturas repetidas de la misma insercion que se descartaron.
        self.repetidas = 0

        # Asignacion elegida por el usuario en la seccion 3. Cada bloque
        # lleva la suya, por eso vive en la fila y no en la ventana.
        self.symbol = None

        self.Familia = "-"
        self.Tipo = "-"
        self.Estado = "Sin asignar"
        self.Aviso = "-"
        self.Instancias = 0
        self.ZMinTxt = "-"
        self.ZMaxTxt = "-"

    def asignar(self, familia_nombre, symbol, nombre_tipo):
        self.symbol = symbol
        self.Familia = familia_nombre
        self.Tipo = nombre_tipo
        self.Estado = "Asignado"

    def limpiar_asignacion(self):
        self.symbol = None
        self.Familia = "-"
        self.Tipo = "-"
        self.Estado = "Sin asignar"

    def agregar(self, insercion):
        self.inserciones.append(insercion)

    def recalcular(self):
        """Actualiza los datos derivados de las inserciones."""
        self.Instancias = len(self.inserciones)

        if not self.inserciones:
            self.ZMinTxt = "-"
            self.ZMaxTxt = "-"
            self.Aviso = "-"
            return

        zs = [ins.punto.Z * PIES_A_METROS for ins in self.inserciones]
        self.ZMinTxt = "{0:.3f}".format(min(zs))
        self.ZMaxTxt = "{0:.3f}".format(max(zs))

        avisos = []

        if self.repetidas:
            avisos.append(
                "{0} lectura(s) repetida(s) descartada(s)".format(
                    self.repetidas
                )
            )

        reflejados = len([i for i in self.inserciones if i.reflejado])
        if reflejados:
            avisos.append("{0} reflejado(s)".format(reflejados))

        escalas = [
            i.escala for i in self.inserciones
            if abs(i.escala - 1.0) > TOLERANCIA_ESCALA
        ]
        if escalas:
            avisos.append(
                "{0} escalado(s) (max {1:.2f})".format(
                    len(escalas), max(escalas)
                )
            )

        self.Aviso = ", ".join(avisos) if avisos else "-"


class FamiliaItem(object):
    """Una familia cargada, tal como se muestra en el desplegable."""

    def __init__(self, family):
        self.family = family
        self.Nombre = nombre_elemento(family, "(sin nombre)")

        try:
            self.Categoria = (
                family.FamilyCategory.Name if family.FamilyCategory else "N/D"
            )
        except Exception:
            self.Categoria = "N/D"

        # symbols: indice por nombre en minusculas, para la asignacion
        # automatica por nombre. tipos: lista ordenada para el
        # desplegable de tipos.
        self.symbols = {}
        self.tipos = []

        try:
            for sid in family.GetFamilySymbolIds():
                symbol = doc.GetElement(sid)
                if symbol is None:
                    continue
                nombre_tipo = nombre_elemento(symbol, "")
                if nombre_tipo:
                    self.symbols[nombre_tipo.strip().lower()] = symbol
                    self.tipos.append(TipoItem(symbol, nombre_tipo))
        except Exception:
            pass

        self.tipos.sort(key=lambda t: t.Nombre.lower())

        self.Display = "{0}  ·  {1}   ({2} tipo(s))".format(
            self.Categoria, self.Nombre, len(self.symbols)
        )

    def symbol_para(self, nombre_bloque):
        """FamilySymbol cuyo nombre coincide con el del bloque, o None.

        La comparacion ignora mayusculas y espacios sobrantes: AutoCAD y
        Revit no siempre respetan la misma capitalizacion."""
        if not nombre_bloque:
            return None
        return self.symbols.get(nombre_bloque.strip().lower())


class TipoItem(object):
    """Un tipo (FamilySymbol) tal como se muestra en el desplegable de
    tipos de la familia elegida."""

    def __init__(self, symbol, nombre):
        self.symbol = symbol
        self.Nombre = nombre

        try:
            self.Id = symbol.Id.IntegerValue
        except Exception:
            self.Id = 0

        self.Display = "{0}   (Id {1})".format(self.Nombre, self.Id)


class ResultadoRow(object):
    """Fila del informe final: que paso con cada bloque marcado."""

    def __init__(self, bloque, tipo):
        self.Bloque = bloque
        self.Tipo = tipo
        self.Colocados = 0
        self.Omitidos = 0
        self.Fallidos = 0
        self.Detalle = ""


class IndiceExistentes(object):
    """Puntos del modelo ya ocupados por un ejemplar de cada tipo.

    Es lo que evita duplicar la colocacion: si se vuelve a pulsar
    'Generar', o si se analiza otra vez un CAD que ya se proceso, los
    ejemplares se apilarian exactamente encima de los anteriores sin
    que se note en la vista.

    Los puntos se guardan en una rejilla de celdas del tamaño de la
    tolerancia. Al consultar se miran las 27 celdas vecinas, porque un
    punto justo en el borde de una celda tiene su pareja en la de al
    lado y una comparacion por celda unica lo dejaria pasar."""

    def __init__(self, celda_ft):
        self.celda = celda_ft
        self.celdas = {}

    def _celda_de(self, punto):
        return (
            int(math.floor(punto.X / self.celda)),
            int(math.floor(punto.Y / self.celda)),
            int(math.floor(punto.Z / self.celda)),
        )

    def agregar(self, tipo_id, punto):
        cx, cy, cz = self._celda_de(punto)
        clave = (tipo_id, cx, cy, cz)
        if clave not in self.celdas:
            self.celdas[clave] = []
        self.celdas[clave].append(punto)

    def existe(self, tipo_id, punto):
        cx, cy, cz = self._celda_de(punto)

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    vecinos = self.celdas.get(
                        (tipo_id, cx + dx, cy + dy, cz + dz)
                    )
                    if not vecinos:
                        continue
                    for previo in vecinos:
                        try:
                            if previo.DistanceTo(punto) <= self.celda:
                                return True
                        except Exception:
                            continue

        return False

    def clonar(self):
        """Copia para simular la generacion sin tocar el indice real.

        Hace falta para poder anunciar el numero EXACTO de omisiones
        antes de abrir la transaccion: la cuenta tiene que incluir las
        inserciones que se pisan entre si dentro de la propia tanda."""
        copia = IndiceExistentes(self.celda)
        for clave in self.celdas:
            copia.celdas[clave] = list(self.celdas[clave])
        return copia


class Diagnostico(object):
    """Registro de lo que Revit devolvio al recorrer la geometria.

    Existe porque la estructura real de un DWG dentro de Revit varia
    entre archivos: sin poder verla, un fallo de deteccion no se puede
    diagnosticar."""

    def __init__(self):
        self.lineas = []
        self.truncado = False
        self.objetos = 0
        self.instancias = 0
        self.por_clase = {}
        self.nombres_descartados = {}

    def anotar(self, linea):
        if len(self.lineas) >= LIMITE_LINEAS_DIAGNOSTICO:
            self.truncado = True
            return
        self.lineas.append(texto(linea))

    def contar_objeto(self, obj):
        self.objetos += 1
        try:
            clase = obj.GetType().Name
        except Exception:
            clase = "(desconocida)"
        self.por_clase[clase] = self.por_clase.get(clase, 0) + 1

    def contar_descarte(self, nombre):
        self.nombres_descartados[nombre] = (
            self.nombres_descartados.get(nombre, 0) + 1
        )


# ==============================================================================
# VENTANA
# ==============================================================================

class ElementosBIMWPF(forms.WPFWindow, CurvasCADMixin):
    """Ventana del generador: CAD -> bloques -> familia -> ejemplares."""

    def __init__(self):
        xaml_path = os.path.join(os.path.dirname(__file__), "ui.xaml")
        forms.WPFWindow.__init__(self, xaml_path)

        self.all_cad_rows = []
        self.all_bloque_rows = []
        self.all_familia_items = []
        self.all_resultado_rows = []

        # Seleccion actual de los desplegables del paso 3. Es solo el
        # estado de la UI: la asignacion definitiva vive en cada
        # BloqueRow, para poder dar familias y tipos distintos a bloques
        # distintos.
        self.familia_activa = None
        self.tipo_activo = None

        self.ultimo_diagnostico = None

        # Ids de los ejemplares creados, para dejarlos seleccionados en
        # Revit despues de cerrar la ventana.
        self.ids_creados = []

        # Guarda contra los eventos que WPF dispara al rellenar los
        # controles (SelectionChanged / TextChanged durante la carga).
        self._cargando = True

        for accion, descripcion in (
            (self.detectar_cads, "los archivos CAD"),
            (self.detectar_familias, "las familias"),
        ):
            try:
                accion()
            except Exception as error:
                self.txt_status_bar.Text = (
                    "No se pudo detectar {0}: {1}".format(descripcion, error)
                )

        self._cargando = False
        self.iniciar_curvas()

    @safe_handler
    def on_curvas_analizar(self, sender, args):
        self.curvas_analizar()

    @safe_handler
    def on_curvas_marcar(self, sender, args):
        self.curvas_marcar(True)

    @safe_handler
    def on_curvas_desmarcar(self, sender, args):
        self.curvas_marcar(False)

    @safe_handler
    def on_curvas_familia(self, sender, args):
        if not getattr(self, '_cargando', True):
            self.curvas_familia()

    @safe_handler
    def on_curvas_buscar(self, sender, args):
        if not getattr(self, '_cargando', True):
            self.curvas_filtrar_familias()

    @safe_handler
    def on_curvas_asignar(self, sender, args):
        self.curvas_asignar()

    @safe_handler
    def on_curvas_quitar(self, sender, args):
        for row in self.capas_rows:
            if row.IsSelected:
                row.symbol = None
                row.Familia = row.Tipo = '-'
                row.Modo = 'Sin asignar'
        self._refrescar(self.lv_capas_curvas, self.capas_rows)

    @safe_handler
    def on_curvas_generar(self, sender, args):
        self.curvas_generar()

    # --------------------------------------------------------------------------
    # ARCHIVOS CAD
    # --------------------------------------------------------------------------

    def detectar_cads(self):
        """Escanea el documento y llena la lista de archivos CAD
        vinculados / importados, clasificandolos."""
        cad_elements = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.ImportInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )

        self.all_cad_rows = [CadRow(c) for c in cad_elements]
        self.lv_cads.ItemsSource = self.all_cad_rows

        if len(self.all_cad_rows) == 1:
            self.all_cad_rows[0].IsSelected = True
            self._refrescar(self.lv_cads, self.all_cad_rows)

        self.txt_status_bar.Text = (
            "{0} archivo(s) CAD detectado(s).".format(len(self.all_cad_rows))
        )

    @safe_handler
    def on_detectar_cad(self, sender, args):
        self.detectar_cads()

    @safe_handler
    def on_cad_select_all(self, sender, args):
        for row in self.all_cad_rows:
            row.IsSelected = True
        self._refrescar(self.lv_cads, self.all_cad_rows)

    @safe_handler
    def on_cad_deselect_all(self, sender, args):
        for row in self.all_cad_rows:
            row.IsSelected = False
        self._refrescar(self.lv_cads, self.all_cad_rows)

    # --------------------------------------------------------------------------
    # ANALISIS DE BLOQUES DEL CAD
    # --------------------------------------------------------------------------

    def _nombre_capa(self, obj):
        """Nombre de la capa (layer) del DWG a la que pertenece un
        objeto de geometria. Revit expone la capa como el GraphicsStyle
        asociado al objeto."""
        try:
            gs_id = obj.GraphicsStyleId
            if gs_id is not None and gs_id != DB.ElementId.InvalidElementId:
                gs = doc.GetElement(gs_id)
                if gs is not None and gs.Name:
                    return gs.Name
        except Exception:
            pass
        return "(sin capa)"

    def _capa_de_bloque(self, geom_instance):
        """Capa de una referencia de bloque.

        El propio GeometryInstance no siempre trae GraphicsStyle: en ese
        caso se toma la de la primera geometria que contenga."""
        capa = self._nombre_capa(geom_instance)
        if capa != "(sin capa)":
            return capa

        try:
            interior = geom_instance.GetSymbolGeometry()
        except Exception:
            return capa

        if interior is None:
            return capa

        revisados = 0
        for obj in interior:
            candidata = self._nombre_capa(obj)
            if candidata != "(sin capa)":
                return candidata
            revisados += 1
            if revisados >= 5:
                break

        return capa

    def _simbolo_de(self, geom_instance):
        """Element al que apunta el GeometryInstance, o None."""
        try:
            return geom_instance.Symbol
        except Exception:
            return None

    def _nombre_bloque(self, geom_instance):
        """Nombre del bloque de AutoCAD de una referencia.

        Revit lo expone en el 'Symbol' del GeometryInstance anidado. El
        GeometryInstance de nivel superior devuelve, en cambio, el
        nombre del propio archivo DWG."""
        symbol = self._simbolo_de(geom_instance)

        if symbol is None:
            return "(sin simbolo)"

        nombre = nombre_elemento(symbol, "")
        if nombre:
            return nombre

        # Respaldo: algunas versiones dejan el nombre del bloque en la
        # categoria del simbolo en lugar de en su Name.
        try:
            if symbol.Category is not None and symbol.Category.Name:
                return symbol.Category.Name
        except Exception:
            pass

        return "(sin nombre)"

    def _id_simbolo(self, geom_instance):
        symbol = self._simbolo_de(geom_instance)
        if symbol is None:
            return 0
        try:
            return symbol.Id.IntegerValue
        except Exception:
            return 0

    def _analizar_transformada(self, transform):
        """Punto de insercion, giro, escala y reflejo de un bloque.

        El giro es el angulo del eje X local del bloque medido en el
        plano XY del modelo: es lo que hay que aplicar al ejemplar de
        familia para que quede alineado con el bloque."""
        origen = transform.Origin
        bx = transform.BasisX

        escala = math.sqrt(bx.X * bx.X + bx.Y * bx.Y + bx.Z * bx.Z)
        angulo = math.atan2(bx.Y, bx.X)

        try:
            reflejado = transform.Determinant < 0
        except Exception:
            try:
                reflejado = (
                    transform.BasisX
                    .CrossProduct(transform.BasisY)
                    .DotProduct(transform.BasisZ) < 0
                )
            except Exception:
                reflejado = False

        return origen, angulo, escala, reflejado

    def _materializar(self, geom_element):
        """Pasa un GeometryElement a lista, o None si no da nada.

        Hace falta poder distinguir 'no devolvio geometria' de 'devolvio
        una coleccion vacia': GetSymbolGeometry() puede devolver un
        GeometryElement valido pero sin un solo objeto dentro, y ahi hay
        que seguir probando por otra via."""
        if geom_element is None:
            return None
        try:
            objetos = list(geom_element)
        except Exception:
            return None
        return objetos if objetos else None

    def _geometria_hija(self, geom_instance, transform_total):
        """Geometria interior de un GeometryInstance y la transformada
        que hay que pasar a los hijos.

        Con GetSymbolGeometry() los hijos vienen SIN transformar, asi que
        se les pasa la transformada acumulada para que la compongan, y
        los bloques anidados SE CONSERVAN como GeometryInstance. Esta es
        la via buena: GetInstanceGeometry() aplana el arbol y los bloques
        desaparecen convertidos en curvas sueltas, que es justo por lo
        que la lista de bloques salia vacia.

        GetInstanceGeometry() queda como ultimo respaldo; su geometria ya
        esta en coordenadas de modelo, asi que los hijos NO deben
        componer nada y se les pasa None."""
        objetos = self._materializar(self._llamar(
            geom_instance, "GetSymbolGeometry"
        ))
        if objetos:
            return objetos, transform_total

        objetos = self._materializar(self._llamar(
            geom_instance, "GetInstanceGeometry"
        ))
        if objetos:
            return objetos, None

        return None, None

    def _llamar(self, obj, nombre_metodo):
        """Invoca un metodo de la API que puede no existir o lanzar."""
        try:
            metodo = getattr(obj, nombre_metodo, None)
            if metodo is None:
                return None
            return metodo()
        except Exception:
            return None

    def _recorrer(self, geom_element, cad_row, mapa, transform_padre,
                  profundidad, diag, registrar_desde):
        """Recorre la geometria del CAD registrando las referencias de
        bloque que encuentra.

        transform_padre es la transformada acumulada hasta este nivel, o
        None si la geometria ya viene en coordenadas de modelo."""
        if geom_element is None or profundidad > PROFUNDIDAD_MAXIMA:
            return

        objetos = self._materializar(geom_element)

        if not objetos:
            diag.anotar(
                u"{0}[prof {1}] nivel vacio.".format(
                    "  " * profundidad, profundidad
                )
            )
            return

        diag.anotar(
            u"{0}[prof {1}] {2} objeto(s): {3}".format(
                "  " * profundidad, profundidad, len(objetos),
                texto(self._resumen_clases(objetos))
            )
        )

        for obj in objetos:

            diag.contar_objeto(obj)

            if not isinstance(obj, DB.GeometryInstance):
                continue

            diag.instancias += 1

            try:
                propia = obj.Transform
            except Exception:
                propia = None

            if propia is None:
                total = transform_padre
            elif transform_padre is None:
                total = propia
            else:
                total = transform_padre.Multiply(propia)

            nombre = self._nombre_bloque(obj)
            id_simbolo = self._id_simbolo(obj)

            origen_txt = "-"
            if total is not None:
                try:
                    origen_txt = "{0:.3f}, {1:.3f}, {2:.3f}".format(
                        total.Origin.X * PIES_A_METROS,
                        total.Origin.Y * PIES_A_METROS,
                        total.Origin.Z * PIES_A_METROS
                    )
                except Exception:
                    origen_txt = "(no legible)"

            diag.anotar(
                u"{0}[prof {1}] GeometryInstance  simbolo='{2}' (Id {3}, {4})"
                u"  origen(m)={5}".format(
                    "  " * profundidad, profundidad, texto(nombre), id_simbolo,
                    texto(self._clase_simbolo(obj)), origen_txt
                )
            )

            es_bloque = profundidad >= registrar_desde

            # El envoltorio del propio DWG lleva el nombre del archivo:
            # no es un bloque aunque este a la profundidad registrable.
            if es_bloque and nombre.strip().lower() == cad_row.Nombre.strip().lower():
                diag.contar_descarte(nombre)
                es_bloque = False

            if es_bloque and total is not None:
                try:
                    punto, angulo, escala, reflejado = (
                        self._analizar_transformada(total)
                    )
                except Exception:
                    punto = None

                if punto is not None:
                    clave = (nombre, id_simbolo, cad_row.Nombre)

                    if clave not in mapa:
                        mapa[clave] = BloqueRow(
                            nombre, self._capa_de_bloque(obj),
                            cad_row.Nombre, id_simbolo
                        )

                    mapa[clave].agregar(
                        Insercion(punto, angulo, escala, reflejado)
                    )

            interior, transform_hijos = self._geometria_hija(obj, total)

            self._recorrer(
                interior, cad_row, mapa, transform_hijos,
                profundidad + 1, diag, registrar_desde
            )

    def _clase_simbolo(self, geom_instance):
        symbol = self._simbolo_de(geom_instance)
        if symbol is None:
            return "sin simbolo"
        try:
            return symbol.GetType().Name
        except Exception:
            return "clase desconocida"

    def _resumen_clases(self, objetos):
        """'Line x120, GeometryInstance x8' para una linea del arbol.

        Es lo que permite ver de un vistazo si un nivel trae bloques
        (GeometryInstance) o solo curvas ya aplanadas."""
        conteo = {}
        for obj in objetos:
            try:
                clase = obj.GetType().Name
            except Exception:
                clase = "(desconocida)"
            conteo[clase] = conteo.get(clase, 0) + 1

        return ", ".join(
            ["{0} x{1}".format(clase, conteo[clase])
             for clase in sorted(conteo.keys())]
        )

    def _variantes_opciones(self):
        """Configuraciones de Options a probar, en orden.

        Un mismo DWG puede no devolver geometria con unas Options y si
        con otras, segun como este importado y que vista este activa."""
        variantes = []

        base = DB.Options()
        base.ComputeReferences = False
        base.IncludeNonVisibleObjects = False
        try:
            base.DetailLevel = DB.ViewDetailLevel.Fine
        except Exception:
            pass
        variantes.append(("solo visible, detalle fino", base))

        completa = DB.Options()
        completa.ComputeReferences = False
        completa.IncludeNonVisibleObjects = True
        try:
            completa.DetailLevel = DB.ViewDetailLevel.Fine
        except Exception:
            pass
        variantes.append(("incluyendo objetos no visibles", completa))

        # Contra la vista activa. La API no permite fijar DetailLevel y
        # View a la vez, por eso esta variante no lo toca.
        try:
            vista = doc.ActiveView
        except Exception:
            vista = None

        if vista is not None:
            try:
                por_vista = DB.Options()
                por_vista.ComputeReferences = False
                por_vista.IncludeNonVisibleObjects = True
                por_vista.View = vista
                variantes.append(("segun la vista activa", por_vista))
            except Exception:
                pass

        return variantes

    def _analizar_cad(self, cad_row, diag):
        """Bloques de UN archivo CAD.

        Prueba cada configuracion de Options y, si ninguna da bloques,
        repite registrando tambien desde la profundidad 0 por si el DWG
        no trae el GeometryInstance envoltorio."""
        for registrar_desde in (1, 0):
            for etiqueta, opts in self._variantes_opciones():

                diag.anotar(
                    u"\n--- {0} | Options: {1} | registrando desde "
                    u"profundidad {2} ---".format(
                        texto(cad_row.Nombre), etiqueta, registrar_desde
                    )
                )

                mapa = {}

                try:
                    geom = cad_row.element.get_Geometry(opts)
                except Exception as error:
                    diag.anotar(
                        u"    get_Geometry fallo: {0}".format(texto(error))
                    )
                    continue

                if geom is None:
                    diag.anotar(u"    get_Geometry devolvio None.")
                    continue

                try:
                    self._recorrer(
                        geom, cad_row, mapa, None, 0, diag, registrar_desde
                    )
                except Exception as error:
                    diag.anotar(
                        u"    el recorrido fallo: {0}".format(texto(error))
                    )
                    continue

                if mapa:
                    diag.anotar(
                        u"    -> {0} bloque(s) distinto(s) con esta "
                        u"configuracion.".format(len(mapa))
                    )
                    return mapa, "{0} (desde prof. {1})".format(
                        etiqueta, registrar_desde
                    )

                diag.anotar(u"    -> sin bloques con esta configuracion.")

        return {}, "ninguna configuracion devolvio bloques"

    def _deduplicar_inserciones(self, bloque_row):
        """Descarta inserciones repetidas en el mismo punto y giro.

        Un mismo bloque puede aparecer mas de una vez en el recorrido si
        el DWG lo referencia desde varios contenedores.

        La comparacion se hace con una clave redondeada y no contra
        todas las inserciones anteriores: un DWG de instalaciones puede
        traer miles de referencias del mismo bloque y el recorrido en
        parejas dejaba a Revit sin responder."""
        vistas = set()
        unicas = []

        for ins in bloque_row.inserciones:
            clave = (
                round(ins.punto.X / TOLERANCIA_DUPLICADOS_FT),
                round(ins.punto.Y / TOLERANCIA_DUPLICADOS_FT),
                round(ins.punto.Z / TOLERANCIA_DUPLICADOS_FT),
                round(ins.angulo, 6),
            )

            if clave in vistas:
                continue

            vistas.add(clave)
            unicas.append(ins)

        bloque_row.repetidas = len(bloque_row.inserciones) - len(unicas)
        bloque_row.inserciones = unicas

    def analizar_bloques(self, cad_rows):
        """Construye el listado de bloques con sus inserciones,
        agregando la informacion de todos los CAD marcados."""
        diag = Diagnostico()
        mapa = {}
        resumen = []

        for cad_row in cad_rows:
            parcial, etiqueta = self._analizar_cad(cad_row, diag)

            # Se FUSIONA, no se reemplaza: dos CAD marcados que sean el
            # mismo archivo vinculado dos veces comparten clave, y un
            # update() habria tirado los bloques del primero. Las
            # inserciones que coincidan se descartan luego en la
            # deduplicacion.
            for clave in parcial:
                if clave in mapa:
                    mapa[clave].inserciones.extend(parcial[clave].inserciones)
                else:
                    mapa[clave] = parcial[clave]

            resumen.append((cad_row.Nombre, len(parcial), etiqueta))

        filas = list(mapa.values())

        for fila in filas:
            self._deduplicar_inserciones(fila)
            fila.recalcular()

        filas.sort(key=lambda f: (-f.Instancias, f.Nombre))

        diag.resumen_por_cad = resumen
        self.ultimo_diagnostico = diag

        return filas, resumen

    @safe_handler
    def on_analizar_bloques(self, sender, args):
        marcados = [r for r in self.all_cad_rows if r.IsSelected]

        if not marcados:
            forms.alert(
                "Marca al menos un archivo CAD de la lista de arriba.",
                title="Atencion"
            )
            return

        if not self._confirmar_reanalisis():
            return

        self.txt_status_bar.Text = "Analizando bloques del CAD..."

        self.all_bloque_rows, resumen = self.analizar_bloques(marcados)
        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

        if not self.all_bloque_rows:
            diag = self.ultimo_diagnostico
            self.txt_status_bar.Text = (
                "No se encontro ninguna referencia de bloque. Se recorrieron "
                "{0} objeto(s) de geometria, {1} de ellos GeometryInstance. "
                "Pulsa 'Guardar Diagnostico' para ver el detalle.".format(
                    diag.objetos, diag.instancias
                )
            )
            return

        total = sum(r.Instancias for r in self.all_bloque_rows)
        repetidas = sum(r.repetidas for r in self.all_bloque_rows)

        detalle = "; ".join(
            ["{0}: {1} bloque(s) [{2}]".format(n, c, e) for n, c, e in resumen]
        )

        aviso_repetidas = ""
        if repetidas:
            aviso_repetidas = (
                " Se descartaron {0} lectura(s) repetida(s) de la misma "
                "insercion.".format(repetidas)
            )

        self.txt_status_bar.Text = (
            "{0} bloque(s) distinto(s) y {1} insercion(es).{2} {3}".format(
                len(self.all_bloque_rows), total, aviso_repetidas, detalle
            )
        )

    def _confirmar_reanalisis(self):
        """Avisa antes de rehacer un analisis que ya existe.

        Volver a analizar reconstruye la tabla desde cero, asi que las
        asignaciones de familia y tipo se pierden. Devuelve False si el
        usuario prefiere no seguir."""
        if not self.all_bloque_rows:
            return True

        asignados = len(
            [r for r in self.all_bloque_rows if r.symbol is not None]
        )

        aviso_asignados = ""
        if asignados:
            aviso_asignados = (
                "\n\nSe perderan las asignaciones de familia y tipo de {0} "
                "bloque(s).".format(asignados)
            )

        aviso_generado = ""
        if self.ids_creados:
            aviso_generado = (
                "\n\nOJO: en esta sesion ya se colocaron {0} elemento(s). "
                "Volver a analizar y generar los duplicaria; para evitarlo, "
                "deja marcada la casilla 'Evitar duplicados' al "
                "generar.".format(len(self.ids_creados))
            )

        return bool(forms.alert(
            "Ya hay {0} bloque(s) analizados. Al volver a analizar se "
            "rehace la tabla desde cero.{1}{2}\n\n¿Continuar?".format(
                len(self.all_bloque_rows), aviso_asignados, aviso_generado
            ),
            title="Volver a analizar",
            yes=True,
            no=True
        ))

    @safe_handler
    def on_bloque_select_all(self, sender, args):
        for row in self.all_bloque_rows:
            row.IsSelected = True
        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

    @safe_handler
    def on_bloque_deselect_all(self, sender, args):
        for row in self.all_bloque_rows:
            row.IsSelected = False
        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

    @safe_handler
    def on_bloque_select_pendientes(self, sender, args):
        """Marca solo los bloques a los que aun no se les ha asignado
        familia y tipo."""
        for row in self.all_bloque_rows:
            row.IsSelected = row.symbol is None
        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

    def _actualizar_resumen_bloques(self):
        if not self.all_bloque_rows:
            self.lbl_resumen_bloques.Text = "Sin bloques analizados."
            return

        marcados = [r for r in self.all_bloque_rows if r.IsSelected]
        listos = [r for r in marcados if r.symbol is not None]
        total = sum(r.Instancias for r in listos)

        self.lbl_resumen_bloques.Text = (
            "{0} bloque(s) marcado(s), {1} asignado(s) -> {2} ejemplar(es) "
            "a crear.".format(len(marcados), len(listos), total)
        )

    # --------------------------------------------------------------------------
    # DIAGNOSTICO
    # --------------------------------------------------------------------------

    @safe_handler
    def on_guardar_diagnostico(self, sender, args):
        """Vuelca a un .txt el arbol de geometria del ultimo analisis.

        Se escribe a archivo, y no a la ventana de salida de pyRevit,
        porque abrir otra ventana desde un dialogo modal es justo lo que
        cierra Revit."""
        if self.ultimo_diagnostico is None:
            forms.alert(
                "Primero marca el/los CAD y pulsa 'Analizar Bloques'.",
                title="Atencion"
            )
            return

        ruta = self._escribir_diagnostico(self.ultimo_diagnostico)

        self.txt_status_bar.Text = "Diagnostico guardado en {0}".format(ruta)

        try:
            os.startfile(ruta)
        except Exception:
            forms.alert(
                "Diagnostico guardado en:\n\n{0}".format(ruta),
                title="Diagnostico"
            )

    def _escribir_diagnostico(self, diag):
        """Escribe el informe y devuelve la ruta del archivo."""
        marca = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta = os.path.join(
            tempfile.gettempdir(),
            "ElementosBIM_diagnostico_{0}.txt".format(marca)
        )

        lineas = []
        lineas.append(u"DIAGNOSTICO DE DETECCION DE BLOQUES CAD")
        lineas.append(u"=" * 70)
        lineas.append(u"Fecha: {0}".format(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        try:
            lineas.append(u"Documento: {0}".format(texto(doc.Title)))
        except Exception:
            pass

        try:
            lineas.append(u"Version de Revit: {0}".format(
                texto(doc.Application.VersionNumber)
            ))
        except Exception:
            pass

        try:
            lineas.append(u"Vista activa: {0}".format(
                texto(nombre_elemento(doc.ActiveView, "N/D"))
            ))
        except Exception:
            pass

        lineas.append(u"")
        lineas.append(u"RESUMEN POR ARCHIVO CAD")
        lineas.append(u"-" * 70)
        for nombre, cuantos, etiqueta in getattr(diag, "resumen_por_cad", []):
            lineas.append(u"  {0}: {1} bloque(s) | {2}".format(
                texto(nombre), cuantos, texto(etiqueta)
            ))

        lineas.append(u"")
        lineas.append(u"TOTALES DEL RECORRIDO")
        lineas.append(u"-" * 70)
        lineas.append(u"  Objetos de geometria recorridos: {0}".format(
            diag.objetos
        ))
        lineas.append(u"  GeometryInstance encontrados: {0}".format(
            diag.instancias
        ))

        lineas.append(u"")
        lineas.append(u"  Objetos por clase:")
        for clase in sorted(diag.por_clase.keys()):
            lineas.append(u"    {0}: {1}".format(
                texto(clase), diag.por_clase[clase]
            ))

        if diag.nombres_descartados:
            lineas.append(u"")
            lineas.append(
                u"  Descartados por coincidir con el nombre del archivo CAD:"
            )
            for nombre in sorted(diag.nombres_descartados.keys()):
                lineas.append(u"    {0}: {1}".format(
                    texto(nombre), diag.nombres_descartados[nombre]
                ))

        lineas.append(u"")
        lineas.append(u"ARBOL DE GEOMETRIA")
        lineas.append(u"-" * 70)
        lineas.extend(diag.lineas)

        if diag.truncado:
            lineas.append(u"")
            lineas.append(
                u"[...] Arbol truncado en {0} lineas.".format(
                    LIMITE_LINEAS_DIAGNOSTICO
                )
            )

        with codecs.open(ruta, "w", "utf-8") as archivo:
            archivo.write(u"\n".join(lineas))

        return ruta

    # --------------------------------------------------------------------------
    # FAMILIAS
    # --------------------------------------------------------------------------

    def detectar_familias(self):
        """Lista las familias cargadas que tienen al menos un tipo."""
        familias = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.Family)
            .ToElements()
        )

        items = []
        for family in familias:
            item = FamiliaItem(family)
            if item.symbols:
                items.append(item)

        items.sort(key=lambda i: (i.Categoria, i.Nombre))
        self.all_familia_items = items

        self._llenar_combo_familias(items)

        self.txt_status_bar.Text = (
            "{0} familia(s) cargada(s) con tipos.".format(len(items))
        )

    def _llenar_combo_familias(self, items):
        """Rellena el desplegable sin que salte on_familia_changed."""
        anterior = self._cargando
        self._cargando = True
        try:
            self.cmb_familias.ItemsSource = items
        finally:
            self._cargando = anterior

    @safe_handler
    def on_recargar_familias(self, sender, args):
        """Vuelve a leer las familias del documento.

        Las asignaciones ya hechas se borran: los FamilySymbol guardados
        en las filas pueden haber dejado de ser validos si se recargo o
        se elimino una familia."""
        self.detectar_familias()
        self.curvas_detectar_tipos()
        for row in self.capas_rows:
            row.symbol = None
            row.Familia = row.Tipo = '-'
            row.Modo = 'Sin asignar'
        self._refrescar(self.lv_capas_curvas, self.capas_rows)

        self.familia_activa = None
        self.tipo_activo = None
        self._llenar_combo_tipos([])

        for row in self.all_bloque_rows:
            row.limpiar_asignacion()

        self.lbl_asignacion.Text = (
            "Familias recargadas. Se han borrado las asignaciones previas."
        )
        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

    @safe_handler
    def on_familia_buscar(self, sender, args):
        if self._cargando:
            return

        criterio = (self.txt_familia_buscar.Text or "").strip().lower()

        if not criterio:
            filtradas = self.all_familia_items
        else:
            filtradas = [
                i for i in self.all_familia_items
                if criterio in i.Nombre.lower()
                or criterio in i.Categoria.lower()
            ]

        self._llenar_combo_familias(filtradas)

        if criterio:
            self.txt_status_bar.Text = (
                "{0} familia(s) coinciden con '{1}'.".format(
                    len(filtradas), criterio
                )
            )
        else:
            self.txt_status_bar.Text = (
                "{0} familia(s) cargada(s) con tipos.".format(
                    len(self.all_familia_items)
                )
            )

    @safe_handler
    def on_familia_changed(self, sender, args):
        """Al cambiar de familia se recarga el desplegable de tipos.

        Cambiar de familia NO toca las asignaciones ya hechas: la idea
        es poder recorrer varias familias asignando bloques distintos."""
        if self._cargando:
            return

        seleccion = self.cmb_familias.SelectedItem
        self.familia_activa = seleccion
        self.tipo_activo = None

        if seleccion is None:
            self._llenar_combo_tipos([])
            self.lbl_asignacion.Text = "Elige una familia para ver sus tipos."
            return

        self._llenar_combo_tipos(seleccion.tipos)

        if seleccion.tipos:
            self.cmb_tipos.SelectedIndex = 0
            self.tipo_activo = self.cmb_tipos.SelectedItem

        self._actualizar_etiqueta_asignacion()

    @safe_handler
    def on_tipo_changed(self, sender, args):
        if self._cargando:
            return

        self.tipo_activo = self.cmb_tipos.SelectedItem
        self._actualizar_etiqueta_asignacion()

    def _llenar_combo_tipos(self, tipos):
        """Rellena el desplegable de tipos sin que salte on_tipo_changed."""
        anterior = self._cargando
        self._cargando = True
        try:
            self.cmb_tipos.ItemsSource = tipos
        finally:
            self._cargando = anterior

    def _actualizar_etiqueta_asignacion(self):
        if self.familia_activa is None:
            self.lbl_asignacion.Text = "Elige una familia para ver sus tipos."
            return

        if self.tipo_activo is None:
            self.lbl_asignacion.Text = (
                "{0} tiene {1} tipo(s). Elige uno.".format(
                    self.familia_activa.Nombre, len(self.familia_activa.tipos)
                )
            )
            return

        self.lbl_asignacion.Text = (
            "Se asignara: {0} : {1}".format(
                self.familia_activa.Nombre, self.tipo_activo.Nombre
            )
        )

    # --------------------------------------------------------------------------
    # ASIGNACION DE FAMILIA Y TIPO A CADA BLOQUE
    # --------------------------------------------------------------------------

    def _bloques_marcados(self):
        """Filas marcadas, avisando si no hay ninguna."""
        marcados = [r for r in self.all_bloque_rows if r.IsSelected]

        if not marcados:
            forms.alert(
                "Marca al menos un bloque en la tabla del paso 2.",
                title="Atencion"
            )
            return None

        return marcados

    @safe_handler
    def on_asignar(self, sender, args):
        """Asigna la familia y el tipo elegidos a los bloques marcados."""
        if self.familia_activa is None or self.tipo_activo is None:
            forms.alert(
                "Elige una familia y un tipo antes de asignar.",
                title="Atencion"
            )
            return

        marcados = self._bloques_marcados()
        if marcados is None:
            return

        for row in marcados:
            row.asignar(
                self.familia_activa.Nombre,
                self.tipo_activo.symbol,
                self.tipo_activo.Nombre
            )

        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

        self.txt_status_bar.Text = (
            "{0} bloque(s) asignado(s) a {1} : {2}.".format(
                len(marcados), self.familia_activa.Nombre,
                self.tipo_activo.Nombre
            )
        )

    @safe_handler
    def on_asignar_auto(self, sender, args):
        """Asigna a cada bloque marcado el tipo de la familia elegida que
        se llama igual que el bloque.

        Es el criterio del grafo de Dynamo original
        (FamilyType.ByFamilyNameAndTypeName con el BlockName)."""
        if self.familia_activa is None:
            forms.alert(
                "Elige primero la familia donde buscar los tipos.",
                title="Atencion"
            )
            return

        marcados = self._bloques_marcados()
        if marcados is None:
            return

        asignados = 0
        sin_tipo = []

        for row in marcados:
            symbol = self.familia_activa.symbol_para(row.Nombre)

            if symbol is None:
                sin_tipo.append(row.Nombre)
                continue

            row.asignar(
                self.familia_activa.Nombre,
                symbol,
                nombre_elemento(symbol, "N/D")
            )
            asignados += 1

        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

        if sin_tipo:
            self.txt_status_bar.Text = (
                "{0} bloque(s) asignado(s) por nombre. Sin tipo en '{1}': "
                "{2}{3}".format(
                    asignados, self.familia_activa.Nombre,
                    ", ".join(sin_tipo[:6]),
                    "..." if len(sin_tipo) > 6 else ""
                )
            )
        else:
            self.txt_status_bar.Text = (
                "{0} bloque(s) asignado(s) por nombre en '{1}'.".format(
                    asignados, self.familia_activa.Nombre
                )
            )

    @safe_handler
    def on_quitar_asignacion(self, sender, args):
        marcados = self._bloques_marcados()
        if marcados is None:
            return

        for row in marcados:
            row.limpiar_asignacion()

        self._refrescar(self.lv_bloques, self.all_bloque_rows)
        self._actualizar_resumen_bloques()

        self.txt_status_bar.Text = (
            "Asignacion quitada de {0} bloque(s).".format(len(marcados))
        )

    # --------------------------------------------------------------------------
    # GENERACION DE EJEMPLARES
    # --------------------------------------------------------------------------

    def _nivel_mas_cercano(self, z_ft):
        """Nivel del documento cuya cota queda mas cerca de z_ft.

        Solo se usa como respaldo, para las familias que exigen nivel al
        colocarse; la cota final se corrige despues."""
        niveles = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.Level)
            .WhereElementIsNotElementType()
            .ToElements()
        )

        mejor = None
        mejor_dz = None

        for nivel in niveles:
            try:
                dz = abs(nivel.Elevation - z_ft)
            except Exception:
                continue
            if mejor_dz is None or dz < mejor_dz:
                mejor = nivel
                mejor_dz = dz

        return mejor

    def _crear_ejemplar(self, symbol, insercion):
        """Coloca un ejemplar en el punto de la insercion y lo gira.

        Debe llamarse dentro de una transaccion abierta."""
        punto = insercion.punto

        try:
            instancia = doc.Create.NewFamilyInstance(
                punto, symbol, DB.Structure.StructuralType.NonStructural
            )
        except Exception:
            # Las familias que dependen de un nivel rechazan la version
            # sin nivel de NewFamilyInstance.
            nivel = self._nivel_mas_cercano(punto.Z)
            if nivel is None:
                raise
            instancia = doc.Create.NewFamilyInstance(
                punto, symbol, nivel, DB.Structure.StructuralType.NonStructural
            )

        doc.Regenerate()

        self._corregir_cota(instancia, punto.Z)
        self._girar(instancia, punto, insercion.angulo)

        return instancia

    def _corregir_cota(self, instancia, z_objetivo_ft):
        """Lleva el ejemplar a la cota del bloque del CAD.

        Al colocar contra un nivel, Revit apoya el ejemplar en la cota
        del nivel y no en la Z pedida."""
        try:
            location = instancia.Location
        except Exception:
            return

        if not isinstance(location, DB.LocationPoint):
            return

        try:
            dz = z_objetivo_ft - location.Point.Z
        except Exception:
            return

        if abs(dz) <= TOLERANCIA_Z_FT:
            return

        try:
            DB.ElementTransformUtils.MoveElement(
                doc, instancia.Id, DB.XYZ(0, 0, dz)
            )
        except Exception:
            pass

    def _girar(self, instancia, punto, angulo):
        """Gira el ejemplar sobre el eje vertical que pasa por su punto
        de insercion."""
        if abs(angulo) <= 1e-9:
            return

        eje = DB.Line.CreateBound(
            DB.XYZ(punto.X, punto.Y, punto.Z),
            DB.XYZ(punto.X, punto.Y, punto.Z + 1.0)
        )

        DB.ElementTransformUtils.RotateElement(
            doc, instancia.Id, eje, angulo
        )

    def _indice_existentes(self, filas):
        """Indice de los ejemplares que YA hay en el modelo, limitado a
        los tipos que se van a colocar.

        Solo se recorren las FamilyInstance una vez y se filtran por
        tipo: en un modelo grande no merece la pena indexar lo que no
        se va a consultar."""
        indice = IndiceExistentes(TOLERANCIA_EXISTENTE_FT)

        tipos = set()
        for row in filas:
            if row.symbol is None:
                continue
            try:
                tipos.add(row.symbol.Id.IntegerValue)
            except Exception:
                continue

        if not tipos:
            return indice

        instancias = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.FamilyInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )

        for instancia in instancias:
            try:
                tipo_id = instancia.GetTypeId().IntegerValue
            except Exception:
                continue

            if tipo_id not in tipos:
                continue

            try:
                location = instancia.Location
            except Exception:
                continue

            if not isinstance(location, DB.LocationPoint):
                continue

            try:
                indice.agregar(tipo_id, location.Point)
            except Exception:
                continue

        return indice

    def _contar_duplicados(self, filas, indice):
        """Cuantas inserciones se van a omitir por estar ya colocadas.

        Se cuenta sobre una copia del indice para que el numero incluya
        las inserciones que se pisan entre si dentro de la propia tanda,
        igual que hara la colocacion real."""
        if indice is None:
            return 0

        simulacion = indice.clonar()
        duplicados = 0

        for row in filas:
            try:
                tipo_id = row.symbol.Id.IntegerValue
            except Exception:
                continue

            for insercion in row.inserciones:
                if simulacion.existe(tipo_id, insercion.punto):
                    duplicados += 1
                else:
                    simulacion.agregar(tipo_id, insercion.punto)

        return duplicados

    @safe_handler
    def on_generar(self, sender, args):
        """Coloca los ejemplares de los bloques marcados.

        Cada bloque usa la familia y el tipo que tenga asignados en el
        paso 3; no hay una familia global."""
        marcados = [r for r in self.all_bloque_rows if r.IsSelected]

        if not marcados:
            forms.alert(
                "Marca al menos un bloque de la lista.\n\n"
                "Si la lista esta vacia, marca el/los archivo(s) CAD y pulsa "
                "'Analizar Bloques'.",
                title="Atencion"
            )
            return

        listos = [r for r in marcados if r.symbol is not None]
        sin_tipo = [r for r in marcados if r.symbol is None]

        if not listos:
            forms.alert(
                "Ninguno de los bloques marcados tiene familia y tipo "
                "asignados.\n\n"
                "Usa el paso 3: elige familia y tipo, y pulsa 'Asignar a los "
                "bloques marcados'.",
                title="Sin asignacion"
            )
            return

        total = sum(r.Instancias for r in listos)

        evitar_duplicados = bool(self.chk_evitar_duplicados.IsChecked)

        indice = self._indice_existentes(listos) if evitar_duplicados else None
        duplicados = self._contar_duplicados(listos, indice)

        if evitar_duplicados and duplicados >= total:
            forms.alert(
                "Las {0} insercion(es) de los bloques marcados YA tienen un "
                "elemento del mismo tipo colocado en su punto.\n\n"
                "No se creara nada: se estarian duplicando los elementos "
                "encima de los que ya existen.\n\n"
                "Si aun asi quieres colocarlos, desmarca la casilla "
                "'Evitar duplicados'.".format(total),
                title="Todo esta ya colocado"
            )
            return

        aviso_duplicados = ""
        if evitar_duplicados and duplicados:
            aviso_duplicados = (
                "\n\nSe omitiran {0} insercion(es) porque ya hay un elemento "
                "del mismo tipo en ese punto. Se colocaran {1}.".format(
                    duplicados, total - duplicados
                )
            )
        elif not evitar_duplicados:
            aviso_duplicados = (
                "\n\nAVISO: la casilla 'Evitar duplicados' esta desmarcada. "
                "Si estas inserciones ya se generaron antes, los elementos "
                "se colocaran encima de los existentes."
            )

        aviso_sin_tipo = ""
        if sin_tipo:
            aviso_sin_tipo = (
                "\n\nSe omitiran {0} bloque(s) marcado(s) sin asignar "
                "({1}).".format(
                    len(sin_tipo),
                    ", ".join([r.Nombre for r in sin_tipo[:4]])
                )
            )

        aviso_volumen = ""
        if total > LIMITE_INSTANCIAS_AVISO:
            aviso_volumen = (
                "\n\nAviso: son muchos ejemplares y Revit puede tardar "
                "varios minutos."
            )

        detalle_asignaciones = "\n".join([
            "   - {0}  ->  {1} : {2}   ({3} ejemplar(es))".format(
                r.Nombre, r.Familia, r.Tipo, r.Instancias
            )
            for r in listos[:12]
        ])
        if len(listos) > 12:
            detalle_asignaciones += "\n   - ... y {0} bloque(s) mas.".format(
                len(listos) - 12
            )

        continuar = forms.alert(
            "Se procesaran {0} insercion(es) de {1} bloque(s) del CAD:"
            "\n\n{2}\n\n"
            "Esta accion modifica el modelo. ¿Continuar?{3}{4}{5}".format(
                total, len(listos), detalle_asignaciones,
                aviso_duplicados, aviso_sin_tipo, aviso_volumen
            ),
            title="Confirmar Generacion de Elementos BIM",
            yes=True,
            no=True
        )

        if not continuar:
            return

        self.txt_status_bar.Text = (
            "Colocando {0} ejemplar(es)...".format(total - duplicados)
        )

        self.all_resultado_rows = []

        with revit.Transaction("Generar Elementos BIM desde CAD"):

            for row in listos:
                resultado = ResultadoRow(
                    row.Nombre,
                    "{0} : {1}".format(row.Familia, row.Tipo)
                )

                symbol = row.symbol

                try:
                    tipo_id = symbol.Id.IntegerValue
                except Exception:
                    tipo_id = 0

                try:
                    if not symbol.IsActive:
                        symbol.Activate()
                        doc.Regenerate()
                except Exception as error:
                    resultado.Fallidos = row.Instancias
                    resultado.Detalle = (
                        "No se pudo activar el tipo: {0}".format(error)
                    )
                    self.all_resultado_rows.append(resultado)
                    continue

                errores = []

                for insercion in row.inserciones:

                    # El indice se va alimentando con lo recien colocado,
                    # asi que tambien frena las inserciones de esta misma
                    # tanda que caigan en el mismo punto.
                    if indice is not None and indice.existe(
                        tipo_id, insercion.punto
                    ):
                        resultado.Omitidos += 1
                        continue

                    try:
                        instancia = self._crear_ejemplar(symbol, insercion)
                        self.ids_creados.append(instancia.Id.IntegerValue)
                        resultado.Colocados += 1

                        if indice is not None:
                            indice.agregar(tipo_id, insercion.punto)
                    except Exception as error:
                        resultado.Fallidos += 1
                        if len(errores) < 3:
                            errores.append(str(error))

                detalles = []

                if resultado.Omitidos:
                    detalles.append(
                        "{0} omitido(s) por existir ya un elemento del mismo "
                        "tipo en ese punto.".format(resultado.Omitidos)
                    )

                if errores:
                    detalles.append(" | ".join(errores))
                elif row.Aviso != "-":
                    detalles.append(
                        "Aviso del bloque: {0} (un ejemplar de familia no se "
                        "puede reflejar ni escalar desde aqui).".format(
                            row.Aviso
                        )
                    )

                if not detalles:
                    detalles.append("Colocados sin incidencias.")

                resultado.Detalle = " ".join(detalles)

                self.all_resultado_rows.append(resultado)

        for row in sin_tipo:
            resultado = ResultadoRow(row.Nombre, "-")
            resultado.Fallidos = row.Instancias
            resultado.Detalle = (
                "Omitido: el bloque no tiene familia y tipo asignados en el "
                "paso 3."
            )
            self.all_resultado_rows.append(resultado)

        self.lv_resultados.ItemsSource = self.all_resultado_rows

        colocados = sum(r.Colocados for r in self.all_resultado_rows)
        omitidos = sum(r.Omitidos for r in self.all_resultado_rows)
        fallidos = sum(r.Fallidos for r in self.all_resultado_rows)

        self.lbl_resumen_generacion.Text = (
            "{0} colocado(s), {1} omitido(s) por duplicado, {2} "
            "fallido(s).".format(colocados, omitidos, fallidos)
        )
        self.txt_status_bar.Text = (
            "Generacion terminada: {0} ejemplar(es) colocado(s), {1} "
            "omitido(s) por duplicado y {2} fallido(s) de {3} bloque(s).".format(
                colocados, omitidos, fallidos, len(listos)
            )
        )

    # --------------------------------------------------------------------------
    # UTILIDADES
    # --------------------------------------------------------------------------

    def _refrescar(self, listview, filas):
        """Fuerza a WPF a repintar un ListView: al no usar
        ObservableCollection, hay que reasignar ItemsSource para que los
        cambios en las filas (por ejemplo IsSelected) se vean."""
        listview.ItemsSource = None
        listview.ItemsSource = filas

    def _lista_element_id(self, ids):
        """Convierte una lista de Ids numericos en la List[ElementId]
        que espera la API de Revit."""
        lista = List[DB.ElementId]()
        for id_num in ids:
            try:
                lista.Add(DB.ElementId(id_num))
            except Exception:
                continue
        return lista

    @safe_handler
    def on_close(self, sender, args):
        self.Close()

    def aplicar_visualizacion_final(self):
        """Se ejecuta despues de cerrar la ventana: deja los ejemplares
        creados seleccionados y encuadrados en Revit.

        Se hace aqui, y no dentro del manejador de cierre, porque
        mientras la ventana modal sigue viva Revit no puede refrescar
        la vista."""
        if not self.chk_seleccionar_al_cerrar.IsChecked:
            return

        if not self.ids_creados:
            return

        ids = self._lista_element_id(self.ids_creados)

        try:
            uidoc.Selection.SetElementIds(ids)
        except Exception:
            return

        try:
            uidoc.ShowElements(ids)
        except Exception:
            pass


if __name__ == "__main__":
    window = ElementosBIMWPF()
    window.ShowDialog()
    window.aplicar_visualizacion_final()
