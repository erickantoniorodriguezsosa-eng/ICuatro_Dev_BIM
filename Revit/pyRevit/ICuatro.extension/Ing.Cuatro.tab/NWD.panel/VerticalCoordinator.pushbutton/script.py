# -*- coding: utf-8 -*-
"""NWD COORDINATOR — coordinación vertical contra la superficie del NWD/IFC.

Una sola pantalla. Todo lo que se puede resolver solo se resuelve AL
ABRIR: se detecta el vínculo de coordinación, se elige el origen de la
geometría (el IFC del que se generó el NWD si está en disco, si no el
propio NWD vía Navisworks), se deriva la transformación y se listan las
superficies que contiene. No hay que pulsar nada para empezar.

Solo dos acciones modifican el modelo: APLICAR NIVELACIÓN y el ajuste
manual. Ambas confirman y van en una transacción, así que Ctrl+Z las
deshace de una vez.

El detalle técnico del arranque (rutas, tiempos, cajas, errores) va al
registro, no a la pantalla: se consulta con «Guardar registro».
"""

__title__ = "NWD\nCoordinator"
__author__ = "Ingenieria Cuatro S.A."
__persistentengine__ = True
__doc__ = "Compara elementos de Revit con la superficie de un NWD/IFC y " \
          "los nivela verticalmente."

import os
import sys
import traceback
import clr
clr.AddReference('PresentationFramework')

# Una sola ventana por sesion. Comprobar ANTES de recargar los modulos o
# reemplazar doc/uidoc: una ventana abierta conserva su documento original.
from System import AppDomain
from System.Windows import WindowState
from pyrevit import script

_VENTANA_KEY = "ICuatro.NWDCoordinator.Window"
_existente = AppDomain.CurrentDomain.GetData(_VENTANA_KEY)
if _existente is not None and not _existente._cerrada:
    if _existente.WindowState == WindowState.Minimized:
        _existente.WindowState = WindowState.Normal
    _existente.Activate()
    script.exit()

# pyRevit mantiene los modulos importados en memoria entre ejecuciones del
# boton. Sin esta recarga explicita, una correccion en lib/nwdcoord puede no
# llegar a Revit aunque el archivo del proyecto ya sea nuevo.
try:
    _recargar_modulo = reload
except NameError:
    from importlib import reload as _recargar_modulo

_NWD_MODULOS_RECARGADOS = 0
for _nombre_modulo in sorted(
        [n for n in list(sys.modules)
         if n == "nwdcoord" or n.startswith("nwdcoord.")],
        key=len, reverse=True):
    try:
        _recargar_modulo(sys.modules[_nombre_modulo])
        _NWD_MODULOS_RECARGADOS += 1
    except Exception:
        # Si un modulo antiguo no puede recargarse, el import posterior sigue
        # funcionando y el registro deja constancia del conteo alcanzado.
        pass

NWD_ENGINE_VERSION = "2026.09.09.01"

clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows.Media import SolidColorBrush, Color as WpfColor
from System.Windows.Controls import StackPanel, TextBlock, Border, Orientation
from System.Windows import Thickness, FontWeights

from pyrevit import revit, DB, UI, forms, script

from nwdcoord import coordination_model
from nwdcoord import movement
from nwdcoord import slope
from nwdcoord import family_progress
from nwdcoord import pipeline
from nwdcoord import revit_elements
from nwdcoord import surface as superficie_mod
from nwdcoord import vertical_analysis as analisis
from nwdcoord import visualization
from nwdcoord.modeless import AccionesRevit
from nwdcoord.logging_utils import Registro

doc = revit.doc
uidoc = revit.uidoc

TODAS = u"(todas las superficies)"


def safe_handler(func):
    """Ningún manejador de WPF debe dejar escapar una excepción: una que se
    escape no pasa por el manejo de errores de pyRevit y puede cerrar
    Revit entero."""
    def envoltorio(self, sender, args):
        try:
            return func(self, sender, args)
        except Exception as error:
            try:
                self.txt_estado.Text = u"Error: {0}".format(error)
                self.registro.excepcion(u"Manejador de interfaz", error)
            except Exception:
                pass

            forms.alert(u"Ocurrio un error inesperado:\n\n{0}\n\n{1}".format(
                error, traceback.format_exc()), title=u"NWD Coordinator")

    return envoltorio


def revit_handler(func):
    """Los callbacks WPF solo solicitan; Execute realiza el acceso a Revit."""
    protegido = safe_handler(func)

    @safe_handler
    def solicitar(self, sender, args):
        if getattr(self, "_cargando", True) or self._cerrada:
            return
        self.acciones.solicitar(lambda: protegido(self, sender, args))
    return solicitar


# ==========================================================================
# PREPARACIÓN AUTOMÁTICA
# ==========================================================================

