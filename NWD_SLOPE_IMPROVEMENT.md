# Pendiente opcional — motor 2026.09.07.10

La captura muestra un elemento alargado y una superficie con diferente
inclinación aparente. Una imagen no identifica su tipo de ubicación Revit,
sus parámetros, su cara de apoyo ni las cotas de la malla.

## Funcionamiento añadido

1. `Detectar pendiente / forma` está marcado inicialmente. Desmarcarlo antes
   de ANALIZAR conserva el análisis exclusivamente vertical.
2. ANALIZAR sigue siendo de solo lectura. Con referencia de inserción, examina
   el eje `LocationCurve` recto de familias y elementos MEP compatibles.
   Conserva la estrategia de superficie y el offset del análisis vertical.
3. Consulta extremos e interiores, mínimo nueve puntos y separación máxima
   de 0,25 m, hasta 100 m de longitud horizontal. Usa impactos de triángulos
   reales, sin extrapolación. Los extremos se incluyen en la zona de extracción.
4. Compara el perfil muestreado con la recta entre extremos usando la tolerancia
   configurada (mínimo 1 mm). Un perfil irregular, hueco o salto fuera del límite
   se informa en Observaciones, con texto completo al pasar el cursor.
5. Si hay una pendiente distinta compatible, APLICAR pregunta: solo vertical,
   adaptar los ejes compatibles o cancelar. Después confirma la modificación.
   También permite proponer pendiente cuando el centro ya estaba en tolerancia.
6. La adaptación cambia únicamente Z de los extremos y conserva sus XY.
   Esto puede cambiar la longitud 3D del eje. El offset es respecto al eje,
   **no** una distancia automática desde la cara inferior del sólido.
7. Relee eje y restricciones antes de aplicar, rechaza análisis obsoleto,
   regenera y comprueba ambos extremos a 1 mm. Un fallo revierte la operación
   del elemento; no intenta un movimiento alternativo silencioso.
8. Una adaptación correcta actualiza el indicador persistente de familia
   movilizada. Rechazar la pendiente mantiene disponible el ajuste vertical.

## Límites explícitos

- No rota familias de `LocationPoint` ni adivina su eje por la caja envolvente.
  No modifica parámetros desconocidos ni familias anidadas. Para la familia
  concreta de la captura hay que comprobar su tipo y sus parámetros en Revit.
- No deforma una sección rígida para seguir una silueta quebrada, no segmenta
  elementos ni genera automáticamente familias adaptativas.
- El muestreo longitudinal **no certifica** el contacto de toda la cara de
  apoyo, la pendiente transversal ni detalles/huecos menores que su separación.
- No altera anfitriones, grupos, geometría unida o conexiones MEP. Las vigas
  con uniones automáticas requieren deshabilitarlas previamente en Revit;
  la herramienta no lo hace por su cuenta.
- Se verifica el eje, no que toda la geometría anidada de una familia responda
  correctamente a él. La adaptación sigue sujeta a las restricciones de Revit.
- Los modelos NWD/IFC y el vínculo deben permanecer sin cambios entre analizar
  y aplicar. Si se modifica el origen o sus coordenadas, repetir el análisis.

## Verificación

28 pruebas aisladas aprobadas: 13 regresiones existentes y 15 nuevas de perfil,
unidades, offset, falta de intersección, irregularidad, límites, consentimiento,
centro ya nivelado, conexiones, análisis obsoleto, rollback e historial familiar.
Ejecutar: `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`.

Se contrastó `LocationCurve.Curve` con el XML instalado de Revit 2022
(`C:\Program Files\Autodesk\Revit 2022\RevitAPI.xml`). Autodesk también
documenta la edición mediante una nueva curva en su
[guía de movimiento de elementos](https://help.autodesk.com/cloudhelp/2024/ENU/Revit-API/files/Revit_API_Developers_Guide/Basic_Interaction_with_Revit_Elements/Editing_Elements/Revit_API_Revit_API_Developers_Guide_Basic_Interaction_with_Revit_Elements_Editing_Elements_Moving_Elements_html.html).

**Pendiente de validar dentro de Revit 2022:** cerrar y reabrir el coordinador,
comprobar versión `.10`, analizar un solo ejemplar, revisar Observaciones,
probar primero «Solo desplazamiento vertical» y luego la adaptación autorizada.
Verificar ambos extremos, geometría de la familia y Ctrl+Z. No se ha modificado
el modelo abierto ni se ha verificado en vivo la familia mostrada.
