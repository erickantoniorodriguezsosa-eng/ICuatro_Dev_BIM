# Familias desde capas CAD

**Estado al 02-10-2026:** el modo CAD 3D con suelos auxiliares está bloqueado tras un cierre de Revit durante su uso. La casilla está deshabilitada y el código rechaza su ejecución antes de crear elementos. La causa nativa exacta no está identificada. Sigue pendiente resolver la generación de barandillas de sistema inclinadas usando solamente CAD.

1. Abrir **Elementos BIM** y marcar los CAD vinculados o importados en la lista superior.
2. Entrar en **Capas · Líneas y curvas** y pulsar **Analizar capas**.
3. Marcar las capas y elegir familia y tipo cargados. El buscador filtra por familia o categoría.
4. Elegir un modo y pulsar **Asignar a capas marcadas**. Repetir para asignar otras familias a otras capas.
5. Pulsar **Generar familias desde capas**. Consultar la columna **Resultado / errores**, desplazando horizontalmente la tabla.

Los cambios de modo, separación o segmentación solo se aplican a una capa al volver a asignarla. Recargar familias o reanalizar las capas borra sus asignaciones.

## Modos y alcance

**Familias de sistema: Barandilla.** El selector incluye los `RailingType` definidos en el documento, aunque no haya barandillas colocadas; por ejemplo, `1100mm`. Pulsar **Recargar familias** para actualizar la lista. Elegir **Familia de sistema: Barandilla**, su tipo y **Familia lineal sobre cada tramo** antes de asignar. Se crea una barandilla por recorrido conectado. En bifurcaciones cada rama forma una barandilla independiente hasta el siguiente extremo o bifurcación; se conservan los circuitos cerrados. Cada tramo original se usa una sola vez, conectando únicamente extremos coincidentes, sin cortar ni extender curvas.

Se admiten líneas y arcos horizontales, y otras curvas horizontales mediante segmentación opcional. Los recorridos con pendiente se rechazan antes de abrir la transacción; el modo con suelos auxiliares está suspendido. La cota base se conserva mediante el nivel más cercano en elevación de proyecto y su desplazamiento base. Se revisan duplicados del mismo tipo, recorrido y cota. Esta incorporación corresponde a Barandilla; no agrega otras categorías de sistema ni las ofrece para inserciones de bloques.

- **Lineal:** una familia basada en línea por tramo. Las familias estructurales basadas en curva reciben la curva original; Revit puede rechazar geometrías que esa familia no admita.
- **Punto medio:** una familia puntual en la mitad de la longitud real de cada tramo, orientada según la tangente.
- **Cada distancia:** separación en metros sobre la longitud real. Reinicia en cada tramo e incluye su inicio, pero no su extremo final. No equivale a una distribución continua sobre toda una polilínea.
- **Segmentar curvas:** aproximación opcional por rectas usando la teselación de Revit. No tiene una tolerancia de aproximación configurable.

Se leen líneas, polilíneas, arcos, círculos, elipses y splines que Revit conserve como curvas. Las polilíneas se descomponen en segmentos; los círculos y elipses completos en dos mitades. La geometría que el importador CAD convierta en mallas no se recupera como curva. Se incluyen curvas dentro de bloques hasta diez niveles; la capa 0 hereda la capa de su contenedor cuando está disponible.

Se conserva la posición y cota del CAD. Las familias puntuales sin anfitrión se asocian al nivel más cercano. Las familias de detalle necesitan una vista 2D compatible y geometría en su plano; no se proyecta automáticamente. Familias con anfitrión, basadas en cara, adaptativas o de dos niveles requieren otro flujo.

La pestaña no modifica el CAD. Cada ejemplar se crea en una subtransacción: si falla su colocación, cota o giro, se revierte. La opción de duplicados compara puntos del mismo tipo y vista a 1 cm, o recorridos del mismo tipo y vista con redondeo a seis decimales en pies. El análisis elimina recorridos coincidentes en una misma capa y CAD. Mover o editar el CAD no actualiza automáticamente familias ya generadas.

## Incidente CAD 3D y bloqueo

El 02-10-2026 el usuario informó del cierre de Revit. El registro `journal.0293.txt`, en la carpeta Journals de Revit 2022, muestra:

- 15:56:34: activación de `chk_soporte_3d`.
- Confirmación de una operación con 51 ejemplares en una capa y hasta 51 suelos auxiliares.
- Últimas entradas: diez mensajes `FullUpdateGraphicCacheUpdater::updateAll() DBView3d id=94488: 126 elements need cache update`.
- Fin del registro a las 15:56:43, sin resultado final de la operación ni excepción que identifique la llamada responsable.

Esto sitúa el cierre durante el flujo de soportes auxiliares, pero no demuestra su causa nativa exacta. Las pruebas con API simulada no detectaron este fallo.

La interfaz, la preparación de recorridos y la entrada de creación 3D bloquean este modo. El código experimental se conserva para investigación; no se debe habilitar en un modelo de trabajo. Una solución futura requiere validación dentro de Revit 2022 en un proyecto desechable con el tipo real de barandilla. Los recorridos horizontales y las familias cargables conservan sus flujos existentes.

## Validación

Pasan 29 pruebas aisladas: geometría, capas, transformaciones, duplicados, reversión por ejemplar, eventos WPF, catálogo de tipos, cadenas y bifurcaciones. Se comprueba que el modo suspendido se rechace antes de cualquier transacción o llamada de creación, incluso con una asignación anterior. Las pruebas del algoritmo experimental usan una API simulada y habilitan el modo únicamente dentro de esas pruebas.

Estas pruebas no validan las sobrecargas de creación ni las familias reales en Revit 2022. El cierre observado impide considerar el modo CAD 3D listo para usar.

La comprobación funcional restante de los modos disponibles debe realizarse en una copia del RVT: capas con una polilínea horizontal, un arco y un bloque anidado con desplazamiento y giro; comparar posición, cota y orientación de familias puntuales y lineales. Para barandillas horizontales, comprobar recorridos abiertos, cerrados y bifurcaciones, además de repetir con **Evitar duplicados**. Una línea inclinada debe mostrar el aviso de modo suspendido sin crear elementos.