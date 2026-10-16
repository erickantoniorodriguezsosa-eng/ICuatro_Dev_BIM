# -*- coding: utf-8 -*-
"""CAD por capas. Compatible con IronPython 2.7 y Revit 2022."""
import math
from pyrevit import DB, forms, revit

METROS_POR_PIE = 0.3048
MAX_CANDIDATOS = 20000
# Bloqueo tras cierre nativo de Revit. No habilitar sin validar en Revit 2022.
SOPORTE_3D_HABILITADO = False
TIPO_SOPORTE_3D = 'ICuatro_AUX_Cerco_3D_20mm'
MARCA_SOPORTE_3D = 'ICuatro: soporte auxiliar de cerco CAD 3D'
ANCHO_SOPORTE_FT = 0.20 / METROS_POR_PIE
ESPESOR_SOPORTE_FT = 0.02 / METROS_POR_PIE
TOLERANCIA_SOPORTE_FT = 1e-5
# Salto maximo entre peldanos al escalonar un cerco inclinado sin soporte 3D.
ALTURA_PELDANO_FT = 0.05 / METROS_POR_PIE
# Ruido habitual de un CAD importado; menor que ShortCurveTolerance de Revit (~0.0026 ft).
TOLERANCIA_CAD_FT = 1e-4


def es_barandilla(tipo):
    arquitectura = getattr(DB, 'Architecture', None)
    clase = getattr(arquitectura, 'RailingType', None)
    return clase is not None and isinstance(tipo, clase)


def colocacion(tipo):
    return 'Barandilla' if es_barandilla(tipo) else str(tipo.Family.FamilyPlacementType)


def nombre_tipo_sistema(tipo):
    """ElementType.Name puede ser solo escritura para el binder de IronPython.

    Prioriza los parametros de nombre; ningun acceso alternativo a Name
    debe impedir abrir la ventana si el descriptor tambien falla.
    """
    for clave in ('SYMBOL_NAME_PARAM', 'ALL_MODEL_TYPE_NAME'):
        try:
            parametro = tipo.get_Parameter(getattr(DB.BuiltInParameter, clave))
            nombre = parametro.AsString() if parametro else None
            if nombre:
                return nombre
        except Exception:
            pass
    for obtener in (
            lambda: DB.Element.Name.GetValue(tipo),
            lambda: DB.Element.Name.__get__(tipo, type(tipo)),
            lambda: tipo.Name):
        try:
            nombre = obtener()
            if nombre:
                return nombre
        except Exception:
            pass
    try:
        return '(tipo sin nombre, Id {0})'.format(tipo.Id.IntegerValue)
    except Exception:
        return '(tipo sin nombre)'


class TipoSistemaItem(object):
    def __init__(self, tipo):
        self.symbol = tipo
        self.Nombre = nombre_tipo_sistema(tipo)
        self.Display = self.Nombre


class BarandillasItem(object):
    Nombre = 'Familia de sistema: Barandilla'
    Categoria = 'Barandillas'

    def __init__(self, tipos):
        self.tipos = sorted([TipoSistemaItem(t) for t in tipos], key=lambda t: t.Nombre.lower())
        self.Display = '{0} ({1} tipos)'.format(self.Nombre, len(self.tipos))


class RecorridoBarandilla(object):
    def __init__(self, curvas):
        self.curvas = curvas
        self.z = curvas[0].GetEndPoint(0).Z
        self.soporte_3d = False


def es_horizontal(curvas):
    z = curvas[0].GetEndPoint(0).Z
    return all(abs(c.Evaluate(t, True).Z - z) <= TOLERANCIA_CAD_FT
               for c in curvas for t in (0.0, 0.25, 0.5, 0.75, 1.0))


def datos_tramo_3d(curva, tolerancia):
    """Orden ascendente, longitud XY y angulo de pendiente en radianes."""
    a, b = curva.GetEndPoint(0), curva.GetEndPoint(1)
    if a.Z > b.Z:
        a, b = b, a
    longitud_xy = math.hypot(b.X - a.X, b.Y - a.Y)
    if longitud_xy <= tolerancia:
        raise ValueError('El CAD contiene un tramo vertical o demasiado corto en planta; no admite una barandilla sobre suelo.')
    return a, b, longitud_xy, math.atan2(b.Z - a.Z, longitud_xy)


def tramos_escalonados(curva, tolerancia):
    """Peldanos horizontales que siguen la pendiente; cada uno sube como maximo ALTURA_PELDANO_FT.

    Cada peldano queda a la cota que tiene la recta en su punto medio. Lista vacia
    si el tramo es vertical o demasiado corto en planta.
    """
    a, b = curva.GetEndPoint(0), curva.GetEndPoint(1)
    longitud_xy = math.hypot(b.X - a.X, b.Y - a.Y)
    if longitud_xy <= tolerancia:
        return []
    n = max(1, int(math.ceil(abs(b.Z - a.Z) / ALTURA_PELDANO_FT - 1e-9)))
    n = min(n, max(1, int(longitud_xy / (2 * tolerancia))))
    tramos = []
    for i in range(n):
        t0, t1, tm = i / float(n), (i + 1) / float(n), (i + 0.5) / n
        z = a.Z + (b.Z - a.Z) * tm
        tramos.append(DB.Line.CreateBound(
            DB.XYZ(a.X + (b.X - a.X) * t0, a.Y + (b.Y - a.Y) * t0, z),
            DB.XYZ(a.X + (b.X - a.X) * t1, a.Y + (b.Y - a.Y) * t1, z)))
    return tramos


