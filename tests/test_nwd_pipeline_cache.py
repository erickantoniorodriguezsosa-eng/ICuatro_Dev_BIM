# -*- coding: utf-8 -*-
"""Cache y selección de puente; la API real se valida dentro de Revit."""
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch


ROOT = os.path.dirname(os.path.dirname(__file__))
LIB = os.path.join(ROOT, 'Prueba.extension', 'lib')
sys.path.insert(0, LIB)

# pipeline importa los adaptadores .NET aunque estas pruebas solo ejercen la
# orquestación. Los módulos mínimos permiten cargarlo en CPython sin pythonnet.
system = ModuleType('System')
system.Array = system.Buffer = system.Double = system.Int32 = object
system_io = ModuleType('System.IO')
system_io.BinaryReader = system_io.FileStream = object
system_io.FileMode = system_io.FileAccess = object
system_diagnostics = ModuleType('System.Diagnostics')
system_diagnostics.Process = system_diagnostics.ProcessStartInfo = object
sys.modules.setdefault('System', system)
sys.modules.setdefault('System.IO', system_io)
sys.modules.setdefault('System.Diagnostics', system_diagnostics)

from nwdcoord import pipeline


class FakeSurface:
    def __init__(self, mesh, registro=None):
        self.mesh = mesh
        self.registro = registro

    def cargar(self, progreso=None):
        return self


class PipelineCacheTests(unittest.TestCase):
    def test_same_filter_retries_and_never_reuses_unfiltered_as_filtered(self):
        box = (10.0, 20.0, 30.0, 40.0)
        origin = pipeline.Origen(pipeline.IFC, 'surface.ifc')
        filtered = pipeline._ruta_cache(origin.ruta, box, 'TRAMO')
        unfiltered = pipeline._ruta_cache(origin.ruta, box, None)
        valid_paths = set()
        calls = []

        def cache_valid(path, source, requested_box):
            return path in valid_paths, 'valid' if path in valid_paths else 'missing'

        def write_metadata(path, source, requested_box):
            valid_paths.add(path)

        def extract(source, destination, **arguments):
            calls.append((destination, arguments['filtro']))
            triangles = 0 if arguments['filtro'] else 1
            return {
                '_codigo': 0,
                '_cache': destination,
                'triangulos': triangles,
                'items': triangles,
                'bbox_m': box,
            }

        mesh = NS(num_triangulos=1, items=[object()])

        with patch.object(pipeline.geometry, 'es_cache_valido',
                          side_effect=cache_valid), \
                patch.object(pipeline.geometry, 'escribir_metadatos',
                             side_effect=write_metadata), \
                patch.object(pipeline.geometry, 'leer_cache',
                             return_value=mesh), \
                patch.object(pipeline.ifc_bridge, 'ruta_indice',
                             return_value='surface.index'), \
                patch.object(pipeline.ifc_bridge, 'extraer',
                             side_effect=extract), \
                patch.object(pipeline.superficie_mod, 'SuperficieNWD',
                             FakeSurface):
            first_surface, first_report = pipeline.preparar_superficie(
                None, box, origen=origin, filtro='TRAMO')
            second_surface, second_report = pipeline.preparar_superficie(
                None, box, origen=origin, filtro='TRAMO')

        self.assertIsNotNone(first_surface)
        self.assertIsNotNone(second_surface)
        self.assertEqual(calls, [
            (filtered, 'TRAMO'),
            (unfiltered, None),
            (filtered, 'TRAMO'),
            (unfiltered, None),
        ])
        self.assertNotIn(filtered, valid_paths)
        self.assertIn(unfiltered, valid_paths)
        self.assertEqual(first_report['cache'], unfiltered)
        self.assertEqual(second_report['cache'], unfiltered)
        self.assertTrue(first_report['filtro_replegado'])
        self.assertTrue(second_report['filtro_replegado'])

    def test_nwd_extraction_does_not_receive_the_ifc_index(self):
        origin = pipeline.Origen(pipeline.NWD, 'surface.nwd')
        bridge = Mock()

        with patch.object(pipeline, '_puente', return_value=bridge):
            pipeline._extraer_zona(
                origin, 'zone.nwcache', (1, 2, 3, 4), 'terrain', 50,
                60, None)

        bridge.extraer.assert_called_once_with(
            'surface.nwd', 'zone.nwcache', caja=(1, 2, 3, 4),
            filtro='terrain', maximo=50, tiempo_maximo_s=60,
            registro=None)


if __name__ == '__main__':
    unittest.main()
