"""Historial: identidades copiadas, conteos y movimientos fallidos."""
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                             'Prueba.extension', 'lib'))
from nwdcoord import family_progress as fp, movement


def element(uid, stored_uid=None, valid=True):
    entity = NS(IsValid=lambda: valid, Get={str: lambda field: stored_uid})
    return NS(UniqueId=uid, GetEntity=lambda schema: entity,
              GetTypeId=lambda: NS(IntegerValue=1))


class Collector(list):
    def OfClass(self, kind):
        return self

    def WhereElementIsNotElementType(self):
        return self


class FamilyProgressTests(unittest.TestCase):
    def setUp(self):
        self.schema = NS(GetField=lambda name: name)

    def test_copy_and_missing_marker_are_not_counted_as_moved(self):
        with patch.dict(sys.modules, {'System': NS(String=str)}):
            self.assertTrue(fp._esta_movido(element('original', 'original'), self.schema))
            self.assertFalse(fp._esta_movido(element('copy', 'original'), self.schema))
            self.assertFalse(fp._esta_movido(element('none', valid=False), self.schema))

    def test_reopening_list_recounts_partial_progress_and_undo(self):
        items = Collector([element('moved', 'moved'), element('pending', valid=False)])
        DB = NS(FilteredElementCollector=lambda doc: items, FamilyInstance=object)
        doc = NS(GetElement=lambda id: NS(FamilyName='Poste'))
        with patch.dict(sys.modules, {'System': NS(String=str)}), \
                patch.object(fp, '_schema', return_value=self.schema):
            first = fp.listar(DB, doc, ['Poste', 'Sin ejemplares'])
            self.assertEqual((first[0].total, first[0].movidos), (2, 1))
            self.assertEqual(first[1].total, 0)
            # Simula que Revit retiro la entidad al deshacer la transaccion.
            items[0] = element('moved', valid=False)
            reopened = fp.listar(DB, doc, ['Poste'])
            self.assertEqual(reopened[0].movidos, 0)

    def test_failed_manual_move_does_not_write_marker(self):
        with patch.object(movement.revit_elements, 'comprobar_movilidad',
                          return_value=(True, '')), \
                patch.object(movement.EstrategiaMovimiento, 'aplicar',
                             return_value=(False, '', 'restriccion')), \
                patch.object(fp, 'registrar') as registrar:
            result = movement.aplicar_ajuste_manual(None, None,
                                                    [NS(Id=NS(IntegerValue=1))], 1)
        registrar.assert_not_called()
        self.assertEqual(result.fallidos, 1)

    def test_successful_manual_move_writes_marker(self):
        with patch.object(movement.revit_elements, 'comprobar_movilidad',
                          return_value=(True, '')), \
                patch.object(movement.EstrategiaMovimiento, 'aplicar',
                             return_value=(True, 'MoveElement', '')), \
                patch.object(fp, 'registrar', return_value=True) as registrar:
            e = NS(Id=NS(IntegerValue=1))
            result = movement.aplicar_ajuste_manual(None, None, [e], 1)
        registrar.assert_called_once_with(e, None)
        self.assertEqual(result.movidos, 1)


if __name__ == '__main__':
    unittest.main()
