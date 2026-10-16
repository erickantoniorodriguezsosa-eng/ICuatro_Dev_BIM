# NWD Coordinator: ventana no modal

Versión: **2026.09.09.01**.

La ventana usa `Show()` y un motor persistente de pyRevit. Es posible
cambiar de vista, navegar por el espacio 3D y seleccionar elementos con
NWD Coordinator abierto. Después de seleccionar, el botón **Usar la
selección de Revit** actualiza el conjunto que se analizará.

Las acciones que acceden a Revit se solicitan mediante `ExternalEvent`
y se ejecutan en `IExternalEventHandler.Execute`. Durante una acción se
deshabilitan los controles del coordinador para evitar dobles ejecuciones.
El análisis, las extracciones y los desplazamientos siguen siendo operaciones
síncronas: la navegación está disponible entre operaciones.

La previsualización colorea la vista activa. Al pulsarla desde otra vista,
se restauran los colores de la anterior y se colorea la nueva. **Quitar
colores** restaura los overrides originales en la vista donde se aplicaron,
aunque el usuario esté mirando otra vista.

La ventana permanece asociada al documento donde se abrió. Las acciones
se rechazan si está activo otro documento; cerrar sigue disponible. Una
edición externa del documento, incluido deshacer, invalida el análisis para
evitar aplicar destinos antiguos. Cambiar el conjunto de elementos, el
modelo de coordinación o la superficie también descarta los resultados.

La X y el botón Cerrar usan la misma ruta de cierre y ofrecen retirar los
colores. Se liberan eventos y memoria al cerrar. Volver a pulsar el botón
de la cinta activa la ventana existente sin crear una segunda instancia.

## Validación realizada

- 12 pruebas aisladas: cola de eventos, rechazo y recuperación, doble clic,
  documento activo, invalidación, cierre y restauración de overrides entre
  vistas, con reintento tras fallos y elementos eliminados.
- Sintaxis Python y XML de la interfaz correctos.
- Comando: `.venv/Scripts/python.exe -m unittest discover -s tests -p test_nwd_modeless.py -v`.

La API y WPF deben validarse dentro de **Revit 2022**. En esta sesión Revit
estaba abierto, pero Computer Use no pudo conectarse a su servicio nativo
(`Computer Use native pipe is unavailable`, error del sistema 2). Las
pruebas aisladas no acreditan esa validación de integración.

## Comprobación en Revit 2022

1. Cerrar el coordinador anterior y ejecutar **Reload / Recargar** de pyRevit
   para actualizar también la configuración del motor persistente. Abrir
   NWD Coordinator y comprobar la versión `2026.09.09.01` en el título.
2. Con la ventana abierta, pasar de planta a 3D, hacer zoom y seleccionar
   elementos. Pulsar **Usar la selección de Revit** y **ANALIZAR**.
3. Pulsar **PREVISUALIZAR**. Navegar por la vista, cambiar a otra y pulsar
   de nuevo. Verificar que los colores aparecen en la nueva vista.
4. Desde una vista distinta, pulsar **Quitar colores**. Comprobar que se
   restauraron los overrides anteriores, también en elementos que ya tenían
   un color personalizado antes de la previsualización.
5. Probar **Escoger en pantalla**, Finalizar y Escape. La ventana debe volver
   sin impedir navegar. Probar selección por familia e ID.
6. Editar un elemento o deshacer una operación desde Revit. Debe solicitarse
   otro análisis. Activar otro documento e intentar analizar: la ventana
   debe indicar que se active el documento original.
7. Volver a pulsar el botón NWD Coordinator de la cinta: debe activarse la
   ventana existente. Probar cierre con la X y con Cerrar, con colores y sin
   ellos, y volver a abrir. Comprobar también el cierre del documento.
8. En un modelo de prueba, comprobar nivelación y ajuste manual, sus
   confirmaciones y Ctrl+Z. No se realizaron desplazamientos en el modelo
   abierto durante esta implementación.

## Referencias de implementación

- [Autodesk: External Events](https://help.autodesk.com/cloudhelp/2023/KOR/Revit-API/files/Revit_API_Developers_Guide/Advanced_Topics/Revit_API_Revit_API_Developers_Guide_Advanced_Topics_External_Events_html.html).
- [pyRevit: metadatos del motor persistente](https://docs.pyrevitlabs.io/reference/pyrevit/extensions/).
