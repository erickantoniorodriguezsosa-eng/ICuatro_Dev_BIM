# -*- coding: utf-8 -*-
"""Puente de la ventana no modal al contexto autorizado de Revit 2022."""
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent, ExternalEventRequest


class AccionesRevit(IExternalEventHandler):
    def __init__(self, ventana):
        self.ventana = ventana
        self.pendiente = None
        self.ejecutando = False
        self.evento = ExternalEvent.Create(self)

    def GetName(self):
        return "NWD Coordinator"

    def solicitar(self, accion, cierre=False):
        if self.pendiente is not None or self.ejecutando:
            return
        self.pendiente = (accion, cierre)
        self.ventana.contenido.IsEnabled = False
        try:
            estado = self.evento.Raise()
            if estado not in (ExternalEventRequest.Accepted, ExternalEventRequest.Pending):
                raise RuntimeError(u"Revit no acepto la accion; vuelve a intentarlo.")
        except Exception:
            self.pendiente = None
            self.ventana.contenido.IsEnabled = True
            raise

    def Execute(self, app):
        solicitud = self.pendiente
        self.pendiente = None
        if solicitud is None or self.ventana._cerrada:
            return
        self.ejecutando = True
        try:
            accion, cierre = solicitud
            if cierre or self.ventana._documento_activo(app):
                accion()
        except Exception as error:
            self.ventana.registro.excepcion(u"Evento externo", error)
            self.ventana._estado(u"Error: {0}".format(error))
        finally:
            self.ejecutando = False
            if self.ventana._cerrada:
                self.liberar()
            else:
                self.ventana.contenido.IsEnabled = True

    def liberar(self):
        self.pendiente = None
        if self.evento is not None:
            self.evento.Dispose()
            self.evento = None