def comprobar_soporte_3d():
    if not SOPORTE_3D_HABILITADO:
        raise ValueError('La generacion CAD 3D con suelos auxiliares esta deshabilitada tras un cierre de Revit. Este modo requiere correccion y validacion en Revit 2022.')


def conectar_recorridos(curvas):
    """Una cadena por rama, limitada por extremos o nodos de bifurcacion.

    Cada curva pertenece exactamente a un recorrido. Los nodos con dos
    curvas mantienen la continuidad; al llegar a una bifurcacion termina
    la cadena y las otras ramas forman recorridos independientes.
    Los ciclos se conservan, incluidos los unidos a una bifurcacion.
    """
    puntos, celdas, adyacentes, extremos = [], {}, {}, []
    tolerancia = TOLERANCIA_CAD_FT

    def nodo(punto):
        celda = tuple(int(math.floor(v / tolerancia)) for v in (punto.X, punto.Y, punto.Z))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in celdas.get((celda[0]+dx, celda[1]+dy, celda[2]+dz), []):
                        if puntos[i].DistanceTo(punto) <= tolerancia:
                            return i
        i = len(puntos)
        puntos.append(punto)
        celdas.setdefault(celda, []).append(i)
        adyacentes[i] = []
        return i

    for i, curva in enumerate(curvas):
        a, b = nodo(curva.GetEndPoint(0)), nodo(curva.GetEndPoint(1))
        extremos.append((a, b))
        adyacentes[a].append(i)
        adyacentes[b].append(i)
    usados, recorridos = set(), []

    def recorrer(actual, i):
        cadena = []
        while i not in usados:
            usados.add(i)
            a, b = extremos[i]
            cadena.append(curvas[i] if actual == a else curvas[i].CreateReversed())
            actual = b if actual == a else a
            if len(adyacentes[actual]) != 2:
                break
            restantes = [j for j in adyacentes[actual] if j not in usados]
            if not restantes:
                break
            i = restantes[0]
        if cadena:
            recorridos.append(RecorridoBarandilla(cadena))

    # Iniciar cada rama de extremos y bifurcaciones; nunca escoger una
    # continuacion arbitraria entre tres o mas curvas del mismo nodo.
    for nodo_id in sorted(adyacentes):
        if len(adyacentes[nodo_id]) != 2:
            for i in adyacentes[nodo_id]:
                if i not in usados:
                    recorrer(nodo_id, i)
    # Las componentes que solo tienen nodos de grado dos son ciclos.
    for i, (a, b) in enumerate(extremos):
        if i not in usados:
            recorrer(a, i)
    return recorridos


def firma_barandilla(recorrido):
    if recorrido.soporte_3d:
        return ('barandilla3d', firma_curva(recorrido.curvas[0], 4))
    firmas = []
    for curva in recorrido.curvas:
        traslacion = DB.Transform.CreateTranslation(DB.XYZ(0, 0, -curva.GetEndPoint(0).Z))
        firmas.append(firma_curva(curva.CreateTransformed(traslacion), 4))
    return ('barandilla', round(recorrido.z, 4), tuple(sorted(firmas)))


def _puntos_curva(curva, relativa):
    """Puntos de muestreo; en recorridos planos la cota propia se descarta, como en la firma."""
    dz = curva.GetEndPoint(0).Z if relativa else 0.0
    return [(p.X, p.Y, p.Z - dz) for p in (curva.Evaluate(i / 8.0, True) for i in range(9))]


def _misma_curva(a, b, tolerancia, relativa):
    if abs(a.Length - b.Length) > tolerancia:
        return False
    pa, pb = _puntos_curva(a, relativa), _puntos_curva(b, relativa)
    for otro in (pb, list(reversed(pb))):
        if all(math.sqrt(sum((u - v) ** 2 for u, v in zip(p, q))) <= tolerancia for p, q in zip(pa, otro)):
            return True
    return False


def mismo_recorrido(a, b, tolerancia=TOLERANCIA_CAD_FT):
    """Comparacion con tolerancia; evita falsos fallos por redondeo de la firma."""
    if a.soporte_3d != b.soporte_3d or len(a.curvas) != len(b.curvas):
        return False
    relativa = not a.soporte_3d
    if relativa and abs(a.z - b.z) > tolerancia:
        return False
    restantes = list(b.curvas)
    for curva in a.curvas:
        for otra in restantes:
            if _misma_curva(curva, otra, tolerancia, relativa):
                restantes.remove(otra)
                break
        else:
            return False
    return True


def firma_candidato(candidato):
    return firma_barandilla(candidato) if isinstance(candidato, RecorridoBarandilla) else firma_curva(candidato)


def distancias(longitud, paso):
    """Incluye inicio; no repite el extremo compartido entre tramos."""
    if paso <= 0 or math.isnan(paso) or math.isinf(paso):
        raise ValueError("La separacion debe ser un numero positivo en metros.")
    cantidad = max(1, int(math.ceil(longitud / paso - 1e-9)))
    if cantidad > MAX_CANDIDATOS:
        raise ValueError("Demasiados puntos. Aumenta la separacion.")
    return [i * paso for i in range(cantidad)]


