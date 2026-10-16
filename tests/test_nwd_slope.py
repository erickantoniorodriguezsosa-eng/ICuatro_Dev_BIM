# -*- coding: utf-8 -*-
"""Perfil y aplicacion opcional con API simulada; no sustituye Revit 2022."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from test_nwd_contact import Mesh
from nwdcoord import slope, surface, transform, units, movement
from nwdcoord import vertical_analysis as va, revit_elements as re


def mesh_plane():
    return surface.SuperficieNWD(Mesh([
        [(0, 0, 1), (10, 0, 2), (0, 10, 1)],
        [(10, 0, 2), (10, 10, 2), (0, 10, 1)]])).cargar()


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.transform = transform.Transformacion(100, -50, 5)
        self.ends = tuple(self.transform.nwd_a_revit(*p)
                          for p in ((1, 2, 20), (9, 2, 20)))
        self.options = va.OpcionesAnalisis()

    def test_real_triangle_slope_units_offset_and_original_xy(self):
        self.options.offset_m = 0.3
        p, note = slope.evaluar_perfil(self.ends, mesh_plane(),
                                       self.transform, self.options)
        self.assertAlmostEqual(p.porcentaje, 10)
        self.assertGreaterEqual(p.muestras, 33)
        for i, expected in enumerate((1.4, 2.2)):
            self.assertEqual(p.objetivos[i][:2], self.ends[i][:2])
            self.assertAlmostEqual(self.transform.revit_a_nwd(*p.objetivos[i])[2],
                                   expected)

    def test_interior_break_is_not_replaced_with_straight_line(self):
        provider = NS(intersecciones=lambda x, y: [1 + abs(x - 5) * .1])
        p, note = slope.evaluar_perfil(self.ends, provider,
                                       self.transform, self.options)
        self.assertIsNone(p)
        self.assertIn('Perfil irregular', note)

    def test_missing_interior_hit_blocks_proposal(self):
        provider = NS(intersecciones=lambda x, y: [] if 4.5 < x < 5.5 else [x / 10])
        p, note = slope.evaluar_perfil(self.ends, provider,
                                       self.transform, self.options)
        self.assertIsNone(p)
        self.assertIn('falta interseccion real', note)

    def test_strict_policy_does_not_take_surface_above_axis(self):
        provider = NS(intersecciones=lambda x, y: [30 + x / 10])
        self.assertIsNone(slope.evaluar_perfil(self.ends, provider,
                                              self.transform, self.options)[0])

    def test_matching_slope_does_not_offer_rotation(self):
        ends = tuple(self.transform.nwd_a_revit(*p)
                     for p in ((1, 2, 10.1), (9, 2, 10.9)))
        self.assertIsNone(slope.evaluar_perfil(ends, mesh_plane(),
                                              self.transform, self.options)[0])

    def test_long_and_vertical_axes_not_adapted(self):
        for b in ((1, 2, 30), (102, 2, 20)):
            ends = (self.ends[0], self.transform.nwd_a_revit(*b))
            self.assertIsNone(slope.evaluar_perfil(ends, mesh_plane(),
                                                  self.transform, self.options)[0])

    def test_endpoint_jump_limit(self):
        self.options.limite_razonable_m = 1
        p, note = slope.evaluar_perfil(self.ends, mesh_plane(),
                                       self.transform, self.options)
        self.assertIsNone(p)
        self.assertIn('limite', note)


class Line:
    IsBound = True

    def __init__(self, a, b):
        self.points = (a, b)

    def GetEndPoint(self, i):
        return self.points[i]

    def Evaluate(self, t, normalized):
        a, b = self.points
        return NS(X=a.X + t * (b.X - a.X), Y=a.Y + t * (b.Y - a.Y),
                  Z=a.Z + t * (b.Z - a.Z))

    CreateBound = staticmethod(lambda a, b: Line(a, b))


class LocationCurve:
    IsReadOnly = False

    def __init__(self, curve):
        self.Curve = curve


class FamilyInstance:
    pass


class SubTransaction:
    def __init__(self, doc):
        self.doc = doc

    def Start(self):
        self.original = self.doc.element.Location.Curve

    def Commit(self):
        return 'committed'

    def RollBack(self):
        self.doc.element.Location.Curve = self.original
        self.doc.rollbacks += 1

    def Dispose(self):
        pass


DB = NS(Line=Line, LocationCurve=LocationCurve, FamilyInstance=FamilyInstance,
        MEPCurve=type('MEPCurve', (), {}), XYZ=lambda x, y, z: NS(X=x, Y=y, Z=z),
        SubTransaction=SubTransaction, TransactionStatus=NS(Committed='committed'),
        JoinGeometryUtils=NS(GetJoinedElements=lambda d, e: NS(Count=0)),
        Structure=NS(StructuralType=NS(Beam='beam', Brace='brace')),
        ConnectorType=NS(Logical='logical'))


class SlopeApplicationTests(unittest.TestCase):
    def setUp(self):
        self.element = FamilyInstance()
        self.element.Id = NS(IntegerValue=42)
        self.element.UniqueId = '42'
        self.element.Location = LocationCurve(Line(DB.XYZ(0, 0, 10), DB.XYZ(10, 0, 10)))
        self.element.MEPModel = None
        self.element.StructuralType = 'none'
        self.doc = NS(element=self.element, Regenerate=lambda: None, rollbacks=0)
        self.r = va.ResultadoCoordinacion(self.element)
        self.r.punto = re.PuntoReferencia(5, 0, 10, 'eje')
        self.r.movible = True
        self.r.estado = va.PENDIENTE
        self.r.modo_referencia = re.INSERCION
        self.r.z_original_m = self.r.z_objetivo_m = units.pies_a_metros(10)
        self.r.delta_m = 0
        self.r.pendiente = slope.PropuestaPendiente(
            ((0, 0, 10), (10, 0, 10)), ((0, 0, 9), (10, 0, 11)), 20, 0, 13)

    def test_center_ok_is_still_selectable_for_slope(self):
        self.assertTrue(self.r.necesita_movimiento())
        self.assertEqual(va.resumir([self.r])['a_mover'], 1)

    def test_no_explicit_authorization_means_no_slope_write(self):
        with patch.object(movement, 'aplicar_pendiente') as apply:
            salida = movement.aplicar_nivelacion(DB, self.doc, [self.r])
        apply.assert_not_called()
        self.assertEqual(salida.movidos, 0)
        self.assertEqual(self.element.Location.Curve.GetEndPoint(0).Z, 10)

    def test_success_verifies_both_endpoints_and_is_idempotent(self):
        with patch.object(re, 'comprobar_movilidad', return_value=(True, '')):
            self.assertTrue(movement.aplicar_pendiente(DB, self.doc, self.r)[0])
            ok, method, error = movement.aplicar_pendiente(DB, self.doc, self.r)
        self.assertTrue(ok)
        self.assertEqual(method, 'pendiente ya aplicada')
        self.assertEqual(slope.extremos(DB, self.element), self.r.pendiente.objetivos)

    def test_constraint_at_one_end_rolls_back(self):
        self.doc.Regenerate = lambda: setattr(
            self.element.Location.Curve.GetEndPoint(1), 'Z', 12)
        with patch.object(re, 'comprobar_movilidad', return_value=(True, '')):
            self.assertFalse(movement.aplicar_pendiente(DB, self.doc, self.r)[0])
        self.assertEqual(self.doc.rollbacks, 1)
        self.assertEqual(slope.extremos(DB, self.element), self.r.pendiente.originales)

    def test_stale_axis_is_not_overwritten(self):
        self.element.Location.Curve.GetEndPoint(0).X = 1
        with patch.object(re, 'comprobar_movilidad', return_value=(True, '')):
            ok, _, error = movement.aplicar_pendiente(DB, self.doc, self.r)
        self.assertFalse(ok)
        self.assertIn('vuelve a analizar', error)
        self.assertEqual(self.element.Location.Curve.GetEndPoint(0).X, 1)

    def test_connected_family_is_rejected(self):
        self.element.MEPModel = NS(ConnectorManager=NS(Connectors=[
            NS(ConnectorType='physical', IsConnected=True)]))
        with patch.object(re, 'comprobar_movilidad', return_value=(True, '')):
            ok, _, error = movement.aplicar_pendiente(DB, self.doc, self.r)
        self.assertFalse(ok)
        self.assertIn('conectores unidos', error)

    def test_authorized_zero_delta_applies_and_records_family(self):
        with patch.object(re, 'comprobar_movilidad', return_value=(True, '')), \
                patch.object(movement.family_progress, 'registrar', return_value=True) as mark:
            salida = movement.aplicar_nivelacion(DB, self.doc, [self.r],
                                                 adaptar_pendientes={42})
        self.assertEqual(salida.movidos, 1)
        mark.assert_called_once()
        self.assertIsNone(self.r.pendiente)
        self.assertFalse(self.r.necesita_movimiento())

    def test_analysis_off_never_evaluates_slope_and_on_does(self):
        options = va.OpcionesAnalisis()
        tr = transform.Transformacion()
        p = NS(altura=lambda *args: units.pies_a_metros(10))
        with patch.object(re, 'describir', return_value=('c', 'f', 't')), \
                patch.object(re, 'comprobar_movilidad', return_value=(True, '')), \
                patch.object(slope, 'evaluar', return_value=(self.r.pendiente, 'propuesta')) as ev:
            result = va.analizar(DB, self.doc, [self.element], p, tr, options)[0]
            self.assertEqual(result.estado, va.OK)
            ev.assert_not_called()
            options.detectar_pendiente = True
            result = va.analizar(DB, self.doc, [self.element], p, tr, options)[0]
            self.assertEqual(result.estado, va.PENDIENTE)
            ev.assert_called_once()


if __name__ == '__main__':
    unittest.main()
