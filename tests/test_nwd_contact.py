# -*- coding: utf-8 -*-
"""Pruebas aisladas del contacto. No sustituyen la validacion en Revit."""
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                             'Prueba.extension', 'lib'))
from nwdcoord import surface, vertical_analysis as va, revit_elements as re
from nwdcoord import movement, transform, units


class Mesh:
    def __init__(self, triangles):
        self.vertices = [v for triangle in triangles for point in triangle for v in point]
        self.triangulos = list(range(len(self.vertices) // 3))
        self.num_triangulos = len(triangles)
        self.minimos = [min(self.vertices[i::3]) for i in range(3)]
        self.maximos = [max(self.vertices[i::3]) for i in range(3)]

    def caja(self):
        return self.minimos, self.maximos


def provider(*heights):
    return surface.SuperficieNWD(Mesh([
        [(0, 0, z), (10, 0, z), (0, 10, z)] for z in heights])).cargar()


class ContactTests(unittest.TestCase):
    def test_lower_real_surface_wins_over_slope_and_upper_surface(self):
        p = surface.SuperficieNWD(Mesh([
            [(0, 0, 2), (10, 0, 2), (0, 10, 2)],
            [(0, 0, 4), (10, 0, 14), (0, 10, 4)],
            [(0, 0, 30), (10, 0, 30), (0, 10, 30)]])).cargar()
        self.assertEqual(p.altura(2, 2, 20, surface.MAS_BAJA_DEBAJO), 2)
        self.assertEqual(p.ultimas_cotas, [2, 6, 30])
        self.assertEqual(p.ultimo_triangulo, 0)
        self.assertFalse(p.ultima_aproximada)

    def test_no_real_triangle_does_not_extend_nearby_slope(self):
        p = provider(2, 5)
        p._altura_cercana = lambda *args: self.fail('No extrapolar en modo estricto')
        self.assertIsNone(p.altura(8, 8, 20, surface.MAS_BAJA_DEBAJO))

    def test_no_reference_or_only_above_returns_no_hit(self):
        p = provider(12, 15)
        self.assertIsNone(p.altura(2, 2, 10, surface.MAS_BAJA_DEBAJO))
        self.assertIsNone(p.altura(2, 2, None, surface.MAS_BAJA_DEBAJO))
        self.assertEqual(surface.elegir([15, 3, 7], 10, surface.MAS_BAJA_DEBAJO), 3)

    def test_shared_edge_and_large_coordinates(self):
        p = surface.SuperficieNWD(Mesh([
            [(190000, 2850000, 2), (190010, 2850000, 2), (190000, 2850010, 2)],
            [(190010, 2850010, 2), (190000, 2850010, 2), (190010, 2850000, 2)]
        ])).cargar()
        self.assertAlmostEqual(p.altura(190005, 2850005, 20,
                                       surface.MAS_BAJA_DEBAJO), 2)

    def test_insertion_outside_bbox_controls_xy_and_z(self):
        t = transform.Transformacion(100, -50, 5)
        xyz = t.nwd_a_revit(2, 2, 20)
        e = NS(Id=NS(IntegerValue=7), UniqueId='7',
               Location=NS(Point=NS(X=xyz[0], Y=xyz[1], Z=xyz[2])))
        e.get_BoundingBox = lambda view: self.fail('Insercion no depende de la caja')
        with patch.object(re, 'describir', return_value=('c', 'f', 't')), \
                patch.object(re, 'comprobar_movilidad', return_value=(True, '')):
            result = va.analizar(None, NS(ActiveView=None), [e], provider(2, 6), t)[0]
        self.assertEqual(result.estado, va.BAJAR)
        self.assertAlmostEqual(result.delta_m, -18)
        self.assertAlmostEqual(result.z_superficie_m, 2)
        self.assertEqual(re.puntos_para_superficie(None, e, result.punto, re.INSERCION),
                         [xyz])

    def test_missing_insertion_never_falls_back_to_bbox(self):
        e = NS(Location=None)
        self.assertIsNone(re.resolver_punto(None, e, re.INSERCION))


class SummaryTextTests(unittest.TestCase):
    def test_approximate_surface_uses_the_normal_separator(self):
        summary = {
            'total': 1,
            'por_estado': {va.OK: 1},
            'bloqueados': 0,
            'aproximadas': 1,
            'pendientes_propuestas': 0,
        }

        text = va.texto_resumen(summary)

        self.assertIn(u' · 1 con superficie aproximada', text)
        self.assertNotIn(u'Â·', text)


class FakeDoc:
    def __init__(self):
        self.point = NS(X=1., Y=2., Z=units.metros_a_pies(20))
        self.mode = 'normal'
        self.calls = 0
        self.element = NS(Id=1, Location=NS(Point=self.point))

    def Regenerate(self):
        if self.mode == 'constraint':
            self.point.Z += units.metros_a_pies(1)


class SubTransaction:
    def __init__(self, doc):
        self.doc = doc

    def Start(self):
        self.saved = (self.doc.point.X, self.doc.point.Y, self.doc.point.Z)

    def Commit(self):
        return 'committed'

    def RollBack(self):
        self.doc.point.X, self.doc.point.Y, self.doc.point.Z = self.saved

    def Dispose(self):
        pass


def move(doc, element_id, vector):
    doc.calls += 1
    doc.point.Z += vector.Z


DB = NS(SubTransaction=SubTransaction,
        TransactionStatus=NS(Committed='committed'),
        XYZ=lambda x, y, z: NS(X=x, Y=y, Z=z),
        ElementTransformUtils=NS(MoveElement=move))


def result_for(doc):
    r = va.ResultadoCoordinacion(doc.element)
    r.punto = re.PuntoReferencia(1, 2, units.metros_a_pies(20), 'insercion')
    r.z_original_m, r.z_objetivo_m = 20, 2
    return r


class MovementTests(unittest.TestCase):
    def test_absolute_target_avoids_double_displacement(self):
        doc = FakeDoc()
        r = result_for(doc)
        doc.point.Z = units.metros_a_pies(10)  # cambio Z despues de analizar
        self.assertTrue(movement.aplicar_insercion(DB, doc, r)[0])
        self.assertAlmostEqual(units.pies_a_metros(doc.point.Z), 2)
        self.assertTrue(movement.aplicar_insercion(DB, doc, r)[0])
        self.assertEqual(doc.calls, 1)

    def test_changed_xy_requires_new_analysis_even_when_already_at_z(self):
        doc = FakeDoc()
        r = result_for(doc)
        doc.point.X += 1
        doc.point.Z = units.metros_a_pies(2)
        self.assertFalse(movement.aplicar_insercion(DB, doc, r)[0])
        self.assertEqual(doc.calls, 0)

    def test_revit_constraint_rolls_back_inaccurate_move(self):
        doc = FakeDoc()
        doc.mode = 'constraint'
        initial = doc.point.Z
        self.assertFalse(movement.aplicar_insercion(DB, doc, result_for(doc))[0])
        self.assertEqual(doc.point.Z, initial)


if __name__ == '__main__':
    unittest.main()
