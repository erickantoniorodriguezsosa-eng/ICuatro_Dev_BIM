# -*- coding: utf-8 -*-
"""Contrato aislado de eventos y colores; la API real se prueba en Revit."""
import ast
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(__file__))
LIB = os.path.join(ROOT, 'Prueba.extension', 'lib')
SCRIPT = os.path.join(ROOT, 'Prueba.extension', 'ICuatro.tab', 'NWD.panel',
                      'VerticalCoordinator.pushbutton', 'script.py')
sys.path.insert(0, LIB)
from nwdcoord import visualization as vis


class ExternalEvent:
    @staticmethod
    def Create(handler):
        return NS(Raise=Mock(return_value='Accepted'), Dispose=Mock())


fake_ui = NS(IExternalEventHandler=object, ExternalEvent=ExternalEvent,
             ExternalEventRequest=NS(Accepted='Accepted', Pending='Pending'))
with patch.dict(sys.modules, {'Autodesk.Revit.UI': fake_ui}):
    # Cargar en un namespace separado para no contaminar otros tests.
    namespace = {}
    with open(os.path.join(LIB, 'nwdcoord', 'modeless.py'), encoding='utf-8') as f:
        exec(compile(f.read(), f.name, 'exec'), namespace)
AccionesRevit = namespace['AccionesRevit']


class EventTests(unittest.TestCase):
    def setUp(self):
        self.window = NS(_cerrada=False, contenido=NS(IsEnabled=True),
                         _documento_activo=Mock(return_value=True),
                         registro=NS(excepcion=Mock()), _estado=Mock())
        self.bridge = AccionesRevit(self.window)

    def test_request_does_not_run_until_revit_calls_execute(self):
        action = Mock()
        self.bridge.solicitar(action)
        action.assert_not_called()
        self.assertFalse(self.window.contenido.IsEnabled)
        self.bridge.Execute('app')
        action.assert_called_once_with()
        self.assertTrue(self.window.contenido.IsEnabled)

    def test_double_click_and_reentrant_callback_cannot_repeat_action(self):
        nested = Mock()
        first = Mock(side_effect=lambda: self.bridge.solicitar(nested))
        self.bridge.solicitar(first)
        self.bridge.solicitar(nested)
        self.bridge.Execute('app')
        self.bridge.Execute('app')
        first.assert_called_once_with()
        nested.assert_not_called()

    def test_another_active_document_blocks_action_but_allows_close(self):
        self.window._documento_activo.return_value = False
        action = Mock()
        self.bridge.solicitar(action)
        self.bridge.Execute('another document')
        action.assert_not_called()
        self.bridge.solicitar(action, cierre=True)
        self.bridge.Execute('another document')
        action.assert_called_once_with()

    def test_rejected_request_and_failed_action_release_controls(self):
        self.bridge.evento.Raise.return_value = 'Denied'
        with self.assertRaises(RuntimeError):
            self.bridge.solicitar(Mock())
        self.assertIsNone(self.bridge.pendiente)
        self.assertTrue(self.window.contenido.IsEnabled)
        self.bridge.evento.Raise.return_value = 'Accepted'
        self.bridge.solicitar(Mock(side_effect=ValueError('failure')))
        self.bridge.Execute('app')
        self.assertTrue(self.window.contenido.IsEnabled)
        self.assertFalse(self.bridge.ejecutando)
        self.window.registro.excepcion.assert_called_once()

    def test_close_disposes_event_after_execution(self):
        event = self.bridge.evento
        self.bridge.solicitar(lambda: setattr(self.window, '_cerrada', True), cierre=True)
        self.bridge.Execute('app')
        event.Dispose.assert_called_once_with()
        self.assertIsNone(self.bridge.evento)


