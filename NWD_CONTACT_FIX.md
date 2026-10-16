# Contacto por inserción — 2026.09.07.8

## Causa reproducida

El origen elegido era `SuperficiesExistentes.ifc`. La consulta debía incluir
también `CircuitoAccesos.ifc`, contenido dentro de
`DiseñoProyectado.nwd` en el NWD compuesto. Cambiar solamente la estrategia
de selección de Z no podía recuperar triángulos ausentes de la malla.

La prolongación de planos de taludes próximos tampoco representa una
intersección real bajo el punto de inserción.

## Evidencia del modelo real

Archivo: `437_Z5.PARA.ADOSAR.ILUMINACION.2022.nwd`.
Extracción Navisworks sin filtro nominal, caja XY en metros NWD:
`189966.54,2859445.26,190016.54,2859495.26`.

- 48 items, 19.256 triángulos; informe `truncado=0`.
- XY de prueba: `(189991.54, 2859470.262)`; Z de referencia `140.645 m`.
  Corresponde, con coordenadas redondeadas y transformadas, a la inserción
  del último registro disponible, no a una nueva lectura del Revit abierto.
- Intersecciones reales: `132.795131622 m` y `132.995173005 m`.
- Componente: `CircuitoAccesos.ifc`, `Acera Der. Pasarela Rarinco`,
  `ACERA HORMIGÓN T1`, y una cara coincidente de imprimación.
- Cota elegida: `132.795131622 m`; DZ de ese caso, offset cero:
  `-7.849868378 m`.

La separación entre las dos caras es aproximadamente 20 cm. Se elige la
inferior conforme al criterio solicitado; no se deduce el componente por
el color mostrado en una captura.

## Cambios

- El modo «La más baja por debajo del punto» extrae todos los componentes
  del NWD en la zona. La transformación NWD/Revit se resuelve y verifica
  antes de calcular la caja; no se reutilizan coordenadas locales del IFC.
- Se utiliza exclusivamente el punto de inserción. Su posición se incluye
  en la caja de extracción aunque esté fuera de la caja de la familia.
- En este modo solo se aceptan impactos reales; no se prolongan taludes.
- Se registran todas las cotas, la cota elegida y el componente intersectado.
- El movimiento calcula un destino absoluto, relee la inserción actual,
  rechaza cambios XY y verifica cada resultado a 1 mm tras regenerar.
  Un fallo revierte el cambio de ese elemento.
- Claude Opus 5 desarrolló el código del camino estricto y de la función
  de movimiento. Se revisó e integró con comprobación adicional de XY
  antes del movimiento y del estado de confirmación de Revit.

La reversión por elemento utiliza una SubTransaction dentro de la
transacción exterior, según la documentación de
[Autodesk sobre transacciones](https://help.autodesk.com/cloudhelp/2014/ENU/Revit/files/GUID-BECA30DB-23B4-4E71-BE24-DC4DD176E52D.htm).

## Verificación

` .venv\Scripts\python.exe -m unittest discover -s tests -v `

Nueve pruebas aisladas: selección inferior entre capas y talud, ausencia
de intersección, cotas superiores, borde compartido y coordenadas grandes,
inserción fuera de la caja, falta de inserción, destino absoluto,
cambio XY y reversión ante restricciones simuladas.

`tests/probe_nwd_cache.py` ejecuta el algoritmo de producción sobre una
extracción binaria real con un adaptador CPython de lectura.

Pendiente: comprobar el poste actual dentro de Revit 2022. La extracción
real y los ensayos aislados no equivalen a ejecutar MoveElement en esa sesión.
Cerrar y reabrir el comando; la ventana debe indicar `2026.09.07.8`.
Analizar con offset cero y verificar las cotas antes de aplicar.