class Contexto(object):
    """Lo que se resuelve solo, antes de mostrar nada."""

    def __init__(self, registro):
        self.registro = registro
        self.modelos = []
        self.modelo = None
        self.origen = None
        self.transformacion = None
        self.diagnostico = None
        self.superficies = []       # (nombre, tipo, facesets, triangulos)
        self.error = u""

    def preparar(self, modelo=None):
        if not self.modelos:
            try:
                self.modelos = coordination_model.detectar(
                    DB, doc, self.registro)
            except Exception as error:
                self.registro.excepcion(u"Deteccion de modelos", error)
                self.error = u"{0}".format(error)
                return self

        self.modelo = modelo or (self.modelos[0] if self.modelos else None)
        self.superficies = []

        if self.modelo is None:
            self.error = u"No hay ningun modelo de coordinacion en el documento."
            return self

        try:
            self.transformacion, self.diagnostico, self.origen = \
                pipeline.resolver_transformacion(self.modelo, self.registro)
        except Exception as error:
            self.registro.excepcion(u"Resolucion de la transformacion", error)
            self.error = u"{0}".format(error)
            return self

        if self.transformacion is None:
            self.error = (self.diagnostico.motivo
                          if self.diagnostico else u"No se pudo preparar.")
            return self

        self.error = u""

        try:
            self.superficies = pipeline.superficies_del_origen(
                self.origen, self.registro)
        except Exception as error:
            self.registro.excepcion(u"Lectura de superficies", error)

        return self

    def resumen(self):
        """Una línea con el estado, para la cabecera."""
        if self.modelo is None:
            return self.error or u"Sin modelo."

        partes = []

        if self.origen is not None:
            partes.append(u"vía {0}".format(self.origen.etiqueta))

        if self.diagnostico is not None:
            partes.append(u"transformación {0}".format(
                u"verificada" if self.diagnostico.valida else u"DUDOSA"))

            if self.diagnostico.dz_es_cero:
                partes.append(u"dZ ≈ 0")

        if self.error:
            partes.append(self.error)

        return u" · ".join(partes) if partes else u"—"


# ==========================================================================
# FILA DE RESULTADOS
# ==========================================================================

class FilaResultado(object):
    """Adaptador de ResultadoCoordinacion para el ListView."""

    def __init__(self, resultado):
        self.resultado = resultado
        self.Marcado = (resultado.necesita_movimiento() and
                        resultado.movible and
                        not getattr(resultado, "superficie_aproximada", False))

        self.Id = u"{0}".format(resultado.id_valor)
        self.Categoria = resultado.categoria
        self.Familia = resultado.familia
        self.Tipo = resultado.tipo
        self.Estado = resultado.etiqueta_estado

        self.ZElemento = (u"—" if resultado.z_original_m is None
                          else u"{0:.3f}".format(resultado.z_original_m))
        self.ZSuperficie = (u"—" if resultado.z_superficie_m is None
                            else u"{0:.3f}".format(resultado.z_superficie_m))
        self.DZ = (u"—" if resultado.delta_m is None
                   else u"{0:+.3f}".format(resultado.delta_m))

        observaciones = []
        if getattr(resultado, "nota_pendiente", u""):
            observaciones.append(resultado.nota_pendiente)

        if resultado.error:
            observaciones.append(resultado.error)

        if getattr(resultado, "superficie_aproximada", False):
            distancia = resultado.distancia_superficie_m
            if distancia is None:
                distancia = getattr(resultado, "distancia_huella_m", None)

            if distancia is None:
                observaciones.append(u"Superficie aproximada")
            else:
                observaciones.append(
                    u"Superficie aproximada; separacion XY {0:.2f} m".format(
                        distancia))

        if resultado.necesita_movimiento() and not resultado.movible:
            observaciones.append(u"No se puede mover: {0}".format(
                resultado.motivo_inmovil))

        self.Observaciones = u" · ".join(observaciones)


# ==========================================================================
# VENTANA
# ==========================================================================