def parametro_distancia(curva, distancia):
    """Busqueda por longitud real, sin confundir parametro con distancia."""
    if distancia <= 0:
        return 0.0
    if distancia >= curva.Length:
        return 1.0
    inicio = curva.GetEndParameter(0)
    fin = curva.GetEndParameter(1)
    abajo, arriba = 0.0, 1.0
    for _ in range(35):
        medio = (abajo + arriba) * 0.5
        parte = curva.Clone()
        parte.MakeBound(inicio, inicio + (fin - inicio) * medio)
        if parte.Length < distancia:
            abajo = medio
        else:
            arriba = medio
    return (abajo + arriba) * 0.5


def firma_curva(curva, decimales=6):
    """Clave orientacion-independiente, incluye interior y longitud."""
    puntos = [curva.Evaluate(i / 8.0, True) for i in range(9)]
    datos = tuple((round(p.X, decimales), round(p.Y, decimales), round(p.Z, decimales)) for p in puntos)
    return (round(curva.Length, decimales), min(datos, tuple(reversed(datos))))


class CapaRow(object):
    def __init__(self, cad, capa):
        self.Cad, self.Capa = cad, capa
        self.IsSelected = False
        self.curvas = []
        self.symbol = None
        self.Familia = self.Tipo = "-"
        self.Modo = "Sin asignar"
        self.Cantidad = 0
        self.Geometria = ""
        self.Detalle = ""
        self.modo = 0
        self.paso = 1.0
        self.aproximar = False
        self.soporte_3d = False


