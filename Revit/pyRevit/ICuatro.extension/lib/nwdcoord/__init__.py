# -*- coding: utf-8 -*-
"""Modulos de la extension NWD Coordinator.

Separacion de responsabilidades (seccion 32 del prompt maestro):

    units.py              conversiones pies <-> metros, centralizadas
    logging_utils.py      registro resumido
    coordination_model.py deteccion de vinculos NWD/NWC en Revit
    navisworks_bridge.py  puente con Navisworks via proceso externo

Viven en lib/ y no en modules/ dentro del pushbutton porque lib/ es la
carpeta que pyRevit anade al sys.path de la extension.
"""