class Coordinador(forms.WPFWindow):

    def __init__(self, contexto):
        self._cargando = True
        self._cerrada = False
        self._permitir_cierre = False
        forms.WPFWindow.__init__(
            self, os.path.join(os.path.dirname(__file__), "ui.xaml"))

        self.contexto = contexto
        self.Title = u"NWD Coordinator - {0}".format(NWD_ENGINE_VERSION)
        self.registro = contexto.registro

        self.superficie = None
        self.informe_superficie = None
        self.zona = None
        self.elementos = []
        self.resultados = []
        self.filas = []
        self.previsualizacion = None
        self._cargando = True

        # Cada paso aislado: un fallo cosmético no debe impedir que la
        # ventana se abra ni borrar lo que ya se detectó.
        for etiqueta, paso in (
            (u"lista de modelos", self._llenar_modelos),
            (u"superficies", self._llenar_superficies),
            (u"opciones", self._llenar_combos),
            (u"leyenda", self._dibujar_leyenda),
            (u"zona", self.calcular_zona),
        ):
            try:
                paso()
            except Exception as error:
                self.registro.excepcion(u"Arranque: {0}".format(etiqueta), error)
                self._estado(u"Aviso: falló {0} ({1}).".format(etiqueta, error))

        self._cargando = False
        self.acciones = AccionesRevit(self)
        self.Closing += self._al_cerrar
        self.Closed += self._al_cerrarse
        self._aplicacion = doc.Application
        self._aplicacion.DocumentChanged += self._documento_cambiado
        self._aplicacion.DocumentClosed += self._documento_cerrado
        self.txt_cabecera.Text = contexto.resumen()

        if contexto.error:
            self._estado(contexto.error)
        else:
            self._estado(u"Listo. Puedes trabajar en las vistas con esta ventana abierta.")

    # ------------------------------------------------------------------
    # ARRANQUE
    # ------------------------------------------------------------------

    def _estado(self, texto):
        try:
            self.txt_estado.Text = texto
        except Exception:
            pass

    def _llenar_modelos(self):
        self.cmb_modelos.Items.Clear()

        for modelo in self.contexto.modelos:
            self.cmb_modelos.Items.Add(modelo.nombre_archivo or modelo.nombre)

        if self.contexto.modelo is not None:
            self.cmb_modelos.SelectedIndex = \
                self.contexto.modelos.index(self.contexto.modelo)

    def _llenar_superficies(self):
        """Las superficies que contiene el origen, para poder elegir una.

        En este proyecto son los tramos de kilometraje del levantamiento
        (IfcGeographicElement con ObjectType TRIANGULACIÓN). Acotar a uno
        reduce mucho lo que hay que leer."""
        self.cmb_superficie.Items.Clear()
        self.cmb_superficie.Items.Add(TODAS)

        superficies = self.contexto.superficies

        for nombre, _tipo, _facesets, _triangulos in superficies:
            self.cmb_superficie.Items.Add(nombre)

        self.cmb_superficie.SelectedIndex = 0

        if not superficies:
            self.txt_superficies.Text = (
                u"No se pudo listar el contenido del origen. Se usará todo.")
            return

        total = sum(t for _, _, _, t in superficies)

        self.txt_superficies.Text = (
            u"{0} superficie(s) detectada(s), {1} triángulos en total.".format(
                len(superficies),
                u"{0:,}".format(total).replace(",", ".")))

    def _llenar_combos(self):
        for modo in revit_elements.MODOS:
            self.cmb_referencia.Items.Add(revit_elements.ETIQUETAS_MODO[modo])

        self.cmb_referencia.SelectedIndex = revit_elements.MODOS.index(
            revit_elements.INSERCION)

        for estrategia in superficie_mod.ESTRATEGIAS:
            self.cmb_estrategia.Items.Add(superficie_mod.ETIQUETAS[estrategia])

        self.cmb_estrategia.SelectedIndex = superficie_mod.ESTRATEGIAS.index(
            superficie_mod.MAS_BAJA_DEBAJO)

    def _dibujar_leyenda(self):
        self.pnl_leyenda.Children.Clear()

        for _, etiqueta, (rojo, verde, azul) in visualization.leyenda():
            fila = StackPanel()
            fila.Orientation = Orientation.Horizontal
            fila.Margin = Thickness(0, 2, 0, 2)

            muestra = Border()
            muestra.Width = 16
            muestra.Height = 12
            muestra.Background = SolidColorBrush(
                WpfColor.FromRgb(rojo, verde, azul))
            muestra.Margin = Thickness(0, 0, 6, 0)

            texto = TextBlock()
            texto.Text = etiqueta
            texto.FontSize = 11
            texto.FontWeight = FontWeights.Normal

            fila.Children.Add(muestra)
            fila.Children.Add(texto)
            self.pnl_leyenda.Children.Add(fila)

    @revit_handler
    def on_modelo_cambiado(self, sender, args):
        if self._cargando:
            return

        indice = self.cmb_modelos.SelectedIndex

        if indice < 0 or indice >= len(self.contexto.modelos):
            return

        modelo = self.contexto.modelos[indice]

        if modelo is self.contexto.modelo:
            return

        self._limpiar_previsualizacion()
        self._invalidar_resultados()

        with forms.ProgressBar(title=u"Preparando el modelo…", indeterminate=True):
            self.contexto.preparar(modelo)

        self.superficie = None
        self._cargando = True
        self._llenar_superficies()
        self._cargando = False
        self.calcular_zona()
        self.txt_cabecera.Text = self.contexto.resumen()

    @revit_handler
    def on_recargar(self, sender, args):
        self._limpiar_previsualizacion()
        self._invalidar_resultados()
        self.contexto.modelos = []

        with forms.ProgressBar(title=u"Detectando…", indeterminate=True):
            self.contexto.preparar()

        self._cargando = True
        self._llenar_modelos()
        self._llenar_superficies()
        self._cargando = False

        self.superficie = None
        self.calcular_zona()
        self.txt_cabecera.Text = self.contexto.resumen()
        self._estado(u"Detección repetida.")

    # ------------------------------------------------------------------
    # ZONA Y SUPERFICIE
    # ------------------------------------------------------------------

    def _numero(self, caja, defecto):
        try:
            return float(caja.Text.replace(",", "."))
        except Exception:
            caja.Text = u"{0}".format(defecto)
            return defecto

    def _filtro_superficie(self):
        elegida = self.cmb_superficie.SelectedItem

        if not elegida or elegida == TODAS:
            return None

        return u"{0}".format(elegida)

    @safe_handler
    def on_superficie_cambiada(self, sender, args):
        """Invalida la malla anterior cuando cambia el filtro.

        Sin esta invalidacion, la primera superficie analizada permanecia en
        memoria aunque el usuario escogiera otra en el combo.
        """
        if self._cargando:
            return

        self._invalidar_resultados()
        self._estado(u"Superficie cambiada. Pulsa ANALIZAR para extraerla.")

    def _caja_de(self, elementos):
        minimos = None
        maximos = None

        for elemento in elementos:
            puntos = []
            insercion = revit_elements._punto_de_insercion(elemento)
            if insercion is not None:
                puntos.append(insercion)
            try:
                extremos = slope.extremos(DB, elemento)
                if extremos is not None:
                    puntos.extend(extremos)
            except Exception:
                pass
            try:
                caja = elemento.get_BoundingBox(None) or \
                       elemento.get_BoundingBox(doc.ActiveView)
            except Exception:
                caja = None

            if caja is not None:
                puntos.extend([(caja.Min.X, caja.Min.Y, caja.Min.Z),
                               (caja.Max.X, caja.Max.Y, caja.Max.Z)])
            for punto in puntos:
                if minimos is None:
                    minimos, maximos = list(punto), list(punto)
                else:
                    for eje, valor in enumerate(punto):
                        minimos[eje] = min(minimos[eje], valor)
                        maximos[eje] = max(maximos[eje], valor)

        if minimos is None:
            return None

        return DB.XYZ(*minimos), DB.XYZ(*maximos)

    def calcular_zona(self):
        """Zona a extraer, derivada de los elementos seleccionados."""
        contexto = self.contexto

        if contexto.transformacion is None:
            self.txt_zona.Text = u"Sin transformación no se puede situar la zona."
            self.zona = None
            return

        margen = abs(self._numero(self.txt_margen, 25.0))
        elementos = self.elementos or revit_elements.de_la_seleccion(uidoc, doc)
        caja = self._caja_de(elementos) if elementos else None

        if caja is None:
            self.txt_zona.Text = (
                u"Zona: selecciona elementos en Revit para acotarla.")
            self.zona = None
            return

        self.zona = contexto.transformacion.caja_revit_a_nwd(
            caja[0], caja[1], margen)

        ancho = self.zona[2] - self.zona[0]
        fondo = self.zona[3] - self.zona[1]

        self.txt_zona.Text = u"Zona: {0:.0f} × {1:.0f} m ({2:.2f} ha)".format(
            ancho, fondo, ancho * fondo / 10000.0)

    def preparar_superficie(self, usar_filtro=True):
        """Extrae y monta la superficie de la zona actual."""
        contexto = self.contexto

        if contexto.modelo is None or self.zona is None:
            return False

        self._estado(u"Extrayendo la superficie…")

        with forms.ProgressBar(title=u"Preparando la superficie…",
                               indeterminate=True):
            superficie, informe = pipeline.preparar_superficie(
                contexto.modelo, self.zona, origen=contexto.origen,
                filtro=(self._filtro_superficie() if usar_filtro else None),
                registro=self.registro)

        self.informe_superficie = informe

        if superficie is None:
            self._estado(u"No se pudo preparar la superficie.")
            forms.alert(u"No se pudo preparar la superficie:\n\n{0}\n\n"
                        u"Detalle en «Guardar registro».".format(
                            informe.get("error")), title=u"NWD Coordinator")
            return False

        self.superficie = superficie

        if informe.get("filtro_replegado"):
            self._estado(
                u"Superficie lista: se usaron las superficies cercanas "
                u"porque la seleccionada no cubria la zona.")

        self._estado(u"Superficie lista: {0} triángulos en {1:.1f} s.".format(
            informe["triangulos"], informe["extraccion_s"]))

        return True

    # ------------------------------------------------------------------
    # SELECCIÓN
    # ------------------------------------------------------------------

    def _fijar_elementos(self, elementos, origen):
        self._limpiar_previsualizacion()
        self._invalidar_resultados()
        self.elementos = elementos
        self.txt_seleccion.Text = u"{0} elemento(s) desde {1}.".format(
            len(elementos), origen)
        self._estado(self.txt_seleccion.Text)

        # La zona depende de la selección, y la superficie de la zona.
        self.superficie = None
        self.calcular_zona()

    @revit_handler
    def on_usar_seleccion(self, sender, args):
        self._fijar_elementos(revit_elements.de_la_seleccion(uidoc, doc),
                              u"la selección de Revit")

    @revit_handler
    def on_escoger(self, sender, args):
        """Oculta la ventana durante PickObjects y vuelve sin bloquear Revit."""
        self.Hide()

        try:
            referencias = uidoc.Selection.PickObjects(
                UI.Selection.ObjectType.Element,
                u"Selecciona los elementos a coordinar y pulsa Finalizar")

            elementos = [doc.GetElement(r.ElementId) for r in referencias]
            self._fijar_elementos([e for e in elementos if e], u"la pantalla")
        except Exception:
            # Cancelar con Escape es lo normal, no un error.
            pass
        finally:
            self.Show()

    @revit_handler
    def on_por_familia(self, sender, args):
        """Selecciona todos los ejemplares de una familia del proyecto."""
        self.Hide()

        try:
            familias = revit_elements.nombres_familias(DB, doc)

            if not familias:
                forms.alert(u"No se encontraron familias cargadas en el proyecto.",
                            title=u"Seleccion por familia")
                return

            from System.Windows.Markup import XamlReader
            import codecs
            with codecs.open(os.path.join(os.path.dirname(__file__),
                                          "family_item.xaml"), "r", "utf-8") as archivo:
                plantilla = XamlReader.Parse(archivo.read())
            estados = family_progress.listar(DB, doc, familias)
            familia = forms.SelectFromList.show(
                estados,
                title=u"Familias: verde movilizada | ambar parcial | gris sin registro",
                button_name=u"Usar familia",
                width=720,
                item_template=plantilla,
                multiselect=False)

            if isinstance(familia, (list, tuple)):
                familia = familia[0] if familia else None

            if not familia:
                return

            familia = familia.nombre

            elementos = revit_elements.por_familia(DB, doc, familia)

            if not elementos:
                forms.alert(
                    u"La familia {0} no tiene ejemplares seleccionables en "
                    u"este documento.".format(familia),
                    title=u"Seleccion por familia")
                return

            self._fijar_elementos(
                elementos,
                u"todos los ejemplares de {0}".format(familia))
        finally:
            self.Show()

    @revit_handler
    def on_buscar_ids(self, sender, args):
        elementos, fallidos = revit_elements.por_ids(DB, doc, self.txt_ids.Text)
        self._fijar_elementos(elementos, u"la lista de ids")

        if fallidos:
            self._estado(u"{0} No encontrados: {1}".format(
                self.txt_seleccion.Text, u", ".join(fallidos[:10])))

    # ------------------------------------------------------------------
    # ANÁLISIS
    # ------------------------------------------------------------------

    def _opciones(self):
        opciones = analisis.OpcionesAnalisis()

        opciones.modo_referencia = revit_elements.MODOS[
            max(self.cmb_referencia.SelectedIndex, 0)]
        opciones.estrategia = superficie_mod.ESTRATEGIAS[
            max(self.cmb_estrategia.SelectedIndex, 0)]
        opciones.offset_m = self._numero(self.txt_offset, 0.0)
        opciones.tolerancia_m = abs(self._numero(self.txt_tolerancia, 0.02))
        opciones.detectar_pendiente = bool(self.chk_pendiente.IsChecked)

        return opciones

    def _preparar_nwd_completo(self):
        """El minimo global requiere todos los componentes de la zona.

        Un solo IFC asociado puede omitir la plataforma bajo el talud.
        Navisworks entrega el conjunto en coordenadas NWD; su transformacion
        debe resolverse antes de calcular la caja de extraccion.
        """
        contexto = self.contexto
        if contexto.modelo is None or not contexto.modelo.existe:
            forms.alert(u"No esta disponible el NWD compuesto.",
                        title=u"NWD Coordinator")
            return False
        disponible, mensaje, _ = pipeline.navisworks_bridge.estado()
        if not disponible:
            forms.alert(u"Para buscar la superficie mas baja de todos los "
                        u"componentes se necesita Navisworks: {0}".format(mensaje),
                        title=u"NWD Coordinator")
            return False
        origen = pipeline.Origen(pipeline.NWD, contexto.modelo.ruta)
        with forms.ProgressBar(title=u"Preparando todos los componentes del NWD...",
                               indeterminate=True):
            trans, diagnostico, origen = pipeline.resolver_transformacion(
                contexto.modelo, self.registro, origen=origen)
        if trans is None or diagnostico is None or not diagnostico.valida:
            forms.alert(u"No se pudo verificar la posicion del NWD completo: {0}".
                        format(getattr(diagnostico, "motivo", u"sin transformacion")),
                        title=u"NWD Coordinator")
            return False
        contexto.transformacion = trans
        contexto.diagnostico = diagnostico
        contexto.origen = origen
        contexto.error = u""
        if self.superficie is not None:
            self.superficie.liberar()
        self.superficie = None
        self.informe_superficie = None
        self._cargando = True
        try:
            self.cmb_superficie.SelectedIndex = 0
        finally:
            self._cargando = False
        self.txt_cabecera.Text = contexto.resumen()
        self.txt_superficies.Text = (
            u"Interseccion con todos los componentes del NWD en la zona.")
        self.registro.info(u"Minimo descendente: NWD completo, sin filtro nominal.")
        return True

    @revit_handler
    def on_analizar(self, sender, args):
        self._limpiar_previsualizacion()
        contexto = self.contexto
        # Si falla la nueva extraccion no deben quedar destinos antiguos
        # disponibles para aplicar sobre el modelo.
        self.resultados = []
        self.filas = []
        self._refrescar()
        self.txt_resumen.Text = u"Analisis en curso..."
        opciones = self._opciones()
        completa = opciones.estrategia == superficie_mod.MAS_BAJA_DEBAJO
        if completa:
            if not self._preparar_nwd_completo():
                return
            opciones.modo_referencia = revit_elements.INSERCION
            self.cmb_referencia.SelectedIndex = revit_elements.MODOS.index(
                revit_elements.INSERCION)

        if contexto.modelo is None or contexto.transformacion is None:
            forms.alert(u"No hay un modelo listo:\n\n{0}".format(
                contexto.error or u"origen no resuelto"),
                title=u"NWD Coordinator")
            return

        if not self.elementos:
            self.elementos = revit_elements.de_la_seleccion(uidoc, doc)

            if not self.elementos:
                forms.alert(u"Selecciona primero los elementos de Revit.",
                            title=u"NWD Coordinator")
                return

            self._fijar_elementos(self.elementos, u"la selección de Revit")

        if contexto.diagnostico is not None and not contexto.diagnostico.valida:
            if not forms.alert(
                    u"La transformación no superó las comprobaciones:\n\n{0}"
                    u"\n\n¿Continúo de todas formas?".format(
                        contexto.diagnostico.motivo),
                    title=u"Transformación dudosa", yes=True, no=True):
                return

        self.calcular_zona()

        if self.zona is None:
            forms.alert(u"No se pudo determinar la zona a extraer.",
                        title=u"NWD Coordinator")
            return

        # La superficie se prepara sola: es parte de analizar.
        if self.superficie is None and not self.preparar_superficie(
                usar_filtro=not completa):
            return

        opciones = self._opciones()

        with forms.ProgressBar(title=u"Analizando {value} de {max_value}",
                               cancellable=True) as barra:
            self.resultados = analisis.analizar(
                DB, doc, self.elementos, self.superficie,
                contexto.transformacion, opciones, self.registro,
                lambda hecho, total: barra.update_progress(hecho, total))

        # Un filtro puede devolver una malla no vacia pero de otro tramo. Si
        # todos los elementos quedan sin superficie, la geometria existe pero
        # la seleccion nominal no coincide con la zona: repetir sin filtro
        # permite resolver la superficie real sin obligar al usuario a
        # adivinar otro nombre en el combo.
        informe = self.informe_superficie or {}
        filtro_elegido = self._filtro_superficie()
        todos_sin_superficie = (
            self.resultados and
            all(r.estado == analisis.SIN_SUPERFICIE
                for r in self.resultados))

        if (filtro_elegido and todos_sin_superficie and
                not informe.get("filtro_replegado")):
            self._estado(
                u"El filtro no cubre los elementos; buscando en todas las "
                u"superficies de la zona…")
            self.superficie = None

            if self.preparar_superficie(usar_filtro=False):
                self.informe_superficie["filtro_replegado"] = True
                with forms.ProgressBar(
                        title=u"Analizando {value} de {max_value}",
                        cancellable=True) as barra:
                    self.resultados = analisis.analizar(
                        DB, doc, self.elementos, self.superficie,
                        contexto.transformacion, opciones, self.registro,
                        lambda hecho, total: barra.update_progress(
                            hecho, total))

        self._pintar_resultados()

    def _pintar_resultados(self):
        self.filas = [FilaResultado(r) for r in self.resultados]
        self._refrescar()

        resumen = analisis.resumir(self.resultados)
        texto = analisis.texto_resumen(resumen)

        if resumen["a_mover"]:
            texto += u"\nDZ máximo: {0:+.3f} m · DZ medio: {1:+.3f} m".format(
                resumen["delta_maximo"], resumen["delta_medio"])

        informe = self.informe_superficie

        if informe:
            origen = informe.get("origen")
            texto += u"\nSuperficie: {0} triángulos{1}.".format(
                informe.get("triangulos", 0),
                u"" if origen is None else u" · {0}".format(origen.etiqueta))

        self.txt_resumen.Text = texto
        self._estado(analisis.texto_resumen(resumen))

    def _refrescar(self):
        cargando = self._cargando
        self._cargando = True
        try:
            self.lv_resultados.ItemsSource = None
            self.lv_resultados.ItemsSource = self.filas
        finally:
            self._cargando = cargando

    # ------------------------------------------------------------------
    # TABLA
    # ------------------------------------------------------------------

    @safe_handler
    def on_marcar_fila(self, sender, args):
        """Guarda el estado del checkbox en la fila.

        A mano en lugar de confiar en el binding: WPF no siempre escribe de
        vuelta en un atributo de un objeto Python, y cuando no lo hace la
        aplicación no mueve nada sin decir por qué."""
        fila = getattr(sender, "DataContext", None)

        if fila is not None and hasattr(fila, "Marcado"):
            fila.Marcado = bool(sender.IsChecked)

    def _marcados(self):
        return set(f.resultado.id_valor for f in self.filas if f.Marcado)

    @safe_handler
    def on_marcar_todo(self, sender, args):
        for fila in self.filas:
            fila.Marcado = True
        self._refrescar()

    @safe_handler
    def on_desmarcar(self, sender, args):
        for fila in self.filas:
            fila.Marcado = False
        self._refrescar()

    @safe_handler
    def on_marcar_movibles(self, sender, args):
        for fila in self.filas:
            fila.Marcado = (fila.resultado.necesita_movimiento() and
                            fila.resultado.movible)
        self._refrescar()

    @revit_handler
    def on_fila_seleccionada(self, sender, args):
        fila = self.lv_resultados.SelectedItem

        if fila is None or fila.resultado.element_id is None:
            return

        visualization.mostrar_en_revit(uidoc, doc, fila.resultado.element_id)

    # ------------------------------------------------------------------
    # PREVISUALIZACIÓN
    # ------------------------------------------------------------------

    @revit_handler
    def on_previsualizar(self, sender, args):
        if not self.resultados:
            forms.alert(u"Analiza primero.", title=u"NWD Coordinator")
            return

        if self.previsualizacion is not None:
            self._limpiar_previsualizacion()

        self.previsualizacion = visualization.Previsualizacion(
            DB, doc, uidoc, self.registro)

        with revit.Transaction("Previsualizar coordinacion vertical", doc=doc):
            pintados = self.previsualizacion.aplicar(self.resultados)

        self._estado(u"{0} elemento(s) coloreados. No se ha modificado "
                     u"geometría.".format(pintados))

    def _limpiar_previsualizacion(self):
        if self.previsualizacion is None:
            return 0

        with revit.Transaction("Quitar colores de coordinacion", doc=doc):
            limpiados = self.previsualizacion.limpiar()

        if self.previsualizacion.tocados:
            raise RuntimeError(u"No se pudieron restaurar todos los colores. "
                               u"Revisa la vista y vuelve a quitar colores.")
        self.previsualizacion = None

        return limpiados

    @revit_handler
    def on_limpiar(self, sender, args):
        self._estado(u"{0} override(s) retirados.".format(
            self._limpiar_previsualizacion()))

    # ------------------------------------------------------------------
    # APLICACIÓN
    # ------------------------------------------------------------------

    @revit_handler
    def on_aplicar(self, sender, args):
        if not self.resultados:
            forms.alert(u"Analiza primero.", title=u"NWD Coordinator")
            return

        marcados = self._marcados()
        pendientes = [r for r in self.resultados
                      if r.necesita_movimiento() and r.id_valor in marcados]

        if not pendientes:
            candidatos = [r for r in self.resultados if r.necesita_movimiento()]

            if not candidatos:
                forms.alert(u"Ningún elemento necesita moverse: todos están "
                            u"dentro de la tolerancia, o sin superficie "
                            u"debajo.", title=u"NWD Coordinator")
                return

            if not forms.alert(
                    u"No hay ninguna fila marcada, pero {0} elemento(s) "
                    u"necesitan moverse.\n\n¿Los aplico todos?".format(
                        len(candidatos)),
                    title=u"NWD Coordinator", yes=True, no=True):
                return

            for fila in self.filas:
                fila.Marcado = fila.resultado.necesita_movimiento()

            self._refrescar()
            marcados = self._marcados()
            pendientes = candidatos

        bloqueados = [r for r in pendientes if not r.movible]
        movibles = len(pendientes) - len(bloqueados)

        adaptar = set()
        propuestas = [r for r in pendientes if r.movible and r.pendiente is not None]
        if propuestas:
            detalle = u"\n".join(
                u"Id {0} | {1}: {2:+.2f}%".format(
                    r.id_valor, r.familia, r.pendiente.porcentaje)
                for r in propuestas[:12])
            adaptar_opcion = u"Adaptar pendiente de los ejes compatibles"
            vertical_opcion = u"Solo desplazamiento vertical"
            eleccion = forms.CommandSwitchWindow.show(
                [vertical_opcion, adaptar_opcion, u"Cancelar"],
                message=(u"Se detecto una pendiente distinta en {0} elemento(s).\n"
                         u"¿Es necesario adecuarlos a la pendiente detectada?\n\n"
                         u"{1}\n\nSe ajustara Z de los extremos manteniendo XY. "
                         u"La longitud del eje puede cambiar. No se deforma la "
                         u"seccion ni se reproduce una silueta irregular.\n"
                         u"Perfil muestreado cada 25 cm como maximo; el offset "
                         u"se mide desde el eje, no desde la cara inferior.".
                         format(len(propuestas), detalle)))
            if eleccion not in (adaptar_opcion, vertical_opcion):
                return
            if eleccion == adaptar_opcion:
                adaptar = set(r.id_valor for r in propuestas)

        mensaje = (u"Se procesaran {0} elemento(s): {1} con adaptacion de "
                   u"pendiente y el resto solo en vertical cuando corresponda.\n\n"
                   u"Esta operación SÍ modifica el modelo. Se podrá deshacer "
                   u"con Ctrl+Z.".format(movibles, len(adaptar)))

        if bloqueados:
            mensaje += u"\n\n{0} elemento(s) marcados no se pueden mover y se " \
                       u"omitirán.".format(len(bloqueados))

        if not forms.alert(mensaje, title=u"Aplicar nivelación",
                           yes=True, no=True):
            return

        self._limpiar_previsualizacion()

        with revit.Transaction("Nivelacion vertical contra NWD", doc=doc):
            with forms.ProgressBar(
                    title=u"Moviendo {value} de {max_value}") as barra:
                salida = movement.aplicar_nivelacion(
                    DB, doc, self.resultados, self.registro,
                    lambda hecho, total: barra.update_progress(hecho, total),
                    marcados, adaptar_pendientes=adaptar)

        self._pintar_resultados()
        self._estado(salida.resumen())

        if salida.errores:
            detalle = u"\n".join(u"{0}: {1}".format(i, m)
                                 for i, m in salida.errores[:20])

            forms.alert(u"{0}\n\nDetalle:\n{1}".format(salida.resumen(), detalle),
                        title=u"Nivelación terminada")

    @revit_handler
    def on_ajuste_manual(self, sender, args):
        delta = self._numero(self.txt_manual, 0.0)

        if abs(delta) < 1e-9:
            forms.alert(u"Indica un desplazamiento distinto de cero.",
                        title=u"Ajuste manual")
            return

        elementos = [f.resultado.elemento for f in self.filas
                     if f.Marcado and f.resultado.elemento is not None]

        if not elementos:
            forms.alert(u"Marca al menos un elemento en la tabla.",
                        title=u"Ajuste manual")
            return

        if not forms.alert(
                u"Se van a desplazar {0} elemento(s) {1:+.3f} m en vertical."
                u"\n\nEsta operación modifica el modelo.".format(
                    len(elementos), delta),
                title=u"Ajuste manual", yes=True, no=True):
            return

        self._limpiar_previsualizacion()
        with revit.Transaction("Ajuste vertical manual", doc=doc):
            salida = movement.aplicar_ajuste_manual(
                DB, doc, elementos, delta, self.registro)

        self._invalidar_resultados()
        self._estado(u"Ajuste manual: {0}. Vuelve a ANALIZAR.".format(salida.resumen()))

        if salida.errores:
            detalle = u"\n".join(u"{0}: {1}".format(i, m)
                                 for i, m in salida.errores[:20])
            forms.alert(u"{0}\n\nDetalle:\n{1}".format(salida.resumen(), detalle),
                        title=u"Ajuste manual")

    # ------------------------------------------------------------------
    # CIERRE
    # ------------------------------------------------------------------

    def _documento_activo(self, app):
        if not doc.IsValidObject:
            self._permitir_cierre = True
            self.Close()
            return False
        activo = app.ActiveUIDocument
        if activo is None or not activo.Document.Equals(doc):
            self._estado(u"Activa el documento donde abriste NWD Coordinator "
                         u"para continuar, o cierra la ventana y abrela en el nuevo.")
            return False
        return True

    def _invalidar_resultados(self):
        self.resultados = []
        self.filas = []
        self._refrescar()
        if self.superficie is not None:
            self.superficie.liberar()
        self.superficie = None
        self.informe_superficie = None
        self.txt_resumen.Text = u"Pulsa ANALIZAR para actualizar los resultados."

    @safe_handler
    def _documento_cambiado(self, sender, args):
        # DocumentChanged es de solo lectura. La limpieza de overrides espera
        # a la siguiente accion externa; aqui solo se descartan datos de UI.
        if self._cerrada or self.acciones.ejecutando:
            return
        if args.GetDocument().Equals(doc):
            self._invalidar_resultados()
            self.elementos = [e for e in self.elementos if e.IsValidObject]
            self._estado(u"El modelo cambio. Vuelve a ANALIZAR antes de desplazar.")

    @safe_handler
    def _documento_cerrado(self, sender, args):
        if not doc.IsValidObject:
            self._permitir_cierre = True
            self.Close()

    @safe_handler
    def _al_cerrar(self, sender, args):
        if not self._permitir_cierre:
            args.Cancel = True
            self.on_cerrar(sender, args)

    @safe_handler
    def _al_cerrarse(self, sender, args):
        self._cerrada = True
        self._aplicacion.DocumentChanged -= self._documento_cambiado
        self._aplicacion.DocumentClosed -= self._documento_cerrado
        AppDomain.CurrentDomain.SetData(_VENTANA_KEY, None)
        if not self.acciones.ejecutando:
            self.acciones.liberar()
        if self.superficie is not None:
            self.superficie.liberar()
        self.superficie = None
        self.previsualizacion = None
        self.elementos = []
        self.resultados = []
        self.filas = []

    @safe_handler
    def on_guardar_registro(self, sender, args):
        ruta = self.registro.guardar()

        if ruta:
            self._estado(u"Registro guardado en {0}".format(ruta))

            try:
                os.startfile(ruta)
            except Exception:
                pass
        else:
            self._estado(u"No se pudo guardar el registro.")

    @safe_handler
    def on_cerrar(self, sender, args):
        self.acciones.solicitar(self._cerrar_en_revit, cierre=True)

    def _cerrar_en_revit(self):
        if doc.IsValidObject and self.previsualizacion is not None:
            if forms.alert(u"Quedan colores de previsualización en la vista."
                           u"\n\n¿Los retiro antes de cerrar?",
                           title=u"NWD Coordinator", yes=True, no=True):
                self._limpiar_previsualizacion()

        self._permitir_cierre = True
        self.Close()


if __name__ == "__main__":
    registro = Registro("NWDCOORD")
    registro.info(
        u"Motor NWD Coordinator {0}; modulos nwdcoord recargados={1}",
        NWD_ENGINE_VERSION, _NWD_MODULOS_RECARGADOS)

    with forms.ProgressBar(title=u"NWD Coordinator — preparando…",
                           indeterminate=True):
        contexto = Contexto(registro).preparar()

    ventana = Coordinador(contexto)
    AppDomain.CurrentDomain.SetData(_VENTANA_KEY, ventana)
    ventana.Show()
