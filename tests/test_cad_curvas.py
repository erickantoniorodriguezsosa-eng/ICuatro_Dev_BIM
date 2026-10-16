"""Pruebas aisladas; no sustituyen cargar el boton en Revit 2022."""
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

BUNDLE = Path(__file__).resolve().parents[1] / 'Revit/pyRevit/ICuatro.extension/Ing.Cuatro.tab/CAD.panel/GeneradorElementosBIM.pushbutton'


class Point:
    def __init__(self, x, y=0, z=0):
        self.X, self.Y, self.Z = x, y, z

    def DistanceTo(self, other):
        return math.sqrt((self.X-other.X)**2 + (self.Y-other.Y)**2 + (self.Z-other.Z)**2)


class Curve:
    IsBound = True

    def __init__(self, a, b):
        self.a, self.b = a, b

    @property
    def Length(self):
        return self.a.DistanceTo(self.b)

    def Evaluate(self, t, normalized):
        return Point(self.a.X+(self.b.X-self.a.X)*t,
                     self.a.Y+(self.b.Y-self.a.Y)*t,
                     self.a.Z+(self.b.Z-self.a.Z)*t)

    def CreateTransformed(self, transform):
        return Curve(transform.OfPoint(self.a), transform.OfPoint(self.b))

    def GetType(self):
        return NS(Name='Line')

    def GetEndPoint(self, end):
        return self.b if end else self.a

    def CreateReversed(self):
        return Curve(self.b, self.a)


class Transform:
    def __init__(self, x=0, scale=1):
        self.x, self.scale = x, scale

    def OfPoint(self, p):
        return Point(self.x+self.scale*p.X, self.scale*p.Y, self.scale*p.Z)

    def Multiply(self, child):
        return Transform(self.x+self.scale*child.x, self.scale*child.scale)


class GeometryInstance:
    GraphicsStyleId = -1

    def __init__(self, transform, symbol=None, instance=None):
        self.Transform = transform
        self.symbol, self.instance = symbol, instance

    def GetSymbolGeometry(self):
        return self.symbol

    def GetInstanceGeometry(self):
        return self.instance


class NonuniformCurve:
    """Parametro t, posicion 10*t^2: distancia no proporcional a t."""
    def __init__(self, a=0, b=1):
        self.a, self.b = a, b

    @property
    def Length(self):
        return 10*(self.b**2-self.a**2)

    def GetEndParameter(self, end):
        return self.b if end else self.a

    def Clone(self):
        return NonuniformCurve(self.a, self.b)

    def MakeBound(self, a, b):
        self.a, self.b = a, b