class View:
    def __init__(self, id, overrides):
        self.Id = id
        self.overrides = dict(overrides)
        self.fail = False

    def GetElementOverrides(self, id):
        return self.overrides.get(id, 'default')

    def SetElementOverrides(self, id, value):
        if self.fail:
            raise RuntimeError('view unavailable')
        self.overrides[id] = value


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.first = View(100, {1: 'original red'})
        self.second = View(200, {1: 'original green'})
        self.elements = {100: self.first, 200: self.second, 1: object()}
        self.doc = NS(ActiveView=self.first, GetElement=self.elements.get)
        self.preview = vis.Previsualizacion(NS(), self.doc, None)
        self.preview._relleno_solido = lambda: None
        self.preview._ajustes = lambda *args: 'preview color'
        self.results = [NS(element_id=1, id_valor=1, estado=vis.analisis.OK)]

    def test_clean_after_switching_view_restores_only_original_view(self):
        self.preview.aplicar(self.results)
        self.doc.ActiveView = self.second
        self.assertEqual(self.preview.limpiar(), 1)
        self.assertEqual(self.first.overrides[1], 'original red')
        self.assertEqual(self.second.overrides[1], 'original green')

    def test_repeated_preview_preserves_originals_in_each_view(self):
        self.preview.aplicar(self.results)
        self.preview.aplicar(self.results)
        self.preview.aplicar(self.results, self.second)
        self.assertEqual(self.preview.limpiar(), 2)
        self.assertEqual(self.first.overrides[1], 'original red')
        self.assertEqual(self.second.overrides[1], 'original green')

    def test_failed_cleanup_keeps_original_for_retry(self):
        self.preview.aplicar(self.results)
        self.first.fail = True
        self.assertEqual(self.preview.limpiar(), 0)
        self.assertEqual(len(self.preview.tocados), 1)
        self.first.fail = False
        self.assertEqual(self.preview.limpiar(), 1)
        self.assertEqual(self.first.overrides[1], 'original red')

    def test_deleted_element_does_not_prevent_cleanup(self):
        self.preview.aplicar(self.results)
        del self.elements[1]
        self.assertEqual(self.preview.limpiar(), 0)
        self.assertEqual(self.preview.tocados, [])


class DocumentTests(unittest.TestCase):
    def setUp(self):
        with open(SCRIPT, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        # Ejecutar los metodos reales sin iniciar WPF ni importar Autodesk.
        names = {'_documento_activo', '_documento_cambiado', '_al_cerrar'}
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == 'Coordinador')
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        for method in methods:
            method.decorator_list = []
        self.doc = NS(IsValidObject=True)
        self.doc.Equals = lambda other: other is self.doc
        env = {'doc': self.doc}
        exec(compile(ast.Module(body=methods, type_ignores=[]), SCRIPT, 'exec'), env)
        self.methods = env
        self.window = NS(_cerrada=False, _permitir_cierre=False,
                         acciones=NS(ejecutando=False), Close=Mock(),
                         _estado=Mock(), _invalidar_resultados=Mock(),
                         on_cerrar=Mock(), elementos=[NS(IsValidObject=False)])

    def test_bound_document_must_be_active_and_valid(self):
        check = self.methods['_documento_activo']
        self.assertFalse(check(self.window, NS(ActiveUIDocument=None)))
        self.assertTrue(check(self.window, NS(ActiveUIDocument=NS(Document=self.doc))))
        self.doc.IsValidObject = False
        self.assertFalse(check(self.window, NS()))
        self.window.Close.assert_called_once()

    def test_external_edits_invalidate_but_own_transactions_do_not(self):
        changed = self.methods['_documento_cambiado']
        changed(self.window, None, NS(GetDocument=lambda: self.doc))
        self.window._invalidar_resultados.assert_called_once()
        self.assertEqual(self.window.elementos, [])
        self.window.acciones.ejecutando = True
        changed(self.window, None, NS(GetDocument=lambda: self.doc))
        self.window._invalidar_resultados.assert_called_once()

    def test_x_button_defers_close_to_external_event(self):
        args = NS(Cancel=False)
        self.methods['_al_cerrar'](self.window, None, args)
        self.assertTrue(args.Cancel)
        self.window.on_cerrar.assert_called_once_with(None, args)


if __name__ == '__main__':
    unittest.main()