class CurvasCADMixin(object):
    """Metodos llamados por manejadores protegidos del script principal."""

    def iniciar_curvas(self):
        self.capas_rows = []
        self.cmb_modo_curvas.SelectedIndex = 0
        self.curvas_detectar_tipos()

    def curvas_detectar_tipos(self):
        self.all_familia_curvas_items = list(self.all_familia_items)
        tipos = list(DB.FilteredElementCollector(revit.doc).OfClass(DB.Architecture.RailingType))
        if tipos:
            self.all_familia_curvas_items.append(BarandillasItem(tipos))
        self.curvas_filtrar_familias()

    def curvas_familia(self):
        item = self.cmb_familias_curvas.SelectedItem
        self.cmb_tipos_curvas.ItemsSource = item.tipos if item else []
        self.cmb_tipos_curvas.SelectedIndex = 0 if item and item.tipos else -1

    def curvas_filtrar_familias(self):
        criterio = (self.txt_buscar_curvas.Text or "").strip().lower()
        self.cmb_familias_curvas.ItemsSource = [
            i for i in self.all_familia_curvas_items
            if criterio in i.Nombre.lower() or criterio in i.Categoria.lower()]
        self.curvas_familia()

    def curvas_marcar(self, valor):
        for row in self.capas_rows:
            row.IsSelected = valor
        self._refrescar(self.lv_capas_curvas, self.capas_rows)

    def curvas_analizar(self):
        cads = [r for r in self.all_cad_rows if r.IsSelected]
        if not cads:
            forms.alert("Marca uno o mas CAD en la lista superior.")
            return
        if any(r.symbol is not None for r in self.capas_rows):
            if not forms.alert("Reanalizar reemplazara las capas y sus asignaciones. ¿Continuar?", yes=True, no=True):
                return
        mapa, incidencias = {}, []
        for cad in cads:
            try:
                opts = DB.Options()
                opts.IncludeNonVisibleObjects = True
                opts.ComputeReferences = False
                geom = cad.element.get_Geometry(opts)
                if geom is None:
                    raise ValueError("El CAD no devuelve geometria.")
                self._curvas_recorrer(geom, DB.Transform.Identity, cad, mapa,
                                     "(sin capa)", 0, incidencias)
            except Exception as error:
                incidencias.append("{0}: {1}".format(cad.Nombre, error))
        filas = sorted(mapa.values(), key=lambda r: (r.Cad, r.Capa))
        for row in filas:
            vistas, unicas, clases = set(), [], {}
            for curva in row.curvas:
                firma = firma_curva(curva)
                if firma in vistas:
                    continue
                vistas.add(firma)
                unicas.append(curva)
                clase = curva.GetType().Name
                clases[clase] = clases.get(clase, 0) + 1
            row.curvas = unicas
            row.Cantidad = len(unicas)
            row.Geometria = ", ".join("{0}: {1}".format(k, clases[k]) for k in sorted(clases))
        self.capas_rows = filas
        self._refrescar(self.lv_capas_curvas, filas)
        self.txt_resumen_curvas.Text = "{0} capas; {1} tramos. {2}".format(
            len(filas), sum(r.Cantidad for r in filas), " | ".join(incidencias[:6]))
        self.txt_status_bar.Text = "Analisis de capas terminado."

    def _curvas_recorrer(self, geom, padre, cad, mapa, heredada, profundidad, incidencias):
        if profundidad > 10:
            incidencias.append("{0}: anidamiento mayor a 10 omitido.".format(cad.Nombre))
            return
        for obj in geom:
            capa = self._curvas_capa(obj, heredada)
            try:
                if isinstance(obj, DB.GeometryInstance):
                    interior = self._materializar(self._llamar(obj, "GetSymbolGeometry"))
                    if interior:
                        self._curvas_recorrer(interior, padre.Multiply(obj.Transform), cad,
                                             mapa, capa, profundidad + 1, incidencias)
                    else:
                        # InstanceGeometry esta en el sistema del PADRE, no del modelo.
                        interior = self._materializar(self._llamar(obj, "GetInstanceGeometry"))
                        if interior:
                            self._curvas_recorrer(interior, padre, cad, mapa, capa,
                                                 profundidad + 1, incidencias)
                elif isinstance(obj, DB.PolyLine):
                    puntos = [padre.OfPoint(p) for p in obj.GetCoordinates()]
                    for a, b in zip(puntos, puntos[1:]):
                        if a.DistanceTo(b) > revit.doc.Application.ShortCurveTolerance:
                            self._curvas_agregar(DB.Line.CreateBound(a, b), cad, capa, mapa)
                elif isinstance(obj, DB.Curve):
                    curva = obj.CreateTransformed(padre)
                    if curva.IsBound:
                        self._curvas_agregar(curva, cad, capa, mapa)
                    elif curva.IsCyclic:
                        # Circulos/elipses completos: dos mitades con longitud finita.
                        for a, b in ((0.0, curva.Period / 2), (curva.Period / 2, curva.Period)):
                            parte = curva.Clone()
                            parte.MakeBound(a, b)
                            self._curvas_agregar(parte, cad, capa, mapa)
                    else:
                        incidencias.append("{0}/{1}: curva infinita omitida.".format(cad.Nombre, capa))
            except Exception as error:
                if len(incidencias) < 20:
                    incidencias.append("{0}/{1}: {2}".format(cad.Nombre, capa, error))

    def _curvas_capa(self, obj, heredada):
        try:
            estilo = revit.doc.GetElement(obj.GraphicsStyleId)
            if estilo and estilo.GraphicsStyleCategory:
                nombre = estilo.GraphicsStyleCategory.Name
                if nombre and nombre != "0":
                    return nombre
                if nombre == "0" and heredada == "(sin capa)":
                    return nombre
        except Exception:
            pass
        return heredada

    def _curvas_agregar(self, curva, cad, capa, mapa):
        if curva.Length <= revit.doc.Application.ShortCurveTolerance:
            return
        clave = (cad.Id, capa)
        if clave not in mapa:
            mapa[clave] = CapaRow(cad.Nombre, capa)
        mapa[clave].curvas.append(curva)

    def curvas_asignar(self):
        familia = self.cmb_familias_curvas.SelectedItem
        tipo = self.cmb_tipos_curvas.SelectedItem
        marcadas = [r for r in self.capas_rows if r.IsSelected]
        if not familia or not tipo or not marcadas:
            forms.alert("Marca capas y elige una familia y un tipo cargados.")
            return
        placement = colocacion(tipo.symbol)
        modo = self.cmb_modo_curvas.SelectedIndex
        if modo == 0 and placement not in ("CurveBased", "CurveBasedDetail", "CurveDrivenStructural", "Barandilla"):
            forms.alert("Esta familia no es lineal. Elige un modo puntual o una familia basada en linea.")
            return
        if modo not in (0, 1, 2):
            raise ValueError("Elige un modo de colocacion.")
        if modo != 0 and placement not in ("OneLevelBased", "ViewBased"):
            mensaje = 'Las barandillas requieren el modo lineal.' if placement == 'Barandilla' else 'Esta familia requiere anfitrion, plano, dos niveles o colocacion especial. Elige una familia puntual sin anfitrion.'
            forms.alert(mensaje)
            return
        paso = float((self.txt_paso_curvas.Text or "").replace(",", ".")) / METROS_POR_PIE if modo == 2 else 1.0
        distancias(1.0, paso)
        for row in marcadas:
            row.symbol = tipo.symbol
            row.Familia, row.Tipo = familia.Nombre, tipo.Nombre
            row.modo, row.paso = modo, paso
            row.aproximar = bool(self.chk_aproximar_curvas.IsChecked)
            row.soporte_3d = placement == 'Barandilla' and bool(self.chk_soporte_3d.IsChecked)
            if row.soporte_3d:
                comprobar_soporte_3d()
            row.Modo = ("Lineal por tramo", "Punto medio", "Cada {0:g} m".format(paso * METROS_POR_PIE))[modo]
            if placement == 'Barandilla':
                row.Modo = 'Barandilla por recorrido conectado'
                if row.soporte_3d:
                    row.Modo = 'CAD 3D + suelos auxiliares'
            if row.aproximar and modo == 0:
                row.Modo += " (curvas segmentadas)"
        self._refrescar(self.lv_capas_curvas, self.capas_rows)

    def _curvas_candidatos(self, row):
        placement = colocacion(row.symbol)
        if placement == 'Barandilla':
            curvas = []
            for curva in row.curvas:
                if isinstance(curva, (DB.Line, DB.Arc)):
                    curvas.append(curva)
                elif row.aproximar:
                    puntos = list(curva.Tessellate())
                    for a, b in zip(puntos, puntos[1:]):
                        if a.DistanceTo(b) > revit.doc.Application.ShortCurveTolerance:
                            curvas.append(DB.Line.CreateBound(a, b))
                else:
                    raise ValueError('Las barandillas admiten lineas y arcos. Para otras curvas, activa segmentar y vuelve a asignar.')
            for recorrido in conectar_recorridos(curvas):
                if es_horizontal(recorrido.curvas):
                    yield recorrido
                    continue
                if not row.soporte_3d:
                    # Sin soporte 3D: cada tramo inclinado se divide en peldanos horizontales.
                    for curva in recorrido.curvas:
                        if es_horizontal([curva]):
                            yield RecorridoBarandilla([curva])
                            continue
                        if isinstance(curva, DB.Line):
                            segmentos = [curva]
                        elif row.aproximar:
                            puntos = list(curva.Tessellate())
                            segmentos = [DB.Line.CreateBound(a, b) for a, b in zip(puntos, puntos[1:])
                                         if a.DistanceTo(b) > revit.doc.Application.ShortCurveTolerance]
                        else:
                            raise ValueError('Una curva inclinada necesita Segmentar curvas. Activa esa opcion y vuelve a asignar la capa.')
                        for segmento in segmentos:
                            for plano in tramos_escalonados(segmento, revit.doc.Application.ShortCurveTolerance):
                                yield RecorridoBarandilla([plano])
                    continue
                comprobar_soporte_3d()
                for curva in recorrido.curvas:
                    if es_horizontal([curva]):
                        yield RecorridoBarandilla([curva])
                        continue
                    if isinstance(curva, DB.Line):
                        segmentos = [curva]
                    elif row.aproximar:
                        puntos = list(curva.Tessellate())
                        segmentos = [DB.Line.CreateBound(a, b) for a, b in zip(puntos, puntos[1:])
                                     if a.DistanceTo(b) > revit.doc.Application.ShortCurveTolerance]
                    else:
                        raise ValueError('Una curva inclinada necesita Segmentar curvas. Activa esa opcion y vuelve a asignar la capa.')
                    for segmento in segmentos:
                        a, b, _, _ = datos_tramo_3d(segmento, revit.doc.Application.ShortCurveTolerance)
                        parte = RecorridoBarandilla([DB.Line.CreateBound(a, b)])
                        parte.soporte_3d = True
                        yield parte
            return
        for curva in row.curvas:
            if row.modo == 0:
                if isinstance(curva, DB.Line) or (placement == "CurveDrivenStructural" and not row.aproximar):
                    yield curva
                elif row.aproximar:
                    puntos = list(curva.Tessellate())
                    for a, b in zip(puntos, puntos[1:]):
                        if a.DistanceTo(b) > revit.doc.Application.ShortCurveTolerance:
                            yield DB.Line.CreateBound(a, b)
                else:
                    raise ValueError("La capa contiene curvas. Esta familia admite rectas; activa segmentar curvas y vuelve a asignar, o usa una familia puntual.")
            else:
                valores = [curva.Length / 2] if row.modo == 1 else distancias(curva.Length, row.paso)
                for distancia in valores:
                    t = parametro_distancia(curva, distancia)
                    punto = curva.Evaluate(t, True)
                    tangente = curva.ComputeDerivatives(t, True).BasisX
                    if placement == "ViewBased":
                        vista = revit.doc.ActiveView
                        angulo = math.atan2(tangente.DotProduct(vista.UpDirection), tangente.DotProduct(vista.RightDirection))
                    else:
                        angulo = math.atan2(tangente.Y, tangente.X)
                    yield (punto, angulo)

    def _curvas_crear(self, symbol, candidato):
        doc = revit.doc
        if es_barandilla(symbol):
            return self._barandilla_crear(symbol, candidato)
        placement = colocacion(symbol)
        if isinstance(candidato, tuple):
            punto, angulo = candidato
            if placement == "ViewBased":
                self._curvas_validar_vista([punto])
                instancia = doc.Create.NewFamilyInstance(punto, symbol, doc.ActiveView)
            else:
                nivel = self._nivel_mas_cercano(punto.Z)
                if nivel is None:
                    raise ValueError("No hay niveles en el modelo.")
                instancia = doc.Create.NewFamilyInstance(punto, symbol, nivel, DB.Structure.StructuralType.NonStructural)
            doc.Regenerate()
            if not isinstance(instancia.Location, DB.LocationPoint):
                raise ValueError("La familia no proporciona un punto de colocacion.")
            dz = punto.Z - instancia.Location.Point.Z
            if abs(dz) > 1e-6:
                DB.ElementTransformUtils.MoveElement(doc, instancia.Id, DB.XYZ(0, 0, dz))
            giro = angulo - instancia.Location.Rotation
            if placement == "ViewBased":
                eje = DB.Line.CreateBound(punto, punto + doc.ActiveView.ViewDirection)
                DB.ElementTransformUtils.RotateElement(doc, instancia.Id, eje, giro)
            else:
                self._girar(instancia, punto, giro)
            doc.Regenerate()
            if instancia.Location.Point.DistanceTo(punto) > 1e-5:
                raise ValueError("La familia no conserva la posicion CAD.")
            return instancia
        curva = candidato
        if placement == "CurveBasedDetail":
            self._curvas_validar_vista([curva.GetEndPoint(0), curva.GetEndPoint(1)])
            instancia = doc.Create.NewFamilyInstance(curva, symbol, doc.ActiveView)
        else:
            nivel = self._nivel_mas_cercano(curva.GetEndPoint(0).Z)
            if nivel is None:
                raise ValueError("No hay niveles en el modelo.")
            estructura = DB.Structure.StructuralType.Beam if placement == "CurveDrivenStructural" else DB.Structure.StructuralType.NonStructural
            instancia = doc.Create.NewFamilyInstance(curva, symbol, nivel, estructura)
        doc.Regenerate()
        if not isinstance(instancia.Location, DB.LocationCurve):
            raise ValueError("La familia no proporciona una ubicacion lineal.")
        instancia.Location.Curve = curva
        doc.Regenerate()
        if firma_curva(instancia.Location.Curve) != firma_curva(curva):
            raise ValueError("La familia no conserva el recorrido CAD.")
        return instancia

    def _barandilla_crear(self, tipo, recorrido):
        if recorrido.soporte_3d:
            return self._barandilla_crear_3d(tipo, recorrido)
        doc = revit.doc
        niveles = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
        if not niveles:
            raise ValueError('No hay niveles para la barandilla.')
        nivel = min(niveles, key=lambda n: abs(n.ProjectElevation - recorrido.z))
        camino = DB.CurveLoop()
        traslado = DB.Transform.CreateTranslation(DB.XYZ(0, 0, nivel.ProjectElevation - recorrido.z))
        for curva in recorrido.curvas:
            camino.Append(curva.CreateTransformed(traslado))
        barandilla = DB.Architecture.Railing.Create(doc, camino, tipo.Id, nivel.Id)
        if barandilla is None:
            raise ValueError('Revit no creo la barandilla.')
        offset = barandilla.get_Parameter(DB.BuiltInParameter.STAIRS_RAILING_HEIGHT_OFFSET)
        dz = recorrido.z - nivel.ProjectElevation
        if offset is None or offset.IsReadOnly:
            if abs(dz) > 1e-6:
                raise ValueError('No se puede ajustar la cota base de la barandilla.')
        elif not offset.Set(dz):
            raise ValueError('Revit rechazo el desplazamiento base de la barandilla.')
        doc.Regenerate()
        real = self._barandilla_recorrido(barandilla)
        if not mismo_recorrido(real, recorrido):
            raise ValueError('La barandilla no conserva el recorrido y cota CAD.')
        return barandilla

    def _tipo_suelo_auxiliar(self):
        """Tipo dedicado; nunca modifica un tipo de suelo del proyecto."""
        doc = revit.doc
        for tipo in DB.FilteredElementCollector(doc).OfClass(DB.FloorType):
            if nombre_tipo_sistema(tipo) == TIPO_SOPORTE_3D:
                estructura = tipo.GetCompoundStructure()
                if estructura is None or abs(estructura.GetWidth() - ESPESOR_SOPORTE_FT) > 1e-6:
                    raise ValueError('El tipo {0} fue modificado. Debe tener 20 mm de espesor.'.format(TIPO_SOPORTE_3D))
                return tipo
        base = doc.GetElement(DB.Floor.GetDefaultFloorType(doc, False))
        if base is None:
            raise ValueError('El proyecto necesita al menos un tipo de suelo para crear el soporte auxiliar.')
        from System.Collections.Generic import List
        capas = List[DB.CompoundStructureLayer]()
        capas.Add(DB.CompoundStructureLayer(ESPESOR_SOPORTE_FT,
                  DB.MaterialFunctionAssignment.Structure, DB.ElementId.InvalidElementId))
        tipo = base.Duplicate(TIPO_SOPORTE_3D)
        tipo.SetCompoundStructure(DB.CompoundStructure.CreateSimpleCompoundStructure(capas))
        return tipo

    def _z_cara_superior(self, suelo, punto):
        """Interseccion vertical con una cara superior plana del soporte."""
        for referencia in DB.HostObjectUtils.GetTopFaces(suelo):
            cara = suelo.GetGeometryObjectFromReference(referencia)
            if not isinstance(cara, DB.PlanarFace) or abs(cara.FaceNormal.Z) < 1e-9:
                continue
            normal, origen = cara.FaceNormal, cara.Origin
            z = origen.Z - (normal.X * (punto.X - origen.X) + normal.Y * (punto.Y - origen.Y)) / normal.Z
            candidato = DB.XYZ(punto.X, punto.Y, z)
            proyectado = cara.Project(candidato)
            if (proyectado is not None and cara.IsInside(proyectado.UVPoint)
                    and proyectado.XYZPoint.DistanceTo(candidato) <= TOLERANCIA_SOPORTE_FT):
                return z
        raise ValueError('No se encontro la cara superior del soporte en el recorrido CAD.')

    def _barandilla_crear_3d(self, tipo, recorrido):
        """Suelo inclinado y barandilla dentro de la misma subtransaccion."""
        comprobar_soporte_3d()
        from System.Collections.Generic import List
        doc = revit.doc
        a, b, longitud_xy, angulo = datos_tramo_3d(recorrido.curvas[0], doc.Application.ShortCurveTolerance)
        niveles = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
        if not niveles:
            raise ValueError('No hay niveles para el soporte del cerco.')
        nivel = min(niveles, key=lambda n: abs(n.ProjectElevation - a.Z))
        z = nivel.ProjectElevation
        dx, dy = (b.X - a.X) / longitud_xy, (b.Y - a.Y) / longitud_xy
        nx, ny = -dy * ANCHO_SOPORTE_FT / 2, dx * ANCHO_SOPORTE_FT / 2
        vertices = [DB.XYZ(a.X+nx, a.Y+ny, z), DB.XYZ(a.X-nx, a.Y-ny, z),
                    DB.XYZ(b.X-nx, b.Y-ny, z), DB.XYZ(b.X+nx, b.Y+ny, z)]
        contorno = DB.CurveLoop()
        for i in range(4):
            contorno.Append(DB.Line.CreateBound(vertices[i], vertices[(i+1) % 4]))
        contornos = List[DB.CurveLoop]()
        contornos.Add(contorno)
        eje = DB.Line.CreateBound(DB.XYZ(a.X, a.Y, z), DB.XYZ(b.X, b.Y, z))
        tipo_suelo = self._tipo_suelo_auxiliar()
        suelo = DB.Floor.Create(doc, contornos, tipo_suelo.Id, nivel.Id, False, eje, angulo)
        doc.Regenerate()
        # No presupone donde ancla Revit la flecha: ajusta desde la cara real.
        centro = recorrido.curvas[0].Evaluate(0.5, True)
        desfase = suelo.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if desfase is None or desfase.IsReadOnly:
            raise ValueError('No se puede ajustar la elevacion del soporte auxiliar.')
        if not desfase.Set(desfase.AsDouble() + centro.Z - self._z_cara_superior(suelo, centro)):
            raise ValueError('Revit rechazo la elevacion del soporte auxiliar.')
        doc.Regenerate()
        for punto in (a, centro, b):
            if abs(self._z_cara_superior(suelo, punto) - punto.Z) > TOLERANCIA_SOPORTE_FT:
                raise ValueError('El suelo auxiliar no reproduce la pendiente CAD. Se revierte este tramo.')
        marca = suelo.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if marca is None or marca.IsReadOnly or not marca.Set(MARCA_SOPORTE_3D):
            raise ValueError('No se pudo identificar el suelo como soporte auxiliar.')
        camino = DB.CurveLoop()
        camino.Append(eje)
        barandilla = DB.Architecture.Railing.Create(doc, camino, tipo.Id, nivel.Id)
        barandilla.HostId = suelo.Id
        offset = barandilla.get_Parameter(DB.BuiltInParameter.STAIRS_RAILING_HEIGHT_OFFSET)
        if offset is not None:
            if offset.IsReadOnly:
                if abs(offset.AsDouble()) > TOLERANCIA_SOPORTE_FT:
                    raise ValueError('La barandilla tiene un desplazamiento bloqueado sobre el soporte auxiliar.')
            elif not offset.Set(0.0):
                raise ValueError('No se pudo apoyar la barandilla en el soporte auxiliar.')
        doc.Regenerate()
        if barandilla.HostId != suelo.Id:
            raise ValueError('Revit no conservo el anfitrion de la barandilla.')
        real = self._barandilla_recorrido_3d(barandilla, suelo)
        if not mismo_recorrido(real, recorrido):
            raise ValueError('El recorrido de la barandilla sobre el soporte no coincide con el CAD.')
        return barandilla

    def _barandilla_recorrido_3d(self, barandilla, suelo):
        curvas = list(barandilla.GetPath())
        if len(curvas) != 1 or not isinstance(curvas[0], DB.Line):
            raise ValueError('El cerco 3D fue editado: el recorrido ya no es un tramo recto.')
        offset = barandilla.get_Parameter(DB.BuiltInParameter.STAIRS_RAILING_HEIGHT_OFFSET)
        dz = offset.AsDouble() if offset else 0.0
        puntos = []
        for extremo in (0, 1):
            p = curvas[0].GetEndPoint(extremo)
            puntos.append(DB.XYZ(p.X, p.Y, self._z_cara_superior(suelo, p) + dz))
        recorrido = RecorridoBarandilla([DB.Line.CreateBound(puntos[0], puntos[1])])
        recorrido.soporte_3d = True
        return recorrido

    def _barandilla_recorrido(self, barandilla):
        doc = revit.doc
        base = barandilla.get_Parameter(DB.BuiltInParameter.STAIRS_RAILING_BASE_LEVEL_PARAM)
        offset = barandilla.get_Parameter(DB.BuiltInParameter.STAIRS_RAILING_HEIGHT_OFFSET)
        nivel = doc.GetElement(base.AsElementId())
        recorrido = RecorridoBarandilla(list(barandilla.GetPath()))
        recorrido.z = nivel.ProjectElevation + (offset.AsDouble() if offset else 0.0)
        return recorrido

    def _curvas_validar_vista(self, puntos):
        vista = revit.doc.ActiveView
        if vista.IsTemplate or vista.ViewType not in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail, DB.ViewType.DraftingView):
            raise ValueError("La familia de detalle requiere una vista 2D compatible.")
        if any(abs((p - vista.Origin).DotProduct(vista.ViewDirection)) > 1e-5 for p in puntos):
            raise ValueError("La geometria CAD no esta en el plano de la vista activa.")

    def curvas_generar(self):
        filas = [r for r in self.capas_rows if r.IsSelected]
        if not filas or any(r.symbol is None for r in filas):
            forms.alert("Marca capas y asigna familia y tipo a todas ellas.")
            return
        # Plan completo antes de la transaccion: una capa incompatible no se genera parcialmente.
        planes, total = [], 0
        for row in filas:
            candidatos = []
            try:
                for candidato in self._curvas_candidatos(row):
                    total += 1
                    if total > MAX_CANDIDATOS:
                        raise ValueError("Mas de 20000 ejemplares. Reduce las capas o aumenta la separacion.")
                    candidatos.append(candidato)
            except Exception as error:
                row.Detalle = 'Revisar geometria: {0}'.format(error)
                self._refrescar(self.lv_capas_curvas, self.capas_rows)
                self.txt_status_bar.Text = 'Generacion pendiente: revisar la capa {0}.'.format(row.Capa)
                forms.alert('CAD: {0}\nCapa: {1}\n\n{2}\n\nNo se han creado elementos.'.format(
                    row.Cad, row.Capa, error), title='Revisar recorrido CAD')
                return
            planes.append((row, candidatos))
        soportes = sum(1 for _, candidatos in planes for c in candidatos
                       if isinstance(c, RecorridoBarandilla) and c.soporte_3d)
        aviso = ''
        if soportes:
            aviso = ('\n\nCAD 3D: hasta {0} suelos auxiliares (ancho 20 cm, espesor 2 cm), '
                     'uno por tramo inclinado. Son elementos reales y apareceran en vistas y tablas de suelos. '
                     'Deben conservarse como anfitriones del cerco. Los duplicados no crean soportes.').format(soportes)
        if not forms.alert("Se procesaran {0} ejemplares en {1} capas.{2}\n\n¿Continuar?".format(total, len(filas), aviso), yes=True, no=True):
            return
        indice = self._indice_existentes([]) if self.chk_duplicados_curvas.IsChecked else None
        firmas = set()
        if indice is not None:
            tipos = set(r.symbol.Id.IntegerValue for r in filas)
            for elem in DB.FilteredElementCollector(revit.doc).OfClass(DB.FamilyInstance):
                try:
                    tipo_id = elem.GetTypeId().IntegerValue
                    if tipo_id not in tipos:
                        continue
                    owner_id = elem.OwnerViewId.IntegerValue
                    if isinstance(elem.Location, DB.LocationPoint):
                        indice.agregar((tipo_id, owner_id), elem.Location.Point)
                    elif isinstance(elem.Location, DB.LocationCurve):
                        firmas.add((elem.GetTypeId().IntegerValue, elem.OwnerViewId.IntegerValue, firma_curva(elem.Location.Curve)))
                except Exception:
                    pass
            if any(es_barandilla(r.symbol) for r in filas):
                for elem in DB.FilteredElementCollector(revit.doc).OfClass(DB.Architecture.Railing):
                    try:
                        if elem.GetTypeId().IntegerValue not in tipos:
                            continue
                        if not elem.HasHost:
                            existente = self._barandilla_recorrido(elem)
                        else:
                            anfitrion = revit.doc.GetElement(elem.HostId)
                            if not isinstance(anfitrion, DB.Floor):
                                continue
                            marca = anfitrion.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                            if marca is None or marca.AsString() != MARCA_SOPORTE_3D:
                                continue
                            existente = self._barandilla_recorrido_3d(elem, anfitrion)
                        firmas.add((elem.GetTypeId().IntegerValue, -1, firma_barandilla(existente)))
                    except Exception:
                        pass
        creados = []
        transaccion = DB.Transaction(revit.doc, "Generar familias desde capas CAD")
        transaccion.Start()
        try:
            for row, candidatos in planes:
                colocados = omitidos = fallidos = 0
                soportes_creados = 0
                errores = []
                symbol = row.symbol
                if not es_barandilla(symbol) and not symbol.IsActive:
                    symbol.Activate()
                    revit.doc.Regenerate()
                view_id = revit.doc.ActiveView.Id.IntegerValue if colocacion(symbol) in ("ViewBased", "CurveBasedDetail") else -1
                for candidato in candidatos:
                    puntual = isinstance(candidato, tuple)
                    tipo_id = symbol.Id.IntegerValue
                    clave = None if puntual else (tipo_id, view_id, firma_candidato(candidato))
                    if indice is not None and ((puntual and indice.existe((tipo_id, view_id), candidato[0])) or (not puntual and clave in firmas)):
                        omitidos += 1
                        continue
                    sub = DB.SubTransaction(revit.doc)
                    sub.Start()
                    try:
                        instancia = self._curvas_crear(symbol, candidato)
                        id_creado = instancia.Id.IntegerValue
                        if sub.Commit() != DB.TransactionStatus.Committed:
                            raise ValueError("Revit no confirmo la colocacion.")
                    except Exception as error:
                        if sub.GetStatus() == DB.TransactionStatus.Started:
                            sub.RollBack()
                        fallidos += 1
                        if len(errores) < 3:
                            errores.append(str(error))
                        continue
                    finally:
                        sub.Dispose()
                    creados.append(id_creado)
                    colocados += 1
                    if isinstance(candidato, RecorridoBarandilla) and candidato.soporte_3d:
                        soportes_creados += 1
                    if indice is not None:
                        if puntual:
                            indice.agregar((tipo_id, view_id), candidato[0])
                        else:
                            firmas.add(clave)
                row.Detalle = "{0} creados, {1} duplicados, {2} fallidos. {3}".format(colocados, omitidos, fallidos, " | ".join(errores))
                if soportes_creados:
                    row.Detalle += ' {0} suelos auxiliares creados.'.format(soportes_creados)
            if transaccion.Commit() != DB.TransactionStatus.Committed:
                raise ValueError("Revit no confirmo la transaccion; no se registran ejemplares creados.")
        except Exception:
            if transaccion.GetStatus() == DB.TransactionStatus.Started:
                transaccion.RollBack()
            for row in filas:
                row.Detalle = "Generacion no confirmada; revisar errores de Revit."
            self._refrescar(self.lv_capas_curvas, self.capas_rows)
            raise
        finally:
            transaccion.Dispose()
        self.ids_creados.extend(creados)
        self._refrescar(self.lv_capas_curvas, self.capas_rows)
        self.txt_status_bar.Text = "Capas CAD: {0} ejemplares creados. Consulta Detalle por capa.".format(len(creados))