class Tests(unittest.TestCase):
    def setUp(self):
        self.db = NS(Curve=Curve, GeometryInstance=GeometryInstance, PolyLine=type('PolyLine', (), {}),
                     Transform=NS(Identity=Transform()), Line=Curve, Arc=type('Arc', (Curve,), {}))
        self.doc = NS(Application=NS(ShortCurveTolerance=0.001), GetElement=lambda _: None)
        self.forms = NS(alert=lambda *a, **kw: True)
        fake = NS(DB=self.db, forms=self.forms, revit=NS(doc=self.doc))
        spec = importlib.util.spec_from_file_location('cad_curvas_test', BUNDLE/'cad_curvas.py')
        self.mod = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'pyrevit': fake}):
            spec.loader.exec_module(self.mod)
        self.ui = self.mod.CurvasCADMixin()
        self.ui._materializar = lambda g: list(g) if g else None
        self.ui._llamar = lambda obj, name: getattr(obj, name)()

    def test_spacing_endpoints_and_invalid_input(self):
        self.assertEqual(self.mod.distancias(10, 2), [0, 2, 4, 6, 8])
        self.assertEqual(self.mod.distancias(0.5, 2), [0])
        for paso in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.mod.distancias(10, paso)
        with self.assertRaises(ValueError):
            self.mod.distancias(100, 0.0001)

    def test_actual_arc_length_not_normalized_parameter(self):
        t = self.mod.parametro_distancia(NonuniformCurve(), 5)
        self.assertAlmostEqual(t, math.sqrt(0.5), places=8)

    def test_signature_preserves_interior_and_reversed_direction(self):
        curva = Curve(Point(0), Point(10))
        self.assertEqual(self.mod.firma_curva(curva), self.mod.firma_curva(Curve(Point(10), Point(0))))
        self.assertNotEqual(self.mod.firma_curva(curva), self.mod.firma_curva(Curve(Point(0, 1), Point(10, 1))))

    def test_nested_symbol_geometry_composes_transforms(self):
        curva = Curve(Point(1), Point(2))
        curva.GraphicsStyleId = 7
        self.doc.GetElement = lambda _: NS(GraphicsStyleCategory=NS(Name='EJES'), Name='archivo.dwg')
        geom = [GeometryInstance(Transform(100, 2), [GeometryInstance(Transform(10), [curva])])]
        mapa, errores = {}, []
        self.ui._curvas_recorrer(geom, Transform(), NS(Id=1, Nombre='a.dwg'), mapa, '(sin capa)', 0, errores)
        row = mapa[(1, 'EJES')]
        self.assertEqual(row.curvas[0].a.X, 122)
        self.assertEqual(row.curvas[0].b.X, 124)
        self.assertFalse(errores)

    def test_instance_geometry_fallback_keeps_parent_transform(self):
        curva = Curve(Point(11), Point(12))
        curva.GraphicsStyleId = -1
        geom = [GeometryInstance(Transform(100), [GeometryInstance(Transform(10), instance=[curva])])]
        mapa = {}
        self.ui._curvas_recorrer(geom, Transform(), NS(Id=1, Nombre='a.dwg'), mapa, 'EJES', 0, [])
        self.assertEqual(mapa[(1, 'EJES')].curvas[0].a.X, 111)

    def test_layer_zero_inherits_block_insertion_layer(self):
        self.doc.GetElement = lambda _: NS(GraphicsStyleCategory=NS(Name='0'))
        self.assertEqual(self.ui._curvas_capa(NS(GraphicsStyleId=2), 'RED'), 'RED')
        self.assertEqual(self.ui._curvas_capa(NS(GraphicsStyleId=2), '(sin capa)'), '0')

    def test_failed_instance_rolls_back_before_next_and_ids_only_after_commit(self):
        status = NS(Started='started', Committed='committed', RolledBack='rollback')
        events = []
        class Transaction:
            def __init__(self, *args):
                self.state = None
                self.label = 'main' if len(args) > 1 else 'sub'
            def Start(self):
                self.state = status.Started
            def GetStatus(self):
                return self.state
            def Commit(self):
                self.state = status.Committed
                events.append(self.label+' commit')
                return self.state
            def RollBack(self):
                self.state = status.RolledBack
                events.append(self.label+' rollback')
            def Dispose(self):
                pass
        self.db.Transaction = self.db.SubTransaction = Transaction
        self.db.TransactionStatus = status
        row = self.mod.CapaRow('a', 'RED')
        row.IsSelected = True
        row.symbol = NS(IsActive=True, Id=NS(IntegerValue=9), Family=NS(FamilyPlacementType='OneLevelBased'))
        self.ui.capas_rows = [row]
        self.ui.chk_duplicados_curvas = NS(IsChecked=False)
        self.ui.ids_creados = []
        self.ui.txt_status_bar = NS(Text='')
        self.ui.lv_capas_curvas = NS()
        self.ui._refrescar = lambda *a: None
        self.ui._curvas_candidatos = lambda r: iter([(Point(0), 0), (Point(1), 0)])
        def crear(symbol, candidato):
            if candidato[0].X == 0:
                raise ValueError('fallo al girar')
            return NS(Id=NS(IntegerValue=123))
        self.ui._curvas_crear = crear
        self.ui.curvas_generar()
        self.assertEqual(events, ['sub rollback', 'sub commit', 'main commit'])
        self.assertEqual(self.ui.ids_creados, [123])
        self.assertIn('1 fallidos', row.Detalle)

    def test_xaml_handlers_and_names(self):
        import ast
        root = ET.parse(BUNDLE/'ui.xaml').getroot()
        names = [e.get('Name') for e in root.iter() if e.get('Name')]
        self.assertEqual(len(names), len(set(names)))
        handlers = {e.get(a) for e in root.iter() for a in ('Click', 'SelectionChanged', 'TextChanged') if e.get(a)}
        tree = ast.parse((BUNDLE/'Script.py').read_text(encoding='utf-8'))
        methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertLessEqual(handlers, methods)

    def test_system_railing_catalog_without_family_symbols_or_instances(self):
        class RailingType:
            def __init__(self, name):
                self.Name = name
        self.db.Architecture = NS(RailingType=RailingType)
        rail = RailingType('1100mm')
        self.assertTrue(self.mod.es_barandilla(rail))
        self.assertEqual(self.mod.colocacion(rail), 'Barandilla')
        item = self.mod.BarandillasItem([rail])
        self.assertEqual(item.Nombre, 'Familia de sistema: Barandilla')
        self.assertEqual(item.tipos[0].Nombre, '1100mm')
        self.assertIs(item.tipos[0].symbol, rail)
        self.db.FilteredElementCollector = lambda doc: NS(OfClass=lambda cls: [rail])
        self.ui.all_familia_items = []
        self.ui.txt_buscar_curvas = NS(Text='barandilla')
        self.ui.cmb_familias_curvas = NS(SelectedItem=None)
        self.ui.cmb_tipos_curvas = NS()
        self.ui.curvas_detectar_tipos()
        self.assertEqual(len(self.ui.cmb_familias_curvas.ItemsSource), 1)

    def test_system_type_name_recovers_from_ironpython_attribute_error(self):
        class BrokenNameType:
            Id = NS(IntegerValue=321)
            @property
            def Name(self):
                raise AttributeError('Name')
            def get_Parameter(self, param):
                return NS(AsString=lambda: '1100mm')
        self.db.BuiltInParameter = NS(SYMBOL_NAME_PARAM=1, ALL_MODEL_TYPE_NAME=2)
        tipo = BrokenNameType()
        self.assertEqual(self.mod.TipoSistemaItem(tipo).Nombre, '1100mm')
        self.assertEqual(self.mod.BarandillasItem([tipo]).tipos[0].Display, '1100mm')

    def test_unnamed_system_type_does_not_block_window_catalog(self):
        class BrokenNameType:
            Id = NS(IntegerValue=321)
            @property
            def Name(self):
                raise AttributeError('Name')
            def get_Parameter(self, param):
                raise AttributeError('parameter unavailable')
        self.db.BuiltInParameter = NS(SYMBOL_NAME_PARAM=1, ALL_MODEL_TYPE_NAME=2)
        item = self.mod.BarandillasItem([BrokenNameType()])
        self.assertEqual(item.tipos[0].Nombre, '(tipo sin nombre, Id 321)')

    def test_cad_noise_is_tolerated_in_join_horizontal_and_compare(self):
        paths = self.mod.conectar_recorridos([Curve(Point(0, 0), Point(1, 0)), Curve(Point(1.00002, 0.00001), Point(2, 0))])
        self.assertEqual([len(p.curvas) for p in paths], [2])
        self.assertTrue(self.mod.es_horizontal([Curve(Point(0, 0, 5), Point(1, 0, 5.00005))]))
        self.assertFalse(self.mod.es_horizontal([Curve(Point(0, 0, 5), Point(1, 0, 5.01))]))
        a = self.mod.RecorridoBarandilla([Curve(Point(0, 0, 3), Point(1, 0, 3))])
        near = self.mod.RecorridoBarandilla([Curve(Point(1.00003, 0, 3), Point(0, 0, 3.00002))])
        far = self.mod.RecorridoBarandilla([Curve(Point(0, 0, 3), Point(1.01, 0, 3))])
        self.assertTrue(self.mod.mismo_recorrido(a, near))
        self.assertFalse(self.mod.mismo_recorrido(a, far))

    def test_connected_railing_path_orders_and_reverses_segments(self):
        paths = self.mod.conectar_recorridos([Curve(Point(2), Point(1)), Curve(Point(0), Point(1)), Curve(Point(10), Point(11))])
        self.assertEqual(sorted(len(p.curvas) for p in paths), [1, 2])
        for path in paths:
            for a, b in zip(path.curvas, path.curvas[1:]):
                self.assertEqual(a.GetEndPoint(1).DistanceTo(b.GetEndPoint(0)), 0)

    def test_closed_railing_path_and_attached_branch(self):
        square = [Curve(Point(0, 0), Point(1, 0)), Curve(Point(1, 1), Point(1, 0)),
                  Curve(Point(0, 1), Point(1, 1)), Curve(Point(0, 0), Point(0, 1))]
        paths = self.mod.conectar_recorridos(square)
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(paths[0].curvas), 4)
        self.assertEqual(paths[0].curvas[0].a.DistanceTo(paths[0].curvas[-1].b), 0)
        paths = self.mod.conectar_recorridos(square + [Curve(Point(0, 0), Point(-1, 0))])
        self.assertEqual(sorted(len(p.curvas) for p in paths), [1, 4])
        loop = next(p for p in paths if len(p.curvas) == 4)
        self.assertEqual(loop.curvas[0].a.DistanceTo(loop.curvas[-1].b), 0)

    def assert_all_curves_used_once(self, source, paths):
        expected = [self.mod.firma_curva(c) for c in source]
        actual = [self.mod.firma_curva(c) for p in paths for c in p.curvas]
        self.assertCountEqual(actual, expected)
        for path in paths:
            for a, b in zip(path.curvas, path.curvas[1:]):
                self.assertEqual(a.b.DistanceTo(b.a), 0)

    def test_t_junction_preserves_each_branch_and_all_original_segments(self):
        source = [Curve(Point(-2), Point(-1)), Curve(Point(0), Point(-1)),
                  Curve(Point(1), Point(0)), Curve(Point(1), Point(2)),
                  Curve(Point(0, 2), Point(0, 1)), Curve(Point(0), Point(0, 1))]
        paths = self.mod.conectar_recorridos(source)
        self.assertEqual([len(p.curvas) for p in paths], [2, 2, 2])
        self.assert_all_curves_used_once(source, paths)
        for path in paths:
            self.assertTrue(path.curvas[0].a.DistanceTo(Point(0)) == 0 or
                            path.curvas[-1].b.DistanceTo(Point(0)) == 0)

    def test_cross_junction_results_do_not_depend_on_curve_order(self):
        source = [Curve(Point(0), Point(-1)), Curve(Point(0), Point(1)),
                  Curve(Point(0), Point(0, -1)), Curve(Point(0), Point(0, 1))]
        for curves in (source, list(reversed(source)), [c.CreateReversed() for c in source]):
            paths = self.mod.conectar_recorridos(curves)
            self.assertEqual([len(p.curvas) for p in paths], [1, 1, 1, 1])
            self.assert_all_curves_used_once(source, paths)

    def test_path_between_two_junctions_and_independent_component(self):
        source = [Curve(Point(-1), Point(0)), Curve(Point(0, 1), Point(0)),
                  Curve(Point(0), Point(1)), Curve(Point(2), Point(1)),
                  Curve(Point(2), Point(3)), Curve(Point(2), Point(2, 1)),
                  Curve(Point(10), Point(11))]
        paths = self.mod.conectar_recorridos(source)
        self.assertEqual(sorted(len(p.curvas) for p in paths), [1, 1, 1, 1, 1, 2])
        self.assert_all_curves_used_once(source, paths)

    def test_expected_geometry_error_is_reported_before_any_transaction(self):
        class RailingType:
            pass
        self.db.Architecture = NS(RailingType=RailingType)
        row = self.mod.CapaRow('test.dwg', 'BARANDAS')
        row.symbol = RailingType()
        row.IsSelected = row.soporte_3d = True
        row.curvas = [Curve(Point(0), Point(10, 0, 2))]
        self.ui.capas_rows = [row]
        self.ui.lv_capas_curvas = NS()
        self.ui.txt_status_bar = NS(Text='')
        self.ui._refrescar = lambda *args: None
        messages = []
        self.forms.alert = lambda message, **kwargs: messages.append(message)
        self.ui.curvas_generar()
        self.assertEqual(len(messages), 1)
        self.assertIn('BARANDAS', messages[0])
        self.assertIn('deshabilitada tras un cierre de Revit', messages[0])
        self.assertIn('Revisar geometria', row.Detalle)

    def test_railing_accepts_horizontal_path_and_steps_slope(self):
        class RailingType:
            pass
        self.db.Architecture = NS(RailingType=RailingType)
        self.db.Line.CreateBound = staticmethod(Curve)
        self.db.XYZ = Point
        row = self.mod.CapaRow('CAD', 'rail')
        row.symbol = RailingType()
        row.curvas = [Curve(Point(0, 0, 20), Point(10, 0, 20))]
        paths = list(self.ui._curvas_candidatos(row))
        self.assertEqual(paths[0].z, 20)
        # Tramo inclinado: peldanos de max. 1 unidad a la cota media de cada uno; el vertical se omite.
        row.curvas = [Curve(Point(0, 0, 0), Point(10, 0, 2)),
                      Curve(Point(10, 0, 2), Point(10, 0, 5)),
                      Curve(Point(10, 0, 5), Point(20, 0, 5))]
        with patch.object(self.mod, 'ALTURA_PELDANO_FT', 1.0):
            paths = list(self.ui._curvas_candidatos(row))
        self.assertEqual([p.z for p in paths], [0.5, 1.5, 5])
        self.assertEqual([p.curvas[0].a.X for p in paths], [0, 5, 10])
        self.assertFalse(any(p.soporte_3d for p in paths))
        self.assertTrue(all(self.mod.es_horizontal(p.curvas) for p in paths))
        # Con el peldano por defecto (5 cm) una subida de 2 ft da 13 peldanos.
        steps = self.mod.tramos_escalonados(Curve(Point(0, 0, 0), Point(10, 0, 2)), .001)
        self.assertEqual(len(steps), 13)

    def test_3d_mode_is_disabled_in_code_and_ui(self):
        self.assertFalse(self.mod.SOPORTE_3D_HABILITADO)
        root = ET.parse(BUNDLE/'ui.xaml').getroot()
        checkbox = next(e for e in root.iter() if e.get('Name') == 'chk_soporte_3d')
        self.assertEqual(checkbox.get('IsEnabled'), 'False')
        self.assertEqual(checkbox.get('IsChecked'), 'False')

    def test_disabled_3d_mode_blocks_direct_creation_before_native_api(self):
        # No native API is configured: the guard must run before any API access.
        with self.assertRaisesRegex(ValueError, 'deshabilitada tras un cierre de Revit'):
            self.ui._barandilla_crear_3d(None, None)

    def test_stale_3d_assignment_is_rejected_before_any_transaction(self):
        class RailingType:
            pass
        self.db.Architecture = NS(RailingType=RailingType)
        row = self.mod.CapaRow('CAD', '3D_CERCO')
        row.symbol = RailingType()
        row.IsSelected = row.soporte_3d = True
        row.curvas = [Curve(Point(0), Point(10, 0, 2))]
        self.ui.capas_rows = [row]
        self.ui.lv_capas_curvas = NS()
        self.ui.txt_status_bar = NS(Text='')
        self.ui._refrescar = lambda *args: None
        messages = []
        self.forms.alert = lambda message, **kwargs: messages.append(message)
        # DB.Transaction is intentionally unavailable; preflight must stop first.
        self.ui.curvas_generar()
        self.assertEqual(len(messages), 1)
        self.assertIn('deshabilitada tras un cierre de Revit', messages[0])
        self.assertIn('No se han creado elementos', messages[0])

    def test_cad_3d_candidates_keep_endpoint_elevations_and_horizontal_parts(self):
        class RailingType:
            pass
        self.db.Architecture = NS(RailingType=RailingType)
        self.db.Line.CreateBound = staticmethod(Curve)
        row = self.mod.CapaRow('CAD', '3D_CERCO')
        row.symbol = RailingType()
        row.soporte_3d = True
        row.curvas = [Curve(Point(0, 0, 4), Point(3, 4, 2)),
                      Curve(Point(3, 4, 2), Point(6, 8, 2)),
                      Curve(Point(6, 8, 2), Point(9, 12, 3))]
        # Experimental geometry only; this does not enable the production mode.
        with patch.object(self.mod, 'SOPORTE_3D_HABILITADO', True):
            paths = list(self.ui._curvas_candidatos(row))
        self.assertEqual(len(paths), 3)
        self.assertEqual(sum(p.soporte_3d for p in paths), 2)
        self.assert_all_curves_used_once(row.curvas, paths)

    def test_slope_angle_is_radians_and_vertical_segment_is_rejected(self):
        a, b, run, angle = self.mod.datos_tramo_3d(Curve(Point(3, 4, 8), Point(0, 0, 6)), .001)
        self.assertEqual(run, 5)
        self.assertEqual((a.Z, b.Z), (6, 8))
        self.assertAlmostEqual(angle, math.atan2(2, 5))
        with self.assertRaisesRegex(ValueError, 'vertical'):
            self.mod.datos_tramo_3d(Curve(Point(1, 2, 0), Point(1, 2, 5)), .001)

    def test_sloped_duplicate_signature_includes_both_heights(self):
        def path(a, b):
            p = self.mod.RecorridoBarandilla([Curve(a, b)])
            p.soporte_3d = True
            return p
        original = path(Point(0), Point(10, 0, 2))
        reverse = path(Point(10, 0, 2), Point(0))
        other_slope = path(Point(0), Point(10, 0, 3))
        self.assertEqual(self.mod.firma_barandilla(original), self.mod.firma_barandilla(reverse))
        self.assertNotEqual(self.mod.firma_barandilla(original), self.mod.firma_barandilla(other_slope))

    def _hosted_railing_fake_api(self, wrong_slope=False):
        class GenericList(list):
            Add = list.append
            Append = list.append
            @classmethod
            def __class_getitem__(cls, _):
                return cls
        class Parameter:
            IsReadOnly = False
            def __init__(self, value=0):
                self.value = value
            def Set(self, value):
                self.value = value
                return True
            def AsDouble(self):
                return self.value
        calls = NS(floors=[], railings=[])
        level = NS(Id=5, ProjectElevation=10)
        floor_offset, floor_comment, rail_offset = Parameter(), Parameter(''), Parameter()
        class Floor:
            @staticmethod
            def Create(doc, loops, type_id, level_id, structural, axis, angle):
                floor = NS(Id=500, get_Parameter=lambda param: floor_offset if param == 'floor_offset' else floor_comment)
                calls.floors.append(floor)
                calls.loops, calls.axis, calls.angle = loops, axis, angle
                return floor
        def create_railing(doc, loop, type_id, level_id):
            rail = NS(Id=600, HostId=-1, GetPath=lambda: loop,
                      get_Parameter=lambda param: rail_offset)
            calls.railings.append(rail)
            return rail
        self.db.Floor = Floor
        self.db.Level = type('Level', (), {})
        self.db.CurveLoop = GenericList
        self.db.XYZ = Point
        self.db.Line.CreateBound = staticmethod(Curve)
        self.db.Architecture = NS(Railing=NS(Create=create_railing))
        self.db.BuiltInParameter = NS(FLOOR_HEIGHTABOVELEVEL_PARAM='floor_offset',
                                     ALL_MODEL_INSTANCE_COMMENTS='comment',
                                     STAIRS_RAILING_HEIGHT_OFFSET='rail_offset')
        self.db.FilteredElementCollector = lambda doc: NS(OfClass=lambda cls: [level])
        self.doc.Regenerate = lambda: None
        self.ui._tipo_suelo_auxiliar = lambda: NS(Id=99)
        def top_z(floor, p):
            start, end = calls.axis.a, calls.axis.b
            dx, dy = end.X-start.X, end.Y-start.Y
            station = ((p.X-start.X)*dx+(p.Y-start.Y)*dy)/math.hypot(dx, dy)
            return 17 + floor_offset.value + station * (0 if wrong_slope else math.tan(calls.angle))
        self.ui._z_cara_superior = top_z
        modules = {'System': NS(), 'System.Collections': NS(),
                   'System.Collections.Generic': NS(List=GenericList)}
        return calls, modules, floor_offset, floor_comment, rail_offset

    def test_simulated_hosted_railing_aligns_mock_top_to_cad(self):
        calls, modules, floor_offset, comment, offset = self._hosted_railing_fake_api()
        path = self.mod.RecorridoBarandilla([Curve(Point(3, 4, 14), Point(0, 0, 12))])
        path.soporte_3d = True
        with patch.dict(sys.modules, modules), patch.object(self.mod, 'SOPORTE_3D_HABILITADO', True):
            rail = self.ui._barandilla_crear_3d(NS(Id=100), path)
        self.assertEqual(len(calls.floors), 1)
        self.assertEqual(len(calls.railings), 1)
        self.assertEqual(rail.HostId, 500)
        self.assertEqual(comment.value, self.mod.MARCA_SOPORTE_3D)
        self.assertEqual(offset.value, 0)
        self.assertAlmostEqual(calls.angle, math.atan2(2, 5))
        self.assertAlmostEqual(floor_offset.value, -5)
        self.assertEqual(len(calls.loops[0]), 4)
        for edge in calls.loops[0]:
            self.assertEqual(edge.a.Z, 10)
        for p in (Point(0, 0, 12), Point(1.5, 2, 13), Point(3, 4, 14)):
            self.assertAlmostEqual(self.ui._z_cara_superior(calls.floors[0], p), p.Z)

    def test_wrong_host_slope_is_not_reported_as_successful_railing(self):
        calls, modules, _, _, _ = self._hosted_railing_fake_api(wrong_slope=True)
        path = self.mod.RecorridoBarandilla([Curve(Point(0, 0, 12), Point(10, 0, 14))])
        path.soporte_3d = True
        with patch.dict(sys.modules, modules), patch.object(self.mod, 'SOPORTE_3D_HABILITADO', True):
            with self.assertRaisesRegex(ValueError, 'no reproduce la pendiente'):
                self.ui._barandilla_crear_3d(NS(Id=100), path)
        self.assertEqual(len(calls.railings), 0)

    def test_failed_3d_host_is_rolled_back_without_orphan_floor(self):
        calls, modules, _, _, _ = self._hosted_railing_fake_api(wrong_slope=True)
        class RailingType:
            Id = NS(IntegerValue=100)
        self.db.Architecture.RailingType = RailingType
        status = NS(Started='started', Committed='committed', RolledBack='rollback')
        class Transaction:
            def __init__(self, *args):
                self.state = None
            def Start(self):
                self.state = status.Started
                self.counts = (len(calls.floors), len(calls.railings))
            def GetStatus(self):
                return self.state
            def Commit(self):
                self.state = status.Committed
                return self.state
            def RollBack(self):
                del calls.floors[self.counts[0]:]
                del calls.railings[self.counts[1]:]
                self.state = status.RolledBack
            def Dispose(self):
                pass
        self.db.TransactionStatus = status
        self.db.Transaction = self.db.SubTransaction = Transaction
        row = self.mod.CapaRow('CAD', '3D_CERCO')
        row.symbol = RailingType()
        row.IsSelected = row.soporte_3d = True
        row.curvas = [Curve(Point(0, 0, 12), Point(10, 0, 14))]
        self.ui.capas_rows = [row]
        self.ui.chk_duplicados_curvas = NS(IsChecked=False)
        self.ui.ids_creados = []
        self.ui.txt_status_bar = NS(Text='')
        self.ui.lv_capas_curvas = NS()
        self.ui._refrescar = lambda *args: None
        with patch.dict(sys.modules, modules), patch.object(self.mod, 'SOPORTE_3D_HABILITADO', True):
            self.ui.curvas_generar()
        self.assertEqual(calls.floors, [])
        self.assertEqual(calls.railings, [])
        self.assertEqual(self.ui.ids_creados, [])
        self.assertIn('1 fallidos', row.Detalle)

    def test_hosted_railing_accepts_readonly_zero_base_offset(self):
        calls, modules, _, _, offset = self._hosted_railing_fake_api()
        offset.IsReadOnly = True
        path = self.mod.RecorridoBarandilla([Curve(Point(0, 0, 12), Point(10, 0, 14))])
        path.soporte_3d = True
        with patch.dict(sys.modules, modules), patch.object(self.mod, 'SOPORTE_3D_HABILITADO', True):
            self.ui._barandilla_crear_3d(NS(Id=100), path)
        self.assertEqual(len(calls.railings), 1)

    def test_railing_create_uses_project_elevation_and_base_offset(self):
        class RailingType:
            Id = 100
        class CurveLoop(list):
            Append = list.append
        class Move:
            def __init__(self, z):
                self.z = z
            def OfPoint(self, p):
                return Point(p.X, p.Y, p.Z+self.z)
        self.db.Transform.CreateTranslation = lambda p: Move(p.Z)
        self.db.XYZ = Point
        self.db.CurveLoop = CurveLoop
        self.db.Level = type('Level', (), {})
        self.db.BuiltInParameter = NS(STAIRS_RAILING_HEIGHT_OFFSET='offset', STAIRS_RAILING_BASE_LEVEL_PARAM='base')
        level = NS(Id=5, ProjectElevation=10, Elevation=1000)
        offset = NS(IsReadOnly=False, value=0)
        def set_offset(value):
            offset.value = value
            return True
        offset.Set = set_offset
        offset.AsDouble = lambda: offset.value
        base = NS(AsElementId=lambda: 5)
        captured = {}
        def create(doc, loop, type_id, level_id):
            captured.update(loop=loop, type_id=type_id, level_id=level_id)
            return NS(get_Parameter=lambda param: offset if param == 'offset' else base,
                      GetPath=lambda: loop)
        self.db.Architecture = NS(RailingType=RailingType, Railing=NS(Create=create))
        self.db.FilteredElementCollector = lambda doc: NS(OfClass=lambda cls: [level])
        self.doc.GetElement = lambda id: level
        self.doc.Regenerate = lambda: None
        path = self.mod.RecorridoBarandilla([Curve(Point(0, 0, 12), Point(10, 0, 12))])
        self.ui._curvas_crear(RailingType(), path)
        self.assertEqual(captured['level_id'], 5)
        self.assertEqual(captured['type_id'], 100)
        self.assertEqual(captured['loop'][0].a.Z, 10)
        self.assertEqual(offset.value, 2)
        self.assertEqual(self.mod.firma_barandilla(path),
                         self.mod.firma_barandilla(self.mod.RecorridoBarandilla([Curve(Point(10, 0, 12), Point(0, 0, 12))])))


if __name__ == '__main__':
    unittest.main()
