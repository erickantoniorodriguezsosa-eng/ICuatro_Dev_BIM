# LAS Terrain App: desarrollo completo

Aplicación de escritorio para convertir nubes de puntos LAS/LAZ (topografía, puentes) en geometría y modelos IFC.
Este documento reúne todo el desarrollo: arquitectura, procedimiento, código fuente completo de cada archivo y
el registro de lo agregado en la última sesión.

- Carpeta: `ICuatro_Dev_BIM/LAS_Terrain_App/`
- Entorno: Python 3.11 (`.venv_las`), `vtk==9.3.1`; Open3D 0.18 no soporta Python 3.12 o superior.
- Caso de prueba: Puente Mafil N2, 89.5 millones de puntos, EPSG:32718.
- Servidor relacionado: `ICuatro_Dev_BIM/MCP_Open3D_Server/` (copia idéntica de `ifc_export.py`; mantener ambas iguales).
- Documento de proceso original: `DOCUMENTACION_PROCESO_NUBE_A_GEOMETRIA.md` (incluido en la parte 2).

## Contenido

1. Arquitectura y flujo de trabajo
2. Procedimiento y errores resueltos (documento de proceso)
3. Código fuente completo
4. Registro de la última sesión (vía, DXF, IFC UTM o local, caja de secciones, cotas y medidas)
5. Comandos del puente de control
6. Resultados de las pruebas y pendientes

---

# 1. Arquitectura y flujo de trabajo

| Módulo | Función |
|---|---|

| `main_gui.py` | Ventana principal PySide6 con visor PyVista, panel lateral, árbol de elementos, selección y puente de control |
| `core_processing.py` | `PointCloudProcessor`: carga LAS/LAZ, clasificación, superficies, terreno depurado, sólidos, vía y exportaciones |
| `reconstruct.py` | Estructura, eje, sección apilada, elementos, sólidos, estribos, barandas, muros de retorno, apoyos y vía |
| `autoclass.py` | Clasificación por forma (terreno, estructura, vegetación) con modelo entrenado |
| `train_autoclass.py` | Entrenamiento del modelo de clasificación |
| `ifc_export.py` | Exportación IFC2X3, IFC4 e IFC4X3 con IfcOpenShell |
| `ifc_dialog.py` | Ventana de exportación a IFC |
| `ifc_schema_info.py` | Restricciones de clase y tipo predefinido leídas de los esquemas IFC |
| `dxf_export.py` | Exportación de líneas a DXF (AutoCAD), UTM o local |
| `viewer_tools.py` | Caja de secciones, asas arrastrables, consulta, cotas y medidas |
| `bridge.py` | Servidor HTTP del puente de control de la GUI |
| `bridge_cli.py` | Cliente de línea de comandos del puente |
| `requirements.txt` | Dependencias |

Flujo de trabajo (cada paso es un botón de la GUI y un comando del puente):

1. `load`: leer el LAS y pasar a coordenadas locales (restar el origen, trabajar en float64).
2. `autoclass`: clasificar por forma terreno (2), vegetación (5) y estructura (6).
3. `structure`: agrupar la estructura y estimar el eje del puente.
4. `section`: sección transversal por cortes apilados.
5. `elements`: separar losa, vigas y barandas y generar los sólidos cerrados.
6. `abutments`, `railings`, `wings`, `bearings`: estribos, barandas por componentes, muros de retorno y apoyos.
7. `road`: separar la vía de acceso del terreno natural (clase 11).
8. `terrain`: terreno depurado (TIN) para IFC.
9. `export_ifc` y `export_dxf`: salidas IFC (UTM o local) y DXF (UTM o local).

Herramientas del visor disponibles en cualquier momento: caja de secciones (asas arrastrables), consulta de
coordenadas, cotas y medidas, selección y visibilidad de elementos por árbol.

---

# 2. Procedimiento y errores resueltos

### De nube de puntos a geometría IFC: proceso documentado

Fecha: 2026-10-09. Caso de prueba: Puente Mafil N2 (89.5 M de puntos, UTM 18S, EPSG:32718).
Código: `LAS_Terrain_App/` (`autoclass.py`, `train_autoclass.py`, `reconstruct.py`, `core_processing.py`, `ifc_export.py`, `main_gui.py`, `bridge.py`).

#### 1. Qué problema resuelve

Una nube de puntos de un puente mezcla terreno, vegetación y elementos constructivos. El flujo convierte esa nube en elementos IFC separados (losa, vigas, barandas, terreno), pasando por una geometría intermedia en líneas que se puede revisar antes de solidificar.

```
LAS ─► clasificar por forma ─► agrupar estructura ─► eje del puente
    ─► sección apilada ─► líneas 3D ─► separar elementos ─► sólidos ─► IFC
```

Cada etapa se valida con una medida, no solo a ojo (ver sección 4).

#### 2. Procedimiento paso a paso

##### Paso 0. Preparar y observar
- Python 3.11 (Open3D 0.18 no soporta 3.12+), `vtk==9.3.1` (pyvista 0.44 falla con vtk 9.7).
- Las coordenadas UTM (~1e6 a 1e7) pierden precisión en float32 y desestabilizan algoritmos. Se restan al mínimo de la nube (`origin`) y se trabaja en float64 local. Para volver a coordenadas reales se suma `origin`.
- La GUI expone un puente de control local (`bridge.py`, `bridge_cli.py`): permite cargar, ejecutar cada etapa y tomar capturas de la ventana abierta. Así se verifica visualmente cada resultado.

##### Paso 1. Clasificar terreno / estructura / vegetación por forma
Archivos: `autoclass.py`, `train_autoclass.py`.

**Por qué no bastan los filtros de altura.** El filtro por celdas (altura sobre el mínimo local) marcó la calzada del puente como terreno. Las reglas fijas por umbral dieron 76 % de acierto, con recall de 12 % en estructura.

**Método adoptado.**
1. Voxelizar a 0.15 m (centroides). 89.5 M puntos pasan a 2.19 M vóxeles.
2. Rasgos por vóxel (13): linealidad, planaridad, dispersión, |nz| de la normal y extensión principal, calculados con vecindad de 0.4 m y de 1.0 m (autovalores de la covarianza local); altura sobre el suelo (percentil 3 de z por celda de 1 m); diferencia de z con el máximo y el mínimo en una ventana de ~1.5 m.
3. Modelo: `HistGradientBoostingClassifier` (200 iteraciones, pesos de clase 2 / 3 / 1 para terreno / estructura / vegetación).
4. La etiqueta de cada vóxel se propaga a todos sus puntos. Salida en códigos ASPRS: 2 suelo, 6 estructura, 5 vegetación.

**Decisiones medidas.**
- Sin color: el RGB casi no aporta (95.5 % sin él). Los rasgos de forma sirven para cualquier nube.
- Pesos de clase: `balanced` dio recall 0.94 pero precisión 0.72; sin pesos, recall 0.85. Los pesos 2 / 3 / 1 equilibran (recall 0.90 / 0.92 / 0.97, precisión 0.82 / 0.85 / 0.98).
- Validación espacial: entrenar en bloques pares de 20 m y probar en impares (damero). Una división por mitad de x falla si el puente queda en un solo lado.

**Resultado:** 95.5 % de acierto. 26 s para 89.5 M de puntos; 44 s para entrenar.

**Límite.** Las etiquetas de entrenamiento son la clasificación v5, que tiene errores conocidos (el puente 2 y los muros laterales como vegetación). El modelo los hereda: el 95.5 % mide coincidencia con v5, no con la realidad.

##### Paso 2. Agrupar la estructura
Archivo: `reconstruct.py`, `structure_clusters`.
- Se toman los puntos de clase 6, se voxelizan y se unen los vóxeles a menos de 0.35 m (componentes conectados con `cKDTree.query_pairs`).
- Se descartan grupos de menos de 300 vóxeles: elimina falsos positivos sueltos sin tocar la clasificación.
- Resultado: el grupo 0 es el tablero (36.6 M de puntos); los otros 10 son defensas, luminarias y postes de señal.

##### Paso 3. Eje del puente
`reconstruct.py`, `_fit_axis`.
1. PCA en planta del grupo mayor da una dirección inicial. Está sesgada por los extremos esviados.
2. Se afina girando ±8° (paso 0.25°, luego 0.02°): el ángulo bueno maximiza la nitidez del histograma transversal (suma de cuadrados de las cuentas, bins de 5 cm), porque barandas, sardineles y vigas son líneas paralelas al eje.
3. El centro sale de los percentiles 0.5 y 99.5 de los extremos, no de la densidad de puntos.
- Resultado: azimut −84.33° (PCA daba −80.9°; corrección −3.4°), largo 45.2 m, ancho 12.4 m.

##### Paso 4. Sección transversal por apilado de cortes
`reconstruct.py`, `extract_section`. En el marco del eje: s (a lo largo), t (transversal), z.

1. **Rasante z(s).** Percentil 99 de z por tramo de 0.5 m en una franja angosta |t| < 0.5 m; polinomio de grado 3 con rechazo iterativo de atípicos. Se resta: `z_rel = z − rasante`.
2. **Extremos esviados.** Puntos del tope de la losa (a ±0.10 m del tope por t); por franja de t se toman percentil 0.5 y 99.5 de s y se ajusta una recta s = a + k·t en cada extremo. Pendientes medidas: 0.563 y 0.534.
3. **Tramo central.** Entre los extremos esviados más 0.5 m de margen; ahí la sección es constante.
4. **Raster.** Cortes de 0.25 m a lo largo del eje; celdas (t, z_rel) de 2 cm. Una celda cuenta una vez por corte (`np.unique` sobre la clave corte-celda).
5. **Umbral.** Se suaviza la frecuencia (gaussiano σ = 0.7 celdas) y se umbrala en 0.15 (la celda tiene punto en al menos el 15 % de los cortes).
6. **Contorno.** Dilatación 3, relleno de huecos, erosión 3, descarte de áreas < 0.05 m², `find_contours` y simplificación Douglas-Peucker a 2 cm.
7. **Líneas 3D.** Cada vértice del contorno se barre a lo largo del eje entre los extremos esviados, sumando la rasante (una estación cada 5 m). Además se dibujan los anillos de ambos extremos. Son las "líneas" de geometría que se revisan antes de solidificar.

**Resultado.** Un contorno cerrado de 129 vértices con losa y pendiente transversal, 4 vigas en I y 2 barandas. Mediana de 3.0 cm, p90 de 5.2 cm; 87 % de los puntos a menos de 5 cm del contorno.

##### Paso 5. Separar elementos
`reconstruct.py`, `separate_elements`. Reglas sobre la máscara (t, z_rel):
- **Tope de losa ZT(t):** recta robusta sobre la zona central |t| < 4.3 m (la losa tiene pendiente transversal).
- **Baranda:** material a más de 0.45 m sobre ZT(t).
- **Viga:** columnas cuyo punto más bajo está a más de 0.9 m bajo ZT. El intradós de la losa se interpola entre las columnas de solo losa; todo lo que cuelga bajo ese intradós es viga (ala superior baja, alma y bulbo).
- **Losa:** el resto, incluidos los sardineles bajos.
- Componentes conexas por región; los polígonos salen en orden antihorario.
- Resultado: 1 losa, 4 vigas, 2 barandas.

##### Paso 6. Sólidos
`reconstruct.py`, `build_solids`. Cada polígono se extruye entre los extremos esviados, sobre la rasante, con 9 estaciones. Las tapas se triangulan con `mapbox_earcut`. La orientación se comprueba con el signo del volumen.
- Los 7 sólidos son cerrados (watertight).
- Volúmenes: losa 171.7 m³, vigas 18.4 a 18.8 m³, barandas 6.1 y 6.6 m³. Total 258.8 m³.
- Las definiciones difieren de las de la metodología previa (la losa allí incluye alas superiores; las vigas se medían bajo la losa), por eso las cifras no son comparables uno a uno.

##### Paso 7. Estribos
`reconstruct.py`, `extract_abutments`.

**Hallazgo clave.** En el corte longitudinal de cada extremo (w = distancia desde la cara de extremo del tablero, z relativo a la rasante) el grupo de estructura solo contiene la cara que queda bajo las vigas y las barandas. El cuerpo del estribo quedó repartido entre terreno y vegetación (el modelo lo toma por terraplén). Por eso los estribos se miden con **todos los puntos de la zona**, sin depender de la clasificación.

**Método.**
1. Marco del extremo: `w = ±(s − s_extremo(t))`, `z_rel = z − rasante(s_extremo)`.
2. Banda de puntos bajo el fondo de las vigas (fondo medido en la sección, recta ZB(t)) y entre 3 m y 0.3 m hacia adentro.
3. Cara frontal: pico del histograma de w (2 cm), refinado con los puntos a ±5 cm del pico.
4. Se miden la extensión en t (percentiles 0.5 y 99.5), la cota del suelo en la cara (percentil 1) y la planaridad (% de puntos a ±3 cm del plano).
5. Sección (w, z): base plana en el suelo entre la cara frontal y el espaldar (w = 0), asiento a la cota del fondo de vigas ZB(t), espaldar hasta el tope del tablero ZT(t). Se extruye en t entre los extremos medidos.

**Resultado.**
- Cara frontal a w = −1.41 m (inicial) y −1.42 m (final). Ancho de asiento perpendicular 1.23 y 1.25 m; la metodología previa midió 1.29 y 1.25 m.
- Ancho en t: 15.3 m (inicial) y 14.2 m (final), mayor que el tablero (12.4 m).
- Cota del suelo en la cara: −3.82 y −4.06 m bajo la rasante.
- Planaridad a 3 cm: 22 % (inicial) y 71 % (final). El inicial tiene una cara menos plana (zona de transición con el terraplén).
- Volúmenes: 53.1 y 55.3 m³, sólidos cerrados.

**Supuestos (no medibles en la nube):** espesor del espaldar 0.30 m (la cara posterior no se ve), base plana, y sin muros de retorno.

##### Paso 8. Barandas por componentes (rieles y postes)
`reconstruct.py`, `extract_railings`.

**Por qué.** La baranda no es continua: son 3 rieles horizontales continuos más postes verticales cada ~1.5 m. En un corte transversal apilado los postes y los espacios entre ellos se mezclan, y el contorno salía dentado (barrera "irregular").

**Método.**
1. Región: material a más de 0.45 m sobre el tope de losa, dentro del rango t de cada baranda separada en el paso 5.
2. Rieles: el histograma de z (1 cm, suavizado) tiene bandas densas; cada intervalo por encima de 0.35 del máximo es un riel.
3. Postes: en la zona que no es riel, el histograma de s (2 cm) da los picos. Los picos se toman relativos al máximo local (ventana de 4 m) con `find_peaks` (altura relativa 0.5, separación mínima 0.6 m) y al menos 15 puntos. Un umbral global dejaba tramos sin postes.
4. Cada riel se mide con los cortes entre postes (a más de 0.20 m de cualquiera): rectángulo con percentiles 2 y 98 de t, extendido en s hasta el percentil 0.2 y 99.8. Cada poste se mide en una ventana propia (ancho s por el percentil 90, t por percentiles 1 y 99).
5. Sólidos prismáticos cerrados sobre la rasante. Clases IFC: `IfcRailing` (rieles) e `IfcMember` (postes).

**Resultado.**
- 3 rieles y 31 postes por lado; separación mediana de postes 1.5 m en ambos lados. Los 68 sólidos son cerrados.
- Riel superior izquierdo 9 a 10 cm (la metodología previa midió 8 a 9 cm); rieles medios e inferiores 11 a 13 cm de ancho (previo: 12 a 14 cm).
- Los rieles se prolongan unos 5 m fuera del tablero, como en la nube.
- Mejora frente al contorno apilado: de una baranda irregular de ~16 vértices a rieles y postes con medidas individuales.
- Errores en el camino: con umbral global solo salían 9 y 20 postes; con detección relativa salen 31 y 31.

##### Paso 9. Exportar IFC
`ifc_export.py`, `export_elements_ifc` (IFC2X3, IFC4, IFC4X3).
- IFC4 / IFC4X3: `IfcTriangulatedFaceSet` (cerrado), con `IfcProjectedCRS` e `IfcMapConversion` si hay EPSG.
- IFC2X3: `IfcFacetedBrep` para sólidos cerrados; exige `OwnerHistory` (persona, organización y aplicación).
- Clases: `IfcSlab`, `IfcBeam`, `IfcRailing` (rieles), `IfcMember` (postes), `IfcWall` (estribos); terreno como `IfcGeographicElement` TERRAIN (IFC4+) o `IfcBuildingElementProxy` (IFC2X3).
- Propiedades: conteo de vértices y triángulos y volumen (`Pset_Open3D`).
- Verificado: los 3 archivos se reabren con IfcOpenShell. Con el modelo completo son 75 elementos (1 losa, 4 vigas, 6 rieles, 62 postes, 2 estribos) y los 75 generan geometría en cada versión (3 162 triángulos).

##### Paso 10. Organización IFC según buildingSMART
`ifc_export.py`, `export_elements_ifc`; categorías en `core_processing.py` (`IFC_CATEGORY`).

**Jerarquía espacial.**
- **IFC4X3:** `IfcProject > IfcSite > IfcBridge (GIRDER) > IfcBridgePart`. Superestructura (`SUPERSTRUCTURE`) con el tablero (`DECK`: losa y vigas) y los "Elementos de superficie y seguridad" (`SURFACESTRUCTURE`: rieles y postes); Subestructura (`SUBSTRUCTURE`) con una parte `ABUTMENT` por estribo. Los elementos se contienen en la parte que les corresponde. El terreno va en `IfcSite`.
- **IFC4 e IFC2X3** (no tienen `IfcBridge`): `IfcSite > IfcBuilding > IfcElementAssembly` anidados con los mismos nombres; los elementos se agregan al ensamblaje.

**Clases y tipos predefinidos.**

| Elemento | Clase IFC | Tipo predefinido |
|---|---|---|
| Losa de tablero | `IfcSlab` | (sin definir) |
| Vigas | `IfcBeam` | `GIRDER_SEGMENT` (IFC4X3), `BEAM` (IFC4 e IFC2X3) |
| Rieles | `IfcRailing` | `GUARDRAIL` |
| Postes | `IfcMember` | `POST` |
| Estribos | `IfcWall` (dentro de `IfcBridgePart` ABUTMENT) | (sin definir) |
| Terreno | `IfcGeographicElement` (IFC2X3: `IfcBuildingElementProxy`) | `TERRAIN` |

**Propiedades estándar.** Pset común por clase (`Pset_SlabCommon`, `Pset_BeamCommon`, `Pset_RailingCommon`, `Pset_MemberCommon`, `Pset_WallCommon`) con `IsExternal`, `LoadBearing` y `Reference`; cantidades base (`Qto_*BaseQuantities`, `GrossVolume`) en IFC4+. Un `Pset_Open3D` propio guarda vértices, triángulos, volumen y los supuestos de modelado.

**Validación.** `ifcopenshell.validate` da **0 incidencias** en IFC2X3, IFC4 e IFC4X3 con el modelo completo de 75 elementos.

**Alcance.** Se sigue la organización y las clases de IFC de buildingSMART. No hay `IfcClassification` (Uniclass u OmniClass), materiales ni tipos (`IfcTypeObject`); sin esas piezas no es una certificación del IFC.

##### Paso 11. Vistas, ejes y selección de elementos
`main_gui.py` (panel "Elementos y vista").
- **Nube original:** capa propia (`cloud_raw`) con los puntos del LAS tal como se cargaron, con su color RGB si el archivo lo trae (si no, por elevación). No cambia al clasificar; al clasificar se oculta automáticamente (son los mismos puntos) y se puede volver a mostrar. Casilla "Mostrar nube original".
- **Nube clasificada y grupos:** capa aparte (`cloud`), casilla "Mostrar nube clasificada".
- **Ejes:** "Ejes con coordenadas reales" dibuja una caja con Este, Norte y cota reales (local + origen), y "Triedro de orientación" muestra u oculta el triedro del rincón.
- **Árbol de elementos** con la misma organización que el IFC (Superestructura, Tablero, Elementos de superficie y seguridad, Subestructura, Terreno). Cada elemento y cada grupo tiene casilla para mostrarlo u ocultarlo (tri-estado en los grupos). "Referencias (no IFC)" agrupa las líneas de sección y el eje.
- **Selección con clic** en el modelo: clic selecciona un elemento, Ctrl o Mayús + clic suma o quita, clic en el vacío limpia. La selección se resalta en magenta y se refleja en el árbol; también se puede elegir desde el árbol.
- **Botones:** Ocultar seleccionados, Aislar seleccionados, Mostrar todo, Quitar selección.
- Un arrastre (rotar la cámara) no cuenta como clic: se ignora si el cursor se movió más de 3 px entre pulsar y soltar.

##### Paso 12. Ventana de exportación a IFC
`ifc_dialog.py`, `ifc_schema_info.py`. El botón "Exportar IFC" abre una ventana con:
- **Versiones** (IFC2X3, IFC4, IFC4X3), una casilla por versión; se genera un archivo por versión con sufijo.
- **Tabla de categorías** (losa, vigas, barandas, rieles, postes, estribos, terreno): casilla de inclusión, cantidad, **clase IFC** (lista de clases permitidas por categoría; por ejemplo, vigas: `IfcBeam`, `IfcMember` o `IfcBuildingElementProxy`), **tipo predefinido** (los valores reales del esquema de cada versión, marcando "solo IFC4X3" cuando no valen en todas; "predeterminado por versión" y "sin tipo" son opciones aparte), contenedor en la jerarquía y **restricciones**.
- **Restricciones** calculadas con los esquemas de IfcOpenShell, no con una lista a mano. Ejemplos: `IfcBridge` solo existe en IFC4X3; `IfcGeographicElement` no existe en IFC2X3 (se usa `IfcBuildingElementProxy`); `IfcBeam` no tiene tipos predefinidos en IFC2X3; `IfcMember` POST no existe en IFC2X3; el valor `GIRDER_SEGMENT` solo vale en IFC4X3. Un tipo no válido en una versión se omite en ella y la ventana lo avisa.
- **Alcance:** todos, solo los visibles en el visor, o solo los seleccionados.
- **Estructura y propiedades:** jerarquía de puente (o todo colgando de `IfcSite`), Pset comunes buildingSMART, cantidades base (Qto, solo IFC4 en adelante) y `Pset_Open3D` con supuestos.
- **Georreferencia:** EPSG y `IfcMapConversion` (solo IFC4 e IFC4X3; el EPSG se toma del LAS si lo trae).
- **Validar con IfcOpenShell al terminar:** informa las incidencias de cada archivo en el registro.
- Si una clase no existe en el esquema elegido, el exportador usa `IfcBuildingElementProxy`; si el atributo `PredefinedType` es obligatorio y no hay tipo (p. ej. `IfcFooting` en IFC2X3), completa `NOTDEFINED`.
- Verificado con 4 configuraciones distintas (todas las categorías; solo visibles con vigas como `IfcMember` y estribos como `IfcFooting`; sin jerarquía, sin Qto y sin Pset_Open3D; losa como proxy y sin vigas): 0 incidencias del validador en las tres versiones.

##### Paso 13. Muros de retorno, apoyos, filtro de árboles y terreno depurado
**Muros de retorno** (`reconstruct.extract_wing_walls`, botón "Reconstruir muros de retorno"). Se miden con todos los puntos tras el espaldar del estribo. La nube solo ve la cara exterior (vertical, a t = ±ancho/2); la interior queda bajo el relleno.
1. Cara: pico del histograma de t (1 cm) de los puntos a menos de 0.35 m sobre el tablero, que excluye barandas y postes.
2. Perfil: columnas de 5 cm en w; cota alta (p98) y baja (p2, el terreno que corta el muro). El muro es la corrida continua desde el espaldar de columnas con al menos 30 puntos y 0.20 m de alto.
3. Polígono (w, z) con mediana de 0.4 m y Douglas-Peucker de 3 cm, extruido 0.30 m hacia el eje (supuesto).
4. Resultado en Puente Mafil: 4 muros de 4.6 a 5.25 m de largo, 1.0 a 1.6 m3 cada uno, todos cerrados. Clase `IfcWall` (RETAININGWALL en IFC4X3, SOLIDWALL en IFC4), dentro de la parte ABUTMENT de su estribo.

**Apoyos** (`extract_bearings`, botón "Ubicar apoyos bajo las vigas"). Uno por viga y estribo. Medido: posición transversal (centro del ala inferior de cada viga), ancho del ala (0.62 m; el apoyo mide 0.50 m) y espesor. El espesor es la diferencia entre el fondo de viga ZB(t) y el asiento medido (moda del histograma, 1 cm): 6.5 cm en ambos estribos. Supuesto: posición longitudinal en la mitad del asiento y largo 0.35 m; el tipo de apoyo no se identifica en la nube (`NOTDEFINED`). El asiento del estribo ahora se modela por debajo de ZB(t) para que el apoyo ocupe ese espacio sin solaparse. Clase `IfcBearing` (no existe en IFC2X3: sale como proxy).

**Filtro de árboles en postes** (`extract_railings`, `tree_filter`). Antes de buscar postes se excluyen los troncos: material compacto (50 puntos por 0.1 m) que sigue sobre el riel superior en un tramo de hasta 1 m. Cada candidato se descarta si la continuidad en z (celdas de 5 cm) es menor que 0.5, si la dispersión en s es mayor que 0.12 m, si el ancho en t supera 0.6 m, si el material sigue sobre el riel o si está fuera del tramo con rieles. Prueba: los 62 postes reales se conservan; un tronco sintético se descarta; un poste a menos de 0.4 m de un tronco también se pierde.

**Terreno** (`build_terrain`, botón "Terreno depurado para IFC", comando `terrain`). Metodología:
1. Fuente: solo la clase terreno. Estructura y vegetación quedan fuera.
2. Entorno: solo a menos de 40 m del tablero (`roi_margin`). Lejos de la obra la clasificación por forma deja copas y ramas dentro del terreno y la malla se llena de picos.
3. Diezmado a un punto por celda de 0.5 m (promedio).
4. Depuración de outliers: rejilla de 1 m, se descarta lo que supera en 2 m el mínimo de su vecindario 3x3.
5. TIN 2.5D de Delaunay; se descartan los triángulos con arista mayor de 4 m (vacíos, no se cubren).
6. Recorte bajo estructuras: se quitan los triángulos cuyo centro cae en la huella de losa, estribos o muros y que están por encima de su cota inferior; el terreno bajo el tablero se conserva.
7. Opcional: `target_triangles` para simplificar.
8. Verificación contra 300 000 puntos de terreno medidos (interpolación baricéntrica): mediana, rms, p95 y cobertura.
9. No se suaviza: el suavizado aleja la malla de los puntos medidos (solo visual).
10. IFC: `IfcGeographicElement` TERRAIN (proxy en IFC2X3) con malla triangulada.
Resultado en Puente Mafil: 30 276 vértices, 59 860 triángulos, 11 837 m2; error vertical mediana 1.3 cm, p95 16 cm, cobertura 97.5 %. El rms (25 cm) lo domina un resto de vegetación clasificada como terreno.

#### 3. Errores encontrados y cómo se resolvieron

| Síntoma | Causa | Solución |
|---|---|---|
| La GUI se cerraba al terminar una tarea | Señales del Worker conectadas con `lambda`: corrían en el hilo del Worker y VTK dibujaba fuera del hilo de UI | Conectar a slots del `MainWindow` (conexión en cola) |
| `pyvista` fallaba al importar | vtk 9.7 incompatible con pyvista 0.44 | Fijar `vtk==9.3.1` |
| TIN con 8 152 de 20 000 puntos | Qhull pierde puntos con XY en UTM | Centrar XY antes de `Delaunay` |
| NaN en el suavizado Laplaciano/Taubin | Vértices sin triángulos y coordenadas grandes | Centrar y suavizar solo los vértices usados |
| Proceso con 19.7 GB de RAM | `query_ball_point` por punto con radio de 1.5 m | Máximo y mínimo locales con rejilla y `maximum_filter` |
| Validación espacial imposible | Dividir por mediana de x deja el puente en un lado | Damero de bloques de 20 m |
| Eje desviado 3° | PCA sesgado por extremos esviados | Afinar por nitidez del histograma transversal |
| Rasante con residuo de 68 cm | Percentil 90 en franja ancha mezcla losa, vigas y pendiente | Percentil 99 en franja de ±0.5 m (residuo ~1 cm) |
| Contorno de losa delgado o partido | Umbral de ocupación mal elegido | Medir las densidades reales (tope 0.06–0.12, intradós 0.07–0.09, ruido 0.009) y fijar 0.15 |
| IFC sin clase de geometría en IFC2X3 | Falta `OwnerHistory` | Crear persona, organización y aplicación |
| Estribos ausentes del grupo de estructura | El cuerpo del estribo se clasifica como terreno o vegetación (terraplén) | Medirlos con todos los puntos de la zona, no con la clasificación |
| Baranda dentada en el corte apilado | Postes y espacios entre postes mezclados con los rieles | Rieles medidos entre postes y postes por ventana propia |
| 529 errores de `OwnerHistory` en IFC4 e IFC4X3 | El parche de usuario y aplicación de IFC2X3 queda activo a nivel de módulo y contamina las exportaciones siguientes | Restaurar las funciones originales al inicio de cada exportación |
| `IfcBridgePart.UsageType` y `IfcElementAssembly.PredefinedType` reportados como obligatorios | Atributos requeridos en IFC4X3 y en IFC2X3 respectivamente | `UsageType` LONGITUDINAL o VERTICAL y `PredefinedType` NOTDEFINED |
| El clic de selección no llegaba al soltar el botón | Al pulsar, el estilo de interacción toma el foco del ratón y VTK bloquea los observadores no pasivos | Observadores marcados como pasivos (`SetPassiveObserver`) |
| `IfcFooting` en IFC2X3 con 2 incidencias del validador | `PredefinedType` obligatorio en esa clase y versión | Completar `NOTDEFINED` si el atributo es obligatorio y no hay tipo |
| La nube original desaparecía al clasificar | La nube clasificada reemplazaba al mismo actor | Capas separadas: `cloud_raw` (original) y `cloud` (clasificada) |
| Terreno con picos verticales de decenas de metros | Copas y ramas clasificadas como terreno lejos de la obra, trianguladas con Delaunay | Entorno de 40 m, depuración de outliers y aristas máximas |
| `matplotlib` "Triangulation is invalid" al verificar el terreno | Malla recortada con huecos | Interpolación baricéntrica propia |
| Volumen del muro 1/3 del esperado | Orientación de tapas inconsistente que Open3D acepta como cerrada | Elegir la orientación cuyo volumen iguala área x espesor |
| Solo 9 y 20 postes detectados | Umbral global sobre el histograma de s | Picos relativos al máximo local (ventana de 4 m): 31 y 31 |

#### 4. Cómo validar cada etapa

| Etapa | Medida |
|---|---|
| Clasificación | Acierto, recall y precisión por clase en bloques espaciales no vistos |
| Grupos | Tamaño de grupos y captura con cada grupo en un color |
| Eje | Paralelismo con los bordes en la vista en planta; ángulo vs. PCA |
| Rasante | Residuo del ajuste por tramo (objetivo: centímetros o menos) |
| Sección | Distancia de los puntos al contorno (mediana, p90, % a menos de 5 cm) y la imagen apilada con el contorno encima |
| Sólidos | `is_watertight`, volumen positivo |
| IFC | Reabrir con IfcOpenShell y generar la geometría de cada elemento |

#### 5. Cómo ejecutarlo

Desde la GUI (`LAS_Terrain_App\.venv_las\Scripts\python.exe main_gui.py`) en este orden: Cargar archivo, Clasificar (forma), Agrupar estructura y estimar eje, Extraer sección y líneas, Separar elementos y generar sólidos, Detallar barandas (rieles y postes), Reconstruir estribos, Exportar IFC.

Por el puente (con la GUI abierta): `python bridge_cli.py <comando> [json]`, con `load`, `autoclass`, `structure`, `section`, `elements`, `railings`, `abutments`, `export_ifc`, `tree`, `visibility`, `select`, `axes`, `view {raw, cloud}`, `screenshot`, `view`, `status`.

Para reentrenar con otra nube clasificada: `python train_autoclass.py <clasificado.las> [--voxel 0.15]`.

#### 6. Limitaciones y pendientes

- El modelo hereda los errores de la v5. Corregir esas etiquetas (puente 2, muros laterales, estribos) y reentrenar mejora el resultado.
- La sección se supone constante en todo el vano; no captura diafragmas ni cambios de sección. La mejora es calcular secciones por tramo.
- Barandas: los postes son prismas rectangulares (la planta real es un perfil en I) y faltan placas base y anclajes; los rieles se modelan macizos (los tubos reales son huecos).
- Estribos: espesor del espaldar supuesto (0.30 m), base plana, sin muros de retorno ni muros laterales; el estribo inicial tiene solo 22 % de planaridad a 3 cm.
- Faltan diafragmas, apoyos, luminarias y vegetación como elementos.
- Los extremos de las vigas se toman iguales a los de la losa; la metodología previa midió diferencias de hasta 0.9 m en el residuo de ajuste.
- La sección y los sólidos solo se probaron en un puente; para otra tipología hay que revisar los umbrales de `separate_elements`.
- Faltan `IfcClassification`, materiales y tipos (`IfcTypeObject`) en el IFC; la estructura de puente solo existe en IFC4X3 (IFC4 e IFC2X3 usan ensamblajes).
- La captura de ventana de Qt no incluye el visor 3D (OpenGL): para verificar el modelo se usa la captura del visor.

---

# 3. Código fuente completo

## 3.1 `main_gui.py`

Ventana principal PySide6 con visor PyVista, panel lateral, árbol de elementos, selección y puente de control. 1197 líneas.

````python
"""
main_gui.py
GUI PySide6 + visor PyVista (pyvistaqt). Se eligió PyVista sobre la ventana de
Open3D porque QtInteractor se incrusta como widget Qt nativo; el visor de Open3D
no se puede incrustar de forma estable en PySide6. Open3D se usa solo para cálculo.
"""
import os
import sys
import traceback

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget,
                               QDoubleSpinBox, QLabel, QScrollArea, QTreeWidget, QTreeWidgetItem,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from bridge import Bridge
from ifc_dialog import IfcExportDialog
from viewer_tools import DxfDialog, ViewerTools
from core_processing import ASPRS_GROUND, IFC_CATEGORY, PointCloudProcessor

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT_PATH = os.path.join(HERE, ".bridge_shot.png")


def _jsonable(o):
    """Convierte numpy a tipos JSON para responder por el puente."""
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    return o

COLOR_GROUND = (0.55, 0.40, 0.20)
COLOR_NON_GROUND = (0.20, 0.65, 0.25)
# Paleta por código ASPRS (clasificación automática): 2 suelo, 6 estructura, 5 vegetación
CLASS_COLORS = {1: (0.55, 0.55, 0.55), 2: COLOR_GROUND, 5: COLOR_NON_GROUND, 6: (0.90, 0.30, 0.20), 11: (0.45, 0.45, 0.52)}


class _Signals(QObject):
    done = Signal(object, object)   # (worker, resultado)
    error = Signal(object, str)     # (worker, traceback)


class Worker(QRunnable):
    """Ejecuta una función pesada fuera del hilo de UI (la UI no se congela)."""
    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.on_done = None
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self, self.fn(*self.args, **self.kw))
        except Exception:
            self.signals.error.emit(self, traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LAS Terrain - v0.1")
        self.resize(1400, 850)
        self.proc = PointCloudProcessor()
        self.pool = QThreadPool.globalInstance()
        self._sample = None
        self._workers = set()  # evita que Python libere el Worker (y sus señales) en vuelo
        self._path = None
        self._pending = None   # petición del puente a la espera de que termine una tarea
        self._only = None      # códigos de clase visibles en la nube (None = todos)
        self._classified_view = False
        self._mode = "class"
        self._build_ui()
        self._set_enabled(classify=False, surface=False, smooth=False)
        self.bridge = Bridge()
        self.bridge.request.connect(self._on_bridge_request)  # hilo HTTP -> hilo UI (en cola)
        self._say(f"Puente de control en 127.0.0.1:{self.bridge.port}")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.side = QWidget()
        self.side.setFixedWidth(310)
        lay = QVBoxLayout(self.side)

        self.btn_load = QPushButton("Cargar archivo .LAS")
        self.btn_load.clicked.connect(self.on_load)
        lay.addWidget(self.btn_load)

        g1 = QGroupBox("Clasificación de terreno")
        f1 = QFormLayout(g1)
        self.cb_cls = QComboBox()
        self.cb_cls.addItems(["Celdas (altura mínima)", "RANSAC (plano único)"])
        # EXTENSIÓN: añade "CSF" aquí y en core_processing.classify_ground
        self.sp_cell = self._spin(2.0, 0.1, 100, 0.5)
        self.sp_tol = self._spin(0.5, 0.01, 20, 0.1)
        f1.addRow("Método", self.cb_cls)
        f1.addRow("Celda (m)", self.sp_cell)
        f1.addRow("Tol. altura / dist. (m)", self.sp_tol)
        self.btn_cls = QPushButton("Clasificar Terreno (Básico)")
        self.btn_cls.clicked.connect(self.on_classify)
        f1.addRow(self.btn_cls)
        lay.addWidget(g1)

        g0 = QGroupBox("Clasificación automática (forma)")
        f0 = QFormLayout(g0)
        self.btn_auto = QPushButton("Clasificar: terreno / estructura / vegetación")
        self.btn_auto.setToolTip("Modelo entrenado con rasgos geométricos locales (models/autoclass.joblib)")
        self.btn_auto.clicked.connect(self.on_auto)
        f0.addRow(self.btn_auto)
        lay.addWidget(g0)

        g1b = QGroupBox("Reconstrucción")
        f1b = QFormLayout(g1b)
        self.btn_struct = QPushButton("Agrupar estructura y estimar eje")
        self.btn_struct.clicked.connect(self.on_structure)
        f1b.addRow(self.btn_struct)
        self.btn_section = QPushButton("Extraer sección y líneas del tablero")
        self.btn_section.clicked.connect(self.on_section)
        f1b.addRow(self.btn_section)
        self.btn_elems = QPushButton("Separar elementos y generar sólidos")
        self.btn_elems.clicked.connect(self.on_elements)
        f1b.addRow(self.btn_elems)
        self.btn_rail = QPushButton("Detallar barandas (rieles y postes)")
        self.btn_rail.clicked.connect(self.on_railings)
        f1b.addRow(self.btn_rail)
        self.btn_abut = QPushButton("Reconstruir estribos")
        self.btn_abut.clicked.connect(self.on_abutments)
        f1b.addRow(self.btn_abut)
        self.btn_wing = QPushButton("Reconstruir muros de retorno")
        self.btn_wing.clicked.connect(self.on_wings)
        f1b.addRow(self.btn_wing)
        self.btn_bear = QPushButton("Ubicar apoyos bajo las vigas")
        self.btn_bear.clicked.connect(self.on_bearings)
        f1b.addRow(self.btn_bear)
        self.btn_road = QPushButton("Separar vía de acceso del terreno")
        self.btn_road.setToolTip("Plano con peralte por tramo junto al tablero; reclasifica a clase 11 (camino)")
        self.btn_road.clicked.connect(self.on_road)
        f1b.addRow(self.btn_road)
        self.btn_dxf = QPushButton("Exportar líneas a AutoCAD (DXF)")
        self.btn_dxf.setToolTip("Eje, sección, aristas de los elementos, curvas de nivel, cotas y medidas; en UTM o local")
        self.btn_dxf.clicked.connect(self.on_export_dxf)
        f1b.addRow(self.btn_dxf)
        self.btn_ifc = QPushButton("Exportar IFC (IFC2X3, IFC4, IFC4X3)")
        self.btn_ifc.clicked.connect(self.on_export_ifc)
        f1b.addRow(self.btn_ifc)
        lay.addWidget(g1b)

        g2 = QGroupBox("Superficie")
        f2 = QFormLayout(g2)
        self.cb_surf = QComboBox()
        self.cb_surf.addItems(["Delaunay 2.5D (TIN)", "Poisson"])
        self.sp_vox = self._spin(0.0, 0.0, 50, 0.1)
        self.sp_edge = self._spin(0.0, 0.0, 1000, 1.0)
        f2.addRow("Método", self.cb_surf)
        f2.addRow("Diezmado voxel (m, 0=off)", self.sp_vox)
        f2.addRow("Arista máx. (m, 0=off)", self.sp_edge)
        self.btn_surf = QPushButton("Generar Superficie (Triangulación)")
        self.btn_surf.clicked.connect(self.on_surface)
        f2.addRow(self.btn_surf)
        self.btn_terr = QPushButton("Terreno depurado para IFC")
        self.btn_terr.setToolTip("Diezmado 0.5 m, aristas <= 4 m, recorte bajo estructuras y verificación "
                                 "contra los puntos medidos")
        self.btn_terr.clicked.connect(self.on_terrain)
        f2.addRow(self.btn_terr)
        lay.addWidget(g2)

        g3 = QGroupBox("Suavizado de malla")
        f3 = QFormLayout(g3)
        self.cb_smooth = QComboBox()
        self.cb_smooth.addItems(["Taubin (sin encoger)", "Laplaciano", "Promedio simple"])
        self.sp_iter = QSpinBox()
        self.sp_iter.setRange(1, 200)
        self.sp_iter.setValue(10)
        self.chk_keepxy = QCheckBox("Conservar X,Y (solo suaviza Z)")
        self.chk_keepxy.setChecked(True)
        f3.addRow("Filtro", self.cb_smooth)
        f3.addRow("Iteraciones", self.sp_iter)
        f3.addRow(self.chk_keepxy)
        self.btn_smooth = QPushButton("Suavizar malla")
        self.btn_smooth.clicked.connect(self.on_smooth)
        f3.addRow(self.btn_smooth)
        lay.addWidget(g3)

        self.chk_raw = QCheckBox("Mostrar nube original")
        self.chk_raw.setToolTip("Puntos del LAS tal como se cargaron (con su color RGB si lo traen, "
                                "o por elevación). No cambia al clasificar.")
        self.chk_raw.setChecked(True)
        self.chk_raw.toggled.connect(lambda v: self._toggle("cloud_raw", v))
        self.chk_cloud = QCheckBox("Mostrar nube clasificada")
        self.chk_cloud.setChecked(True)
        self.chk_cloud.toggled.connect(lambda v: self._toggle("cloud", v))
        self.chk_mesh = QCheckBox("Mostrar malla")
        self.chk_mesh.setChecked(True)
        self.chk_mesh.toggled.connect(lambda v: self._toggle("mesh", v))
        row = QHBoxLayout()
        row.addWidget(self.chk_cloud)
        row.addWidget(self.chk_mesh)
        lay.addWidget(self.chk_raw)
        lay.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        lay.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)

        self.plotter = QtInteractor(self)
        self.plotter.set_background("#1e1e24")
        self.plotter.add_axes()

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()          # el panel de controles crece: se desplaza si no cabe
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.side)
        scroll.setFixedWidth(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        h.addWidget(scroll)
        h.addWidget(self.plotter.interactor, 1)
        self.setCentralWidget(central)
        self._build_elements_dock()

    @staticmethod
    def _spin(val, lo, hi, step):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(2)
        s.setValue(val)
        return s

    def _set_enabled(self, classify=None, surface=None, smooth=None):
        if classify is not None:
            self.btn_cls.setEnabled(classify)
        if surface is not None:
            self.btn_surf.setEnabled(surface)
        if smooth is not None:
            self.btn_smooth.setEnabled(smooth)
        if surface is not None:
            self.btn_terr.setEnabled(surface)

    def _toggle(self, name, visible):
        actor = self.plotter.renderer.actors.get(name)
        if actor is not None:
            actor.SetVisibility(visible)
            self.plotter.render()

    def _say(self, msg):
        self.log.appendPlainText(msg)

    # ------------------------------------------------------- tareas en hilo
    def _run(self, fn, on_done, *args, **kw):
        self.side.setEnabled(False)       # el visor sigue interactivo durante el cálculo
        self.progress.setRange(0, 0)      # barra indeterminada
        w = Worker(fn, *args, **kw)
        w.on_done = on_done
        self._workers.add(w)
        # Slots del MainWindow (hilo UI) -> conexión en cola. NO usar lambdas:
        # correrían en el hilo del Worker y VTK/Qt se cerraría al dibujar.
        w.signals.done.connect(self._on_worker_done)
        w.signals.error.connect(self._on_worker_error)
        self.pool.start(w)

    def _end_task(self, w):
        self._workers.discard(w)
        self.side.setEnabled(True)
        self.progress.setRange(0, 1)

    @Slot(object, object)
    def _on_worker_done(self, w, result):
        self._end_task(w)
        try:
            w.on_done(result)
        except Exception:
            tb = traceback.format_exc()
            self._say(tb)
            self._bridge_complete(False, error=tb.strip().splitlines()[-1])
            return
        self._bridge_complete(True, result=_jsonable(result))

    @Slot(object, str)
    def _on_worker_error(self, w, tb):
        self._end_task(w)
        self._say(tb)
        if self._pending is not None:  # comando remoto: sin diálogo modal que bloquee la UI
            self._bridge_complete(False, error=tb.strip().splitlines()[-1])
            return
        QMessageBox.critical(self, "Error", tb.strip().splitlines()[-1])

    # --------------------------------------------------------------- acciones
    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Abrir nube", "", "LAS/LAZ (*.las *.laz)")
        if not path:
            return
        self._load_path(path)

    def _load_path(self, path):
        self._say(f"Cargando {path} ...")
        self._path = path
        self._run(self.proc.load_las, self._loaded, path)

    def _loaded(self, info):
        self._say(f"{info['n_points']:,} puntos | Z {info['z_range'][0]:.2f}..{info['z_range'][1]:.2f}"
                  f" | origen {np.round(info['origin'], 2)}")
        self._sample = self.proc.display_sample()
        self.plotter.remove_actor("mesh")
        self.plotter.remove_actor("cloud")
        self.chk_raw.setChecked(True)
        self._draw_cloud(classified=False)
        self.plotter.reset_camera()
        self._set_enabled(classify=True, surface=False, smooth=False)

    def on_classify(self):
        if self.cb_cls.currentIndex() == 0:
            kw = dict(method="grid", cell_size=self.sp_cell.value(),
                      height_tol=self.sp_tol.value())
        else:
            kw = dict(method="ransac", distance=self.sp_tol.value())
        self._say("Clasificando ...")
        self._run(self.proc.classify_ground, self._classified, **kw)

    def on_auto(self):
        self._say("Clasificación automática (vóxeles + rasgos + modelo) ...")
        self._run(self.proc.classify_auto, self._auto_done)

    def _auto_done(self, st):
        self._say(f"Terreno {st['ground']:,} | Estructura {st['structure']:,} | "
                  f"Vegetación {st['vegetation']:,} (vóxel {st['voxel']} m)")
        self._classified(st, quiet=True)

    def on_structure(self):
        self._say("Agrupando estructura (conectividad de vóxeles) ...")
        self._run(self.proc.analyze_structure, self._structure_done)

    def _structure_done(self, st):
        for c in st["clusters"]:
            self._say(f"  grupo {c['id']}: {c['points']:,} pts, caja "
                      f"{c['bbox_xyz'][0]:.1f} x {c['bbox_xyz'][1]:.1f} x {c['bbox_xyz'][2]:.1f} m")
        ax = st["axis"]
        if ax:
            self._say(f"Eje: azimut {ax['angle_deg']:.2f} grados, largo {ax['length']:.1f} m, "
                      f"ancho {ax['width']:.1f} m")
        self._draw_clusters()

    def on_section(self):
        self._say("Apilando cortes transversales ...")
        self._run(self.proc.extract_section, self._section_done)

    def _section_done(self, st):
        v = st["validation"]
        self._say(f"Sección: {st['contours']} contorno(s), {st['vertices']} vértices, {st['slabs']} cortes. "
                  f"Puntos a <5 cm: {v.get('pct_dentro_5cm', 0):.0f} %, <10 cm: {v.get('pct_dentro_10cm', 0):.0f} %")
        sec = self.proc.section
        pts, cells, off = [], [], 0
        for ln in sec.lines3d:
            pts.append(ln)
            cells.append(np.r_[len(ln), np.arange(off, off + len(ln))])
            off += len(ln)
        poly = pv.PolyData(np.vstack(pts))
        poly.lines = np.concatenate(cells)
        self.plotter.add_mesh(poly, color="#ffd400", line_width=2, name="section_lines")
        self.plotter.render()

    def on_elements(self):
        self._say("Separando elementos y construyendo sólidos ...")
        self._run(self.proc.build_elements, self._elements_done)

    def _elements_done(self, st):
        for e in st["elements"]:
            self._say(f"  {e['name']} ({e['ifc_class']}): {e['volume_m3']} m3, "
                      f"{'cerrado' if e['watertight'] else 'ABIERTO'}")
        self._say(f"Volumen total: {st['total_volume_m3']} m3")
        self._draw_solids()

    def _draw_solids(self):
        for a in [k for k in self.plotter.renderer.actors if k.startswith("solid_")]:
            self.plotter.remove_actor(a)
        pal = {"losa": "#4a86d8", "viga": "#d8703b", "baranda": "#3bd87a", "estribo": "#9a9a9a",
               "riel": "#3bd87a", "poste": "#f0e040", "muro": "#b08cd8", "apoyo": "#e04040",
               "via": "#6e6e78"}
        for i, s in enumerate(self.proc.solids):
            faces = np.hstack([np.full((len(s.triangles), 1), 3), s.triangles]).ravel()
            self.plotter.add_mesh(pv.PolyData(s.vertices, faces), color=pal.get(s.kind, "#cccccc"),
                                  show_edges=False, name=f"solid_{i}")
        self._register_elements()
        self.plotter.render()

    def on_railings(self):
        self._say("Detectando rieles y postes ...")
        self._run(self.proc.build_railings, self._railings_done)

    def _railings_done(self, st):
        self._say(f"Barandas: {st['railings']} | separación mediana de postes {st['post_spacing_median_m']} m "
                  f"| cerrados {st['watertight']}")
        self._draw_solids()

    def on_abutments(self):
        self._say("Midiendo caras frontales de los estribos ...")
        self._run(self.proc.build_abutments, self._abutments_done)

    def _abutments_done(self, st):
        for a in st["abutments"]:
            self._say(f"  {a['name']}: cara a {a['w_front_m']} m del extremo, asiento {a['seat_width_perp_m']} m, "
                      f"ancho {a['t_range_m'][1] - a['t_range_m'][0]:.1f} m, {a['volume_m3']} m3, "
                      f"plano a 3 cm {a['plane_within_3cm_pct']:.0f} %")
        self._draw_solids()

    def on_wings(self):
        self._say("Midiendo muros de retorno ...")
        self._run(self.proc.build_wings, self._wings_done)

    def _wings_done(self, st):
        for w in st["wing_walls"]:
            self._say(f"  {w['name']}: {w['length_m']} m largo, alto {w['height_m'][0]}-{w['height_m'][1]} m, "
                      f"{w['volume_m3']} m3, cara en t={w['t_face_m']}")
        self._draw_solids()

    def on_bearings(self):
        self._say("Ubicando apoyos bajo las vigas ...")
        self._run(self.proc.build_bearings, self._bearings_done)

    def _bearings_done(self, st):
        self._say(f"Apoyos: {st['bearings']} | espesor {st['height_m']} m "
                  f"({'medido' if st['height_measured'] else 'supuesto'}) | cerrados {st['watertight']}")
        self._draw_solids()

    def on_road(self):
        self._say("Separando la vía de acceso del terreno ...")
        self._run(self.proc.build_road, self._road_done)

    def _road_done(self, st):
        self._say(f"Vía: {st['road_points']:,} puntos pasan a clase 11")
        for k, m in st["sides"].items():
            self._say(f"  {k}: {m['length_m']} m largo x {m['width_m']} m ancho (t {m['t_left_m']} a {m['t_right_m']}), "
                      f"peralte {m['cross_slope_pct']} %")
        self._draw_solids()

    def on_export_dxf(self):
        epsg = 0
        if self.proc.crs is not None:
            try:
                epsg = int(self.proc.crs.to_epsg() or 0)
            except Exception:
                epsg = 0
        dlg = DxfDialog(self, epsg, self.proc.mesh is not None, os.path.join(HERE, "lineas.dxf"))
        if dlg.exec() != DxfDialog.Accepted:
            return
        cfg = dlg.config()
        path = cfg.pop("path")
        self._say(f"Exportando DXF ({cfg['coords']}) a {path} ...")
        self._run(self.proc.export_dxf, self._dxf_done, path, **cfg)

    def _dxf_done(self, r):
        self._say(f"DXF: {r['file']} | {r['coords']} | EPSG {r['epsg']} | {r['polylines']} polilíneas, "
                  f"{r['points']} cotas, {r['faces3d']} caras | capas {r['layers']}")
        for a in r["aux"]:
            self._say(f"  auxiliar: {a}")

    def on_export_ifc(self):
        if not self.proc.solids:
            QMessageBox.information(self, "Exportar IFC", "Primero genera los sólidos (Separar elementos).")
            return
        counts = {}
        for sol in self.proc.solids:
            counts[sol.kind] = counts.get(sol.kind, 0) + 1
        if self.proc.mesh is not None:
            counts["terreno"] = 1
        classes = {sol.kind: sol.ifc_class for sol in self.proc.solids}
        epsg = 0
        if self.proc.crs is not None:
            try:
                epsg = int(self.proc.crs.to_epsg() or 0)
            except Exception:
                epsg = 0
        dlg = IfcExportDialog(self, counts, classes, epsg, len(self._items) - len(self._hidden),
                              len(self._selected), os.path.join(HERE, "modelo.ifc"),
                              {**{k: v.get("predefined_type") for k, v in IFC_CATEGORY.items()},
                               "terreno": "TERRAIN"})
        if dlg.exec() != IfcExportDialog.Accepted:
            return
        cfg = dlg.config()
        if not cfg["path"].lower().endswith(".ifc"):
            cfg["path"] += ".ifc"
        self._export_with(cfg)

    def _export_with(self, cfg):
        """Resuelve el alcance (todos / visibles / seleccionados) y exporta en un hilo."""
        if cfg.get("scope") == "visible":
            cfg["only_names"] = {k for k in self._items if k not in self._hidden}
        elif cfg.get("scope") == "selected":
            cfg["only_names"] = set(self._selected)
        self._say(f"Exportando IFC {cfg['versions']} a {cfg['path']} ...")
        self._run(self.proc.export_ifc, self._export_done, cfg["path"], "ALL", cfg.get("epsg"), True, cfg)

    def _export_done(self, r):
        self._say(f"IFC: {r['elements']} elementos, EPSG {r['epsg']}")
        for v, f in r["files"].items():
            extra = ""
            if "validation_issues" in r:
                extra = f" | validador: {r['validation_issues'].get(v)} incidencias"
            self._say(f"  {v}: {f}{extra}")

    def _draw_clusters(self):
        r = self.proc.structure
        self._mode = "clusters"
        self.plotter.remove_actor("mesh")
        m = r.labels >= 0
        poly = pv.PolyData(self.proc.points[m])
        pal = np.array([(0.90, 0.30, 0.20), (0.20, 0.55, 0.90), (0.95, 0.75, 0.10),
                        (0.60, 0.30, 0.80), (0.20, 0.75, 0.55), (0.95, 0.50, 0.70)])
        poly["rgb"] = (pal[r.labels[m] % len(pal)] * 255).astype(np.uint8)
        self.plotter.add_mesh(poly, scalars="rgb", rgb=True, style="points", point_size=2, name="cloud")
        if r.axis_dir is not None:
            half = r.length / 2 + 5
            a = r.axis_center - r.axis_dir * half
            b = r.axis_center + r.axis_dir * half
            self.plotter.add_mesh(pv.Line(a, b), color="yellow", line_width=4, name="axis")
        self._toggle("cloud", self.chk_cloud.isChecked())
        self.plotter.render()

    def _classified(self, stats, quiet=False):
        if not quiet:
            self._say(f"Terreno: {stats['ground']:,} | No terreno: {stats['non_ground']:,}")
        self.plotter.remove_actor("mesh")
        self._draw_cloud(classified=True)
        self.chk_raw.setChecked(False)      # mismos puntos que la clasificada: se evita superponerlas
        self._set_enabled(surface=stats["ground"] >= 3, smooth=False)

    def on_surface(self):
        method = "delaunay" if self.cb_surf.currentIndex() == 0 else "poisson"
        self._say(f"Generando superficie ({method}) ...")
        self._run(self.proc.generate_surface, self._surfaced, method=method,
                  voxel_size=self.sp_vox.value(), max_edge=self.sp_edge.value())

    def _surfaced(self, stats):
        self._say(f"Malla: {stats['vertices']:,} vértices, {stats['triangles']:,} triángulos")
        self._draw_mesh()
        self._set_enabled(smooth=True)

    def on_terrain(self):
        self._say("Terreno depurado: diezmado 0.5 m, aristas <= 4 m, recorte bajo estructuras ...")
        self._run(self.proc.build_terrain, self._terrain_done)

    def _terrain_done(self, st):
        ck = st["check"]
        self._say(f"Terreno: {st['vertices']:,} vértices, {st['triangles']:,} triángulos, {st['area_m2']} m2 | "
                  f"descartados {st['removed_outliers']:,} outliers, {st['removed_long_edge']:,} por arista larga y {st['removed_in_structure']} bajo estructuras | "
                  f"error vertical mediana {ck['median_abs_dz_m']} m, rms {ck['rms_dz_m']} m, p95 {ck['p95_abs_dz_m']} m, cobertura {ck['covered_pct']} %")
        self._draw_mesh()
        self._set_enabled(smooth=True)

    def on_smooth(self):
        method = ["taubin", "laplacian", "simple"][self.cb_smooth.currentIndex()]
        self._say(f"Suavizando ({method} x{self.sp_iter.value()}) ...")
        self._run(self.proc.smooth_mesh, self._smoothed, method=method,
                  iterations=self.sp_iter.value(), preserve_xy=self.chk_keepxy.isChecked())

    def _smoothed(self, st):
        self._say(f"Suavizado: cambio Z RMS {st['rms_dz']:.4f} m, máx {st['max_dz']:.4f} m")
        self._draw_mesh()

    def _draw_mesh(self):
        m = self.proc.mesh
        faces = np.hstack([np.full((len(m.triangles), 1), 3), m.triangles]).ravel()
        poly = pv.PolyData(m.vertices, faces)
        poly["Elevación"] = m.vertices[:, 2] + self.proc.origin[2]
        self.plotter.add_mesh(poly, scalars="Elevación", cmap="terrain", show_edges=True,
                              edge_color="#222222", line_width=0.3, name="mesh")
        self._register_elements()
        self._toggle("mesh", self.chk_mesh.isChecked())
        self.plotter.render()

    # ---------------------------------------------------------------- render
    def _draw_cloud(self, classified: bool):
        self._classified_view = classified
        idx = self._sample
        if classified and self._only:
            idx = idx[np.isin(self.proc.classification[idx], list(self._only))]
            if len(idx) == 0:
                return
        pts = self.proc.points[idx]
        poly = pv.PolyData(pts)
        if classified:
            cl = self.proc.classification[idx]
            rgb = np.tile(np.array(COLOR_NON_GROUND), (len(idx), 1))
            for code, col in CLASS_COLORS.items():
                rgb[cl == code] = col
            poly["rgb"] = (rgb * 255).astype(np.uint8)
            self.plotter.add_mesh(poly, scalars="rgb", rgb=True, style="points",
                                  point_size=2, name="cloud")
        else:
            if self.proc.rgb is not None:      # color original del LAS
                poly["rgb"] = self.proc.rgb[idx]
                self.plotter.add_mesh(poly, scalars="rgb", rgb=True, style="points", point_size=2,
                                      name="cloud_raw")
            else:                              # sin color: por elevación
                poly["Elevación"] = pts[:, 2] + self.proc.origin[2]
                self.plotter.add_mesh(poly, scalars="Elevación", cmap="viridis", style="points",
                                      point_size=2, name="cloud_raw")
        self._toggle("cloud" if classified else "cloud_raw",
                     (self.chk_cloud if classified else self.chk_raw).isChecked())
        self.plotter.render()

    # ----------------------------------------------------------------- puente
    def _bridge_complete(self, ok, **data):
        req, self._pending = self._pending, None
        if req is not None:
            req.finish(ok, **data)

    @Slot(object)
    def _on_bridge_request(self, req):
        """Corre en el hilo de UI. Comandos 'sync' responden ya; los de tarea, al terminar."""
        try:
            sync = {"status": self._cmd_status, "log": self._cmd_log, "view": self._cmd_view,
                    "tree": self._cmd_tree, "visibility": self._cmd_visibility,
                    "select": self._cmd_select, "axes": self._cmd_axes,
                    "screenshot": self._cmd_screenshot, "sectionbox": self._cmd_sectionbox,
                    "annot": self._cmd_annot}
            task = {"load": self._cmd_load, "classify": self._cmd_classify,
                    "surface": self._cmd_surface, "smooth": self._cmd_smooth,
                    "analyze": self._cmd_analyze, "autoclass": self._cmd_autoclass,
                    "structure": self._cmd_structure, "section": self._cmd_section,
                    "elements": self._cmd_elements, "export_ifc": self._cmd_export_ifc,
                    "abutments": self._cmd_abutments, "railings": self._cmd_railings,
                    "wings": self._cmd_wings, "bearings": self._cmd_bearings,
                    "road": self._cmd_road, "terrain": self._cmd_terrain,
                    "export_dxf": self._cmd_export_dxf}
            if req.cmd in sync:
                req.finish(True, result=_jsonable(sync[req.cmd](req.args)))
            elif req.cmd in task:
                if self._pending is not None or not self.side.isEnabled():
                    return req.finish(False, error="la app está ocupada con otra tarea")
                self._pending = req
                task[req.cmd](req.args)
            else:
                req.finish(False, error=f"comando desconocido: {req.cmd}",
                           commands=sorted([*sync, *task]))
        except Exception as e:
            if self._pending is req:
                self._pending = None
            self._say(traceback.format_exc())
            req.finish(False, error=f"{type(e).__name__}: {e}")

    def _cmd_status(self, a):
        p = self.proc
        return {
            "busy": self._pending is not None or not self.side.isEnabled(),
            "file": self._path,
            "n_points": None if p.points is None else int(len(p.points)),
            "origin": None if p.points is None else p.origin,
            "classes": None if p.classification is None else
                dict(zip(*[x.tolist() for x in np.unique(p.classification, return_counts=True)])),
            "mesh": None if p.mesh is None else {"vertices": len(p.mesh.vertices),
                                                  "triangles": len(p.mesh.triangles)},
            "ui": {"classify_method": self.cb_cls.currentText(), "cell": self.sp_cell.value(),
                   "tol": self.sp_tol.value(), "surface_method": self.cb_surf.currentText(),
                   "voxel": self.sp_vox.value(), "max_edge": self.sp_edge.value(),
                   "smooth": self.cb_smooth.currentText(), "smooth_iter": self.sp_iter.value(),
                   "keep_xy": self.chk_keepxy.isChecked()},
            "log_tail": self.log.toPlainText().splitlines()[-8:],
        }

    def _cmd_log(self, a):
        return self.log.toPlainText().splitlines()[-int(a.get("n", 30)):]

    def _cmd_view(self, a):
        """view: iso|top|front|side|reset; cloud/mesh: bool para mostrar u ocultar."""
        if "raw" in a:
            self.chk_raw.setChecked(bool(a["raw"]))
        if "cloud" in a:
            self.chk_cloud.setChecked(bool(a["cloud"]))
        if "mesh" in a:
            self.chk_mesh.setChecked(bool(a["mesh"]))
        if "only" in a:  # lista de códigos ASPRS (2, 6, 5) o null para todos
            self._only = set(a["only"]) if a["only"] else None
            if self._classified_view:
                self._draw_cloud(classified=True)
        v = a.get("view")
        if v:
            {"iso": self.plotter.view_isometric, "top": self.plotter.view_xy,
             "front": self.plotter.view_xz, "side": self.plotter.view_yz,
             "reset": self.plotter.reset_camera}[v]()
        if a.get("focus") == "deck" and self.proc.structure is not None and self.proc.section is not None:
            r = self.proc.structure   # encuadra el tablero (no toda la escena)
            self.plotter.set_focus(r.axis_center)
        if "zoom" in a:
            self.plotter.camera.zoom(float(a["zoom"]))
        self.plotter.render()
        return "ok"

    def _cmd_screenshot(self, a):
        if a.get("window"):             # ventana completa (con paneles), no solo el visor 3D
            path = os.path.join(HERE, ".bridge_window.png")
            self.grab().save(path)
            return {"path": path}
        self.plotter.render()
        self.plotter.screenshot(SHOT_PATH)
        return {"path": SHOT_PATH}

    def _cmd_load(self, a):
        path = os.path.expanduser(str(a.get("path", "")))
        if not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in (".las", ".laz"):
            raise ValueError(f"archivo .las/.laz no encontrado: {path}")
        self._load_path(path)

    def _cmd_classify(self, a):
        if self.proc.points is None:
            raise ValueError("carga un archivo primero")
        self.cb_cls.setCurrentIndex({"grid": 0, "ransac": 1}[a.get("method", "grid")])
        if "cell_size" in a:
            self.sp_cell.setValue(float(a["cell_size"]))
        if "height_tol" in a:
            self.sp_tol.setValue(float(a["height_tol"]))
        self.on_classify()

    def _cmd_surface(self, a):
        if not self.btn_surf.isEnabled():
            raise ValueError("clasifica terreno primero")
        self.cb_surf.setCurrentIndex({"delaunay": 0, "poisson": 1}[a.get("method", "delaunay")])
        if "voxel_size" in a:
            self.sp_vox.setValue(float(a["voxel_size"]))
        if "max_edge" in a:
            self.sp_edge.setValue(float(a["max_edge"]))
        self.on_surface()

    def _cmd_smooth(self, a):
        if not self.btn_smooth.isEnabled():
            raise ValueError("genera la superficie primero")
        self.cb_smooth.setCurrentIndex({"taubin": 0, "laplacian": 1, "simple": 2}[a.get("method", "taubin")])
        if "iterations" in a:
            self.sp_iter.setValue(int(a["iterations"]))
        if "preserve_xy" in a:
            self.chk_keepxy.setChecked(bool(a["preserve_xy"]))
        self.on_smooth()

    def _cmd_railings(self, a):
        self.on_railings()

    def _cmd_terrain(self, a):
        if not self.btn_terr.isEnabled():
            raise ValueError("clasifica terreno primero")
        self._say("Terreno depurado ...")
        kw = {k: a[k] for k in ("voxel", "max_edge", "clip_structure", "target_triangles", "outlier_cell", "outlier_tol", "roi_margin") if k in a}
        self._run(self.proc.build_terrain, self._terrain_done, **kw)

    def _cmd_wings(self, a):
        self.on_wings()

    def _cmd_export_dxf(self, a):
        path = os.path.expanduser(str(a.get("path", "")))
        if not path.lower().endswith(".dxf"):
            raise ValueError("path debe terminar en .dxf")
        kw = {k: a[k] for k in ("coords", "epsg", "edge_angle", "contour_interval", "terrain_mesh", "kinds") if k in a}
        self._run(self.proc.export_dxf, self._dxf_done, path, **kw)

    def _cmd_sectionbox(self, a):
        """on, frame (puente|mundo), bounds [s0,s1,t0,t1,z0,z1] (null = sin cambio), show, preset
        (all|transversal|longitudinal|planta) con pos y thick."""
        t = self.tools
        if "drag" in a:                  # {face, dx, dy[, x, y]}: arrastre simulado de un asa
            g = a["drag"]
            return t.drag_face(g["face"], g.get("dx", 0), g.get("dy", 0), g.get("x"), g.get("y"))
        if "handles" in a:               # posición en pantalla de cada asa
            return {k: t._to_display(c).round(1).tolist() for k, (c, _) in t.face_centers().items()}
        if "preset" in a:
            if "frame" in a:
                t.set_box(frame=a["frame"])
            return t.preset(a["preset"], a.get("pos"), float(a.get("thick", 1.0)))
        if a.get("on") and all(sp.value() == 0 for sp in t.spins.values()):
            t.reset_bounds()
        return t.set_box(on=a.get("on"), bounds=a.get("bounds"), frame=a.get("frame"), show=a.get("show"))

    def _cmd_annot(self, a):
        """mode (consulta|cota|medida|ninguna); click [x,y]; point o a+b (reales si real=true, si no
        locales); clear; csv path."""
        t, o = self.tools, self.proc.origin
        loc = (lambda p: np.asarray(p, float) - (o if a.get("real") else 0))
        if "face" in a:                  # cara de la caja que fija el modo "cara"
            t.cb_face.setCurrentIndex(t.cb_face.findData(a["face"]))
        if "mode" in a:
            t.set_mode(a["mode"])
        if "click" in a:
            x, y = a["click"][:2]
            p = t.pick_world(int(x), int(y))
            if p is None:
                raise ValueError("sin geometría bajo ese píxel")
            t.at_point(p)
        if "point" in a:
            t.at_point(loc(a["point"]))
        if "a" in a and "b" in a:
            self.proc.add_annotation("medida", a=loc(a["a"]), b=loc(a["b"]), text=a.get("text", ""))
            t.refresh_annotations()
        if a.get("clear"):
            t.clear_annotations()
        if "csv" in a:
            t.export_csv(a["csv"])
        return {"mode": t.mode, "info": t.lbl_info.text(), "annotations": list(self.proc.annotations)}

    def _cmd_road(self, a):
        self.on_road()

    def _cmd_bearings(self, a):
        self.on_bearings()

    def _cmd_abutments(self, a):
        self.on_abutments()

    def _cmd_elements(self, a):
        self.on_elements()

    def _cmd_export_ifc(self, a):
        path = os.path.expanduser(str(a.get("path", "")))
        if not path.lower().endswith(".ifc"):
            raise ValueError("path debe terminar en .ifc")
        cfg = dict(a.get("config") or {})
        cfg["path"] = path
        if a.get("version") and not cfg.get("versions"):
            cfg["versions"] = None if str(a["version"]).upper() == "ALL" else [a["version"]]
        if "epsg" in a and "epsg" not in cfg:
            cfg["epsg"] = a["epsg"]
        if "scope" in a and "scope" not in cfg:
            cfg["scope"] = a["scope"]
        if cfg.get("scope") == "visible":
            cfg["only_names"] = {k for k in self._items if k not in self._hidden}
        elif cfg.get("scope") == "selected":
            cfg["only_names"] = set(self._selected)
        self._run(self.proc.export_ifc, lambda r: self._export_done(r), path, "ALL",
                  cfg.get("epsg"), bool(a.get("terrain", True)), cfg)

    def _cmd_section(self, a):
        self.on_section()

    def _cmd_structure(self, a):
        if self.proc.points is None:
            raise ValueError("carga un archivo primero")
        self.on_structure()

    def _cmd_autoclass(self, a):
        if self.proc.points is None:
            raise ValueError("carga un archivo primero")
        self.on_auto()

    def _cmd_analyze(self, a):
        if self.proc.points is None:
            raise ValueError("carga un archivo primero")
        self._run(self.proc.analyze, lambda r: self._say("Análisis completado"))

    # -------------------------------------- elementos: árbol IFC, visibilidad, selección y ejes
    # Categoría (ruta del árbol) por tipo de elemento; sigue la jerarquía IFC del exportador.
    CATEGORY = {
        "losa": ("Superestructura", "Tablero"), "viga": ("Superestructura", "Tablero"),
        "baranda": ("Superestructura", "Elementos de superficie y seguridad"),
        "riel": ("Superestructura", "Elementos de superficie y seguridad", "Rieles"),
        "poste": ("Superestructura", "Elementos de superficie y seguridad", "Postes"),
        "estribo": ("Subestructura", "Estribos"),
        "muro": ("Subestructura", "Estribos", "Muros de retorno"),
        "apoyo": ("Subestructura", "Estribos", "Apoyos"),
        "via": ("Vía de acceso",),
    }
    HIGHLIGHT = "#ff4fd8"

    def _build_elements_dock(self):
        self._items, self._hidden, self._selected = {}, set(), set()
        self._addr_key, self._building, self._render_pending = {}, False, False
        dock = QDockWidget("Elementos y vista", self)
        w = QWidget()
        v = QVBoxLayout(w)
        self.chk_axes = QCheckBox("Ejes con coordenadas reales (Este, Norte, cota)")
        self.chk_axes.toggled.connect(lambda on: self._toggle_axes(on))
        self.chk_triad = QCheckBox("Triedro de orientación")
        self.chk_triad.setChecked(True)
        self.chk_triad.toggled.connect(self._toggle_triad)
        v.addWidget(self.chk_axes)
        v.addWidget(self.chk_triad)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Elementos (organización IFC)")
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemChanged.connect(self._tree_item_changed)
        self.tree.itemSelectionChanged.connect(self._tree_selection_changed)
        v.addWidget(self.tree, 1)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for text, fn, row in (("Ocultar seleccionados", self.hide_selected, row1),
                              ("Aislar seleccionados", self.isolate_selected, row1),
                              ("Mostrar todo", self.show_all, row2),
                              ("Quitar selección", lambda: self.tree.clearSelection(), row2)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row1)
        v.addLayout(row2)
        hint = QLabel("Clic en el modelo: selecciona un elemento.\nCtrl o Mayús + clic: suma o quita.\n"
                      "Clic en el vacío: limpia la selección.")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)
        dock.setWidget(w)
        dock.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._install_picker()
        self.tools = ViewerTools(self)
        dock2 = QDockWidget("Caja de secciones, cotas y medidas", self)
        dock2.setWidget(self.tools.build_panel())
        dock2.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, dock2)
        self.tabifyDockWidget(dock, dock2)
        dock.raise_()

    def _request_render(self):
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(0, self._do_render)

    def _do_render(self):
        self._render_pending = False
        self.plotter.render()

    def _register_elements(self):
        """Reconstruye el árbol con los sólidos (y el terreno) que están en el visor."""
        entries = []
        for i, s in enumerate(self.proc.solids):
            a = self.plotter.renderer.actors.get(f"solid_{i}")
            if a is None:
                continue
            path = list(self.CATEGORY.get(s.kind, ("Otros",)))
            side = getattr(s, "metrics", {}).get("side")
            if side:
                path.append(side.capitalize())
            entries.append((s.name, s.kind, path, f"{s.name}  [{s.ifc_class}]", a))
        t = self.plotter.renderer.actors.get("mesh")
        if t is not None and self.proc.mesh is not None:
            entries.append(("Terreno", "terreno", ["Terreno"], "Terreno  [IfcGeographicElement]", t))

        for name, label in (("section_lines", "Líneas de sección"), ("axis", "Eje del puente")):
            a = self.plotter.renderer.actors.get(name)
            if a is not None:       # referencias de apoyo, no son elementos IFC
                entries.append((label, "linea", ["Referencias (no IFC)"], label, a))

        self._building = True
        self.tree.clear()
        self._items, self._addr_key = {}, {}
        groups = {}
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        for key, kind, path, label, actor in entries:
            parent, acc = self.tree.invisibleRootItem(), ()
            for p in path:
                acc += (p,)
                if acc not in groups:
                    g = QTreeWidgetItem(parent, [p])
                    g.setFlags(flags | Qt.ItemIsAutoTristate)
                    g.setCheckState(0, Qt.Checked)
                    groups[acc] = g
                parent = groups[acc]
            it = QTreeWidgetItem(parent, [label])
            it.setFlags(flags)
            it.setData(0, Qt.UserRole, key)
            vis = key not in self._hidden
            it.setCheckState(0, Qt.Checked if vis else Qt.Unchecked)
            actor.SetVisibility(vis)
            color = None if kind in ("terreno", "linea") else tuple(actor.prop.color.float_rgb)
            self._items[key] = {"item": it, "actor": actor, "kind": kind, "color": color, "path": path}
            self._addr_key[actor.GetAddressAsString("")] = key
        for g in sorted(groups.values(), key=lambda x: -self._depth(x)):    # estado de grupos, de abajo hacia arriba
            states = {g.child(i).checkState(0) for i in range(g.childCount())}
            g.setCheckState(0, Qt.Checked if states == {Qt.Checked} else
                            Qt.Unchecked if states == {Qt.Unchecked} else Qt.PartiallyChecked)
        self.tree.expandAll()
        self._building = False
        self._selected &= set(self._items)
        for k in self._selected:
            self._items[k]["item"].setSelected(True)
            self._highlight(k, True)
        if self.chk_axes.isChecked():
            self._toggle_axes(True)
        self._request_render()

    @staticmethod
    def _depth(item):
        d = 0
        while item.parent() is not None:
            item, d = item.parent(), d + 1
        return d

    def _leaf_keys(self, item):
        key = item.data(0, Qt.UserRole)
        if key is not None:
            return [key]
        return [k for i in range(item.childCount()) for k in self._leaf_keys(item.child(i))]

    def _set_visible(self, key, vis):
        rec = self._items.get(key)
        if rec is None:
            return
        rec["actor"].SetVisibility(bool(vis))
        (self._hidden.discard if vis else self._hidden.add)(key)

    def _tree_item_changed(self, item, col):
        if self._building:
            return
        key = item.data(0, Qt.UserRole)
        if key is not None:
            self._set_visible(key, item.checkState(0) == Qt.Checked)
            self._request_render()

    def _highlight(self, key, on):
        rec = self._items.get(key)
        if rec is None:
            return
        a = rec["actor"]
        if rec["kind"] == "linea":
            return
        if rec["kind"] == "terreno":
            a.mapper.SetScalarVisibility(not on)
            if on:
                a.prop.color = self.HIGHLIGHT
        else:
            a.prop.color = self.HIGHLIGHT if on else rec["color"]

    def _tree_selection_changed(self):
        if self._building:
            return
        keys = set()
        for it in self.tree.selectedItems():
            keys.update(self._leaf_keys(it))
        for k in self._selected - keys:
            self._highlight(k, False)
        for k in keys - self._selected:
            self._highlight(k, True)
        self._selected = keys
        self._request_render()

    def _set_leaf_checks(self, keys_on, keys_off):
        for k in keys_on:
            self._items[k]["item"].setCheckState(0, Qt.Checked)
        for k in keys_off:
            self._items[k]["item"].setCheckState(0, Qt.Unchecked)

    def hide_selected(self):
        self._set_leaf_checks([], list(self._selected))

    def isolate_selected(self):
        if self._selected:
            self._set_leaf_checks(list(self._selected), [k for k in self._items if k not in self._selected])

    def show_all(self):
        self._set_leaf_checks(list(self._items), [])

    # --- selección con clic en el visor
    def _install_picker(self):
        from vtkmodules.vtkRenderingCore import vtkPropPicker
        self._picker = vtkPropPicker()
        self._press = None
        iren = self.plotter.iren.interactor
        # Observadores PASIVOS: al pulsar, el estilo de interacción toma el foco del ratón
        # (GrabFocus) y VTK bloquea a los observadores no pasivos; sin esto el "soltar" no llega.
        for ev, fn in (("LeftButtonPressEvent", self._on_lpress), ("LeftButtonReleaseEvent", self._on_lrelease)):
            tag = iren.AddObserver(ev, fn, 10.0)
            iren.GetCommand(tag).SetPassiveObserver(True)

    def _on_lpress(self, obj, ev):
        self._press = obj.GetEventPosition()

    def _on_lrelease(self, obj, ev):
        try:
            self._do_release(obj)
        except Exception:               # un error aquí no debe romper el visor: queda en el registro
            self._say(traceback.format_exc())

    def _do_release(self, obj):
        if self._press is None:
            return
        x0, y0 = self._press
        x, y = obj.GetEventPosition()
        self._press = None
        if abs(x - x0) > 3 or abs(y - y0) > 3:        # arrastre = rotar la cámara, no seleccionar
            return
        add = bool(obj.GetControlKey() or obj.GetShiftKey())
        if self.tools.handle_click(x, y, add):       # cota, medida o consulta activas
            return
        self._picker.Pick(x, y, 0, self.plotter.renderer)
        act = self._picker.GetActor()
        key = self._addr_key.get(act.GetAddressAsString("")) if act is not None else None
        self._last_pick = {"xy": [x, y], "size": list(self.plotter.window_size),
                           "actor": act.GetAddressAsString("") if act is not None else None, "key": key}
        self._pick_select(key, add)

    def _pick_select(self, key, add):
        if key is None:
            if not add:
                self.tree.clearSelection()
            return
        it = self._items[key]["item"]
        if add:
            it.setSelected(not it.isSelected())
        else:
            self.tree.clearSelection()
            it.setSelected(True)
        self.tree.scrollToItem(it)

    # --- ejes
    def _toggle_triad(self, on):
        (self.plotter.show_axes if on else self.plotter.hide_axes)()
        self._request_render()

    def _scene_bounds(self):
        vs = [s.vertices for s in self.proc.solids]
        if vs:
            V = np.vstack(vs)
        elif self.proc.points is not None:
            V = self.proc.points[::max(1, len(self.proc.points) // 200000)]
        else:
            return None
        mn, mx = V.min(axis=0), V.max(axis=0)
        pad = (mx - mn) * 0.05 + 0.5
        return [mn[0] - pad[0], mx[0] + pad[0], mn[1] - pad[1], mx[1] + pad[1], mn[2] - pad[2], mx[2] + pad[2]]

    def _toggle_axes(self, on):
        self.plotter.remove_bounds_axes()
        if on:
            b = self._scene_bounds()
            if b is not None:
                o = self.proc.origin     # los ejes muestran coordenadas reales (local + origen)
                rng = [b[0] + o[0], b[1] + o[0], b[2] + o[1], b[3] + o[1], b[4] + o[2], b[5] + o[2]]
                self.plotter.show_bounds(bounds=b, axes_ranges=rng, xtitle="Este (m)", ytitle="Norte (m)",
                                         ztitle="Cota (m)", grid=False, location="outer", ticks="outside",
                                         n_xlabels=4, n_ylabels=4, n_zlabels=4, fmt="%.1f",
                                         font_size=10, color="white", minor_ticks=False)
        self._request_render()

    # --- comandos del puente
    def _match_keys(self, text):
        t = str(text).lower()
        return [k for k, r in self._items.items()
                if t in (" / ".join(r["path"]) + " / " + k).lower()]

    def _cmd_tree(self, a):
        return [{"key": k, "path": r["path"], "visible": k not in self._hidden, "selected": k in self._selected}
                for k, r in self._items.items()]

    def _cmd_visibility(self, a):
        """match + visible; only (oculta el resto); all (muestra todo)."""
        if a.get("all"):
            self.show_all()
        elif "only" in a:
            keep = set(self._match_keys(a["only"]))
            self._set_leaf_checks(list(keep), [k for k in self._items if k not in keep])
        elif "match" in a:
            ks = self._match_keys(a["match"])
            vis = bool(a.get("visible", True))
            self._set_leaf_checks(ks if vis else [], [] if vis else ks)
        return {"hidden": sorted(self._hidden), "n_visible": len(self._items) - len(self._hidden)}

    def _cmd_select(self, a):
        """match (texto) o clear; click {x, y, ctrl}: simula un clic en el visor."""
        if "click" in a:
            x, y = a["click"][:2]
            iren = self.plotter.iren.interactor
            iren.SetEventInformation(int(x), int(y), int(bool(a.get("ctrl"))), 0)
            iren.LeftButtonPressEvent()      # mismos métodos que usa Qt con un clic real
            iren.LeftButtonReleaseEvent()
            return {"selected": sorted(self._selected), "pick": getattr(self, "_last_pick", None)}
        elif a.get("clear"):
            self.tree.clearSelection()
        elif "match" in a:
            if not a.get("add"):
                self.tree.clearSelection()
            for k in self._match_keys(a["match"]):
                self._items[k]["item"].setSelected(True)
        return sorted(self._selected)

    def _cmd_axes(self, a):
        if "on" in a:
            self.chk_axes.setChecked(bool(a["on"]))
        if "triad" in a:
            self.chk_triad.setChecked(bool(a["triad"]))
        return {"axes": self.chk_axes.isChecked(), "triad": self.chk_triad.isChecked()}

    def closeEvent(self, e):
        self.bridge.stop()
        self.plotter.close()
        super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
````

## 3.2 `core_processing.py`

`PointCloudProcessor`: carga LAS/LAZ, clasificación, superficies, terreno depurado, sólidos, vía y exportaciones. 703 líneas.

````python
"""
core_processing.py
Lógica pesada (sin GUI): lectura LAS/LAZ, clasificación de terreno y triangulación.

Convención: las coordenadas se guardan RECENTRADAS (restando self.origin) en float64.
Motivo: coordenadas UTM (~1e5-1e7) pierden precisión en float32 (OpenGL / Open3D).
Para exportar a coordenadas reales: points_local + processor.origin.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import laspy
import numpy as np
import open3d as o3d
import pyvista as pv

# Códigos ASPRS estándar: 2 = Ground, 1 = Unclassified
ASPRS_GROUND = 2
ASPRS_UNCLASSIFIED = 1
ASPRS_ROAD = 11          # superficie de camino


@dataclass
class MeshResult:
    vertices: np.ndarray   # (M, 3) float64, coords locales
    triangles: np.ndarray  # (K, 3) int64


# Categorización buildingSMART por tipo de elemento reconstruido.
# part: jerarquía de puente "TIPO:Nombre" (IFC4X3: IfcBridgePart; IFC4/2X3: IfcElementAssembly).
_DECK = ["SUPERSTRUCTURE:Superestructura", "DECK:Tablero"]
_SAFETY = ["SUPERSTRUCTURE:Superestructura", "SURFACESTRUCTURE:Elementos de superficie y seguridad"]
IFC_CATEGORY = {
    "losa": {"part": _DECK},
    "viga": {"part": _DECK, "predefined_type": {"IFC4X3": "GIRDER_SEGMENT", "default": "BEAM"}},
    "baranda": {"part": _SAFETY, "predefined_type": "GUARDRAIL"},
    "riel": {"part": _SAFETY, "predefined_type": "GUARDRAIL"},
    "poste": {"part": _SAFETY, "predefined_type": "POST"},
    "estribo": {"part": ["SUBSTRUCTURE:Subestructura"]},
    "muro": {"part": ["SUBSTRUCTURE:Subestructura"], "predefined_type": {"IFC4X3": "RETAININGWALL", "default": "SOLIDWALL"}},
    "apoyo": {"part": ["SUBSTRUCTURE:Subestructura"], "predefined_type": "NOTDEFINED"},
    "via": {"predefined_type": "NOTDEFINED"},      # vía de acceso: fuera del puente, directo en el sitio
}
ASSUMPTIONS = {"via": "Superficie y bordes medidos (plano con peralte por tramo); espesor 0.10 m supuesto, "
                      "sin capas de pavimento",
               "muro": "Cara exterior medida; espesor 0.30 m supuesto (la cara interior queda bajo el relleno)",
               "apoyo": "Posición transversal, ancho del ala y espesor medidos; posición longitudinal (mitad del asiento) "
                        "y largo 0.35 m supuestos; tipo de apoyo no identificable en la nube",
               "estribo": "Espesor de espaldar 0.30 m supuesto; base plana; sin muros de retorno",
               "poste": "Prisma rectangular; el perfil real es una I", "riel": "Riel macizo; el tubo real es hueco"}


class PointCloudProcessor:
    def __init__(self) -> None:
        self.points: Optional[np.ndarray] = None          # (N, 3) float64 locales
        self.origin: np.ndarray = np.zeros(3)             # desplazamiento a coords reales
        self.classification: Optional[np.ndarray] = None  # (N,) uint8 (códigos ASPRS)
        self.mesh: Optional[MeshResult] = None
        self.crs = None                                   # CRS del LAS si existe
        self.rgb = None                                   # (N,3) uint8 si el LAS trae color
        self.structure = None                             # reconstruct.StructureResult
        self.section = None                               # reconstruct.SectionResult
        self.elements = []                                # reconstruct.Element
        self.solids = []                                  # reconstruct.Solid
        self.annotations = []                             # cotas y medidas (coords locales)

    # ------------------------------------------------------------------ carga
    def load_las(self, path: str) -> dict:
        """Lee .las/.laz. Para .laz hace falta laspy[lazrs] (ya en requirements)."""
        las = laspy.read(path)
        xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
        fin = np.isfinite(xyz).all(axis=1)
        xyz = xyz[fin]
        self.annotations = []                             # las cotas y medidas son de la nube anterior
        self.rgb = None      # color original del LAS (uint8) si lo trae; la vista de la nube original lo usa
        if {"red", "green", "blue"} <= set(las.point_format.dimension_names):
            rgb = np.column_stack((np.asarray(las.red), np.asarray(las.green), np.asarray(las.blue)))
            top = int(rgb.max()) if rgb.size else 0
            if top > 0:
                self.rgb = (rgb[fin] >> (8 if top > 255 else 0)).astype(np.uint8)
            del rgb
        if len(xyz) == 0:
            raise ValueError("El archivo no contiene puntos válidos.")

        self.origin = xyz.min(axis=0)
        self.points = xyz - self.origin
        self.classification = np.full(len(xyz), ASPRS_UNCLASSIFIED, dtype=np.uint8)
        self.mesh = None
        try:
            self.crs = las.header.parse_crs()
        except Exception:
            self.crs = None

        # EXTENSIÓN: si el LAS ya trae clasificación (las.classification), úsala aquí
        # como punto de partida o para validar tu clasificador contra una referencia.
        # Ojo: si filtras puntos no finitos arriba, filtra también ese atributo.
        return {
            "n_points": len(xyz),
            "origin": self.origin.copy(),
            "extent": self.points.max(axis=0),
            "z_range": (float(xyz[:, 2].min()), float(xyz[:, 2].max())),
        }

    # ----------------------------------------------------------- clasificación
    def classify_ground(self, method: str = "grid", **kw) -> dict:
        """
        Clasificación BÁSICA de terreno. Devuelve estadísticas.

        >>> PUNTO DE INTEGRACIÓN <<<
        Para algoritmos avanzados añade un método `_ground_csf` (Cloth Simulation
        Filter: pip install cloth-simulation-filter -> import CSF) o PMF/SMRF (PDAL),
        y regístralo en el `if` de abajo. Contrato: debe devolver una máscara
        booleana (N,) con True = terreno. El resto del pipeline no cambia.
        """
        self._require_points()
        if method == "grid":
            mask = self._ground_grid_min(**kw)
        elif method == "ransac":
            mask = self._ground_ransac(**kw)
        # elif method == "csf":
        #     mask = self._ground_csf(**kw)
        else:
            raise ValueError(f"Método desconocido: {method}")

        self.classification[:] = ASPRS_UNCLASSIFIED
        self.classification[mask] = ASPRS_GROUND
        self.mesh = None
        return {"ground": int(mask.sum()), "non_ground": int((~mask).sum())}

    def classify_auto(self, model_path: str | None = None, voxel: float | None = None) -> dict:
        """
        Clasificación por forma (terreno / estructura / vegetación) con el modelo de
        autoclass.py. Escribe códigos ASPRS: 2 suelo, 6 estructura, 5 vegetación.
        >>> Para otro criterio o más clases: ver autoclass.GROUPS y train_autoclass.py.
        """
        import os
        import autoclass
        self._require_points()
        path = model_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "models", "autoclass.joblib")
        if not os.path.isfile(path):
            raise RuntimeError("Falta el modelo (models/autoclass.joblib). Ejecuta train_autoclass.py.")
        bundle = autoclass.load_model(path)
        self.classification = autoclass.predict_points(self.points, bundle, voxel)
        self.mesh = None
        codes, counts = np.unique(self.classification, return_counts=True)
        n = dict(zip(codes.tolist(), counts.tolist()))
        return {"ground": n.get(2, 0), "structure": n.get(6, 0), "vegetation": n.get(5, 0),
                "non_ground": len(self.points) - n.get(2, 0), "voxel": voxel or bundle["voxel"],
                "model_val_acc": bundle.get("val_acc")}

    def analyze_structure(self, **kw) -> dict:
        """Agrupa la estructura (clase 6) y estima el eje del puente. Ver reconstruct.py."""
        import reconstruct
        self._require_points()
        if not (self.classification == 6).any():
            raise RuntimeError("No hay estructura clasificada. Usa la clasificación automática primero.")
        self.structure = reconstruct.structure_clusters(self.points, self.classification, **kw)
        r = self.structure
        return {"clusters": r.clusters,
                "axis": None if r.axis_dir is None else {
                    "angle_deg": r.axis_angle_deg, "pca_angle_deg": r.pca_angle_deg, "length": r.length, "width": r.width,
                    "center_real": (r.axis_center + self.origin).tolist()}}

    def extract_section(self, **kw) -> dict:
        """Sección transversal del tablero y líneas 3D (ver reconstruct.extract_section)."""
        import reconstruct
        if self.structure is None or not self.structure.clusters:
            raise RuntimeError("Agrupa la estructura y estima el eje primero.")
        self.section = reconstruct.extract_section(self.points, self.structure, **kw)
        sec = self.section
        png = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".section.png")
        reconstruct.save_section_png(sec, png)
        return {"contours": len(sec.polygons), "vertices": [len(p) for p in sec.polygons],
                "slabs": sec.n_slabs, "s_mid": sec.s_mid, "ends": sec.ends,
                "grade_coef": sec.grade.tolist(), "validation": sec.validation,
                "lines3d": len(sec.lines3d), "png": png}

    def build_elements(self, **kw) -> dict:
        """Separa la sección en losa / vigas / barandas y genera sólidos cerrados."""
        import reconstruct
        if self.section is None:
            raise RuntimeError("Extrae la sección primero.")
        self.elements = reconstruct.separate_elements(self.section, **kw)
        self.solids = reconstruct.build_solids(self.section, self.elements)
        return {"elements": [{"name": s.name, "ifc_class": s.ifc_class, "volume_m3": round(s.volume, 2),
                              "watertight": s.watertight, "triangles": int(len(s.triangles)),
                              "area_m2": round(e.area, 3)}
                             for s, e in zip(self.solids, self.elements)],
                "total_volume_m3": round(sum(s.volume for s in self.solids), 1)}

    def build_abutments(self, **kw) -> dict:
        """Estribos en ambos extremos del tablero (reconstruct.extract_abutments); se agregan a solids."""
        import reconstruct
        if self.section is None or self.structure is None:
            raise RuntimeError("Extrae la sección primero.")
        ab = reconstruct.extract_abutments(self.points, self.section, self.structure.width, **kw)
        if not ab:
            raise RuntimeError("No se detectó la cara frontal de los estribos.")
        self.solids = [s for s in self.solids if s.kind not in ("estribo", "muro", "apoyo")] + ab
        return {"abutments": [{"name": s.name, "volume_m3": round(s.volume, 2), "watertight": s.watertight,
                               **{k: (round(v, 3) if isinstance(v, float) else v)
                                  for k, v in s.metrics.items()}} for s in ab]}

    def _abutment_solids(self) -> list:
        ab = [s for s in self.solids if s.kind == "estribo"]
        if not ab or self.section is None:
            raise RuntimeError("Reconstruye los estribos primero.")
        return ab

    def build_wings(self, **kw) -> dict:
        """Muros de retorno de cada estribo (reconstruct.extract_wing_walls); se agregan a solids."""
        import reconstruct
        ab = self._abutment_solids()
        ww = reconstruct.extract_wing_walls(self.points, self.section, self.structure.width, ab, **kw)
        if not ww:
            raise RuntimeError("No se detectó la cara de ningún muro de retorno.")
        self.solids = [s for s in self.solids if s.kind != "muro"] + ww
        return {"wing_walls": [{"name": s.name, "volume_m3": round(s.volume, 2), "watertight": s.watertight,
                                "length_m": round(s.metrics["length_m"], 2),
                                "height_m": [round(h, 2) for h in s.metrics["height_m"]],
                                "t_face_m": round(s.metrics["t_face_m"], 3),
                                "face_points": s.metrics["face_points"]} for s in ww]}

    def build_bearings(self, **kw) -> dict:
        """Un apoyo bajo cada viga en cada estribo (reconstruct.extract_bearings)."""
        import reconstruct
        ab = self._abutment_solids()
        if not self.elements:
            raise RuntimeError("Separa los elementos primero.")
        br = reconstruct.extract_bearings(ab, self.section, self.elements, **kw)
        if not br:
            raise RuntimeError("No hay vigas para ubicar apoyos.")
        self.solids = [s for s in self.solids if s.kind != "apoyo"] + br
        return {"bearings": len(br), "watertight": f"{sum(s.watertight for s in br)}/{len(br)}",
                "height_m": sorted({round(s.metrics['height_m'], 3) for s in br}),
                "height_measured": all(s.metrics["height_measured"] for s in br),
                "t_centers_m": [round(s.metrics["t_center_m"], 2) for s in br if s.name.startswith("Apoyo inicial")]}

    def build_road(self, **kw) -> dict:
        """
        Vía de acceso a ambos lados del tablero (reconstruct.extract_road): separa de la clase
        terreno (2) los puntos que forman la calzada y los reclasifica a ASPRS 11; agrega los
        sólidos 'via'. Después, `build_terrain` ya no los usa (queda solo el terreno natural).
        """
        import reconstruct
        if self.section is None or self.structure is None:
            raise RuntimeError("Extrae la sección primero.")
        # si ya se corrió, devolver la vía anterior al terreno para repetir con otros parámetros
        self.classification[self.classification == ASPRS_ROAD] = ASPRS_GROUND
        c, d, n = self.section.frame
        (a_lo, k_lo), (a_hi, k_hi) = self.section.ends["lo"], self.section.ends["hi"]
        hw = self.structure.width / 2
        s_hi = max(a_hi + k_hi * hw, a_hi - k_hi * hw)
        s_lo = min(a_lo + k_lo * hw, a_lo - k_lo * hw)
        mask, solids, info = reconstruct.extract_road(
            self.points, self.classification == ASPRS_GROUND, (c, d, n), s_lo, s_hi, **kw)
        if not solids:
            raise RuntimeError("No se detectó la vía (sin plano continuo en el suelo junto al tablero).")
        self.classification[mask] = ASPRS_ROAD
        self.solids = [x for x in self.solids if x.kind != "via"] + solids
        return {"road_points": info["points"], "sides": {k: {m: round(v, 3) for m, v in d_.items()}
                                                         for k, d_ in info["sides"].items()},
                "solids": [{"name": x.name, "volume_m3": round(x.volume, 2), "watertight": x.watertight}
                           for x in solids]}

    # ------------------------------------------------------------------ líneas, cotas, medidas, DXF
    def cad_lines(self, edge_angle: float = 30.0, contour_interval: float = 1.0, kinds=None) -> dict:
        """
        Líneas aprovechables en CAD, en coords locales: eje, sección (lines3d), aristas vivas de cada
        sólido (por tipo) y curvas de nivel del terreno si hay malla (`contour_interval` > 0).
        """
        import dxf_export
        out = {}
        if self.section is not None:
            c, d, n = self.section.frame
            (a_lo, _), (a_hi, _) = self.section.ends["lo"], self.section.ends["hi"]
            ss = np.linspace(a_lo, a_hi, 25)
            xy = c[None, :] + ss[:, None] * d[None, :]
            out["EJE"] = [np.c_[xy, np.polyval(self.section.grade, ss)]]
            if self.section.lines3d:
                out["SECCION"] = [np.asarray(l, float) for l in self.section.lines3d]
        for s in self.solids:
            if kinds is not None and s.kind not in kinds:
                continue
            layer = dxf_export.KIND_LAYER.get(s.kind, s.kind.upper())
            out.setdefault(layer, []).extend(dxf_export.feature_edges(s.vertices, s.triangles, edge_angle))
        if self.mesh is not None and contour_interval and contour_interval > 0:
            out["TERRENO_CURVAS"] = dxf_export.contour_lines(self.mesh.vertices, self.mesh.triangles, contour_interval)
        return out

    def add_annotation(self, kind: str, **kw) -> dict:
        """cota {p, text}, medida {a, b}: puntos en coords locales. Devuelve la anotación con sus valores."""
        if kind == "cota":
            p = np.asarray(kw["p"], float)
            an = {"type": "cota", "p": p, "text": kw.get("text") or "", "id": len(self.annotations) + 1}
            an["e"], an["n"], an["z"] = (p + self.origin).tolist()
        elif kind == "medida":
            a, b = np.asarray(kw["a"], float), np.asarray(kw["b"], float)
            dv = b - a
            hz = float(np.hypot(dv[0], dv[1]))
            an = {"type": "medida", "a": a, "b": b, "id": len(self.annotations) + 1,
                  "dist3d": float(np.linalg.norm(dv)), "dist_h": hz, "dz": float(dv[2]),
                  "slope_pct": float(100 * dv[2] / hz) if hz > 1e-9 else None, "text": kw.get("text") or ""}
        else:
            raise ValueError("tipo de anotación: cota | medida")
        self.annotations.append(an)
        return an

    def export_dxf(self, path: str, coords: str = "utm", epsg: int | None = None, edge_angle: float = 30.0,
                   contour_interval: float = 1.0, terrain_mesh: bool = False, kinds=None) -> dict:
        """DXF con las líneas del modelo + cotas y medidas. coords: 'utm' (reales) | 'local'."""
        import dxf_export
        if epsg is None and self.crs is not None:
            try:
                epsg = self.crs.to_epsg()
            except Exception:
                epsg = None
        lines = self.cad_lines(edge_angle, contour_interval, kinds)
        if not any(lines.values()) and not self.annotations:
            raise RuntimeError("No hay líneas que exportar: separa elementos o genera el terreno primero.")
        mesh = (self.mesh.vertices, self.mesh.triangles) if (terrain_mesh and self.mesh is not None) else None
        return dxf_export.write_dxf(path, lines, self.annotations, self.origin, coords, epsg, mesh)

    def build_railings(self, **kw) -> dict:
        """Baranda por componentes: rieles continuos y postes. Reemplaza los sólidos 'baranda' toscos."""
        import reconstruct
        if not self.elements:
            raise RuntimeError("Separa los elementos primero.")
        rl = reconstruct.extract_railings(self.points, self.section, self.structure, self.elements, **kw)
        if not rl:
            raise RuntimeError("No se detectaron rieles.")
        self.solids = [s for s in self.solids if s.kind not in ("baranda", "riel", "poste")] + rl
        by = {}
        for s in rl:
            by.setdefault((s.metrics["side"], s.kind), []).append(s)
        return {"railings": {f"{side} {kind}": len(v) for (side, kind), v in by.items()},
                "post_spacing_median_m": {side: round(float(np.median(np.diff(sorted(x.metrics["s"] for x in v)))), 3)
                                          for (side, kind), v in by.items() if kind == "poste"},
                "rails": [{"name": s.name, "z_rel": [round(z, 3) for z in s.metrics["z_rel"]],
                           "t": [round(z, 3) for z in s.metrics["t"]]} for s in rl if s.kind == "riel"],
                "posts_rejected": {side: next((s.metrics.get("posts_rejected", 0) for s in rl if s.metrics["side"] == side), 0)
                                   for side in {s.metrics["side"] for s in rl}},
                "watertight": f"{sum(s.watertight for s in rl)}/{len(rl)}"}

    def export_ifc(self, path: str, version: str = "ALL", epsg: int | None = None,
                   include_terrain: bool = True, config: dict | None = None) -> dict:
        """
        Exporta los sólidos (y la malla de terreno) a IFC. `config` (de la ventana de exportación):
        versions, categories {tipo: {include, ifc_class, predefined_type}}, only_names, hierarchy,
        psets, qto, custom, georef, validate. predefined_type None = el predeterminado de la categoría,
        "" = sin tipo, otro texto = ese tipo (se omite en las versiones donde no es válido).
        """
        import ifc_export
        if not getattr(self, "solids", None):
            raise RuntimeError("Genera los sólidos primero.")
        cfg = {"versions": None, "categories": {}, "only_names": None, "hierarchy": True, "psets": True,
               "qto": True, "custom": True, "georef": True, "validate": False}
        cfg.update(config or {})
        if epsg is None and self.crs is not None:
            try:
                epsg = self.crs.to_epsg()
            except Exception:
                epsg = None
        only = cfg["only_names"]

        def wanted(kind, name):
            return cfg["categories"].get(kind, {}).get("include", True) is not False and (only is None or name in only)

        items = []
        for s in self.solids:
            if not wanted(s.kind, s.name):
                continue
            cat = cfg["categories"].get(s.kind, {})
            meta = dict(IFC_CATEGORY.get(s.kind, {}))
            if s.kind == "estribo":     # cada estribo es una parte ABUTMENT propia
                meta["part"] = ["SUBSTRUCTURE:Subestructura", f"ABUTMENT:{s.name}"]
            elif s.kind in ("muro", "apoyo"):   # muros de retorno y apoyos van en la parte de su estribo
                meta["part"] = ["SUBSTRUCTURE:Subestructura", f"ABUTMENT:{s.metrics['abutment']}"]
            it = {"name": s.name, "ifc_class": cat.get("ifc_class") or s.ifc_class,
                  "vertices": s.vertices + self.origin, "triangles": s.triangles, "volume": s.volume,
                  "closed": s.watertight, **meta, "assumptions": ASSUMPTIONS.get(s.kind, "")}
            if cat.get("predefined_type") is not None:
                it["predefined_type"] = cat["predefined_type"] or None
            items.append(it)
        if include_terrain and self.mesh is not None and wanted("terreno", "Terreno"):
            cat = cfg["categories"].get("terreno", {})
            pt = cat.get("predefined_type")
            items.append({"name": "Terreno", "ifc_class": cat.get("ifc_class") or "IfcGeographicElement",
                          "vertices": self.mesh.vertices + self.origin, "triangles": self.mesh.triangles,
                          "predefined_type": "TERRAIN" if pt is None else (pt or None)})
        if not items:
            raise RuntimeError("Ningún elemento seleccionado para exportar.")

        base, ext = os.path.splitext(path)
        versions = cfg["versions"] or (list(ifc_export.SCHEMAS) if version.upper() == "ALL" else [version])
        out, validation = {}, {}
        for v in versions:
            target = f"{base}_{v.upper()}.ifc" if len(versions) > 1 else path
            ifc_export.export_elements_ifc(items, target, v, epsg, options=cfg)
            out[v.upper()] = target
            if cfg["validate"]:
                import ifcopenshell
                import ifcopenshell.validate as val
                lg = val.json_logger()
                val.validate(ifcopenshell.open(target), lg)
                validation[v.upper()] = len(lg.statements)
        res = {"files": out, "epsg": epsg, "elements": len(items)}
        if validation:
            res["validation_issues"] = validation
        return res

    def _ground_grid_min(self, cell_size: float = 2.0, height_tol: float = 0.5) -> np.ndarray:
        """
        Filtro de altura por celdas: terreno = puntos a <= height_tol sobre el mínimo
        de su celda. Funciona en terreno ondulado (a diferencia de un plano único).
        Limitaciones: pendiente fuerte dentro de una celda -> reducir cell_size;
        un outlier bajo en la celda sesga el mínimo (filtrar ruido antes).
        """
        p = self.points
        ij = np.floor((p[:, :2] - p[:, :2].min(axis=0)) / cell_size).astype(np.int64)
        key = ij[:, 0] * (ij[:, 1].max() + 1) + ij[:, 1]
        _, inv = np.unique(key, return_inverse=True)
        inv = inv.ravel()
        zmin = np.full(inv.max() + 1, np.inf)
        np.minimum.at(zmin, inv, p[:, 2])
        return (p[:, 2] - zmin[inv]) <= height_tol

    def _ground_ransac(self, distance: float = 0.3, n_iter: int = 2000) -> np.ndarray:
        """Un único plano global (RANSAC Open3D). Solo válido para terreno casi plano."""
        pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(self.points))
        _, inliers = pcd.segment_plane(distance_threshold=distance, ransac_n=3,
                                       num_iterations=n_iter)
        mask = np.zeros(len(self.points), dtype=bool)
        mask[inliers] = True
        return mask

    # ----------------------------------------------------------- superficie
    def generate_surface(self, method: str = "delaunay", voxel_size: float = 0.0,
                         max_edge: float = 0.0, poisson_depth: int = 9) -> dict:
        """
        Malla a partir SOLO de puntos de terreno.
        - 'delaunay': TIN 2.5D (triangulación en XY, Z como atributo). Método
          topográfico estándar: respeta los puntos exactos, sin suavizado.
        - 'poisson': superficie implícita suavizada. No interpola exacto y es poco
          adecuada para terreno abierto (superficie 2.5D, no cerrada).
        voxel_size > 0 diezma el terreno antes de triangular (0 = sin diezmar).
        max_edge > 0 elimina triángulos con arista mayor (huecos, bordes falsos).
        """
        self._require_points()
        ground = self.points[self.classification == ASPRS_GROUND]
        if len(ground) < 3:
            raise ValueError("Sin puntos de terreno. Clasifica primero.")

        if voxel_size > 0:
            pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(ground))
            ground = np.asarray(pcd.voxel_down_sample(voxel_size).points)

        if method == "delaunay":
            self.mesh = self._tin_delaunay(ground, max_edge)
        elif method == "poisson":
            self.mesh = self._poisson(ground, poisson_depth)
        else:
            raise ValueError(f"Método desconocido: {method}")
        return {"vertices": len(self.mesh.vertices), "triangles": len(self.mesh.triangles)}

    def build_terrain(self, voxel: float = 0.5, max_edge: float = 4.0, clip_structure: bool = True,
                      target_triangles: int = 0, check_samples: int = 300_000,
                      outlier_cell: float = 1.0, outlier_tol: float = 2.0, roi_margin: float = 40.0) -> dict:
        """
        Terreno depurado para exportar a IFC (metodología en DOCUMENTACION, Paso 13):
        1) puntos: solo la clase terreno (2); la estructura y la vegetación quedan fuera. Si hay eje de
           estructura y `roi_margin` > 0, solo los que están a menos de `roi_margin` m del tablero (lejos
           de la obra la clasificación por forma deja copas y ramas dentro del terreno);
        2) diezmado a un punto por celda de `voxel` m (promedio) para una malla manejable, y depuración
           de outliers (copas y ramas clasificadas como terreno): en una rejilla de `outlier_cell` m se
           toma la cota mínima de cada celda y se descartan los puntos más de `outlier_tol` m por encima
           del mínimo de su vecindario 3x3;
        3) TIN 2.5D de Delaunay y descarte de triángulos con arista > `max_edge` m (cierra los vacíos
           donde no hay terreno medido, p. ej. bajo el tablero, en vez de cubrirlos);
        4) `clip_structure`: se quitan los triángulos cuyo centro cae en la huella (envolvente convexa en
           planta) de losa, estribos o muros y que están por encima de su cota inferior (el terreno no
           atraviesa los sólidos); el terreno bajo el tablero se conserva;
        5) `target_triangles` > 0: simplificación cuadrática si la malla es mayor;
        6) verificación contra hasta `check_samples` puntos de terreno medidos: error vertical de la malla.
        """
        from matplotlib.path import Path as MplPath
        from scipy.spatial import ConvexHull
        self._require_points()
        ground = self.points[self.classification == ASPRS_GROUND]
        if len(ground) < 3:
            raise ValueError("Sin puntos de terreno. Clasifica primero.")
        n_ground = len(ground)
        if roi_margin > 0 and self.section is not None and self.structure is not None:
            cen, dv, nv = self.section.frame
            q = ground[:, :2] - cen
            s_, t_ = q @ dv, q @ nv
            (a_lo, _), (a_hi, _) = self.section.ends["lo"], self.section.ends["hi"]
            ground = ground[(s_ > a_lo - roi_margin) & (s_ < a_hi + roi_margin)
                            & (np.abs(t_) < self.structure.width / 2 + roi_margin)]
            n_ground = len(ground)
            if n_ground < 3:
                raise ValueError("Sin puntos de terreno en el entorno de la estructura.")
        pts = ground
        if voxel > 0:
            pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(ground))
            pts = np.asarray(pcd.voxel_down_sample(voxel).points)
        from scipy.ndimage import minimum_filter
        ij = np.floor((pts[:, :2] - pts[:, :2].min(axis=0)) / outlier_cell).astype(np.int64)
        shape = ij.max(axis=0) + 1
        cmin = np.full(shape, np.inf)
        np.minimum.at(cmin, (ij[:, 0], ij[:, 1]), pts[:, 2])
        ref = minimum_filter(cmin, size=3, mode="nearest")
        keep = pts[:, 2] <= ref[ij[:, 0], ij[:, 1]] + outlier_tol
        removed_out = int((~keep).sum())
        pts = pts[keep]
        mesh = self._tin_delaunay(pts, 0.0)
        v, tris = mesh.vertices, mesh.triangles
        n_all = len(tris)
        a, b, c = v[tris[:, 0], :2], v[tris[:, 1], :2], v[tris[:, 2], :2]
        longest = np.maximum.reduce([np.linalg.norm(a - b, axis=1), np.linalg.norm(b - c, axis=1),
                                     np.linalg.norm(c - a, axis=1)])
        tris = tris[longest <= max_edge]
        removed_edge = n_all - len(tris)
        removed_struct = 0
        if clip_structure and self.solids:
            cen = v[tris].mean(axis=1)
            drop = np.zeros(len(tris), bool)
            for s in self.solids:
                if s.kind not in ("losa", "estribo", "muro"):
                    continue
                xy = s.vertices[:, :2]
                hull = xy[ConvexHull(xy).vertices]
                inside = MplPath(hull).contains_points(cen[:, :2])
                drop |= inside & (cen[:, 2] > s.vertices[:, 2].min() - 0.05)
            removed_struct = int(drop.sum())
            tris = tris[~drop]
        if target_triangles and len(tris) > target_triangles:
            m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(v - v.mean(axis=0)),
                                          o3d.utility.Vector3iVector(tris.astype(np.int32)))
            m = m.simplify_quadric_decimation(int(target_triangles))
            m.remove_unreferenced_vertices()
            v = np.asarray(m.vertices) + v.mean(axis=0)
            tris = np.asarray(m.triangles).astype(np.int64)
        used = np.unique(tris)
        remap = np.full(len(v), -1, np.int64)
        remap[used] = np.arange(len(used))
        v, tris = v[used], remap[tris]
        # verificación: error vertical contra puntos de terreno medidos
        rng = np.random.default_rng(0)
        samp = ground[rng.choice(n_ground, min(check_samples, n_ground), replace=False)]
        d1, d2 = v[tris[:, 1], :2] - v[tris[:, 0], :2], v[tris[:, 2], :2] - v[tris[:, 0], :2]
        cw = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0] < 0       # matplotlib exige triángulos antihorarios en planta
        tri_ccw = tris.copy()
        tri_ccw[cw] = tris[cw][:, ::-1]
        tris = tri_ccw                                            # normales hacia arriba
        self.mesh = MeshResult(v, tris)
        zi = self._mesh_z_at(v, tris, samp[:, :2])
        ok = ~np.isnan(zi)
        dz = samp[ok, 2] - zi[ok]
        e = np.stack([np.linalg.norm(v[tris[:, i]] - v[tris[:, (i + 1) % 3]], axis=1) for i in range(3)], axis=1)
        n3 = np.cross(v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]])
        area3 = 0.5 * np.linalg.norm(n3, axis=1)
        slope = np.degrees(np.arccos(np.clip(np.abs(n3[:, 2]) / (np.linalg.norm(n3, axis=1) + 1e-12), 0, 1)))
        return {"ground_points": int(n_ground), "removed_outliers": removed_out, "vertices": int(len(v)), "triangles": int(len(tris)),
                "removed_long_edge": int(removed_edge), "removed_in_structure": removed_struct,
                "area_m2": round(float(area3.sum()), 1), "z_range_m": [round(float(v[:, 2].min() + self.origin[2]), 2),
                                                                      round(float(v[:, 2].max() + self.origin[2]), 2)],
                "edge_m": {"p50": round(float(np.median(e)), 2), "p95": round(float(np.percentile(e, 95)), 2),
                           "max": round(float(e.max()), 2)},
                "slope_deg": {"p50": round(float(np.median(slope)), 1), "p95": round(float(np.percentile(slope, 95)), 1)},
                "check": {"samples": int(ok.sum()), "covered_pct": round(float(ok.mean() * 100), 1),
                          "rms_dz_m": round(float(np.sqrt((dz ** 2).mean())), 3), "median_abs_dz_m": round(float(np.median(np.abs(dz))), 3),
                          "p95_abs_dz_m": round(float(np.percentile(np.abs(dz), 95)), 3),
                          "max_abs_dz_m": round(float(np.abs(dz).max()), 3)}}

    @staticmethod
    def _mesh_z_at(v: np.ndarray, tris: np.ndarray, xy: np.ndarray, k: int = 12) -> np.ndarray:
        """Cota de la malla en cada (x, y) por coordenadas baricéntricas (NaN fuera de la malla)."""
        from scipy.spatial import cKDTree
        cen = v[tris][:, :, :2].mean(axis=1)
        _, cand = cKDTree(cen).query(xy, k=k)
        out = np.full(len(xy), np.nan)
        for j in range(k):
            todo = np.isnan(out)
            if not todo.any():
                break
            t = tris[cand[todo, j]]
            p = xy[todo]
            a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
            den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
            l1 = ((b[:, 1] - c[:, 1]) * (p[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[:, 1] - c[:, 1])) / den
            l2 = ((c[:, 1] - a[:, 1]) * (p[:, 0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[:, 1] - c[:, 1])) / den
            l3 = 1 - l1 - l2
            hit = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
            z = l1 * a[:, 2] + l2 * b[:, 2] + l3 * c[:, 2]
            idx = np.flatnonzero(todo)[hit]
            out[idx] = z[hit]
        return out

    def _tin_delaunay(self, pts: np.ndarray, max_edge: float) -> MeshResult:
        # Delaunay 2D sobre XY (z viaja como coordenada del vértice)
        surf = pv.PolyData(pts).delaunay_2d()
        tris = surf.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
        v = np.asarray(surf.points, dtype=np.float64)
        if max_edge > 0:
            a, b, c = v[tris[:, 0], :2], v[tris[:, 1], :2], v[tris[:, 2], :2]
            longest = np.maximum.reduce([np.linalg.norm(a - b, axis=1),
                                         np.linalg.norm(b - c, axis=1),
                                         np.linalg.norm(c - a, axis=1)])
            tris = tris[longest <= max_edge]
        # EXTENSIÓN: breaklines (quiebres, ejes de vía) -> Delaunay restringida
        # (librería `triangle` o CGAL); curvas de nivel -> contour sobre la malla;
        # volúmenes/cortes-rellenos -> diferencia entre dos TIN.
        return MeshResult(v, tris)

    def _poisson(self, pts: np.ndarray, depth: int) -> MeshResult:
        pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=3.0, max_nn=30))
        pcd.orient_normals_to_align_with_direction(np.array([0.0, 0.0, 1.0]))
        mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth)
        # Quita 5 % de menor densidad (lengüetas que Poisson inventa fuera de datos)
        mesh.remove_vertices_by_mask(np.asarray(dens) < np.quantile(dens, 0.05))
        mesh = mesh.crop(pcd.get_axis_aligned_bounding_box())
        return MeshResult(np.asarray(mesh.vertices), np.asarray(mesh.triangles).astype(np.int64))

    # ------------------------------------------------------------- suavizado
    def smooth_mesh(self, method: str = "taubin", iterations: int = 10,
                    lambda_filter: float = 0.5, mu: float = -0.53,
                    preserve_xy: bool = True) -> dict:
        """
        Suaviza la malla con filtros de Open3D (simple / laplacian / taubin).
        preserve_xy=True mantiene X,Y y solo suaviza Z (evita encoger bordes).
        Aleja la malla de los puntos medidos: se devuelve cuánto cambió Z.
        """
        if self.mesh is None or len(self.mesh.triangles) == 0:
            raise RuntimeError("Genera la superficie primero.")
        before = self.mesh.vertices
        work = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(before - before.mean(axis=0)),
                                         o3d.utility.Vector3iVector(self.mesh.triangles))
        if method == "simple":
            out = work.filter_smooth_simple(number_of_iterations=iterations)
        elif method == "laplacian":
            out = work.filter_smooth_laplacian(number_of_iterations=iterations,
                                               lambda_filter=lambda_filter)
        elif method == "taubin":
            out = work.filter_smooth_taubin(number_of_iterations=iterations,
                                            lambda_filter=lambda_filter, mu=mu)
        else:
            raise ValueError(f"Método desconocido: {method}")
        # Vértices sin triángulos (quedan al filtrar con arista máx.) dan NaN: no se tocan.
        used = np.zeros(len(before), dtype=bool)
        used[np.asarray(self.mesh.triangles).ravel()] = True
        after = before.copy()
        after[used] = (np.asarray(out.vertices) + before.mean(axis=0))[used]
        if not np.isfinite(after[used]).all():
            raise RuntimeError("El suavizado produjo valores no finitos; malla sin cambios.")
        if preserve_xy:
            after[:, :2] = before[:, :2]
        dz = after[:, 2] - before[:, 2]
        self.mesh = MeshResult(after, self.mesh.triangles)
        return {"rms_dz": float(np.sqrt((dz ** 2).mean())), "max_dz": float(np.abs(dz).max())}

    # -------------------------------------------------------------- análisis
    def analyze(self) -> dict:
        """Resumen cuantitativo de la nube (densidad, espaciado, Z, clases, ruido)."""
        self._require_points()
        p = self.points
        ext = p.max(axis=0)
        area = max(ext[0] * ext[1], 1e-9)
        dens = len(p) / area
        z = p[:, 2] + self.origin[2]
        pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(
            p[self.display_sample(200_000)]))
        nn = np.asarray(pcd.compute_nearest_neighbor_distance())
        scale = len(p) / max(len(nn), 1)
        codes, counts = np.unique(self.classification, return_counts=True)
        return {
            "n_points": int(len(p)),
            "extent_xyz": [float(v) for v in ext],
            "area_bbox": float(area),
            "density_pts_m2": float(dens),
            "spacing_median_m": float(np.median(nn) / np.sqrt(scale)),  # corrige el efecto de la muestra
            "z_percentiles": {str(q): float(np.percentile(z, q)) for q in (0, 1, 5, 50, 95, 99, 100)},
            "classes": {int(c): int(n) for c, n in zip(codes, counts)},
            "mesh": None if self.mesh is None else {
                "vertices": int(len(self.mesh.vertices)), "triangles": int(len(self.mesh.triangles))},
        }

    # ----------------------------------------------------------- utilidades
    def display_sample(self, max_points: int = 2_000_000) -> np.ndarray:
        """Índices para render (diezmado aleatorio si la nube es enorme)."""
        n = len(self.points)
        if n <= max_points:
            return np.arange(n)
        return np.random.default_rng(0).choice(n, max_points, replace=False)

    def _require_points(self) -> None:
        if self.points is None:
            raise RuntimeError("Carga un archivo .LAS primero.")
````

## 3.3 `reconstruct.py`

Estructura, eje, sección apilada, elementos, sólidos, estribos, barandas, muros de retorno, apoyos y vía. 1073 líneas.

````python
"""
reconstruct.py
Pasos 1-2 de la reconstrucción: agrupar la estructura en conjuntos conectados y estimar
el eje del puente.

>>> PUNTO DE INTEGRACIÓN <<<
Los pasos siguientes (planos RANSAC, secciones transversales apiladas, separación de
elementos, extrusión) parten de `StructureResult`: labels por punto + eje (centro, dirección).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

import autoclass


@dataclass
class StructureResult:
    labels: np.ndarray                    # (N,) int32: grupo de cada punto (-1 = no estructura/descartado)
    clusters: list = field(default_factory=list)   # tabla por grupo (ordenada por tamaño)
    axis_center: np.ndarray = None        # (3,) coords locales (restar origin para real)
    axis_dir: np.ndarray = None           # (3,) unitario en XY (z = 0)
    axis_angle_deg: float = 0.0           # azimut desde +X, antihorario
    length: float = 0.0
    width: float = 0.0
    pca_angle_deg: float = 0.0            # dirección inicial (PCA), solo informativa


def structure_clusters(points: np.ndarray, classification: np.ndarray, struct_code: int = 6,
                       voxel: float = 0.15, link: float = 0.35, min_voxels: int = 300,
                       max_clusters: int = 12) -> StructureResult:
    """
    Une vóxeles de estructura que están a <= `link` m entre sí (componentes conectados).
    Los grupos con menos de `min_voxels` vóxeles se descartan (ruido y falsos positivos).
    """
    idx = np.flatnonzero(classification == struct_code)
    labels = np.full(len(points), -1, dtype=np.int32)
    if len(idx) == 0:
        return StructureResult(labels)
    cen, inv = autoclass.voxelize(points[idx], voxel)
    n = len(cen)
    pairs = cKDTree(cen).query_pairs(link, output_type="ndarray")
    g = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, comp = connected_components(g, directed=False)
    sizes = np.bincount(comp)
    order = np.argsort(-sizes)
    order = [c for c in order if sizes[c] >= min_voxels][:max_clusters]
    remap = np.full(len(sizes), -1, dtype=np.int32)
    for new, old in enumerate(order):
        remap[old] = new
    labels[idx] = remap[comp][inv]

    clusters = []
    mn0 = points[idx].min(axis=0)
    for new, old in enumerate(order):
        m = comp == old
        c = cen[m]
        ext = c.max(0) - c.min(0)
        clusters.append({"id": new, "voxels": int(m.sum()),
                         "points": int((labels == new).sum()),
                         "bbox_xyz": [float(v) for v in ext],
                         "centroid_local": [float(v) for v in c.mean(0) + mn0]})
    res = StructureResult(labels, clusters)
    if clusters:
        _fit_axis(points, res)
    return res


def _sharpness(s_coord: np.ndarray, bin_m: float = 0.05) -> float:
    """Nitidez de picos del histograma transversal: sube cuando las líneas longitudinales alinean."""
    h, _ = np.histogram(s_coord, bins=np.arange(s_coord.min(), s_coord.max() + bin_m, bin_m))
    return float((h.astype(np.float64) ** 2).sum())


def _fit_axis(points: np.ndarray, res: StructureResult) -> None:
    """
    Eje del puente en planta.
    1) PCA sobre XY del grupo mayor da una dirección inicial (sesgada por los extremos esviados).
    2) Se afina girando +-8 grados: el ángulo bueno es el que hace más nítidos los picos del
       histograma transversal (barandas, sardineles y vigas son líneas paralelas al eje).
    3) El centro sale de los extremos (percentiles), no de la densidad de puntos.
    """
    pts = points[res.labels == 0]
    if len(pts) > 2_000_000:
        pts = pts[np.random.default_rng(0).choice(len(pts), 2_000_000, replace=False)]
    p = pts[:, :2]
    c0 = p.mean(axis=0)
    w, v = np.linalg.eigh(np.cov((p - c0).T))
    a0 = np.arctan2(v[1, 1], v[0, 1])

    def score(a):
        n = np.array([-np.sin(a), np.cos(a)])
        return _sharpness((p - c0) @ n)

    coarse = np.radians(np.arange(-8.0, 8.01, 0.25))
    best = a0 + coarse[int(np.argmax([score(a0 + da) for da in coarse]))]
    fine = best + np.radians(np.arange(-0.3, 0.301, 0.02))
    a = fine[int(np.argmax([score(x) for x in fine]))]

    d = np.array([np.cos(a), np.sin(a)])
    if d[0] < 0 or (abs(d[0]) < 1e-9 and d[1] < 0):
        d = -d
    n = np.array([-d[1], d[0]])
    t, sv = (p - c0) @ d, (p - c0) @ n
    t_lo, t_hi = np.percentile(t, [0.5, 99.5])
    s_lo, s_hi = np.percentile(sv, [0.5, 99.5])
    ctr = c0 + d * (t_lo + t_hi) / 2 + n * (s_lo + s_hi) / 2
    res.axis_dir = np.array([d[0], d[1], 0.0])
    res.axis_center = np.array([ctr[0], ctr[1], float(pts[:, 2].mean())])
    res.axis_angle_deg = float(np.degrees(np.arctan2(d[1], d[0])))
    res.pca_angle_deg = float(np.degrees(a0))
    res.length = float(t_hi - t_lo)
    res.width = float(s_hi - s_lo)


# ---------------------------------------------------------------- sección transversal
@dataclass
class SectionResult:
    polygons: list = field(default_factory=list)   # cada (K,2) [t, z_rel], cerrado
    grade: np.ndarray = None                       # coeficientes polyfit de z_tope(s)
    ends: dict = field(default_factory=dict)       # s = a + k*t en extremo inicial y final
    s_mid: tuple = (0.0, 0.0)                      # tramo usado para la sección
    n_slabs: int = 0
    raster: np.ndarray = None                      # conteo de cortes con punto (t, z)
    extent: tuple = (0, 0, 0, 0)                   # t0, t1, z0, z1
    lines3d: list = field(default_factory=list)    # polilíneas (M,3) en coords locales
    validation: dict = field(default_factory=dict)
    frame: tuple = None                            # c(2), d(2), n(2)
    mask: np.ndarray = None                        # máscara rellena (t, z_rel)
    cell: float = 0.02


def _robust_poly(x, y, deg=2, it=4, k=3.0):
    m = np.ones(len(x), bool)
    for _ in range(it):
        c = np.polyfit(x[m], y[m], deg)
        r = y - np.polyval(c, x)
        mad = np.median(np.abs(r[m] - np.median(r[m]))) * 1.4826 + 1e-6
        m = np.abs(r) < k * mad
    return np.polyfit(x[m], y[m], deg)


def _group_stats(keys, values, min_n, fn):
    """Aplica fn(values del grupo) por cada valor de keys (enteros) con >= min_n elementos."""
    order = np.argsort(keys, kind="stable")
    ks, vs = keys[order], values[order]
    edges = np.r_[0, np.flatnonzero(np.diff(ks)) + 1, len(ks)]
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a >= min_n:
            out.append((ks[a], fn(vs[a:b])))
    return out


def extract_section(points, res: StructureResult, cell: float = 0.02, slab: float = 0.25,
                    occ_frac: float = 0.15, blur: float = 0.7, simplify: float = 0.02, max_pts: int = 8_000_000,
                    station_step: float = 5.0) -> SectionResult:
    """
    Sección transversal del tablero por apilado de cortes a lo largo del eje.
    1) Marco (s a lo largo, t transversal, z). 2) Rasante z(s) del tope (polinomio robusto);
    z_rel = z - rasante evita que la pendiente difumine la sección. 3) Extremos esviados: recta
    s = a + k t en cada extremo. 4) Se apilan cortes de `slab` m en el tramo central; una celda
    (t, z_rel) es superficie si tiene punto en >= occ_frac de los cortes. 5) Cierre + relleno +
    contorno + simplificación -> polígonos. 6) Líneas 3D: cada vértice se barre a lo largo del
    eje entre los extremos esviados.
    """
    from scipy import ndimage as ndi
    from skimage.measure import approximate_polygon, find_contours

    pts = points[res.labels == 0]
    if len(pts) > max_pts:
        pts = pts[np.random.default_rng(0).choice(len(pts), max_pts, replace=False)]
    c, d = res.axis_center[:2], res.axis_dir[:2]
    n = np.array([-d[1], d[0]])
    q = pts[:, :2] - c
    s, t, z = q @ d, q @ n, pts[:, 2]
    W = res.width

    # rasante: percentil 99 de z por tramo de 0.5 m en una franja central angosta (|t| < 0.5 m):
    # ahí el tope de la losa es lo más alto; una franja ancha o un percentil bajo mezcla vigas y
    # pendiente transversal (probado: residuo 68 cm con p90 en franja ancha, 1 cm con p99 en 0.5 m)
    cen = np.abs(t) < 0.5
    smin = s.min()
    kb = np.floor((s[cen] - smin) / 0.5).astype(np.int64)
    res_b = _group_stats(kb, z[cen], 50, lambda v: np.percentile(v, 99))
    xs = np.array([smin + (k + 0.5) * 0.5 for k, _ in res_b])
    ys = np.array([v for _, v in res_b])
    grade = _robust_poly(xs, ys, deg=3)
    zr = z - np.polyval(grade, s)

    # tope real por t (la losa tiene pendiente transversal): percentil 90 de z_rel por franja de
    # 0.25 m en el tramo central; luego se toman los puntos a +-0.10 m de ese tope
    s5, s95 = np.percentile(s, [20, 80])
    mid = (s > s5) & (s < s95) & (zr < 0.4) & (zr > -0.8)
    tk = np.floor(t[mid] / 0.25).astype(np.int64)
    tops = _group_stats(tk, zr[mid], 200, lambda v: np.percentile(v, 98))
    tx = np.array([(k + 0.5) * 0.25 for k, _ in tops])
    tz_ = np.array([v for _, v in tops])
    ztop = np.interp(t, tx, tz_)
    top = (np.abs(zr - ztop) < 0.10) & (np.abs(t) < 0.45 * W)
    tb = np.floor(t[top] / 0.5).astype(np.int64)
    st, tt_ = s[top], t[top]
    lo_b = _group_stats(tb, st, 100, lambda v: np.percentile(v, 0.5))
    hi_b = _group_stats(tb, st, 100, lambda v: np.percentile(v, 99.5))
    t_b = _group_stats(tb, tt_, 100, lambda v: v.mean())
    tcs = np.array([v for _, v in t_b])
    k_lo, a_lo = np.polyfit(tcs, [v for _, v in lo_b], 1)
    k_hi, a_hi = np.polyfit(tcs, [v for _, v in hi_b], 1)
    ends = {"lo": (float(a_lo), float(k_lo)), "hi": (float(a_hi), float(k_hi))}
    t_ext = np.array([-W / 2, W / 2])
    s_mid = (float((a_lo + k_lo * t_ext).max() + 0.5), float((a_hi + k_hi * t_ext).min() - 0.5))

    # apilar cortes del tramo central
    m = (s > s_mid[0]) & (s < s_mid[1])
    sm, tm, zm = s[m], t[m], zr[m]
    t0, t1 = np.percentile(tm, [0.05, 99.95])
    z0, z1 = np.percentile(zm, [0.05, 99.95])
    nt, nz = int((t1 - t0) / cell) + 1, int((z1 - z0) / cell) + 1
    ok = (tm >= t0) & (tm <= t1) & (zm >= z0) & (zm <= z1)
    si = np.floor((sm[ok] - s_mid[0]) / slab).astype(np.int64)
    ti = np.floor((tm[ok] - t0) / cell).astype(np.int64)
    zi = np.floor((zm[ok] - z0) / cell).astype(np.int64)
    n_slabs = int(si.max()) + 1
    uk = np.unique((si * nt + ti) * nz + zi)      # una vez por (corte, celda)
    raster = np.bincount(uk % (nt * nz), minlength=nt * nz).reshape(nt, nz)
    # las líneas se difuminan unos cm entre cortes: se suaviza la frecuencia antes de umbralar
    freq = ndi.gaussian_filter(raster / n_slabs, blur)
    occ = freq >= occ_frac

    # cierre, relleno y contorno
    closed = ndi.binary_dilation(occ, iterations=3)
    filled = ndi.binary_erosion(ndi.binary_fill_holes(closed), iterations=3)
    lab, nl = ndi.label(filled)
    sizes = ndi.sum(filled, lab, range(1, nl + 1))
    keep = [i + 1 for i, sz in enumerate(sizes) if sz * cell * cell > 0.05]
    filled = np.isin(lab, keep)
    polys = []
    for cnt in find_contours(np.pad(filled, 1).astype(float), 0.5):
        cnt = approximate_polygon(cnt - 1, simplify / cell)
        if len(cnt) >= 4:
            polys.append(np.column_stack((t0 + cnt[:, 0] * cell, z0 + cnt[:, 1] * cell)))

    # validación: distancia de puntos del tramo central al contorno
    val = {}
    if polys:
        bnd = []
        for pl in polys:
            for a, b in zip(pl[:-1], pl[1:]):
                kseg = max(2, int(np.hypot(*(b - a)) / 0.01))
                bnd.append(a + (b - a) * np.linspace(0, 1, kseg)[:, None])
        bnd = np.vstack(bnd)
        sub = np.random.default_rng(1).choice(len(tm), min(300_000, len(tm)), replace=False)
        dist, _ = cKDTree(bnd).query(np.column_stack((tm[sub], zm[sub])))
        near = dist < 0.30      # puntos de otros elementos (estribos, retornos) quedan fuera de la métrica
        val = {"p50_cm": float(np.percentile(dist[near], 50) * 100),
               "p90_cm": float(np.percentile(dist[near], 90) * 100),
               "pct_dentro_5cm": float((dist < 0.05).mean() * 100),
               "pct_dentro_10cm": float((dist < 0.10).mean() * 100),
               "pct_lejos_30cm": float((~near).mean() * 100)}

    # líneas 3D: barrido de cada vértice entre los extremos esviados
    lines = []
    for pl in polys:
        ring_lo, ring_hi = [], []
        for tv, zv in pl:
            s0, s1 = a_lo + k_lo * tv, a_hi + k_hi * tv
            S = np.linspace(s0, s1, max(2, int((s1 - s0) / station_step) + 1))
            xy = c[None, :] + S[:, None] * d[None, :] + tv * n[None, :]
            lines.append(np.column_stack((xy, np.polyval(grade, S) + zv)))
            ring_lo.append(lines[-1][0])
            ring_hi.append(lines[-1][-1])
        lines.append(np.array(ring_lo))
        lines.append(np.array(ring_hi))

    return SectionResult(polys, grade, ends, s_mid, n_slabs, raster, (t0, t1, z0, z1), lines, val,
                         (c, d, n), filled, cell)


def save_section_png(sec: SectionResult, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t0, t1, z0, z1 = sec.extent
    fig, ax = plt.subplots(figsize=(13, 5), dpi=110)
    ax.imshow(np.log1p(sec.raster.T), origin="lower", extent=(t0, t1, z0, z1),
              cmap="gray_r", aspect="equal")
    for pl in sec.polygons:
        ax.plot(pl[:, 0], pl[:, 1], "r-", lw=1.4)
        ax.plot(pl[:, 0], pl[:, 1], "b.", ms=3)
    ax.set_xlabel("t (m, transversal)")
    ax.set_ylabel("z relativo a rasante (m)")
    ax.set_title(f"Sección apilada ({sec.n_slabs} cortes): {len(sec.polygons)} contornos | "
                 f"p50 {sec.validation.get('p50_cm', 0):.1f} cm  p90 {sec.validation.get('p90_cm', 0):.1f} cm")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------- separación de elementos y sólidos
@dataclass
class Element:
    name: str
    kind: str                 # "losa" | "viga" | "baranda"
    ifc_class: str            # IfcSlab | IfcBeam | IfcRailing
    polygon: np.ndarray       # (K,2) [t, z_rel], antihorario
    area: float = 0.0


@dataclass
class Solid:
    name: str
    kind: str
    ifc_class: str
    vertices: np.ndarray      # coords locales (restar origin para reales)
    triangles: np.ndarray
    volume: float = 0.0
    watertight: bool = False
    metrics: dict = field(default_factory=dict)


IFC_CLASS = {"losa": "IfcSlab", "viga": "IfcBeam", "baranda": "IfcRailing"}


def _poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def separate_elements(sec: SectionResult, simplify: float = 0.02, bar_height: float = 0.45,
                      beam_depth: float = 0.9, min_area: float = 0.03) -> list:
    """
    Separa el contorno apilado en losa, vigas y barandas con reglas sobre la máscara (t, z_rel):
    - tope de losa ZT(t): recta robusta sobre la zona central (la losa tiene pendiente transversal);
    - baranda = material a más de `bar_height` m sobre ZT(t);
    - viga = material bajo el intradós de la losa; columnas con espesor > `beam_depth` son vigas y
      el intradós se interpola entre las columnas de solo losa (todo lo que cuelga es viga: ala
      superior, alma y bulbo);
    - losa = el resto (incluye sardineles bajos).
    """
    from scipy import ndimage as ndi
    from skimage.measure import approximate_polygon, find_contours

    M, (t0, t1, z0, z1), cell = sec.mask, sec.extent, sec.cell
    nt, nz = M.shape
    tc = t0 + (np.arange(nt) + 0.5) * cell
    zc = z0 + (np.arange(nz) + 0.5) * cell
    has = M.any(axis=1)
    top = np.where(has, zc[(nz - 1) - np.argmax(M[:, ::-1], axis=1)], np.nan)
    low = np.where(has, zc[np.argmax(M, axis=1)], np.nan)

    cen = has & (np.abs(tc) < 4.3)
    k, b = _robust_poly(tc[cen], top[cen], deg=1)
    ZT = k * tc + b

    thick = top - low
    beam_col = has & ((low < ZT - beam_depth))
    slab_col = has & ~beam_col
    zu = np.interp(tc, tc[slab_col], low[slab_col])          # intradós de la losa (interpolado)
    Z = zc[None, :]
    bar = M & (Z > (ZT + bar_height)[:, None])
    beam = M & (Z < (zu - 0.03)[:, None]) & ~bar
    slab = M & ~beam & ~bar

    def comps(mask):
        lab, n = ndi.label(mask, structure=np.ones((3, 3)))
        out = []
        for i in range(1, n + 1):
            m = lab == i
            if m.sum() * cell * cell >= min_area:
                out.append(m)
        return out

    def outline(m):
        cs = find_contours(np.pad(m, 1).astype(float), 0.5)
        c = max(cs, key=len)
        c = approximate_polygon(c - 1, simplify / cell)[:-1]
        p = np.column_stack((t0 + c[:, 0] * cell, z0 + c[:, 1] * cell))
        return p if _poly_area(p) > 0 else p[::-1]           # antihorario

    els = []
    for m in comps(slab):
        els.append(Element("Losa tablero", "losa", IFC_CLASS["losa"], outline(m)))
    beams = sorted(comps(beam), key=lambda m: float(tc[np.flatnonzero(m.any(axis=1))].mean()))
    for i, m in enumerate(beams, 1):
        els.append(Element(f"Viga {i}", "viga", IFC_CLASS["viga"], outline(m)))
    bars = sorted(comps(bar), key=lambda m: float(tc[np.flatnonzero(m.any(axis=1))].mean()))
    for i, m in enumerate(bars):
        els.append(Element("Baranda izquierda" if i == 0 else "Baranda derecha",
                           "baranda", IFC_CLASS["baranda"], outline(m)))
    for e in els:
        e.area = _poly_area(e.polygon)
    return els


def build_solids(sec: SectionResult, elements: list, stations: int = 9) -> list:
    """
    Extruye cada polígono (t, z_rel) a lo largo del eje entre los extremos esviados y sobre la
    rasante. Cada vértice se barre de s0(t) a s1(t) (extremos esviados). Tapas con earcut.
    """
    import mapbox_earcut as earcut
    import open3d as o3d

    c, d, n = sec.frame
    (a_lo, k_lo), (a_hi, k_hi) = sec.ends["lo"], sec.ends["hi"]
    solids = []
    for el in elements:
        P = el.polygon
        K = len(P)
        f = np.linspace(0, 1, stations)
        s0, s1 = a_lo + k_lo * P[:, 0], a_hi + k_hi * P[:, 0]
        S = s0[None, :] + f[:, None] * (s1 - s0)[None, :]                 # (stations, K)
        xy = c[None, None, :] + S[..., None] * d[None, None, :] + P[None, :, 0, None] * n[None, None, :]
        zz = np.polyval(sec.grade, S) + P[None, :, 1]
        V = np.concatenate((xy, zz[..., None]), axis=2).reshape(-1, 3)
        T = []
        for k in range(stations - 1):
            for i in range(K):
                j = (i + 1) % K
                a, b_, c_, d_ = k * K + i, k * K + j, (k + 1) * K + i, (k + 1) * K + j
                T += [(a, b_, c_), (b_, d_, c_)]
        cap = earcut.triangulate_float64(P.astype(np.float64), np.array([K], dtype=np.uint32)).reshape(-1, 3)
        T += [tuple(r) for r in cap[:, ::-1] + 0]                         # tapa inicial (normal -s)
        T += [tuple(r + (stations - 1) * K) for r in cap]                 # tapa final (normal +s)
        T = np.array(T, dtype=np.int64)
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                         o3d.utility.Vector3iVector(T.astype(np.int32)))
        wt = bool(mesh.is_watertight())
        vol = float(mesh.get_volume()) if wt else 0.0
        if wt and vol < 0:                     # orientación invertida: se voltea todo
            T = T[:, ::-1]
            vol = -vol
        solids.append(Solid(el.name, el.kind, el.ifc_class, V, T, vol, wt))
    return solids


# ------------------------------------------------------------------ estribos
def _beam_lines(sec: SectionResult):
    """Rectas transversales de tope de losa ZT(t) e intradós/fondo de vigas ZB(t) (z relativo)."""
    M, (t0, t1, z0, z1), cell = sec.mask, sec.extent, sec.cell
    nt, nz = M.shape
    tc = t0 + (np.arange(nt) + 0.5) * cell
    zc = z0 + (np.arange(nz) + 0.5) * cell
    has = M.any(axis=1)
    top = np.where(has, zc[(nz - 1) - np.argmax(M[:, ::-1], axis=1)], np.nan)
    low = np.where(has, zc[np.argmax(M, axis=1)], np.nan)
    cen = has & (np.abs(tc) < 4.3)
    kt, bt = _robust_poly(tc[cen], top[cen], deg=1)
    beam_col = has & (low < kt * tc + bt - 0.9)
    kb, bb = np.polyfit(tc[beam_col], low[beam_col], 1)
    return (kt, bt), (kb, bb)


def extract_abutments(points: np.ndarray, sec: SectionResult, width: float, e_back: float = 0.30,
                      margin_t: float = 3.0, w_range=(-3.0, -0.3), band=(0.25, 1.9)) -> list:
    """
    Estribo en cada extremo del tablero, medido con TODOS los puntos de la zona (no depende de la
    clasificación: el cuerpo del estribo suele quedar como terreno o vegetación).
    En el marco del extremo: w = distancia desde la cara de extremo del tablero hacia afuera,
    z_rel = z - rasante en ese extremo.
    1) Cara frontal: pico del histograma de w (2 cm) de los puntos bajo el fondo de las vigas;
       refinada con los puntos a +-5 cm del pico. Se mide extensión en t y cota de apoyo del suelo.
    2) Sección (w, z) = zapata-asiento entre la cara frontal y el espaldar (w = 0) más el espaldar de
       espesor `e_back` (SUPUESTO: la cara posterior no es visible). Cotas: base plana en el suelo,
       asiento a la altura del fondo de vigas ZB(t) y tope a la altura del tablero ZT(t), ambas rectas.
    3) Se extruye en t entre los extremos medidos. Muros de retorno y apoyos: extract_wing_walls y extract_bearings.
    """
    import mapbox_earcut as earcut
    import open3d as o3d
    c, d, n = sec.frame
    (kt, bt), (kb, bb) = _beam_lines(sec)
    out = []
    for name, key, sign in (("Estribo inicial", "lo", -1), ("Estribo final", "hi", 1)):
        a, k = sec.ends[key]
        sel = []
        for i0 in range(0, len(points), 8_000_000):
            blk = points[i0:i0 + 8_000_000]
            q = blk[:, :2] - c
            s, t = q @ d, q @ n
            w = sign * (s - (a + k * t))
            m = (np.abs(t) < width / 2 + margin_t) & (w > w_range[0] - 0.5) & (w < w_range[1] + 0.5)
            if m.any():
                zr = blk[m, 2] - np.polyval(sec.grade, a + k * t[m])
                sel.append(np.column_stack((w[m], t[m], zr)))
        if not sel:
            continue
        A = np.vstack(sel)
        w, t, zr = A[:, 0], A[:, 1], A[:, 2]
        zb = kb * t + bb
        face = (w > w_range[0]) & (w < w_range[1]) & (zr < zb - band[0]) & (zr > zb - band[1])
        if face.sum() < 1000:
            continue
        hist, edges = np.histogram(w[face], bins=np.arange(w_range[0], w_range[1] + 0.02, 0.02))
        pk = edges[int(np.argmax(np.convolve(hist, np.ones(3), "same")))] + 0.01
        near = face & (np.abs(w - pk) < 0.05)
        w_f = float(np.mean(w[near]))
        t_lo, t_hi = np.percentile(t[near], [0.5, 99.5])
        in_t = near & (t > t_lo) & (t < t_hi)
        z_g = float(np.percentile(zr[near & (zr < zb - band[0])], 1))
        flat = face & (np.abs(w - w_f) < 0.5)
        plane_pct = float((np.abs(w[flat] - w_f) < 0.03).mean() * 100)
        # asiento: superficie bajo los fondos de viga entre la cara frontal y el extremo del tablero;
        # su cota respecto a ZB(t) es el espacio que ocupan los apoyos
        seat = (w > w_f + 0.15) & (w < -0.15) & (zr < zb - 0.02) & (zr > zb - 0.35)
        seat_drop = 0.0
        if seat.sum() > 500:                    # moda (1 cm): el asiento es la superficie dominante
            hd, ed = np.histogram(zr[seat] - zb[seat], bins=np.arange(-0.35, -0.019, 0.01))
            seat_drop = float(np.clip(-(ed[int(np.argmax(np.convolve(hd, np.ones(3), "same")))] + 0.005), 0.0, 0.25))

        # sección (w, tipo): g = base en el suelo, s = asiento (ZB), t = tope de tablero (ZT)
        spec = [(w_f, "g"), (e_back, "g"), (e_back, "t"), (0.0, "t"), (0.0, "s"), (w_f, "s")]

        def zfun(kind, tt):
            return {"g": z_g + 0 * tt, "s": kb * tt + bb - seat_drop, "t": kt * tt + bt}[kind]

        V, rings = [], []
        for tt in (t_lo, t_hi):
            ring = []
            for wv, kind in spec:
                s_end = a + k * tt
                s_ = s_end + sign * wv
                xy = c + s_ * d + tt * n
                V.append((xy[0], xy[1], float(np.polyval(sec.grade, s_end)) + float(zfun(kind, tt))))
                ring.append(len(V) - 1)
            rings.append(ring)
        V = np.array(V)
        K = len(spec)
        T = []
        for i in range(K):
            j = (i + 1) % K
            a0, b0, a1, b1 = rings[0][i], rings[0][j], rings[1][i], rings[1][j]
            T += [(a0, b0, a1), (b0, b1, a1)]
        for r_, flip in ((0, True), (1, False)):
            p2 = np.array([[wv, float(zfun(kind, (t_lo, t_hi)[r_]))] for wv, kind in spec])
            cap = earcut.triangulate_float64(p2, np.array([K], dtype=np.uint32)).reshape(-1, 3)
            cap = cap + (0 if r_ == 0 else K)
            T += [tuple(x[::-1]) if flip else tuple(x) for x in cap]
        T = np.array(T, dtype=np.int64)
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                         o3d.utility.Vector3iVector(T.astype(np.int32)))
        wt = bool(mesh.is_edge_manifold() and mesh.is_watertight())
        vol = float(mesh.get_volume()) if wt else 0.0
        if wt and vol < 0:
            T = T[:, ::-1]
            vol = -vol
        sol = Solid(name, "estribo", "IfcWall", V, T, vol, wt)
        sol.metrics = {"w_front_m": w_f, "seat_width_perp_m": abs(w_f) / np.sqrt(1 + k * k),
                       "t_range_m": [float(t_lo), float(t_hi)], "z_ground_rel_m": z_g,
                       "plane_within_3cm_pct": plane_pct, "face_points": int(near.sum()),
                       "e_back_assumed_m": e_back, "seat_drop_m": seat_drop, "w_end": sign,
                       "end": key}
        out.append(sol)
    return out


# ------------------------------------------------------------------ barandas (rieles y postes)
def _prism(poly: np.ndarray, s0: float, s1: float, sec: SectionResult, name: str, kind: str,
           ifc_class: str, step: float = 5.0) -> Solid:
    """Prisma de la sección (t, z_rel) entre s0 y s1 con extremos perpendiculares y sobre la rasante."""
    import mapbox_earcut as earcut
    import open3d as o3d
    c, d, n = sec.frame
    K = len(poly)
    S = np.linspace(s0, s1, max(2, int((s1 - s0) / step) + 2))
    V = []
    for sv in S:
        for tv, zv in poly:
            xy = c + sv * d + tv * n
            V.append((xy[0], xy[1], float(np.polyval(sec.grade, sv)) + zv))
    V = np.array(V)
    m = len(S)
    T = []
    for k in range(m - 1):
        for i in range(K):
            j = (i + 1) % K
            a, b, c_, d_ = k * K + i, k * K + j, (k + 1) * K + i, (k + 1) * K + j
            T += [(a, b, c_), (b, d_, c_)]
    cap = earcut.triangulate_float64(poly.astype(np.float64), np.array([K], dtype=np.uint32)).reshape(-1, 3)
    T += [tuple(r[::-1]) for r in cap]
    T += [tuple(r + (m - 1) * K) for r in cap]
    T = np.array(T, dtype=np.int64)
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                     o3d.utility.Vector3iVector(T.astype(np.int32)))
    wt = bool(mesh.is_watertight())
    vol = float(mesh.get_volume()) if wt else 0.0
    if wt and vol < 0:
        T, vol = T[:, ::-1], -vol
    return Solid(name, kind, ifc_class, V, T, vol, wt)


def _intervals(mask: np.ndarray, edges: np.ndarray, merge: float, min_w: float):
    """Intervalos contiguos de una máscara sobre bins; une huecos < merge y descarta anchos < min_w."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append([edges[i], edges[j + 1]])
            i = j + 1
        else:
            i += 1
    merged = []
    for iv in out:
        if merged and iv[0] - merged[-1][1] < merge:
            merged[-1][1] = iv[1]
        else:
            merged.append(iv)
    return [iv for iv in merged if iv[1] - iv[0] >= min_w]


def extract_railings(points: np.ndarray, sec: SectionResult, structure: StructureResult,
                     elements: list, bar_height: float = 0.45, post_gap: float = 0.20,
                     post_frac: float = 0.5, tree_filter: bool = True, min_occ: float = 0.5,
                     max_post_spread: float = 0.12, max_post_width: float = 0.6, tree_pts: int = 50,
                     tree_max_run: float = 1.0) -> list:
    """
    Barandas por componentes (la baranda no es continua: rieles continuos + postes cada ~1.5 m).
    1) Región = material a más de `bar_height` sobre el tope de losa, en el rango t de cada baranda
       ya separada (`separate_elements`).
    2) Rieles: el histograma de z (1 cm) tiene picos anchos y densos (tubos horizontales); cada
       intervalo denso es un riel.
    3) Postes: en la franja de z entre los dos primeros rieles solo hay postes; los picos del
       histograma de s (2 cm) dan sus posiciones.
    4) Cada riel se mide con los cortes ENTRE postes (a más de `post_gap` m de cualquiera): rectángulo
       con percentiles 2-98 de t, extendido a lo largo de s. Cada poste se mide en una ventana propia.
    5) Filtro de árboles (`tree_filter`): un candidato a poste se descarta si no es un elemento vertical
       compacto que termina en el riel superior: continuidad en z (fracción de celdas de 5 cm con puntos)
       menor que `min_occ`, dispersión en s de sus puntos no-riel mayor que `max_post_spread`, ancho en t
       mayor que `max_post_width`, material que sigue por encima del riel (tronco, poste de luz) o fuera
       del tramo con rieles. Antes de buscar postes se excluyen los troncos (material compacto de al menos
       `tree_pts` puntos por 0.1 m que sigue sobre el riel superior en un tramo de hasta `tree_max_run` m).
       Los descartes se cuentan en metrics["posts_rejected"] de los rieles del lado.
    """
    (kt, bt), _ = _beam_lines(sec)
    c, d, n = sec.frame
    pts = points[structure.labels == 0]
    out = []
    for el in [e for e in elements if e.kind == "baranda"]:
        tl, th = float(el.polygon[:, 0].min()) - 0.05, float(el.polygon[:, 0].max()) + 0.05
        side = "izquierda" if (tl + th) / 2 < 0 else "derecha"
        sel = []
        for i0 in range(0, len(pts), 8_000_000):
            blk = pts[i0:i0 + 8_000_000]
            q = blk[:, :2] - c
            s, t = q @ d, q @ n
            m = (t > tl) & (t < th)
            if m.any():
                zr = blk[m, 2] - np.polyval(sec.grade, s[m])
                keep = zr > kt * t[m] + bt + bar_height
                sel.append(np.column_stack((s[m][keep], t[m][keep], zr[keep])))
        if not sel:
            continue
        A = np.vstack(sel)
        s, t, zr = A[:, 0], A[:, 1], A[:, 2]
        ze = np.arange(zr.min(), zr.max() + 0.01, 0.01)
        zh = np.convolve(np.histogram(zr, bins=ze)[0], np.ones(3) / 3, "same")
        rails = _intervals(zh > 0.35 * zh.max(), ze, merge=0.03, min_w=0.03)
        if len(rails) < 2:
            continue
        rails.sort()
        # zona de postes: todo lo que no es riel (+-2 cm) bajo el borde superior del último riel
        gm = (zr < rails[-1][1] + 0.02)
        for r0, r1 in rails:
            gm &= ~((zr > r0 - 0.02) & (zr < r1 + 0.02))
        trees = 0
        if tree_filter:
            # troncos y postes de luz: material compacto que sigue por encima del riel superior. Se
            # excluyen del histograma de postes (si no, por su densidad ocultan a los postes vecinos)
            from scipy.ndimage import binary_dilation, label as nd_label
            sb = np.arange(s.min(), s.max() + 0.1, 0.1)
            bad = np.histogram(s[zr > rails[-1][1] + 0.10], bins=sb)[0] >= tree_pts
            runs, nr = nd_label(bad)
            for ri in range(1, nr + 1):
                if (runs == ri).sum() * 0.1 > tree_max_run:      # tramo largo: follaje sobre la baranda, no un tronco
                    bad[runs == ri] = False
            trees = int(nd_label(bad)[1])
            bad = binary_dilation(bad, iterations=3)
            gm &= ~bad[np.clip(np.digitize(s, sb) - 1, 0, len(bad) - 1)]
        se = np.arange(s.min(), s.max() + 0.02, 0.02)
        sh = np.convolve(np.histogram(s[gm], bins=se)[0], np.ones(5) / 5, "same")
        # picos relativos al máximo local (ventana de 4 m): los postes de zonas con menos puntos
        # (oclusión, menor densidad) también se detectan
        from scipy.ndimage import maximum_filter1d
        from scipy.signal import find_peaks
        rel = sh / (maximum_filter1d(sh, size=int(4.0 / 0.02)) + 1e-9)
        pk, _ = find_peaks(rel, height=post_frac, distance=int(0.6 / 0.02))
        pk = [i for i in pk if sh[i] >= 15]
        posts = [0.5 * (se[i] + se[i + 1]) for i in pk]
        posts = np.array(posts)
        far = np.ones(len(s), bool)
        for sp in posts:
            far &= np.abs(s - sp) > post_gap
        rail_start = len(out)
        for r, (z0, z1) in enumerate(rails, 1):
            mr = far & (zr > z0) & (zr < z1)
            if mr.sum() < 500:
                continue
            t_lo, t_hi = np.percentile(t[mr], [2, 98])
            s_lo, s_hi = np.percentile(s[(zr > z0) & (zr < z1)], [0.2, 99.8])
            poly = np.array([[t_lo, z0], [t_hi, z0], [t_hi, z1], [t_lo, z1]])
            sol = _prism(poly, float(s_lo), float(s_hi), sec, f"Riel {r} {side}", "riel", "IfcRailing")
            sol.metrics = {"side": side, "z_rel": [float(z0), float(z1)], "t": [float(t_lo), float(t_hi)],
                           "s": [float(s_lo), float(s_hi)], "points": int(mr.sum())}
            out.append(sol)
        zb0 = float(np.percentile(zr, 0.5))
        zb1 = float(rails[-1][1])
        rail_s = [min(o.metrics["s"][0] for o in out[rail_start:]), max(o.metrics["s"][1] for o in out[rail_start:])] \
            if len(out) > rail_start else [-np.inf, np.inf]
        rejected, kept = trees, 0
        for i, sp in enumerate(posts, 1):
            mp = (np.abs(s - sp) < 0.15) & (zr > zb0) & (zr < zb1 + 0.02)
            if mp.sum() < 100:
                continue
            gp = mp & gm
            spread = float(np.percentile(np.abs(s[gp] - sp), 90)) if gp.sum() > 20 else 0.04
            hw = float(np.clip(spread, 0.02, 0.10))
            t_lo, t_hi = np.percentile(t[mp], [1, 99])
            if tree_filter:
                zbins = np.arange(zb0, zb1, 0.05)
                occ = float((np.histogram(zr[mp], bins=zbins)[0] > 3).mean()) if len(zbins) > 1 else 0.0
                above = int(((np.abs(s - sp) < 0.15) & (zr > zb1 + 0.10) & (zr < zb1 + 0.60)).sum())
                if (occ < min_occ or spread > max_post_spread or (t_hi - t_lo) > max_post_width
                        or above > 0.3 * mp.sum() or sp < rail_s[0] - 0.5 or sp > rail_s[1] + 0.5):
                    rejected += 1
                    continue
            kept += 1
            poly = np.array([[t_lo, zb0], [t_hi, zb0], [t_hi, zb1], [t_lo, zb1]])
            sol = _prism(poly, sp - hw, sp + hw, sec, f"Poste {kept} {side}", "poste", "IfcMember")
            sol.metrics = {"side": side, "s": float(sp), "half_width_s": hw, "points": int(mp.sum())}
            out.append(sol)
        for o in out[rail_start:]:
            o.metrics["posts_rejected"] = rejected
    return out


# ------------------------------------------------------------------ muros de retorno y apoyos
def _frame_solid(poly: np.ndarray, t_a: float, t_b: float, sec: SectionResult, a: float, k: float,
                 sign: int, name: str, kind: str, ifc_class: str) -> Solid:
    """
    Prisma del polígono (w, z_rel) extruido entre t_a y t_b en el marco del extremo del tablero
    (w = distancia hacia afuera desde la cara de extremo; z_rel = z - rasante en ese extremo).
    Prueba las dos orientaciones de las tapas hasta obtener una malla cerrada y orientable.
    """
    import mapbox_earcut as earcut
    import open3d as o3d
    c, d, n = sec.frame
    K = len(poly)
    V = []
    for tt in (t_a, t_b):
        s_end = a + k * tt
        for wv, zv in poly:
            xy = c + (s_end + sign * wv) * d + tt * n
            V.append((xy[0], xy[1], float(np.polyval(sec.grade, s_end)) + float(zv)))
    V = np.array(V)
    side = []
    for i in range(K):
        j = (i + 1) % K
        side += [(i, j, K + i), (j, K + j, K + i)]
    cap = earcut.triangulate_float64(np.asarray(poly, np.float64), np.array([K], dtype=np.uint32)).reshape(-1, 3)
    x_, y_ = np.asarray(poly)[:, 0], np.asarray(poly)[:, 1]
    expected = 0.5 * abs(float(np.dot(x_, np.roll(y_, -1)) - np.dot(y_, np.roll(x_, -1)))) * abs(t_b - t_a)
    best = None
    for flip_side in (False, True):
        sd = [tuple(x[::-1]) for x in side] if flip_side else side
        for flip_cap in (False, True):
            c0 = [tuple(r[::-1]) if flip_cap else tuple(r) for r in cap]
            c1 = [tuple((r + K)[::-1]) if not flip_cap else tuple(r + K) for r in cap]
            T = np.array(sd + c0 + c1, dtype=np.int64)
            Vc = V - V.mean(0)
            vol = float(np.einsum("ij,ij->i", Vc[T[:, 0]], np.cross(Vc[T[:, 1]], Vc[T[:, 2]])).sum() / 6)
            if abs(abs(vol) - expected) < 0.03 * expected:      # orientación correcta: volumen = área x espesor
                mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(Vc),
                                                 o3d.utility.Vector3iVector(T.astype(np.int32)))
                if mesh.is_edge_manifold() and mesh.is_watertight():
                    best = (T, vol)
                    break
        if best:
            break
    if best is None:
        return Solid(name, kind, ifc_class, V, T, 0.0, False)
    T, vol = best
    if vol < 0:
        T, vol = T[:, ::-1], -vol
    return Solid(name, kind, ifc_class, V, T, vol, True)


def extract_wing_walls(points: np.ndarray, sec: SectionResult, width: float, abutments: list,
                       e_back: float = 0.30, thickness: float = 0.30, w_max: float = 16.0,
                       col: float = 0.05, min_height: float = 0.20, min_points: int = 30) -> list:
    """
    Muro de retorno en cada lado de cada estribo, medido con todos los puntos tras el espaldar.
    La nube ve la cara EXTERIOR (vertical, a t ~ +-ancho/2); la cara interior queda bajo el relleno.
    En el marco del extremo (w hacia afuera, z_rel sobre la rasante del extremo):
    1) Cara: pico del histograma de t (1 cm) de los puntos a menos de 0.35 m sobre el tablero
       (así se excluyen barandas y postes) en +-0.4/+0.8 m del borde del tablero.
    2) Perfil: por columnas de `col` m en w, cota alta (p98, limitada a ZT+0.35) y baja (p2, el
       terreno que corta el muro) de los puntos de la cara (+-4 cm). Cuenta como muro la corrida
       continua desde el espaldar de columnas con al menos `min_points` puntos y `min_height` m.
    3) Polígono (w, z) suavizado, simplificado (Douglas-Peucker 3 cm) y extruido hacia el eje
       `thickness` m (SUPUESTO: la cara interior no es visible).
    """
    from scipy.ndimage import median_filter
    from skimage.measure import approximate_polygon
    (kt, bt), _ = _beam_lines(sec)
    c, d, n = sec.frame
    out = []
    for ab in abutments:
        key, sign = ab.metrics["end"], ab.metrics["w_end"]
        a, k = sec.ends[key]
        sel = []
        for i0 in range(0, len(points), 8_000_000):
            blk = points[i0:i0 + 8_000_000]
            q = blk[:, :2] - c
            s, t = q @ d, q @ n
            w = sign * (s - (a + k * t))
            m = (np.abs(t) < width / 2 + 1.0) & (np.abs(t) > width / 2 - 0.5) & (w > e_back - 0.05) & (w < w_max)
            if m.any():
                zr = blk[m, 2] - np.polyval(sec.grade, a + k * t[m])
                sel.append(np.column_stack((w[m], t[m], zr)))
        if not sel:
            continue
        A = np.vstack(sel)
        for side in (-1, 1):
            B = A[np.sign(A[:, 1]) == side]
            if len(B) < 1000:
                continue
            w, t, zr = B[:, 0], B[:, 1], B[:, 2]
            zt = kt * t + bt
            cand = (zr < zt + 0.35) & (zr > zt - 5.0) & (w < e_back + 8.0)
            tt = side * t[cand]
            lo_t, hi_t = width / 2 - 0.4, width / 2 + 0.8
            hist, ed = np.histogram(tt, bins=np.arange(lo_t, hi_t, 0.01))
            if hist.max() < 500:
                continue
            pk = ed[int(np.argmax(np.convolve(hist, np.ones(3), "same")))] + 0.005
            face = (np.abs(side * t - pk) < 0.04) & (zr < zt + 0.35)
            t_f = float(side * np.mean(side * t[face]))
            wf, zf = w[face], zr[face]
            edges = np.arange(e_back, w_max + col, col)
            idx = np.digitize(wf, edges) - 1
            nb = len(edges) - 1
            top = np.full(nb, np.nan)
            bot = np.full(nb, np.nan)
            ok = np.zeros(nb, bool)
            order = np.argsort(idx, kind="stable")
            ids, starts = np.unique(idx[order], return_index=True)
            for u, st, en in zip(ids, starts, list(starts[1:]) + [len(order)]):
                if 0 <= u < nb and en - st >= min_points:
                    zz = zf[order[st:en]]
                    top[u], bot[u] = np.percentile(zz, [98, 2])
                    ok[u] = top[u] - bot[u] >= min_height
            if not ok.any():
                continue
            first = int(np.argmax(ok))
            if edges[first] > e_back + 0.6:
                continue
            last, gap = first, 0
            for u in range(first, nb):
                if ok[u]:
                    last, gap = u, 0
                else:
                    gap += 1
                    if gap * col > 0.25:
                        break
            seg = np.arange(first, last + 1)
            wc = edges[seg] + col / 2
            tp = np.where(ok[seg], top[seg], np.nan)
            bp = np.where(ok[seg], bot[seg], np.nan)
            for arr in (tp, bp):                       # huecos pequeños: interpolar
                good = ~np.isnan(arr)
                arr[~good] = np.interp(wc[~good], wc[good], arr[good])
            win = max(3, int(0.4 / col) | 1)
            tp, bp = median_filter(tp, win, mode="nearest"), median_filter(bp, win, mode="nearest")
            L = float(wc[-1] - wc[0] + col)
            if L < 1.0:
                continue
            tpts = np.column_stack((np.r_[wc[0] - col / 2, wc, wc[-1] + col / 2], np.r_[tp[0], tp, tp[-1]]))
            bpts = np.column_stack((np.r_[wc[0] - col / 2, wc, wc[-1] + col / 2], np.r_[bp[0], bp, bp[-1]]))
            tpts = approximate_polygon(tpts, 0.03)
            bpts = approximate_polygon(bpts, 0.03)
            poly = np.vstack((bpts, tpts[::-1]))            # recorrido: base hacia afuera y tope de vuelta
            poly = poly[np.r_[True, np.any(np.abs(np.diff(poly, axis=0)) > 1e-6, axis=1)]]
            name = (f"Muro de retorno {'inicial' if key == 'lo' else 'final'} "
                    f"{'izquierdo' if side < 0 else 'derecho'}")
            sol = _frame_solid(poly, t_f, t_f - side * thickness, sec, a, k, sign, name, "muro", "IfcWall")
            sol.metrics = {"abutment": ab.name, "side": "izquierdo" if side < 0 else "derecho",
                           "t_face_m": t_f, "length_m": L, "w_range_m": [float(wc[0] - col / 2), float(wc[-1] + col / 2)],
                           "top_z_rel_m": [float(tp.min()), float(tp.max())],
                           "height_m": [float((tp - bp).min()), float((tp - bp).max())],
                           "face_points": int(face.sum()), "thickness_assumed_m": thickness}
            out.append(sol)
    return out


def extract_bearings(abutments: list, sec: SectionResult, elements: list, length: float = 0.35,
                     width_max: float = 0.50, default_height: float = 0.05) -> list:
    """
    Un apoyo bajo cada viga en cada estribo. Medido: posición transversal (centro de cada viga),
    ancho del ala inferior y espesor (diferencia entre el fondo de viga ZB y el asiento medido).
    SUPUESTO: posición longitudinal en la mitad del asiento y largo `length` m (el apoyo queda tapado
    por la viga y no se ve en la nube). Si el estribo no tiene espacio medido, usa `default_height`.
    """
    (kt, bt), (kb, bb) = _beam_lines(sec)
    out = []
    beams = [e for e in elements if e.kind == "viga"]
    for ab in abutments:
        key, sign = ab.metrics["end"], ab.metrics["w_end"]
        a, k = sec.ends[key]
        w_f = ab.metrics["w_front_m"]
        h = ab.metrics.get("seat_drop_m", 0.0)
        measured = h >= 0.01
        if not measured:
            h = default_height
        w_c = w_f / 2.0
        L = min(length, 0.8 * abs(w_f))
        for i, e in enumerate(beams, 1):
            p = e.polygon
            zmin = float(p[:, 1].min())
            low = p[p[:, 1] < zmin + 0.04]
            tc = float(0.5 * (low[:, 0].max() + low[:, 0].min()))
            wd = float(min(width_max, low[:, 0].max() - low[:, 0].min() - 0.10))
            zb = kb * tc + bb
            poly = np.array([[w_c - L / 2, zb - h], [w_c + L / 2, zb - h], [w_c + L / 2, zb], [w_c - L / 2, zb]])
            name = f"Apoyo {'inicial' if key == 'lo' else 'final'} viga {i}"
            sol = _frame_solid(poly, tc - wd / 2, tc + wd / 2, sec, a, k, sign, name, "apoyo", "IfcBearing")
            sol.metrics = {"abutment": ab.name, "beam": e.name, "t_center_m": tc, "width_m": wd,
                           "length_assumed_m": L, "height_m": h, "height_measured": measured,
                           "w_center_assumed_m": w_c}
            out.append(sol)
    return out


# ------------------------------------------------------------------------------- vía
def _fit_plane_line(t, z, tol, rng, it=150):
    """Recta z = a + b*t por RANSAC + mínimos cuadrados sobre los inliers (None si no hay)."""
    best = None
    for _ in range(it):
        i, j = rng.choice(len(t), 2, replace=False)
        if abs(t[i] - t[j]) < 1.0:
            continue
        b = (z[j] - z[i]) / (t[j] - t[i])
        a = z[i] - b * t[i]
        n = int((np.abs(z - (a + b * t)) < tol).sum())
        if best is None or n > best[0]:
            best = (n, a, b)
    if best is None:
        return None
    _, a, b = best
    m = np.abs(z - (a + b * t)) < tol
    return tuple(np.linalg.lstsq(np.c_[np.ones(int(m.sum())), t[m]], z[m], rcond=None)[0])


def _running_median(y, k=5):
    pad = k // 2
    yp = np.pad(y, pad, mode="edge")
    return np.array([np.median(yp[i:i + k]) for i in range(len(y))])


def extract_road(points: np.ndarray, ground: np.ndarray, frame: tuple, s_lo: float, s_hi: float,
                 step: float = 1.0, window: float = 2.0, reach: float = 80.0, core: float = 3.0,
                 tol: float = 0.05, thickness: float = 0.10, t_max: float = 14.0,
                 min_core: int = 30, seed: int = 0):
    """
    Vía de acceso (calzada) a ambos lados del tablero. Se separa del terreno natural porque es
    una superficie plana con peralte constante en cada sección transversal: por tramo de
    `step` m a lo largo del eje se ajusta z = a + b*t (RANSAC) a los puntos de suelo del núcleo
    |t| < `core`; la vía es la franja contigua (huecos <= 0.6 m) de puntos a menos de `tol` m del
    plano. Plano y bordes se suavizan entre tramos (mediana móvil de 5) y la máscara final son
    todos los puntos de suelo dentro de los bordes y del plano.

    frame = (c, d, n) con c (2,) centro, d (2,) eje, n (2,) normal en planta (coords locales);
    s_lo / s_hi: s del borde exterior del tablero en cada extremo (las vías empiezan allí).
    Devuelve (mask (N,) bool sobre `points`, solids, info). El espesor del sólido es supuesto.
    """
    import open3d as o3d
    c, d, n = frame
    idx_g = np.flatnonzero(ground)                  # solo suelo: evita arreglos de N puntos en float64
    q = points[idx_g, :2] - c
    s_g, t_g = q @ d, q @ n
    keep = (np.abs(t_g) < t_max) & ((s_g > s_hi) | (s_g < s_lo))
    idx_c, s, t = idx_g[keep], s_g[keep], t_g[keep]
    z = points[idx_c, 2]
    rng = np.random.default_rng(seed)
    mask = np.zeros(len(points), bool)
    solids, info = [], {"sides": {}}
    for sgn, s_edge, name in ((1.0, s_hi, "final"), (-1.0, s_lo, "inicial")):
        u = sgn * (s - s_edge)                      # distancia desde el borde del tablero (> 0 hacia afuera)
        rows = []
        bins = np.arange(-t_max, t_max + 1e-9, 0.2)
        in_core = np.abs(bins[:-1]) < core
        for u0 in np.arange(0.0, reach, step):
            m = (u >= u0) & (u < u0 + window)
            ts, zs = t[m], z[m]
            cc = np.abs(ts) < core
            if cc.sum() < min_core:
                continue
            f = _fit_plane_line(ts[cc], zs[cc], tol, rng)
            if f is None:
                continue
            a, b = f
            inl = np.abs(zs - (a + b * ts)) < tol
            h, _ = np.histogram(ts[inl], bins)
            hc = h[in_core][h[in_core] > 0]
            if len(hc) == 0:
                continue
            ok = h >= max(2, 0.1 * float(np.median(hc)))
            k0 = int(np.searchsorted(bins, 0.0)) - 1
            if not ok[k0]:
                continue
            lo = k0
            while lo > 0 and ok[max(0, lo - 3):lo].any():
                lo -= 1
            hi = k0
            while hi < len(ok) - 1 and ok[hi + 1:hi + 4].any():
                hi += 1
            while not ok[lo]:
                lo += 1
            while not ok[hi]:
                hi -= 1
            rows.append((u0 + window / 2, a, b, bins[lo], bins[hi + 1]))
        if len(rows) < 5:
            continue
        R = np.array(rows)
        for col in range(1, 5):
            R[:, col] = _running_median(R[:, col], 5)
        uu = np.arange(float(R[0, 0]), float(R[-1, 0]) + 1e-9, step)
        A, B, TL, TR = (np.interp(uu, R[:, 0], R[:, k]) for k in (1, 2, 3, 4))
        # --- máscara: puntos de suelo dentro de bordes y plano
        ui = np.clip(np.searchsorted(uu, u, side="left"), 0, len(uu) - 1)
        inside = (u >= uu[0] - step / 2) & (u <= uu[-1] + step / 2)
        res = z - (A[ui] + B[ui] * t)
        sel = inside & (t >= TL[ui]) & (t <= TR[ui]) & (np.abs(res) < tol)
        mask[idx_c[sel]] = True
        # --- sólido: prisma a lo largo del eje, caras laterales y tapas
        sv = s_edge + sgn * uu
        base = c[None, :] + sv[:, None] * d[None, :]
        top_l = np.c_[base + TL[:, None] * n[None, :], A + B * TL]
        top_r = np.c_[base + TR[:, None] * n[None, :], A + B * TR]
        bot_l, bot_r = top_l.copy(), top_r.copy()
        bot_l[:, 2] -= thickness
        bot_r[:, 2] -= thickness
        m_ = len(uu)
        V = np.concatenate([top_l, top_r, bot_r, bot_l])           # 4 anillos de m_ vértices
        T = []
        for i in range(m_ - 1):
            for r0, r1 in ((0, 1), (1, 2), (2, 3), (3, 0)):
                p, q_, p2, q2 = r0 * m_ + i, r1 * m_ + i, r0 * m_ + i + 1, r1 * m_ + i + 1
                T += [(p, q_, p2), (q_, q2, p2)]
        for e in (0, m_ - 1):                                    # tapas
            a_, b_, c_, d_ = e, m_ + e, 2 * m_ + e, 3 * m_ + e
            T += [(a_, b_, c_), (a_, c_, d_)] if e == 0 else [(a_, c_, b_), (a_, d_, c_)]
        T = np.array(T, dtype=np.int64)
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V - V.mean(0)),
                                         o3d.utility.Vector3iVector(T.astype(np.int32)))
        wt = bool(mesh.is_watertight())
        vol = float(mesh.get_volume()) if wt else 0.0
        if wt and vol < 0:
            T = T[:, ::-1]
            vol = -vol
        sol = Solid(f"Vía {name}", "via", "IfcSlab", V, T, vol, wt)
        sol.metrics = {"length_m": float(uu[-1] - uu[0]), "width_m": float(np.median(TR - TL)),
                       "t_left_m": float(np.median(TL)), "t_right_m": float(np.median(TR)),
                       "cross_slope_pct": float(np.median(B) * 100), "points": int(sel.sum()),
                       "thickness_assumed_m": thickness}
        solids.append(sol)
        info["sides"][name] = sol.metrics
    info["points"] = int(mask.sum())
    return mask, solids, info
````

## 3.4 `autoclass.py`

Clasificación por forma (terreno, estructura, vegetación) con modelo entrenado. 117 líneas.

````python
"""
autoclass.py
Clasificación automática por forma: terreno / estructura / vegetación.

Flujo: voxelizar (centroides) -> rasgos geométricos por vóxel -> modelo -> etiqueta a cada punto.
Los rasgos NO usan color ni intensidad: solo forma local y altura sobre el suelo, así que sirven
para cualquier nube. El modelo se entrena con train_autoclass.py a partir de una nube ya
clasificada; las etiquetas de entrenamiento definen qué es "estructura" (ver GROUPS).

>>> PUNTO DE INTEGRACIÓN <<<
Para cambiar el criterio (más clases, otro modelo, CSF para el suelo) toca: GROUPS (clases de
entrenamiento), `features` (rasgos) y `make_model`. Contrato de `predict_points`: devuelve un
arreglo (N,) con códigos ASPRS (2 suelo, 6 estructura, 5 vegetación).
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
from scipy.ndimage import maximum_filter, minimum_filter

# Grupos de entrenamiento -> clases de la nube de referencia (v5 del Puente Mafil)
GROUPS = {
    1: [2, 26, 11],                                       # terreno (suelo, terraplén, calzada acceso)
    2: [17, 20, 21, 22, 24, 25, 27, 28, 29, 30, 31],      # estructura y equipamiento
    3: [3, 4, 5],                                         # vegetación
}
# Grupo -> código ASPRS de salida
OUT_CODES = {1: 2, 2: 6, 3: 5}
FEATURE_NAMES = ["lin_s", "pla_s", "sca_s", "nz_s", "sig_s", "lin_l", "pla_l", "sca_l", "nz_l",
                 "sig_l", "h", "dzmax", "dzmin"]


def voxelize(points: np.ndarray, voxel: float):
    """Centroides de vóxeles ocupados (relativos al mínimo) + índice de vóxel de cada punto."""
    mn = points.min(axis=0)
    ij = np.floor((points - mn) / voxel).astype(np.int64)
    dims = ij.max(axis=0) + 1
    if float(dims[0]) * float(dims[1]) * float(dims[2]) > 4e18:
        raise ValueError("Nube demasiado extensa para este tamaño de vóxel; aumenta voxel.")
    key = (ij[:, 0] * dims[1] + ij[:, 1]) * dims[2] + ij[:, 2]
    del ij
    _, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    cnt = np.bincount(inv).astype(np.float64)
    cen = np.column_stack([np.bincount(inv, weights=points[:, k] - mn[k]) / cnt for k in range(3)])
    return cen, inv


def _eig(pcd, radius, max_nn):
    pcd.estimate_covariances(o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=max_nn))
    w, v = np.linalg.eigh(np.asarray(pcd.covariances))
    w = np.clip(w, 1e-12, None)
    s = np.sqrt(w[:, 2]) + 1e-9
    return [(np.sqrt(w[:, 2]) - np.sqrt(w[:, 1])) / s,    # linealidad
            (np.sqrt(w[:, 1]) - np.sqrt(w[:, 0])) / s,    # planaridad
            np.sqrt(w[:, 0]) / s,                         # dispersión
            np.abs(v[:, 2, 0]),                           # |nz| de la normal
            np.sqrt(w[:, 2])]                             # extensión principal


def features(cen: np.ndarray, r_small: float = 0.4, r_large: float = 1.0) -> np.ndarray:
    """Matriz (M, 13) de rasgos por vóxel. cen en coordenadas locales (z real relativo)."""
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cen))
    f = _eig(pcd, r_small, 40) + _eig(pcd, r_large, 80)

    # altura sobre el suelo: percentil 3 de z por celda de 1 m
    ij = np.floor(cen[:, :2]).astype(np.int64)
    key = ij[:, 0] * (ij[:, 1].max() + 1) + ij[:, 1]
    _, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    zs = cen[order, 2]
    cnt = np.bincount(inv)
    st = np.r_[0, np.cumsum(cnt)[:-1]]
    zlow = np.array([np.percentile(zs[a:a + n], 3) for a, n in zip(st, cnt)])
    h = cen[:, 2] - zlow[inv]

    # máx/mín de z en ventana ~1.5 m (rejilla 0.5 m): ¿hay algo encima o debajo?
    c5 = np.floor(cen[:, :2] / 0.5).astype(np.int64)
    H, W = c5[:, 0].max() + 1, c5[:, 1].max() + 1
    gmax = np.full((H, W), -1e9)
    gmin = np.full((H, W), 1e9)
    np.maximum.at(gmax, (c5[:, 0], c5[:, 1]), cen[:, 2])
    np.minimum.at(gmin, (c5[:, 0], c5[:, 1]), cen[:, 2])
    gmax = maximum_filter(gmax, size=3)
    gmin = minimum_filter(gmin, size=3)
    dzmax = cen[:, 2] - gmax[c5[:, 0], c5[:, 1]]
    dzmin = cen[:, 2] - gmin[c5[:, 0], c5[:, 1]]
    return np.column_stack(f + [h, dzmax, dzmin]).astype(np.float32)


def make_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    # Pesos por clase codificada (0 terreno, 1 estructura, 2 vegetación): equilibra recall y
    # precisión (probado: "balanced" sube recall pero deja precisión 0.72; sin pesos pierde recall).
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1,
                                          class_weight={0: 2.0, 1: 3.0, 2: 1.0},
                                          early_stopping=False, random_state=0)


def load_model(path: str):
    import joblib
    bundle = joblib.load(path)
    if bundle.get("features") != FEATURE_NAMES:
        raise ValueError("El modelo no coincide con los rasgos actuales; reentrena (train_autoclass.py).")
    return bundle


def predict_points(points: np.ndarray, bundle: dict, voxel: float | None = None) -> np.ndarray:
    """Códigos ASPRS (N,) para cada punto. voxel por defecto = el del entrenamiento."""
    voxel = voxel or bundle["voxel"]
    cen, inv = voxelize(points, voxel)
    pred = bundle["model"].predict(features(cen))
    lut = np.zeros(4, dtype=np.uint8)
    for g, code in OUT_CODES.items():
        lut[g] = code
    return lut[pred][inv]
````

## 3.5 `train_autoclass.py`

Entrenamiento del modelo de clasificación. 75 líneas.

````python
"""
train_autoclass.py
Entrena el modelo de clasificación automática a partir de un LAS ya clasificado.

Uso: python train_autoclass.py <clasificado.las> [--voxel 0.15] [--out models/autoclass.joblib]
Valida con bloques espaciales de 20 m (damero): entrena en bloques pares, prueba en impares,
para no medir la memoria de la vecindad. Luego reentrena con todos los bloques y guarda.
"""
import argparse
import os
import time

import joblib
import laspy
import numpy as np

import autoclass as ac

ap = argparse.ArgumentParser()
ap.add_argument("las")
ap.add_argument("--voxel", type=float, default=0.15)
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "models", "autoclass.joblib"))
ap.add_argument("--max-train", type=int, default=3_000_000, help="vóxeles máx. para entrenar")
a = ap.parse_args()
t0 = time.time()

las = laspy.read(a.las)
xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
cls = np.asarray(las.classification, dtype=np.uint8)
del las
print(f"{len(xyz):,} puntos leídos ({time.time() - t0:.0f}s)", flush=True)

cen, inv = ac.voxelize(xyz, a.voxel)
nv = len(cen)
# etiqueta del vóxel = grupo mayoritario entre sus puntos
votes = np.zeros((nv, 3))
for g, codes in ac.GROUPS.items():
    votes[:, g - 1] = np.bincount(inv, weights=np.isin(cls, codes).astype(np.float64), minlength=nv)
has = votes.sum(1) > 0
y = np.where(has, votes.argmax(1) + 1, 0).astype(np.uint8)
del xyz, cls, inv
print(f"{nv:,} vóxeles ({a.voxel} m); etiquetados {int(has.sum()):,}", flush=True)

X = ac.features(cen)
print(f"rasgos {X.shape} ({time.time() - t0:.0f}s)", flush=True)

blk = (np.floor(cen[:, 0] / 20).astype(int) + np.floor(cen[:, 1] / 20).astype(int)) % 2
rng = np.random.default_rng(0)


def sample(mask):
    idx = np.flatnonzero(mask)
    return rng.choice(idx, a.max_train, replace=False) if len(idx) > a.max_train else idx


tr, te = sample(has & (blk == 0)), np.flatnonzero(has & (blk == 1))
model = ac.make_model().fit(X[tr], y[tr])
pred = model.predict(X[te])
cm = np.zeros((3, 3), int)
for i in (1, 2, 3):
    for j in (1, 2, 3):
        cm[i - 1, j - 1] = ((y[te] == i) & (pred == j)).sum()
print("VALIDACIÓN ESPACIAL (bloques 20 m)")
print("acierto", round(float(np.trace(cm) / cm.sum()), 3))
print("recall   terreno/estructura/vegetación", (np.diag(cm) / cm.sum(1)).round(3))
print("precisión", (np.diag(cm) / np.maximum(cm.sum(0), 1)).round(3))
print(cm, flush=True)

model = ac.make_model().fit(X[sample(has)], y[sample(has)])
os.makedirs(os.path.dirname(a.out), exist_ok=True)
joblib.dump({"model": model, "features": ac.FEATURE_NAMES, "voxel": a.voxel,
             "groups": ac.GROUPS, "trained_on": os.path.basename(a.las),
             "val_acc": float(np.trace(cm) / cm.sum())}, a.out)
print(f"modelo guardado en {a.out} ({time.time() - t0:.0f}s)")
````

## 3.6 `ifc_export.py`

Exportación IFC2X3, IFC4 e IFC4X3 con IfcOpenShell. 277 líneas.

````python
"""
ifc_export.py
Exporta una malla de terreno (TIN) a IFC en IFC2X3, IFC4 o IFC4X3.

- IFC4 / IFC4X3: IfcGeographicElement (TERRAIN) con IfcTriangulatedFaceSet.
- IFC2X3: IfcBuildingElementProxy con IfcFaceBasedSurfaceModel (esa versión no tiene
  IfcTriangulatedFaceSet ni IfcGeographicElement).
- Geometría en coordenadas locales (restando el mínimo). El desplazamiento a coordenadas
  reales va en IfcMapConversion (IFC4+, si se da EPSG) o en el placement del elemento.
"""
import numpy as np
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.owner.settings as owner_settings

_ORIG_OWNER = (owner_settings.get_user, owner_settings.get_application)

SCHEMAS = {"IFC2X3": "IFC2X3", "IFC4": "IFC4", "IFC4X3": "IFC4X3_ADD2"}


def export_terrain_ifc(vertices: np.ndarray, triangles: np.ndarray, path: str,
                       version: str = "IFC4", epsg: int | None = None,
                       name: str = "Terreno") -> str:
    version = version.upper().replace(".", "")
    if version not in SCHEMAS:
        raise ValueError(f"Versión '{version}' no válida. Usa: {', '.join(SCHEMAS)}.")
    if len(triangles) == 0:
        raise ValueError("La malla no tiene triángulos.")

    owner_settings.get_user, owner_settings.get_application = _ORIG_OWNER   # el parche de IFC2X3 no debe filtrarse a otras versiones
    f = ifcopenshell.file(schema=SCHEMAS[version])
    api = ifcopenshell.api
    if version == "IFC2X3":  # IFC2X3 exige OwnerHistory con usuario y aplicación
        person = f.createIfcPerson(None, "Open3D", "MCP")
        org = f.createIfcOrganization(None, "Open3D MCP")
        user = f.createIfcPersonAndOrganization(person, org)
        app = f.createIfcApplication(org, "1.0", "Open3D MCP Server", "Open3DMCP")
        owner_settings.get_user = lambda _f: user
        owner_settings.get_application = lambda _f: app
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name=name)
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})  # la geometría está en metros (el valor por defecto es milímetro)
    model = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=model)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Sitio")
    api.run("aggregate.assign_object", f, products=[site], relating_object=project)

    if version == "IFC2X3":
        terrain = api.run("root.create_entity", f, ifc_class="IfcBuildingElementProxy", name=name)
    else:
        terrain = api.run("root.create_entity", f, ifc_class="IfcGeographicElement", name=name,
                          predefined_type="TERRAIN")
    api.run("spatial.assign_container", f, products=[terrain], relating_structure=site)

    offset = vertices.min(axis=0)
    local = vertices - offset
    georef = False
    if epsg and version != "IFC2X3":
        api.run("georeference.add_georeferencing", f)
        api.run("georeference.edit_georeferencing", f,
                projected_crs={"Name": f"EPSG:{int(epsg)}"},
                coordinate_operation={"Eastings": float(offset[0]), "Northings": float(offset[1]),
                                      "OrthogonalHeight": float(offset[2])})
        georef = True
    if not georef:
        m = np.eye(4)
        m[:3, 3] = offset
        api.run("geometry.edit_object_placement", f, product=terrain, matrix=m)

    if version == "IFC2X3":
        pts = [f.createIfcCartesianPoint(v) for v in local.tolist()]
        faces = [f.createIfcFace([f.createIfcFaceOuterBound(
                    f.createIfcPolyLoop([pts[a], pts[b], pts[c]]), True)])
                 for a, b, c in triangles.tolist()]
        item = f.createIfcFaceBasedSurfaceModel([f.createIfcConnectedFaceSet(faces)])
        rep_type = "SurfaceModel"
    else:
        coords = f.createIfcCartesianPointList3D(local.tolist())
        item = f.createIfcTriangulatedFaceSet(coords, None, None, (triangles + 1).tolist(), None)
        rep_type = "Tessellation"
    rep = f.createIfcShapeRepresentation(body, "Body", rep_type, [item])
    api.run("geometry.assign_representation", f, product=terrain, representation=rep)

    pset = api.run("pset.add_pset", f, product=terrain, name="Pset_TerrenoOpen3D")
    api.run("pset.edit_pset", f, pset=pset, properties={
        "Vertices": int(len(vertices)), "Triangulos": int(len(triangles)),
        "OrigenX": float(offset[0]), "OrigenY": float(offset[1]), "OrigenZ": float(offset[2]),
        "EPSG": int(epsg) if epsg else 0})

    f.write(path)
    return path

# Propiedades estándar buildingSMART por clase: (Pset común, Qto de cantidades base)
_PSET_COMMON = {"IfcSlab": "Pset_SlabCommon", "IfcBeam": "Pset_BeamCommon", "IfcRailing": "Pset_RailingCommon",
                "IfcMember": "Pset_MemberCommon", "IfcWall": "Pset_WallCommon"}
_QTO = {"IfcSlab": "Qto_SlabBaseQuantities", "IfcBeam": "Qto_BeamBaseQuantities",
        "IfcMember": "Qto_MemberBaseQuantities", "IfcWall": "Qto_WallBaseQuantities"}


def _pt(item: dict, version: str):
    """Tipo predefinido del ítem para la versión (acepta str o dict por versión con clave 'default')."""
    pt = item.get("predefined_type")
    if isinstance(pt, dict):
        return pt.get(version, pt.get("default"))
    return pt


def export_elements_ifc(items: list, path: str, version: str = "IFC4", epsg: int | None = None,
                        name: str = "Modelo desde nube", options: dict | None = None) -> str:
    """
    Exporta elementos a IFC siguiendo la organización de buildingSMART.
    Cada ítem: name, ifc_class, vertices (coords reales), triangles, y opcionales volume, closed,
    predefined_type (str o dict por versión), part (lista de "TIPO:Nombre" de la jerarquía de puente).

    Jerarquía espacial:
    - IFC4X3: IfcProject > IfcSite > IfcBridge (GIRDER) > IfcBridgePart (SUPERSTRUCTURE > DECK,
      SUBSTRUCTURE > ABUTMENT). Los elementos se contienen en la parte que corresponde.
    - IFC4 / IFC2X3 (sin IfcBridge): IfcSite > IfcBuilding > IfcElementAssembly anidados con los mismos
      nombres; los elementos se agregan al ensamblaje.
    - Elementos sin `part` (terreno) se contienen en IfcSite.
    Propiedades: Pset común y Qto base de la clase (IFC4+) con IsExternal/LoadBearing y GrossVolume.
    Geometría local respecto al mínimo común; el desplazamiento va en IfcMapConversion (IFC4+ con EPSG)
    o en el placement de cada elemento.
    options (todas True por defecto): hierarchy (jerarquía de puente), psets (Pset común), qto (cantidades
    base), custom (Pset_Open3D con supuestos), georef (IfcMapConversion si hay EPSG).
    Si una clase no existe en el esquema elegido se usa IfcBuildingElementProxy.
    """
    version = version.upper().replace(".", "")
    if version not in SCHEMAS:
        raise ValueError(f"Versión '{version}' no válida. Usa: {', '.join(SCHEMAS)}.")
    if not items:
        raise ValueError("No hay elementos para exportar.")
    opt = {"hierarchy": True, "psets": True, "qto": True, "custom": True, "georef": True}
    opt.update({k: bool(v) for k, v in (options or {}).items() if k in opt})
    coords = str((options or {}).get("coords") or "local").lower()
    if coords not in ("local", "utm"):
        raise ValueError("coords debe ser 'local' o 'utm'")
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(SCHEMAS[version])

    owner_settings.get_user, owner_settings.get_application = _ORIG_OWNER   # el parche de IFC2X3 no debe filtrarse a otras versiones
    f = ifcopenshell.file(schema=SCHEMAS[version])
    api = ifcopenshell.api
    if version == "IFC2X3":
        person = f.createIfcPerson(None, "Open3D", "MCP")
        org = f.createIfcOrganization(None, "Open3D MCP")
        user = f.createIfcPersonAndOrganization(person, org)
        app = f.createIfcApplication(org, "1.0", "Open3D MCP Server", "Open3DMCP")
        owner_settings.get_user = lambda _f: user
        owner_settings.get_application = lambda _f: app
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name=name)
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})  # la geometría está en metros (el valor por defecto es milímetro)
    model = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=model)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Sitio")
    api.run("aggregate.assign_object", f, products=[site], relating_object=project)

    def create(cls, nm, pt=None):
        el = None
        if pt:
            try:
                el = api.run("root.create_entity", f, ifc_class=cls, name=nm, predefined_type=pt)
            except Exception:       # tipo predefinido no válido en esta versión: se omite
                pass
        if el is None:
            el = api.run("root.create_entity", f, ifc_class=cls, name=nm)
        try:                        # PredefinedType obligatorio (p. ej. IfcFooting en IFC2X3): NOTDEFINED
            for a in schema.declaration_by_name(cls).all_attributes():
                if a.name() == "PredefinedType" and not a.optional() and getattr(el, "PredefinedType", None) is None:
                    el.PredefinedType = "NOTDEFINED"
        except Exception:
            pass
        return el

    has_parts = opt["hierarchy"] and any(i.get("part") for i in items)
    root = site
    if has_parts:
        if version == "IFC4X3":
            root = create("IfcBridge", "Puente", "GIRDER")
        else:
            root = create("IfcBuilding", "Puente")
        api.run("aggregate.assign_object", f, products=[root], relating_object=site)

    containers = {}

    def container_for(parts):
        """Devuelve el contenedor (parte de puente o ensamblaje) para la ruta, creándolo si falta."""
        if not parts:
            return site, False
        parent, key = root, ()
        for p in parts:
            key += (p,)
            if key not in containers:
                kind, _, nm = p.partition(":")
                if version == "IFC4X3":
                    node = create("IfcBridgePart", nm, kind)
                    # UsageType es obligatorio en IfcBridgePart (IFC4X3_ADD2)
                    node.UsageType = "VERTICAL" if kind in ("SUBSTRUCTURE", "ABUTMENT", "PIER", "FOUNDATION") else "LONGITUDINAL"
                    api.run("aggregate.assign_object", f, products=[node], relating_object=parent)
                else:
                    node = create("IfcElementAssembly", nm, "NOTDEFINED")   # PredefinedType obligatorio en IFC2X3
                    if parent is root:
                        api.run("spatial.assign_container", f, products=[node], relating_structure=root)
                    else:
                        api.run("aggregate.assign_object", f, products=[node], relating_object=parent)
                containers[key] = node
            parent = containers[key]
        return parent, version != "IFC4X3"          # True: se agrega como parte de un ensamblaje

    offset = np.min([np.asarray(i["vertices"]).min(axis=0) for i in items], axis=0)
    # coords="local": origen del modelo en el mínimo común (x, y, z locales); IfcMapConversion lleva el
    #   desplazamiento a coordenadas reales si hay EPSG.
    # coords="utm": el modelo ya está en coordenadas reales (los elementos se colocan en `offset`);
    #   IfcMapConversion queda en identidad (0, 0, 0) si hay EPSG.
    georef = False
    if epsg and version != "IFC2X3" and opt["georef"]:
        api.run("georeference.add_georeferencing", f)
        e0, n0, h0 = (offset if coords == "local" else (0.0, 0.0, 0.0))
        api.run("georeference.edit_georeferencing", f, projected_crs={"Name": f"EPSG:{int(epsg)}"},
                coordinate_operation={"Eastings": float(e0), "Northings": float(n0), "OrthogonalHeight": float(h0)})
        georef = True

    for it in items:
        cls = it["ifc_class"]
        pt = _pt(it, version)
        try:
            schema.declaration_by_name(cls)
        except Exception:                      # la clase no existe en este esquema
            cls, pt = "IfcBuildingElementProxy", None
        el = create(cls, it["name"], pt)
        node, as_part = container_for(it.get("part") if opt["hierarchy"] else None)
        if as_part:
            api.run("aggregate.assign_object", f, products=[el], relating_object=node)
        else:
            api.run("spatial.assign_container", f, products=[el], relating_structure=node)
        if coords == "utm":
            m = np.eye(4)
            m[:3, 3] = offset
            api.run("geometry.edit_object_placement", f, product=el, matrix=m)
        V = np.asarray(it["vertices"]) - offset
        T = np.asarray(it["triangles"])
        if version == "IFC2X3":
            pts = [f.createIfcCartesianPoint(v) for v in V.tolist()]
            faces = [f.createIfcFace([f.createIfcFaceOuterBound(
                        f.createIfcPolyLoop([pts[a], pts[b], pts[c]]), True)]) for a, b, c in T.tolist()]
            if it.get("closed"):
                item = f.createIfcFacetedBrep(f.createIfcClosedShell(faces))
                rep_type = "Brep"
            else:
                item = f.createIfcFaceBasedSurfaceModel([f.createIfcConnectedFaceSet(faces)])
                rep_type = "SurfaceModel"
        else:
            coords = f.createIfcCartesianPointList3D(V.tolist())
            item = f.createIfcTriangulatedFaceSet(coords, None, bool(it.get("closed", False)),
                                                  (T + 1).tolist(), None)
            rep_type = "Tessellation"
        rep = f.createIfcShapeRepresentation(body, "Body", rep_type, [item])
        api.run("geometry.assign_representation", f, product=el, representation=rep)

        if opt["psets"] and cls in _PSET_COMMON:
            ps = api.run("pset.add_pset", f, product=el, name=_PSET_COMMON[cls])
            api.run("pset.edit_pset", f, pset=ps, properties={"IsExternal": True, "Reference": it["name"],
                                                              **({"LoadBearing": True} if cls != "IfcRailing" else {})})
        if opt["qto"] and version != "IFC2X3" and cls in _QTO and it.get("volume"):
            q = api.run("pset.add_qto", f, product=el, name=_QTO[cls])
            api.run("pset.edit_qto", f, qto=q, properties={"GrossVolume": float(it["volume"])})
        if opt["custom"]:
            props = {"Vertices": int(len(V)), "Triangulos": int(len(T))}
            if it.get("volume"):
                props["Volumen_m3"] = float(it["volume"])
            if it.get("assumptions"):
                props["Supuestos"] = str(it["assumptions"])
            pset = api.run("pset.add_pset", f, product=el, name="Pset_Open3D")
            api.run("pset.edit_pset", f, pset=pset, properties=props)

    f.write(path)
    return path
````

## 3.7 `ifc_dialog.py`

Ventana de exportación a IFC. 282 líneas.

````python
"""
ifc_dialog.py
Ventana de exportación a IFC: versiones, categorías IFC (clase y tipo predefinido por tipo de
elemento), alcance, estructura espacial, propiedades y georreferencia. Las restricciones se calculan
con los esquemas reales de IfcOpenShell (ifc_schema_info), no con una lista fija.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

import ifc_schema_info as info

VERSIONS = ("IFC2X3", "IFC4", "IFC4X3")

# tipo de elemento: (etiqueta, clases IFC permitidas, contenedor en la jerarquía de puente)
KINDS = {
    "losa": ("Losa de tablero", ["IfcSlab", "IfcPlate", "IfcCovering", "IfcBuildingElementProxy"],
             "Superestructura > Tablero (DECK)"),
    "viga": ("Vigas", ["IfcBeam", "IfcMember", "IfcBuildingElementProxy"],
             "Superestructura > Tablero (DECK)"),
    "baranda": ("Barandas (sólido simple)", ["IfcRailing", "IfcMember", "IfcBuildingElementProxy"],
                "Superestructura > Elementos de superficie y seguridad"),
    "riel": ("Rieles de baranda", ["IfcRailing", "IfcMember", "IfcBuildingElementProxy"],
             "Superestructura > Elementos de superficie y seguridad"),
    "poste": ("Postes de baranda", ["IfcMember", "IfcColumn", "IfcBuildingElementProxy"],
              "Superestructura > Elementos de superficie y seguridad"),
    "estribo": ("Estribos", ["IfcWall", "IfcFooting", "IfcBuildingElementProxy"],
                "Subestructura > una parte ABUTMENT por estribo"),
    "muro": ("Muros de retorno", ["IfcWall", "IfcBuildingElementProxy"],
             "Subestructura > en la parte ABUTMENT de su estribo"),
    "apoyo": ("Apoyos bajo vigas", ["IfcBearing", "IfcBuildingElementProxy"],
              "Subestructura > en la parte ABUTMENT de su estribo"),
    "terreno": ("Terreno (malla)", ["IfcGeographicElement", "IfcBuildingElementProxy"], "Sitio (IfcSite)"),
}
AUTO, NONE = "__auto__", "__none__"


class IfcExportDialog(QDialog):
    def __init__(self, parent, counts: dict, default_classes: dict, epsg: int, n_visible: int,
                 n_selected: int, default_path: str, default_types: dict | None = None):
        super().__init__(parent)
        self.default_types = default_types or {}
        self.setWindowTitle("Exportar a IFC")
        self.resize(1080, 720)
        self.rows = {}
        v = QVBoxLayout(self)

        gv = QGroupBox("Versiones de IFC (se genera un archivo por versión)")
        hv = QHBoxLayout(gv)
        self.chk_ver = {}
        for ver in VERSIONS:
            cb = QCheckBox(ver)
            cb.setChecked(True)
            cb.setToolTip(info.structure_note(ver))
            cb.toggled.connect(self._refresh)
            hv.addWidget(cb)
            self.chk_ver[ver] = cb
        hv.addStretch(1)
        v.addWidget(gv)
        self.lbl_struct = QLabel()
        self.lbl_struct.setWordWrap(True)
        self.lbl_struct.setStyleSheet("color: #aaa;")
        v.addWidget(self.lbl_struct)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Incluir", "Categoría (cantidad)", "Clase IFC",
                                              "Tipo predefinido", "Contenedor en la jerarquía", "Restricciones"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        for c, w in ((0, 60), (1, 170), (2, 180), (3, 190), (4, 220)):
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        for kind in KINDS:
            if counts.get(kind):
                self._add_row(kind, counts[kind], default_classes.get(kind, KINDS[kind][1][0]))
        self.table.resizeRowsToContents()
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        gs = QGroupBox("Alcance")
        ls = QVBoxLayout(gs)
        self.scope = QButtonGroup(self)
        self.rb = {}
        for key, text in (("all", "Todos los elementos"),
                          ("visible", f"Solo los visibles en el visor ({n_visible})"),
                          ("selected", f"Solo los seleccionados ({n_selected})")):
            rb = QRadioButton(text)
            self.scope.addButton(rb)
            ls.addWidget(rb)
            self.rb[key] = rb
        self.rb["all"].setChecked(True)
        self.rb["selected"].setEnabled(n_selected > 0)
        row.addWidget(gs)

        go = QGroupBox("Estructura y propiedades")
        lo = QVBoxLayout(go)
        self.chk_hier = QCheckBox("Jerarquía de puente (IfcBridge / IfcBridgePart; ensamblajes en IFC4 e IFC2X3)")
        self.chk_hier.setChecked(True)
        self.chk_hier.setToolTip("Desmarcada: todos los elementos cuelgan directamente de IfcSite")
        self.chk_ps = QCheckBox("Propiedades comunes buildingSMART (Pset_*Common)")
        self.chk_ps.setChecked(True)
        self.chk_qto = QCheckBox("Cantidades base (Qto_*BaseQuantities; solo IFC4 e IFC4X3)")
        self.chk_qto.setChecked(True)
        self.chk_custom = QCheckBox("Pset_Open3D: vértices, volumen y supuestos de modelado")
        self.chk_custom.setChecked(True)
        for w in (self.chk_hier, self.chk_ps, self.chk_qto, self.chk_custom):
            lo.addWidget(w)
        row.addWidget(go, 1)

        gg = QGroupBox("Georreferencia y validación")
        lg = QVBoxLayout(gg)
        self.chk_geo = QCheckBox("Georreferenciar (IfcMapConversion; solo IFC4 e IFC4X3)")
        self.chk_geo.setChecked(epsg > 0)
        self.chk_geo.toggled.connect(self._refresh)
        self.sp_epsg = QSpinBox()
        self.sp_epsg.setRange(0, 99999)
        self.sp_epsg.setValue(epsg)
        self.sp_epsg.setPrefix("EPSG: ")
        self.sp_epsg.setToolTip("0 = sin georreferencia. Ej.: 32718 (WGS84 / UTM 18S)")
        self.cb_coords = QComboBox()
        self.cb_coords.addItem("Coordenadas del modelo: UTM (reales)", "utm")
        self.cb_coords.addItem("Coordenadas del modelo: local x, y, z", "local")
        self.cb_coords.setCurrentIndex(0 if epsg > 0 else 1)
        self.cb_coords.setToolTip("UTM: los elementos quedan en coordenadas reales.\n"
                                  "Local: el origen del modelo es la esquina mínima de los elementos; con EPSG el "
                                  "desplazamiento a reales va en IfcMapConversion.")
        self.chk_val = QCheckBox("Validar con IfcOpenShell al terminar")
        self.chk_val.setChecked(True)
        for w in (self.chk_geo, self.sp_epsg, self.cb_coords, self.chk_val):
            lg.addWidget(w)
        row.addWidget(gg, 1)
        v.addLayout(row)

        hp = QHBoxLayout()
        hp.addWidget(QLabel("Archivo:"))
        self.ed_path = QLineEdit(default_path)
        hp.addWidget(self.ed_path, 1)
        b = QPushButton("Examinar…")
        b.clicked.connect(self._browse)
        hp.addWidget(b)
        v.addLayout(hp)
        self.lbl_hint = QLabel("Con varias versiones se agrega el sufijo _IFC2X3, _IFC4 o _IFC4X3 al nombre.")
        self.lbl_hint.setStyleSheet("color: #888;")
        v.addWidget(self.lbl_hint)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Exportar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self._refresh()

    # ------------------------------------------------------------------ filas
    def _add_row(self, kind, count, default_cls):
        label, classes, container = KINDS[kind]
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QCheckBox()
        chk.setChecked(True)
        chk.toggled.connect(self._refresh)
        holder = QLabel()           # centra la casilla
        cell = QHBoxLayout(holder)
        cell.addWidget(chk, alignment=Qt.AlignCenter)
        cell.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(r, 0, holder)
        self.table.setItem(r, 1, QTableWidgetItem(f"{label} ({count})"))
        cls = QComboBox()
        cls.addItems(classes)
        if default_cls in classes:
            cls.setCurrentText(default_cls)
        cls.currentTextChanged.connect(lambda _t, k=kind: self._class_changed(k))
        self.table.setCellWidget(r, 2, cls)
        pt = QComboBox()
        pt.currentIndexChanged.connect(lambda _i, k=kind: self._refresh_row(k))
        self.table.setCellWidget(r, 3, pt)
        self.table.setItem(r, 4, QTableWidgetItem(container))
        self.table.setItem(r, 5, QTableWidgetItem(""))
        for c in (1, 4, 5):
            self.table.item(r, c).setFlags(Qt.ItemIsEnabled)
        self.rows[kind] = {"row": r, "chk": chk, "cls": cls, "pt": pt}

    def _versions(self):
        return [v for v in VERSIONS if self.chk_ver[v].isChecked()]

    def _class_changed(self, kind):
        self._fill_types(kind)
        self._refresh_row(kind)

    def _fill_types(self, kind):
        """Tipos predefinidos válidos: unión de las versiones elegidas, marcando los que no valen en todas."""
        rw = self.rows[kind]
        cls = rw["cls"].currentText()
        vers = self._versions() or list(VERSIONS)
        prev = rw["pt"].currentData()
        rw["pt"].blockSignals(True)
        rw["pt"].clear()
        rw["pt"].addItem("(predeterminado por versión)", AUTO)
        rw["pt"].addItem("(sin tipo)", NONE)
        seen = []
        for ver in vers:
            for t in info.predefined_types(ver, cls):
                if t not in seen:
                    seen.append(t)
        for t in seen:
            ok = [x for x in vers if t in info.predefined_types(x, cls)]
            rw["pt"].addItem(t if len(ok) == len(vers) else f"{t}  (solo {', '.join(ok)})", t)
        idx = rw["pt"].findData(prev) if prev is not None else 0
        rw["pt"].setCurrentIndex(max(idx, 0))
        rw["pt"].blockSignals(False)

    def _restrictions(self, kind):
        rw = self.rows[kind]
        cls, pt = rw["cls"].currentText(), rw["pt"].currentData()
        notes = []
        for ver in self._versions():
            if not info.class_exists(ver, cls):
                notes.append(f"{ver}: {cls} no existe, se usa IfcBuildingElementProxy")
                continue
            valid = info.predefined_types(ver, cls)
            if pt == NONE:
                continue
            eff = pt
            if pt == AUTO:                       # el predeterminado de la categoría para esa versión
                d = self.default_types.get(kind)
                eff = d.get(ver, d.get("default")) if isinstance(d, dict) else d
                if cls != KINDS[kind][1][0]:     # con otra clase el predeterminado no aplica
                    eff = None
            if not eff:
                continue
            if not valid:
                notes.append(f"{ver}: {cls} no tiene tipo predefinido, se omite {eff}")
            elif eff not in valid:
                notes.append(f"{ver}: '{eff}' no es válido en {cls}, se omite")
        if cls == "IfcBuildingElementProxy":
            notes.append("clase genérica: pierde la semántica del elemento")
        return "; ".join(notes) if notes else "Sin restricciones en las versiones elegidas"

    def _refresh_row(self, kind):
        rw = self.rows[kind]
        it = self.table.item(rw["row"], 5)
        it.setText(self._restrictions(kind))
        warn = "no " in it.text() or "omite" in it.text() or "genérica" in it.text()
        it.setForeground(QBrush(QColor("#e8c547" if warn else "#5fd68a")))
        self.table.resizeRowToContents(rw["row"])

    def _refresh(self, *_):
        vers = self._versions()
        self.lbl_struct.setText("\n".join(info.structure_note(v) for v in vers) or "Elige al menos una versión.")
        for kind in self.rows:
            self._fill_types(kind)
            self._refresh_row(kind)
        any_geo = any(v != "IFC2X3" for v in vers)
        self.chk_geo.setEnabled(any_geo)
        self.sp_epsg.setEnabled(any_geo and self.chk_geo.isChecked())
        self.chk_qto.setEnabled(any_geo)
        ok = bool(vers) and any(rw["chk"].isChecked() for rw in self.rows.values())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar IFC", self.ed_path.text(), "IFC (*.ifc)")
        if path:
            self.ed_path.setText(path)

    # ------------------------------------------------------------------ resultado
    def config(self) -> dict:
        cats = {}
        for kind, rw in self.rows.items():
            d = rw["pt"].currentData()
            cats[kind] = {"include": rw["chk"].isChecked(), "ifc_class": rw["cls"].currentText(),
                          "predefined_type": None if d == AUTO else ("" if d == NONE else d)}
        scope = next(k for k, rb in self.rb.items() if rb.isChecked())
        epsg = self.sp_epsg.value() if self.chk_geo.isChecked() and self.chk_geo.isEnabled() else None
        return {"versions": self._versions(), "categories": cats, "scope": scope,
                "hierarchy": self.chk_hier.isChecked(), "psets": self.chk_ps.isChecked(),
                "qto": self.chk_qto.isChecked(), "custom": self.chk_custom.isChecked(),
                "georef": epsg is not None, "epsg": epsg, "coords": self.cb_coords.currentData(), "validate": self.chk_val.isChecked(),
                "path": self.ed_path.text().strip()}
````

## 3.8 `ifc_schema_info.py`

Restricciones de clase y tipo predefinido leídas de los esquemas IFC. 51 líneas.

````python
"""
ifc_schema_info.py
Consulta los esquemas IFC (IfcOpenShell) para mostrar en la ventana de exportación qué clases y
tipos predefinidos existen en cada versión. Son las restricciones reales del estándar, no una lista a mano.
"""
import functools

import ifcopenshell.ifcopenshell_wrapper as w

SCHEMA_IDS = {"IFC2X3": "IFC2X3", "IFC4": "IFC4", "IFC4X3": "IFC4X3_ADD2"}


@functools.lru_cache(maxsize=None)
def _schema(version: str):
    return w.schema_by_name(SCHEMA_IDS[version])


def class_exists(version: str, ifc_class: str) -> bool:
    try:
        _schema(version).declaration_by_name(ifc_class)
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=None)
def predefined_types(version: str, ifc_class: str) -> tuple:
    """Valores del atributo PredefinedType de la clase en esa versión (vacío si no tiene)."""
    try:
        decl = _schema(version).declaration_by_name(ifc_class)
        for a in decl.all_attributes():
            if a.name() == "PredefinedType":
                t = a.type_of_attribute()
                en = t.declared_type() if hasattr(t, "declared_type") else t
                return tuple(en.enumeration_items())
    except Exception:
        pass
    return ()


def structure_note(version: str) -> str:
    """Cómo se organiza el modelo en cada versión (limitaciones del esquema)."""
    return {
        "IFC2X3": "IFC2X3: sin IfcBridge ni IfcBridgePart (se usan IfcBuilding e IfcElementAssembly), "
                  "sin IfcGeographicElement (se usa IfcBuildingElementProxy) y sin IfcMapConversion "
                  "(no se puede georreferenciar). Es la versión que lee Revit 2022.",
        "IFC4": "IFC4: sin IfcBridge (se usan IfcBuilding e IfcElementAssembly); con IfcGeographicElement "
                "e IfcMapConversion. Revit 2022 lo importa solo como referencia.",
        "IFC4X3": "IFC4X3: jerarquía IfcBridge > IfcBridgePart (UsageType obligatorio), con tipos como "
                  "GIRDER_SEGMENT. Revit 2022 no lo lee.",
    }[version]
````

## 3.9 `dxf_export.py`

Exportación de líneas a DXF (AutoCAD), UTM o local. 190 líneas.

````python
"""
dxf_export.py
Exporta las líneas del modelo (aristas de los sólidos, sección, eje, curvas de nivel del terreno,
cotas y medidas) a DXF para AutoCAD.

Georreferencia: las coordenadas se escriben en el sistema del proyecto (p. ej. UTM, `coords="utm"`)
o en local x, y, z respecto al origen de la nube (`coords="local"`). El DXF no tiene un campo estándar
para el CRS, así que además de las coordenadas reales se escribe un `.prj` (WKT) y un `.txt` con el
EPSG y el origen; en AutoCAD Map/Civil 3D se asigna con MAPCSASSIGN o GEO.
"""
from __future__ import annotations

import os

import numpy as np

LAYERS = {   # nombre: color ACI
    "EJE": 1, "SECCION": 4, "LOSA": 5, "VIGA": 30, "BARANDA": 3, "RIEL": 3, "POSTE": 2, "ESTRIBO": 8,
    "MURO": 6, "APOYO": 1, "VIA": 9, "TERRENO_CURVAS": 94, "TERRENO_MALLA": 252, "COTAS": 7, "MEDIDAS": 40,
}
KIND_LAYER = {"losa": "LOSA", "viga": "VIGA", "baranda": "BARANDA", "riel": "RIEL", "poste": "POSTE",
              "estribo": "ESTRIBO", "muro": "MURO", "apoyo": "APOYO", "via": "VIA"}


def chain_segments(a: np.ndarray, b: np.ndarray, decimals: int = 4) -> list:
    """Une segmentos sueltos (a_i -> b_i) en polilíneas (listas de puntos) recorriendo vértices de grado 2."""
    key = lambda p: tuple(np.round(p, decimals))
    adj, pts = {}, {}
    for p, q in zip(a, b):
        kp, kq = key(p), key(q)
        if kp == kq:
            continue
        pts[kp], pts[kq] = p, q
        adj.setdefault(kp, set()).add(kq)
        adj.setdefault(kq, set()).add(kp)
    seen, out = set(), []
    # empezar por los extremos (grado != 2) para no cortar cadenas abiertas por la mitad
    order = [k for k, v in adj.items() if len(v) != 2] + list(adj)
    for start in order:
        for nxt in list(adj[start]):
            if frozenset((start, nxt)) in seen:
                continue
            path, prev, cur = [start, nxt], start, nxt
            seen.add(frozenset((start, nxt)))
            while len(adj[cur]) == 2:
                n2 = next(x for x in adj[cur] if x != prev)
                e = frozenset((cur, n2))
                if e in seen:
                    break
                seen.add(e)
                path.append(n2)
                prev, cur = cur, n2
            out.append(np.array([pts[k] for k in path]))
    return out


def feature_edges(vertices: np.ndarray, triangles: np.ndarray, angle_deg: float = 30.0) -> list:
    """Polilíneas de las aristas de borde y de las aristas con ángulo diedro > angle_deg."""
    V, T = np.asarray(vertices, float), np.asarray(triangles)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    n = n / np.where(ln > 0, ln, 1)[:, None]
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    tri = np.tile(np.arange(len(T)), 3)
    lo, hi = e.min(axis=1), e.max(axis=1)
    key = lo.astype(np.int64) * (len(V) + 1) + hi
    order = np.argsort(key, kind="stable")
    ks, ts = key[order], tri[order]
    first = np.r_[True, ks[1:] != ks[:-1]]
    idx = np.flatnonzero(first)
    cnt = np.diff(np.r_[idx, len(ks)])
    a_, b_ = lo[order][idx], hi[order][idx]
    keep = cnt == 1                                      # borde abierto
    two = np.flatnonzero(cnt == 2)
    if len(two):
        cosang = (n[ts[idx[two]]] * n[ts[idx[two] + 1]]).sum(axis=1)
        keep[two] = cosang < np.cos(np.radians(angle_deg))
    return chain_segments(V[a_[keep]], V[b_[keep]])


def contour_lines(vertices: np.ndarray, triangles: np.ndarray, interval: float = 1.0) -> list:
    """Curvas de nivel del TIN (cada una a su cota, 3D) con matplotlib.tri."""
    from matplotlib.figure import Figure
    import matplotlib.tri as mtri
    V = np.asarray(vertices, float)
    levels = np.arange(np.ceil(V[:, 2].min() / interval) * interval, V[:, 2].max(), interval)
    if len(levels) == 0:
        return []
    tri = mtri.Triangulation(V[:, 0], V[:, 1], np.asarray(triangles))
    cs = Figure().add_subplot().tricontour(tri, V[:, 2], levels=levels)
    out = []
    for lv, segs in zip(cs.levels, cs.allsegs):
        for seg in segs:
            if len(seg) >= 2:
                out.append(np.c_[seg, np.full(len(seg), lv)])
    return out


def _utm_wkt(epsg: int):
    """WKT ESRI de WGS84 / UTM (EPSG 32601-32660 norte, 32701-32760 sur); None para otros EPSG."""
    if 32601 <= epsg <= 32660:
        zone, south = epsg - 32600, False
    elif 32701 <= epsg <= 32760:
        zone, south = epsg - 32700, True
    else:
        return None
    lon0 = -183 + 6 * zone
    return (f'PROJCS["WGS_1984_UTM_Zone_{zone}{"S" if south else "N"}",GEOGCS["GCS_WGS_1984",'
            f'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
            f'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],'
            f'PARAMETER["False_Northing",{10000000.0 if south else 0.0}],PARAMETER["Central_Meridian",{lon0}.0],'
            f'PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')


def write_dxf(path: str, lines: dict, annotations: list | None = None, origin=(0.0, 0.0, 0.0),
              coords: str = "utm", epsg: int | None = None, mesh: tuple | None = None) -> dict:
    """
    lines: {capa: [ndarray (N,3) en coords locales]}; annotations: [{"type": "cota"|"medida", ...}] en locales;
    mesh: (vertices, triangles) locales del terreno para 3DFACE (opcional).
    Devuelve un resumen con el archivo, conteos y los archivos auxiliares (.prj, .txt).
    """
    import ezdxf
    if coords not in ("utm", "local"):
        raise ValueError("coords debe ser 'utm' o 'local'")
    o = np.asarray(origin, float) if coords == "utm" else np.zeros(3)
    doc = ezdxf.new("R2018")
    doc.units = 6                                           # metros
    msp = doc.modelspace()
    for nm, col in LAYERS.items():
        doc.layers.add(nm, color=col)
    n_lines = n_pts = n_txt = 0
    for layer, polys in lines.items():
        if layer not in doc.layers:
            doc.layers.add(layer, color=7)
        for p in polys:
            p = np.asarray(p, float) + o
            if len(p) >= 2:
                msp.add_polyline3d(p.tolist(), dxfattribs={"layer": layer})
                n_lines += 1
    th = 0.5
    for an in annotations or []:
        if an["type"] == "cota":
            p = np.asarray(an["p"], float) + o
            msp.add_point(p.tolist(), dxfattribs={"layer": "COTAS"})
            txt = an.get("text") or f"Z={p[2]:.3f}"
            t = msp.add_text(txt, height=th, dxfattribs={"layer": "COTAS"})
            t.set_placement((p[0] + 0.3, p[1] + 0.3, p[2]))
            n_pts += 1
            n_txt += 1
        elif an["type"] == "medida":
            a, b = np.asarray(an["a"], float) + o, np.asarray(an["b"], float) + o
            msp.add_line(a.tolist(), b.tolist(), dxfattribs={"layer": "MEDIDAS"})
            m = (a + b) / 2
            t = msp.add_text(an.get("text") or f"{np.linalg.norm(b - a):.3f} m", height=th,
                             dxfattribs={"layer": "MEDIDAS"})
            t.set_placement((m[0], m[1], m[2]))
            n_lines += 1
            n_txt += 1
    n_faces = 0
    if mesh is not None:
        V, T = np.asarray(mesh[0], float) + o, np.asarray(mesh[1])
        for a, b, c in T:
            msp.add_3dface([V[a].tolist(), V[b].tolist(), V[c].tolist(), V[c].tolist()],
                           dxfattribs={"layer": "TERRENO_MALLA"})
        n_faces = len(T)
    doc.header["$PROJECTNAME"] = f"EPSG:{epsg}" if epsg else "local"
    doc.saveas(path)
    base = os.path.splitext(path)[0]
    aux = []
    info = [f"Sistema de coordenadas del DXF: {'UTM (coordenadas reales)' if coords == 'utm' else 'local x, y, z'}",
            f"EPSG: {epsg if epsg else 'no definido'}",
            f"Origen local (real E, N, cota): {origin[0]:.4f}, {origin[1]:.4f}, {origin[2]:.4f}",
            "Para asignar el CRS en AutoCAD Map/Civil 3D: MAPCSASSIGN (o GEO) con el EPSG indicado."]
    with open(base + "_georef.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(info) + "\n")
    aux.append(base + "_georef.txt")
    if epsg and coords == "utm":
        wkt = _utm_wkt(int(epsg))
        if wkt is None:
            try:
                from pyproj import CRS
                wkt = CRS.from_epsg(int(epsg)).to_wkt(version="WKT1_ESRI")
            except Exception:
                wkt = None
        if wkt:
            with open(base + ".prj", "w", encoding="utf-8") as f:
                f.write(wkt)
            aux.append(base + ".prj")
    return {"file": path, "aux": aux, "coords": coords, "epsg": epsg, "polylines": n_lines, "points": n_pts,
            "texts": n_txt, "faces3d": n_faces, "layers": {k: len(v) for k, v in lines.items()}}
````

## 3.10 `viewer_tools.py`

Caja de secciones, asas arrastrables, consulta, cotas y medidas. 709 líneas.

````python
"""
viewer_tools.py
Herramientas del visor 3D: caja de secciones (recorte en el marco del eje del puente o en el marco
del mundo), consulta de coordenadas/cotas, cotas persistentes y medidas. Todo trabaja en las coordenadas
locales de la nube (restar/sumar `proc.origin` para pasar a reales). La GUI crea un `ViewerTools`
y le delega los clics del visor mientras haya una herramienta activa.
"""
from __future__ import annotations

import csv

import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from vtkmodules.vtkCommonDataModel import vtkPlane
from vtkmodules.vtkRenderingCore import vtkPropPicker

KEEP_PREFIXES = ("ann_", "secbox")          # actores de ayuda: nunca se recortan
HANDLE_PX = 22                              # radio de captura (píxeles) de las asas de la caja
FACES = ("s0", "s1", "t0", "t1", "z0", "z1")
FACE_LABEL = {"s0": "s mín", "s1": "s máx", "t0": "t mín", "t1": "t máx", "z0": "z mín", "z1": "z máx"}
FACE_COLOR = {"s": "#ff5050", "t": "#50d060", "z": "#5090ff"}
SNAP_RADIUS = 0.35                          # m: imán a vértices de sólidos, malla y líneas de referencia


class DxfDialog(QDialog):
    def __init__(self, parent, epsg: int, has_mesh: bool, default_path: str):
        super().__init__(parent)
        self.setWindowTitle("Exportar líneas a AutoCAD (DXF)")
        lo = QVBoxLayout(self)
        form = QFormLayout()
        self.cb_coords = QComboBox()
        self.cb_coords.addItem("UTM (coordenadas reales, georreferenciado)", "utm")
        self.cb_coords.addItem("Local x, y, z (origen de la nube)", "local")
        self.sp_epsg = QDoubleSpinBox()
        self.sp_epsg.setDecimals(0)
        self.sp_epsg.setRange(0, 99999)
        self.sp_epsg.setValue(epsg)
        self.sp_epsg.setPrefix("EPSG: ")
        self.sp_angle = QDoubleSpinBox()
        self.sp_angle.setRange(5, 89)
        self.sp_angle.setValue(30)
        self.sp_angle.setSuffix(" °")
        self.sp_cont = QDoubleSpinBox()
        self.sp_cont.setRange(0, 50)
        self.sp_cont.setValue(1.0 if has_mesh else 0.0)
        self.sp_cont.setSuffix(" m (0 = sin curvas)")
        self.sp_cont.setEnabled(has_mesh)
        self.chk_mesh = QCheckBox("Incluir malla del terreno como 3DFACE")
        self.chk_mesh.setEnabled(has_mesh)
        form.addRow("Coordenadas", self.cb_coords)
        form.addRow("CRS", self.sp_epsg)
        form.addRow("Ángulo mínimo de arista", self.sp_angle)
        form.addRow("Curvas de nivel cada", self.sp_cont)
        lo.addLayout(form)
        lo.addWidget(self.chk_mesh)
        note = QLabel("Líneas: eje, sección, aristas de cada elemento (capas por tipo), curvas de nivel, "
                      "cotas y medidas. Se escribe un .prj y un _georef.txt junto al DXF.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        lo.addWidget(note)
        bar = QHBoxLayout()
        self.path = default_path
        self.lbl = QLabel(default_path)
        b = QPushButton("Examinar…")
        b.clicked.connect(self._browse)
        bar.addWidget(self.lbl, 1)
        bar.addWidget(b)
        lo.addLayout(bar)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lo.addWidget(bb)

    def _browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Exportar DXF", self.path, "DXF (*.dxf)")
        if p:
            self.path = p
            self.lbl.setText(p)

    def config(self) -> dict:
        p = self.path if self.path.lower().endswith(".dxf") else self.path + ".dxf"
        epsg = int(self.sp_epsg.value())
        return {"path": p, "coords": self.cb_coords.currentData(), "epsg": epsg or None,
                "edge_angle": self.sp_angle.value(), "contour_interval": self.sp_cont.value(),
                "terrain_mesh": self.chk_mesh.isChecked()}


class ViewerTools:
    def __init__(self, win):
        self.win = win
        self.plotter = win.plotter
        self.proc = win.proc
        self.mode = None                   # None | "consulta" | "cota" | "medida"
        self._pending_a = None             # primer punto de una medida
        self._picker = vtkPropPicker()
        self._planes = []                  # vtkPlane x 6 si la caja está activa
        self._snap = None                  # (KDTree, puntos) de referencia
        self._snap_key = None
        self.spins = {}
        self.chk_box = None
        self._drag = None                  # dict mientras se arrastra un asa
        self._wrap_add_mesh()
        self._install_drag()

    # ----------------------------------------------------------- marco y límites de la caja
    def frame(self):
        """(c, d, n, nombre): eje del puente si hay sección; si no, los ejes del mundo."""
        sec = self.proc.section
        if self.frame_mode == "puente" and sec is not None and sec.frame is not None:
            c, d, n = (np.asarray(x, float) for x in sec.frame)
            return c, d, n
        return np.zeros(2), np.array([1.0, 0.0]), np.array([0.0, 1.0])

    frame_mode = "puente"

    def scene_points(self, max_pts=300000):
        vs = [s.vertices for s in self.proc.solids]
        if self.proc.mesh is not None:
            vs.append(self.proc.mesh.vertices)
        if vs:
            return np.vstack(vs)
        p = self.proc.points
        if p is None:
            return None
        return p[::max(1, len(p) // max_pts)]

    def full_range(self):
        """Límites (s0, s1, t0, t1, z0, z1) de toda la escena en el marco activo."""
        V = self.scene_points()
        if V is None:
            return None
        c, d, n = self.frame()
        q = V[:, :2] - c
        s, t = q @ d, q @ n
        return [float(s.min()), float(s.max()), float(t.min()), float(t.max()), float(V[:, 2].min()),
                float(V[:, 2].max())]

    def current_bounds(self):
        return [self.spins[k].value() for k in ("s0", "s1", "t0", "t1", "z0", "z1")]

    # --------------------------------------------------------------------- caja de secciones
    def set_box(self, on=None, bounds=None, frame=None, show=None):
        if frame is not None:
            self.frame_mode = frame
            self.cb_frame.blockSignals(True)
            self.cb_frame.setCurrentIndex(0 if frame == "puente" else 1)
            self.cb_frame.blockSignals(False)
            self.reset_bounds()
        if bounds is not None:
            for k, v in zip(("s0", "s1", "t0", "t1", "z0", "z1"), bounds):
                if v is not None:
                    self.spins[k].blockSignals(True)
                    self.spins[k].setValue(float(v))
                    self.spins[k].blockSignals(False)
        if show is not None:
            self.chk_show.blockSignals(True)
            self.chk_show.setChecked(bool(show))
            self.chk_show.blockSignals(False)
        if on is not None:
            self.chk_box.blockSignals(True)
            self.chk_box.setChecked(bool(on))
            self.chk_box.blockSignals(False)
        self._update_box()
        return self.box_state()

    def box_state(self):
        return {"on": self.chk_box.isChecked(), "frame": self.frame_mode, "bounds": self.current_bounds()}

    def reset_bounds(self):
        r = self.full_range()
        if r is None:
            return
        pad = 0.5
        vals = [r[0] - pad, r[1] + pad, r[2] - pad, r[3] + pad, r[4] - pad, r[5] + pad]
        for k, v in zip(("s0", "s1", "t0", "t1", "z0", "z1"), vals):
            sp = self.spins[k]
            sp.blockSignals(True)
            sp.setRange(vals[0] - 200 if k[0] == "s" else (vals[2] - 200 if k[0] == "t" else vals[4] - 200),
                        vals[1] + 200 if k[0] == "s" else (vals[3] + 200 if k[0] == "t" else vals[5] + 200))
            sp.setValue(v)
            sp.blockSignals(False)

    def _make_planes(self):
        c, d, n = self.frame()
        s0, s1, t0, t1, z0, z1 = self.current_bounds()
        c3 = np.array([c[0], c[1], 0.0])
        d3, n3 = np.array([d[0], d[1], 0.0]), np.array([n[0], n[1], 0.0])
        z3 = np.array([0.0, 0.0, 1.0])
        defs = [(c3 + s0 * d3, d3), (c3 + s1 * d3, -d3), (c3 + t0 * n3, n3), (c3 + t1 * n3, -n3),
                (np.array([0, 0, z0]), z3), (np.array([0, 0, z1]), -z3)]
        planes = []
        for o, nv in defs:
            p = vtkPlane()
            p.SetOrigin(*o)
            p.SetNormal(*nv)
            planes.append(p)
        return planes

    def _update_box(self):
        on = self.chk_box.isChecked()
        self._planes = self._make_planes() if on else []
        self._apply_all()
        self._draw_outline()
        self.win._request_render()

    def _apply_to(self, actor):
        m = getattr(actor, "GetMapper", lambda: None)()
        if m is None:
            return
        m.RemoveAllClippingPlanes()
        if not self._planes:
            return
        for p in self._planes:
            m.AddClippingPlane(p)

    def _apply_all(self):
        for name, a in list(self.plotter.renderer.actors.items()):
            if name.startswith(KEEP_PREFIXES):
                continue
            self._apply_to(a)

    def _wrap_add_mesh(self):
        orig = self.plotter.add_mesh

        def add_mesh(*a, **kw):
            res = orig(*a, **kw)
            act = res[0] if isinstance(res, tuple) else res
            name = kw.get("name") or ""
            if self._planes and not str(name).startswith(KEEP_PREFIXES):
                try:
                    self._apply_to(act)
                except Exception:
                    pass
            return res
        self.plotter.add_mesh = add_mesh

    def face_centers(self):
        """Centro 3D (coords locales) y eje unitario de cada cara de la caja."""
        c, d, n = self.frame()
        s0, s1, t0, t1, z0, z1 = self.current_bounds()
        sm, tm, zm = (s0 + s1) / 2, (t0 + t1) / 2, (z0 + z1) / 2

        def at(s, t, z):
            xy = c + s * d + t * n
            return np.array([xy[0], xy[1], z])
        d3, n3 = np.array([d[0], d[1], 0.0]), np.array([n[0], n[1], 0.0])
        return {"s0": (at(s0, tm, zm), d3), "s1": (at(s1, tm, zm), d3), "t0": (at(sm, t0, zm), n3),
                "t1": (at(sm, t1, zm), n3), "z0": (at(sm, tm, z0), np.array([0.0, 0.0, 1.0])),
                "z1": (at(sm, tm, z1), np.array([0.0, 0.0, 1.0]))}

    def _draw_handles(self):
        for k in FACES:
            self.plotter.remove_actor(f"secbox_h_{k}")
        if not (self.chk_box.isChecked() and self.chk_show.isChecked()):
            return
        fc = self.face_centers()
        b = self.current_bounds()
        r = max(0.5, 0.03 * max(b[1] - b[0], b[3] - b[2], b[5] - b[4]))
        for k, (cen, _) in fc.items():
            hot = self._drag is not None and self._drag["face"] == k
            self.plotter.add_mesh(pv.Sphere(radius=r * (1.5 if hot else 1.0), center=cen),
                                  color="#ffffff" if hot else FACE_COLOR[k[0]], name=f"secbox_h_{k}", pickable=False)

    # ----------------------------------------------------------------- arrastre con el cursor
    def _install_drag(self):
        iren = self.plotter.iren.interactor
        self._drag_cmds = {}
        for ev, fn, pri in (("LeftButtonPressEvent", self._on_press, 30.0), ("MouseMoveEvent", self._on_move, 30.0),
                            ("LeftButtonReleaseEvent", self._on_release, 30.0)):
            tag = iren.AddObserver(ev, fn, pri)
            cmd = iren.GetCommand(tag)
            cmd.SetPassiveObserver(True)       # sigue recibiendo eventos aunque el estilo tenga el foco
            self._drag_cmds[ev] = cmd

    def _to_display(self, p):
        ren = self.plotter.renderer
        ren.SetWorldPoint(float(p[0]), float(p[1]), float(p[2]), 1.0)
        ren.WorldToDisplay()
        x, y, _ = ren.GetDisplayPoint()
        return np.array([x, y])

    def handle_at(self, x, y):
        """Cara cuya asa está a menos de HANDLE_PX del cursor (la más cercana) o None."""
        if not (self.chk_box.isChecked() and self.chk_show.isChecked()):
            return None
        best = None
        for k, (cen, _) in self.face_centers().items():
            dpx = float(np.linalg.norm(self._to_display(cen) - np.array([x, y])))
            if dpx <= HANDLE_PX and (best is None or dpx < best[1]):
                best = (k, dpx)
        return best[0] if best else None

    def begin_drag(self, face, x, y):
        cen, v = self.face_centers()[face]
        u = self._to_display(cen + v) - self._to_display(cen)        # píxeles por metro a lo largo del eje
        ppm = float(np.linalg.norm(u))
        if ppm < 1e-6:
            return False
        self._drag = {"face": face, "x0": x, "y0": y, "u": u / ppm, "ppm": ppm, "v0": self.spins[face].value()}
        self._draw_handles()
        return True

    def move_drag(self, x, y):
        dg = self._drag
        delta = float((np.array([x - dg["x0"], y - dg["y0"]]) @ dg["u"]) / dg["ppm"])
        val = dg["v0"] + delta
        k = dg["face"]
        mn, mx = self.spins[k[0] + "0"], self.spins[k[0] + "1"]
        if k.endswith("0"):
            val = min(val, mx.value() - 0.05)
        else:
            val = max(val, mn.value() + 0.05)
        sp = self.spins[k]
        sp.blockSignals(True)
        sp.setValue(val)
        sp.blockSignals(False)
        self._update_box()
        self.lbl_info.setText(f"Cara {FACE_LABEL[k]} = {sp.value():.3f}")

    def end_drag(self):
        self._drag = None
        self._draw_handles()
        self.win._request_render()

    def _on_press(self, obj, ev):
        cmd = self._drag_cmds[ev]
        cmd.SetAbortFlag(0)
        if self.mode is not None:
            return
        x, y = obj.GetEventPosition()
        face = self.handle_at(x, y)
        if face is not None and self.begin_drag(face, x, y):
            cmd.SetAbortFlag(1)                # el estilo de cámara no recibe este clic

    def _on_move(self, obj, ev):
        cmd = self._drag_cmds[ev]
        cmd.SetAbortFlag(0)
        if self._drag is None:
            return
        x, y = obj.GetEventPosition()
        self.move_drag(x, y)
        cmd.SetAbortFlag(1)

    def _on_release(self, obj, ev):
        cmd = self._drag_cmds[ev]
        cmd.SetAbortFlag(0)
        if self._drag is not None:
            self.end_drag()
            cmd.SetAbortFlag(1)

    def drag_face(self, face, dx=0, dy=0, x=None, y=None):
        """Simula con eventos reales un arrastre del asa `face`; sirve para el puente y las pruebas."""
        cen, _ = self.face_centers()[face]
        px = self._to_display(cen)
        x0, y0 = (int(px[0]), int(px[1])) if x is None else (int(x), int(y))
        iren = self.plotter.iren.interactor
        iren.SetEventInformation(x0, y0, 0, 0)
        iren.LeftButtonPressEvent()
        iren.SetEventInformation(x0 + int(dx), y0 + int(dy), 0, 0)
        iren.MouseMoveEvent()
        iren.LeftButtonReleaseEvent()
        return self.box_state()

    def _draw_outline(self):
        self.plotter.remove_actor("secbox_outline")
        self._draw_handles()
        if not (self.chk_box.isChecked() and self.chk_show.isChecked()):
            return
        c, d, n = self.frame()
        s0, s1, t0, t1, z0, z1 = self.current_bounds()
        pts = []
        for z in (z0, z1):
            for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1)):
                xy = c + s * d + t * n
                pts.append([xy[0], xy[1], z])
        pts = np.array(pts)
        edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        lines = np.array([[2, a, b] for a, b in edges]).ravel()
        poly = pv.PolyData(pts)
        poly.lines = lines
        self.plotter.add_mesh(poly, color="#ffd400", line_width=2, name="secbox_outline", pickable=False)

    def preset(self, kind: str, pos=None, thick=1.0):
        """kind: all | transversal (corte en s=pos) | longitudinal (corte en t=pos) | planta (z >= pos)."""
        self.reset_bounds()
        r = self.full_range()
        h = thick / 2
        if kind == "transversal":
            p = (r[0] + r[1]) / 2 if pos is None else pos
            self.set_box(on=True, bounds=[p - h, p + h, None, None, None, None])
        elif kind == "longitudinal":
            p = (r[2] + r[3]) / 2 if pos is None else pos
            self.set_box(on=True, bounds=[None, None, p - h, p + h, None, None])
        elif kind == "planta":
            p = (r[4] + r[5]) / 2 if pos is None else pos
            self.set_box(on=True, bounds=[None, None, None, None, None, p])
        else:
            self.set_box(on=False)
            return self.box_state()
        return self.box_state()

    # ------------------------------------------------------------------- clic y consulta
    def set_mode(self, mode):
        self.mode = None if mode in (None, "ninguna") else mode
        self._pending_a = None
        for m, b in self.mode_btns.items():
            b.blockSignals(True)
            b.setChecked(m == self.mode)
            b.blockSignals(False)
        self.btn_face.blockSignals(True)
        self.btn_face.setChecked(self.mode == "cara")
        self.btn_face.blockSignals(False)
        self.lbl_info.setText({"consulta": "Consulta: clic en el modelo.", "cota": "Cota: clic para fijar una cota.",
                               "medida": "Medida: clic en el punto A y luego en el B.",
                               "cara": "Cara de la caja: clic en el modelo para llevar la cara elegida a ese punto.",
                               None: "Herramienta apagada: el clic selecciona elementos."}[self.mode])

    def _snap_tree(self):
        key = (len(self.proc.solids), None if self.proc.mesh is None else len(self.proc.mesh.vertices),
               id(self.proc.section))
        if self._snap_key != key:
            from scipy.spatial import cKDTree
            vs = [s.vertices for s in self.proc.solids]
            if self.proc.mesh is not None:
                vs.append(self.proc.mesh.vertices)
            if self.proc.section is not None:
                vs += [np.asarray(l, float) for l in self.proc.section.lines3d]
            P = np.vstack(vs) if vs else None
            self._snap = (cKDTree(P), P) if P is not None else None
            self._snap_key = key
        return self._snap

    def pick_world(self, x, y, snap=True):
        """Posición 3D local bajo el píxel (x, y); None si no hay geometría. Con imán a vértices cercanos."""
        self._picker.Pick(x, y, 0, self.plotter.renderer)
        act = self._picker.GetActor()
        if act is None:
            return None
        name = next((k for k, v in self.plotter.renderer.actors.items() if v is act), "")
        if name.startswith(KEEP_PREFIXES):
            return None
        p = np.array(self._picker.GetPickPosition(), float)
        if snap:
            st = self._snap_tree()
            if st is not None:
                dist, i = st[0].query(p)
                if dist <= SNAP_RADIUS:
                    return st[1][i].copy()
        return p

    def handle_click(self, x, y, add=False) -> bool:
        """True si la herramienta consumió el clic."""
        if self.mode is None:
            return False
        p = self.pick_world(x, y)
        if p is None:
            self.lbl_info.setText("Sin geometría bajo el cursor.")
            return True
        self.at_point(p)
        return True

    def at_point(self, p):
        if self.mode == "cara":
            self.set_face_from_point(self.cb_face.currentData(), p)
        elif self.mode == "consulta":
            self.lbl_info.setText(self.describe(p))
        elif self.mode == "cota":
            self.proc.add_annotation("cota", p=p)
            self.lbl_info.setText(self.describe(p))
            self.refresh_annotations()
        elif self.mode == "medida":
            if self._pending_a is None:
                self._pending_a = np.asarray(p, float)
                self.lbl_info.setText("Punto A fijado. Clic en el punto B.")
                self._draw_pending()
            else:
                an = self.proc.add_annotation("medida", a=self._pending_a, b=p)
                self._pending_a = None
                self.lbl_info.setText(self.describe_measure(an))
                self.refresh_annotations()

    def set_face_from_point(self, face, p):
        """Mueve la cara `face` de la caja a la posición del punto `p` (coords locales)."""
        c, d, n = self.frame()
        q = np.asarray(p[:2], float) - c
        val = {"s": float(q @ d), "t": float(q @ n), "z": float(p[2])}[face[0]]
        mn, mx = self.spins[face[0] + "0"], self.spins[face[0] + "1"]
        val = min(val, mx.value() - 0.05) if face.endswith("0") else max(val, mn.value() + 0.05)
        if not self.chk_box.isChecked():
            self.set_box(on=True)
        self.set_box(bounds=[val if k == face else None for k in FACES])
        self.lbl_info.setText(f"Cara {FACE_LABEL[face]} fijada en {val:.3f}")

    def describe(self, p) -> str:
        o = self.proc.origin
        r = np.asarray(p, float) + o
        s = f"E {r[0]:.3f}  N {r[1]:.3f}  Cota {r[2]:.3f}\nLocal x {p[0]:.3f}  y {p[1]:.3f}  z {p[2]:.3f}"
        sec = self.proc.section
        if sec is not None and sec.frame is not None:
            c, d, n = sec.frame
            q = np.asarray(p[:2]) - c
            ss, tt = float(q @ d), float(q @ n)
            s += f"\nEje: s {ss:.3f}  t {tt:.3f}"
            try:
                s += f"  | sobre rasante {p[2] - float(np.polyval(sec.grade, ss)):+.3f} m"
            except Exception:
                pass
        st = self._snap_tree()
        names = self.nearest_element(p)
        if names:
            s += f"\nElemento más cercano: {names[0]} ({names[1]:.2f} m)"
        return s

    def nearest_element(self, p):
        best = None
        for s in self.proc.solids:
            d = float(np.min(np.linalg.norm(s.vertices - p, axis=1)))
            if best is None or d < best[1]:
                best = (s.name, d)
        return best

    @staticmethod
    def describe_measure(an) -> str:
        sl = "—" if an["slope_pct"] is None else f"{an['slope_pct']:.2f} %"
        return (f"Medida #{an['id']}: 3D {an['dist3d']:.3f} m | horizontal {an['dist_h']:.3f} m | "
                f"ΔZ {an['dz']:+.3f} m | pendiente {sl}")

    # ------------------------------------------------------------- anotaciones en el visor
    def _draw_pending(self):
        self.plotter.remove_actor("ann_pending")
        if self._pending_a is not None:
            self.plotter.add_mesh(pv.PolyData(np.array([self._pending_a])), color="#ffd400", point_size=14,
                                  render_points_as_spheres=True, name="ann_pending", pickable=False)
        self.win._request_render()

    def refresh_annotations(self):
        for nm in ("ann_cotas", "ann_cotas_lbl", "ann_med", "ann_med_lbl", "ann_pending"):
            self.plotter.remove_actor(nm)
        o = self.proc.origin
        cotas = [a for a in self.proc.annotations if a["type"] == "cota"]
        meds = [a for a in self.proc.annotations if a["type"] == "medida"]
        if cotas:
            P = np.array([a["p"] for a in cotas])
            self.plotter.add_mesh(pv.PolyData(P), color="#ff4fd8", point_size=12, render_points_as_spheres=True,
                                  name="ann_cotas", pickable=False)
            self.plotter.add_point_labels(P, [a["text"] or f"{a['id']}: Z {a['z']:.3f}" for a in cotas],
                                          name="ann_cotas_lbl", font_size=14, text_color="white", shape=None,
                                          show_points=False, always_visible=True, pickable=False)
        if meds:
            pts, cells, mids, labels = [], [], [], []
            for a in meds:
                i = len(pts)
                pts += [a["a"], a["b"]]
                cells += [2, i, i + 1]
                mids.append((a["a"] + a["b"]) / 2)
                labels.append(a["text"] or f"#{a['id']}  {a['dist3d']:.3f} m")
            poly = pv.PolyData(np.array(pts))
            poly.lines = np.array(cells)
            self.plotter.add_mesh(poly, color="#ffd400", line_width=3, name="ann_med", pickable=False)
            self.plotter.add_point_labels(np.array(mids), labels, name="ann_med_lbl", font_size=14,
                                          text_color="#ffd400", shape=None, show_points=False,
                                          always_visible=True, pickable=False)
        self._fill_table()
        self.win._request_render()

    def _fill_table(self):
        t = self.table
        t.setRowCount(len(self.proc.annotations))
        for r, a in enumerate(self.proc.annotations):
            if a["type"] == "cota":
                desc = f"E {a['e']:.3f}  N {a['n']:.3f}  Z {a['z']:.3f}"
            else:
                sl = "—" if a["slope_pct"] is None else f"{a['slope_pct']:.1f} %"
                desc = f"3D {a['dist3d']:.3f} | H {a['dist_h']:.3f} | ΔZ {a['dz']:+.3f} | {sl}"
            for c, v in enumerate((str(a["id"]), a["type"], desc)):
                t.setItem(r, c, QTableWidgetItem(v))
        t.resizeColumnsToContents()

    def delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.proc.annotations[r]
        self.refresh_annotations()

    def clear_annotations(self):
        self.proc.annotations.clear()
        self.refresh_annotations()

    def export_csv(self, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self.win, "Exportar cotas y medidas", "cotas_medidas.csv", "CSV (*.csv)")
        if not path:
            return None
        o = self.proc.origin
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["id", "tipo", "E_A", "N_A", "Z_A", "E_B", "N_B", "Z_B", "dist3d", "dist_horizontal", "dz",
                        "pendiente_pct", "texto"])
            for a in self.proc.annotations:
                if a["type"] == "cota":
                    w.writerow([a["id"], "cota", f"{a['e']:.4f}", f"{a['n']:.4f}", f"{a['z']:.4f}", "", "", "", "", "",
                                "", "", a["text"]])
                else:
                    A, B = a["a"] + o, a["b"] + o
                    w.writerow([a["id"], "medida", *[f"{v:.4f}" for v in A], *[f"{v:.4f}" for v in B],
                                f"{a['dist3d']:.4f}", f"{a['dist_h']:.4f}", f"{a['dz']:.4f}",
                                "" if a["slope_pct"] is None else f"{a['slope_pct']:.3f}", a["text"]])
        return path

    # ------------------------------------------------------------------------- panel
    def build_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        # --- caja de secciones
        gb = QGroupBox("Caja de secciones")
        g = QGridLayout(gb)
        self.chk_box = QCheckBox("Activar recorte")
        self.chk_box.toggled.connect(self._box_toggled)
        self.chk_show = QCheckBox("Ver caja")
        self.chk_show.setChecked(True)
        self.chk_show.toggled.connect(lambda _: self._update_box())
        self.cb_frame = QComboBox()
        self.cb_frame.addItems(["Marco: eje del puente (s, t, z)", "Marco: mundo (x, y, z)"])
        self.cb_frame.currentIndexChanged.connect(self._frame_changed)
        g.addWidget(self.chk_box, 0, 0)
        g.addWidget(self.chk_show, 0, 1)
        g.addWidget(self.cb_frame, 1, 0, 1, 3)
        for r, (lab, k0, k1) in enumerate((("Longitudinal s", "s0", "s1"), ("Transversal t", "t0", "t1"),
                                           ("Cota z (local)", "z0", "z1")), start=2):
            g.addWidget(QLabel(lab), r, 0)
            for c, k in enumerate((k0, k1), start=1):
                sp = QDoubleSpinBox()
                sp.setDecimals(2)
                sp.setSingleStep(0.5)
                sp.setRange(-9999, 9999)
                sp.valueChanged.connect(lambda _v: self._update_box() if self.chk_box.isChecked() else None)
                self.spins[k] = sp
                g.addWidget(sp, r, c)
        row = QHBoxLayout()
        for text, kind in (("Todo", "all"), ("Corte transversal", "transversal"), ("Corte longitudinal", "longitudinal"),
                           ("Planta", "planta")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: self.preset(k))
            row.addWidget(b)
        g.addLayout(row, 5, 0, 1, 3)
        rowf = QHBoxLayout()
        self.cb_face = QComboBox()
        for k in FACES:
            self.cb_face.addItem(f"Cara {FACE_LABEL[k]}", k)
        self.btn_face = QPushButton("Fijar con clic")
        self.btn_face.setCheckable(True)
        self.btn_face.setToolTip("Clic en el modelo: la cara elegida pasa por ese punto")
        self.btn_face.clicked.connect(lambda checked: self.set_mode("cara" if checked else None))
        rowf.addWidget(self.cb_face, 1)
        rowf.addWidget(self.btn_face)
        g.addLayout(rowf, 6, 0, 1, 3)
        hint = QLabel("Arrastra las esferas de la caja (rojo s, verde t, azul z) para mover cada cara.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        g.addWidget(hint, 7, 0, 1, 3)
        v.addWidget(gb)
        # --- cotas y medidas
        gm = QGroupBox("Cotas y medidas")
        lm = QVBoxLayout(gm)
        rb = QHBoxLayout()
        self.mode_btns = {}
        for text, m in (("Consultar", "consulta"), ("Cota", "cota"), ("Medir", "medida")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.clicked.connect(lambda checked, mm=m: self.set_mode(mm if checked else None))
            self.mode_btns[m] = b
            rb.addWidget(b)
        lm.addLayout(rb)
        self.lbl_info = QLabel("Herramienta apagada: el clic selecciona elementos.")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lm.addWidget(self.lbl_info)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Tipo", "Valores"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(120)
        lm.addWidget(self.table)
        rb2 = QHBoxLayout()
        for text, fn in (("Borrar selección", self.delete_selected), ("Borrar todo", self.clear_annotations),
                         ("Exportar CSV", lambda: self.export_csv())):
            b = QPushButton(text)
            b.clicked.connect(fn)
            rb2.addWidget(b)
        lm.addLayout(rb2)
        v.addWidget(gm)
        v.addStretch(1)
        return w

    def _box_toggled(self, on):
        if on and all(sp.value() == 0 for sp in self.spins.values()):
            self.reset_bounds()
        self._update_box()

    def _frame_changed(self, i):
        self.frame_mode = "puente" if i == 0 else "mundo"
        self.reset_bounds()
        if self.chk_box.isChecked():
            self._update_box()
````

## 3.11 `bridge.py`

Servidor HTTP del puente de control de la GUI. 81 líneas.

````python
"""
bridge.py
Puente de control local: servidor HTTP dentro de la GUI para que una herramienta externa
(Claude Code) consulte y comande la app abierta.

Seguridad: escucha SOLO en 127.0.0.1, puerto aleatorio, exige token (cabecera X-Token).
Host y token se escriben en .bridge.json (se regenera en cada arranque).
Los comandos son una lista cerrada (ver MainWindow._bridge_dispatch); no hay eval/exec.
"""
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

INFO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bridge.json")
TIMEOUT_S = 600


class Request:
    def __init__(self, cmd: str, args: dict):
        self.cmd, self.args = cmd, args
        self.event = threading.Event()
        self.result: dict = {}

    def finish(self, ok: bool, **data):
        self.result = {"ok": ok, **data}
        self.event.set()


class Bridge(QObject):
    request = Signal(object)  # se entrega al hilo de UI (conexión en cola)

    def __init__(self):
        super().__init__()
        self.token = secrets.token_hex(16)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # sin ruido en consola
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.headers.get("X-Token") != bridge.token:
                    return self._reply(403, {"ok": False, "error": "token inválido"})
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(n) or b"{}")
                    cmd = str(payload["cmd"])
                    args = payload.get("args") or {}
                except Exception as e:
                    return self._reply(400, {"ok": False, "error": f"petición inválida: {e}"})
                req = Request(cmd, args)
                bridge.request.emit(req)
                if not req.event.wait(TIMEOUT_S):
                    return self._reply(504, {"ok": False, "error": "timeout esperando a la GUI"})
                self._reply(200, req.result)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        with open(INFO_FILE, "w", encoding="utf-8") as f:
            json.dump({"host": "127.0.0.1", "port": self.port, "token": self.token,
                       "pid": os.getpid()}, f)

    def stop(self):
        self.httpd.shutdown()
        try:
            os.remove(INFO_FILE)
        except OSError:
            pass
````

## 3.12 `bridge_cli.py`

Cliente de línea de comandos del puente. 40 líneas.

````python
"""
bridge_cli.py
Cliente del puente de control de la GUI (usa solo la biblioteca estándar).

Uso:  python bridge_cli.py <comando> [json_args]
Ej.:  python bridge_cli.py status
      python bridge_cli.py classify '{"method":"grid","cell_size":2,"height_tol":0.5}'
      python bridge_cli.py screenshot
Comandos: status, log, view, screenshot, load, classify, surface, smooth, analyze
"""
import json
import os
import sys
import urllib.request

INFO = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bridge.json")


def call(cmd: str, args: dict | None = None, timeout: float = 620) -> dict:
    with open(INFO, encoding="utf-8") as f:
        info = json.load(f)
    req = urllib.request.Request(
        f"http://{info['host']}:{info['port']}/", method="POST",
        data=json.dumps({"cmd": cmd, "args": args or {}}).encode("utf-8"),
        headers={"X-Token": info["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        out = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    except FileNotFoundError:
        sys.exit("La GUI no está abierta (falta .bridge.json).")
    except OSError as e:
        sys.exit(f"No se pudo conectar con la GUI: {e}")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
````

## 3.13 `requirements.txt`

Dependencias. 16 líneas.

````text
# Probado con Python 3.10 - 3.11 (Open3D 0.18 no soporta 3.12+)
numpy==1.26.4
laspy[lazrs]==2.5.4
open3d==0.18.0
pyvista==0.44.1
pyvistaqt==0.11.1
PySide6==6.7.2
vtk==9.3.1
scikit-learn
scipy
scikit-image
matplotlib
joblib
mapbox-earcut
ifcopenshell>=0.8.0
ezdxf
````


---

# 4. Registro de la última sesión

Copias de respaldo previas a los cambios: `reconstruct.py.bak3`, `core_processing.py.bak5`, `main_gui.py.bak12`,
`ifc_export.py.bak1`. Los archivos nuevos son `dxf_export.py` y `viewer_tools.py`; `ezdxf` se añadió a `requirements.txt`.

## 4.1 Vía de acceso

Por cada tramo de 1 m a lo largo del eje se ajusta el plano `z = a + b·t` (RANSAC, tolerancia 5 cm) a los puntos de
suelo del núcleo |t| < 3 m. La vía es la franja contigua de puntos a menos de 5 cm del plano. Esos puntos pasan a la
clase ASPRS 11 y se crea un sólido cerrado por lado (espesor 0.10 m supuesto). El código está en `reconstruct.py`
(`extract_road`) y `core_processing.py` (`build_road`). Ejecutar `road` antes de `terrain`.

## 4.2 Cambios en `core_processing.py`

````diff

--- core_processing.py.bak5
+++ core_processing.py
@@ -21,4 +21,5 @@
 ASPRS_GROUND = 2
 ASPRS_UNCLASSIFIED = 1
+ASPRS_ROAD = 11          # superficie de camino
 
 
@@ -42,6 +43,9 @@
     "muro": {"part": ["SUBSTRUCTURE:Subestructura"], "predefined_type": {"IFC4X3": "RETAININGWALL", "default": "SOLIDWALL"}},
     "apoyo": {"part": ["SUBSTRUCTURE:Subestructura"], "predefined_type": "NOTDEFINED"},
+    "via": {"predefined_type": "NOTDEFINED"},      # vía de acceso: fuera del puente, directo en el sitio
 }
-ASSUMPTIONS = {"muro": "Cara exterior medida; espesor 0.30 m supuesto (la cara interior queda bajo el relleno)",
+ASSUMPTIONS = {"via": "Superficie y bordes medidos (plano con peralte por tramo); espesor 0.10 m supuesto, "
+                      "sin capas de pavimento",
+               "muro": "Cara exterior medida; espesor 0.30 m supuesto (la cara interior queda bajo el relleno)",
                "apoyo": "Posición transversal, ancho del ala y espesor medidos; posición longitudinal (mitad del asiento) "
                         "y largo 0.35 m supuestos; tipo de apoyo no identificable en la nube",
@@ -62,4 +66,5 @@
         self.elements = []                                # reconstruct.Element
         self.solids = []                                  # reconstruct.Solid
+        self.annotations = []                             # cotas y medidas (coords locales)
 
     # ------------------------------------------------------------------ carga
@@ -70,4 +75,5 @@
         fin = np.isfinite(xyz).all(axis=1)
         xyz = xyz[fin]
+        self.annotations = []                             # las cotas y medidas son de la nube anterior
         self.rgb = None      # color original del LAS (uint8) si lo trae; la vista de la nube original lo usa
         if {"red", "green", "blue"} <= set(las.point_format.dimension_names):
@@ -234,4 +240,89 @@
                 "height_measured": all(s.metrics["height_measured"] for s in br),
                 "t_centers_m": [round(s.metrics["t_center_m"], 2) for s in br if s.name.startswith("Apoyo inicial")]}
+
+    def build_road(self, **kw) -> dict:
+        """
+        Vía de acceso a ambos lados del tablero (reconstruct.extract_road): separa de la clase
+        terreno (2) los puntos que forman la calzada y los reclasifica a ASPRS 11; agrega los
+        sólidos 'via'. Después, `build_terrain` ya no los usa (queda solo el terreno natural).
+        """
+        import reconstruct
+        if self.section is None or self.structure is None:
+            raise RuntimeError("Extrae la sección primero.")
+        # si ya se corrió, devolver la vía anterior al terreno para repetir con otros parámetros
+        self.classification[self.classification == ASPRS_ROAD] = ASPRS_GROUND
+        c, d, n = self.section.frame
+        (a_lo, k_lo), (a_hi, k_hi) = self.section.ends["lo"], self.section.ends["hi"]
+        hw = self.structure.width / 2
+        s_hi = max(a_hi + k_hi * hw, a_hi - k_hi * hw)
+        s_lo = min(a_lo + k_lo * hw, a_lo - k_lo * hw)
+        mask, solids, info = reconstruct.extract_road(
+            self.points, self.classification == ASPRS_GROUND, (c, d, n), s_lo, s_hi, **kw)
+        if not solids:
+            raise RuntimeError("No se detectó la vía (sin plano continuo en el suelo junto al tablero).")
+        self.classification[mask] = ASPRS_ROAD
+        self.solids = [x for x in self.solids if x.kind != "via"] + solids
+        return {"road_points": info["points"], "sides": {k: {m: round(v, 3) for m, v in d_.items()}
+                                                         for k, d_ in info["sides"].items()},
+                "solids": [{"name": x.name, "volume_m3": round(x.volume, 2), "watertight": x.watertight}
+                           for x in solids]}
+
+    # ------------------------------------------------------------------ líneas, cotas, medidas, DXF
+    def cad_lines(self, edge_angle: float = 30.0, contour_interval: float = 1.0, kinds=None) -> dict:
+        """
+        Líneas aprovechables en CAD, en coords locales: eje, sección (lines3d), aristas vivas de cada
+        sólido (por tipo) y curvas de nivel del terreno si hay malla (`contour_interval` > 0).
+        """
+        import dxf_export
+        out = {}
+        if self.section is not None:
+            c, d, n = self.section.frame
+            (a_lo, _), (a_hi, _) = self.section.ends["lo"], self.section.ends["hi"]
+            ss = np.linspace(a_lo, a_hi, 25)
+            xy = c[None, :] + ss[:, None] * d[None, :]
+            out["EJE"] = [np.c_[xy, np.polyval(self.section.grade, ss)]]
+            if self.section.lines3d:
+                out["SECCION"] = [np.asarray(l, float) for l in self.section.lines3d]
+        for s in self.solids:
+            if kinds is not None and s.kind not in kinds:
+                continue
+            layer = dxf_export.KIND_LAYER.get(s.kind, s.kind.upper())
+            out.setdefault(layer, []).extend(dxf_export.feature_edges(s.vertices, s.triangles, edge_angle))
+        if self.mesh is not None and contour_interval and contour_interval > 0:
+            out["TERRENO_CURVAS"] = dxf_export.contour_lines(self.mesh.vertices, self.mesh.triangles, contour_interval)
+        return out
+
+    def add_annotation(self, kind: str, **kw) -> dict:
+        """cota {p, text}, medida {a, b}: puntos en coords locales. Devuelve la anotación con sus valores."""
+        if kind == "cota":
+            p = np.asarray(kw["p"], float)
+            an = {"type": "cota", "p": p, "text": kw.get("text") or "", "id": len(self.annotations) + 1}
+            an["e"], an["n"], an["z"] = (p + self.origin).tolist()
+        elif kind == "medida":
+            a, b = np.asarray(kw["a"], float), np.asarray(kw["b"], float)
+            dv = b - a
+            hz = float(np.hypot(dv[0], dv[1]))
+            an = {"type": "medida", "a": a, "b": b, "id": len(self.annotations) + 1,
+                  "dist3d": float(np.linalg.norm(dv)), "dist_h": hz, "dz": float(dv[2]),
+                  "slope_pct": float(100 * dv[2] / hz) if hz > 1e-9 else None, "text": kw.get("text") or ""}
+        else:
+            raise ValueError("tipo de anotación: cota | medida")
+        self.annotations.append(an)
+        return an
+
+    def export_dxf(self, path: str, coords: str = "utm", epsg: int | None = None, edge_angle: float = 30.0,
+                   contour_interval: float = 1.0, terrain_mesh: bool = False, kinds=None) -> dict:
+        """DXF con las líneas del modelo + cotas y medidas. coords: 'utm' (reales) | 'local'."""
+        import dxf_export
+        if epsg is None and self.crs is not None:
+            try:
+                epsg = self.crs.to_epsg()
+            except Exception:
+                epsg = None
+        lines = self.cad_lines(edge_angle, contour_interval, kinds)
+        if not any(lines.values()) and not self.annotations:
+            raise RuntimeError("No hay líneas que exportar: separa elementos o genera el terreno primero.")
+        mesh = (self.mesh.vertices, self.mesh.triangles) if (terrain_mesh and self.mesh is not None) else None
+        return dxf_export.write_dxf(path, lines, self.annotations, self.origin, coords, epsg, mesh)
 
     def build_railings(self, **kw) -> dict:
````

## 4.3 Cambios en `ifc_export.py` (coordenadas UTM o local y unidad en metros)

````diff
--- ifc_export.py.bak1
+++ ifc_export.py
@@ -39,5 +39,5 @@
         owner_settings.get_application = lambda _f: app
     project = api.run("root.create_entity", f, ifc_class="IfcProject", name=name)
-    api.run("unit.assign_unit", f)  # metros, radianes, etc. por defecto
+    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})  # la geometría está en metros (el valor por defecto es milímetro)
     model = api.run("context.add_context", f, context_type="Model")
     body = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
@@ -133,4 +133,7 @@
     opt = {"hierarchy": True, "psets": True, "qto": True, "custom": True, "georef": True}
     opt.update({k: bool(v) for k, v in (options or {}).items() if k in opt})
+    coords = str((options or {}).get("coords") or "local").lower()
+    if coords not in ("local", "utm"):
+        raise ValueError("coords debe ser 'local' o 'utm'")
     schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(SCHEMAS[version])
 
@@ -146,5 +149,5 @@
         owner_settings.get_application = lambda _f: app
     project = api.run("root.create_entity", f, ifc_class="IfcProject", name=name)
-    api.run("unit.assign_unit", f)
+    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})  # la geometría está en metros (el valor por defecto es milímetro)
     model = api.run("context.add_context", f, context_type="Model")
     body = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
@@ -206,10 +209,14 @@
 
     offset = np.min([np.asarray(i["vertices"]).min(axis=0) for i in items], axis=0)
+    # coords="local": origen del modelo en el mínimo común (x, y, z locales); IfcMapConversion lleva el
+    #   desplazamiento a coordenadas reales si hay EPSG.
+    # coords="utm": el modelo ya está en coordenadas reales (los elementos se colocan en `offset`);
+    #   IfcMapConversion queda en identidad (0, 0, 0) si hay EPSG.
     georef = False
     if epsg and version != "IFC2X3" and opt["georef"]:
         api.run("georeference.add_georeferencing", f)
+        e0, n0, h0 = (offset if coords == "local" else (0.0, 0.0, 0.0))
         api.run("georeference.edit_georeferencing", f, projected_crs={"Name": f"EPSG:{int(epsg)}"},
-                coordinate_operation={"Eastings": float(offset[0]), "Northings": float(offset[1]),
-                                      "OrthogonalHeight": float(offset[2])})
+                coordinate_operation={"Eastings": float(e0), "Northings": float(n0), "OrthogonalHeight": float(h0)})
         georef = True
 
@@ -227,5 +234,5 @@
         else:
             api.run("spatial.assign_container", f, products=[el], relating_structure=node)
-        if not georef:
+        if coords == "utm":
             m = np.eye(4)
             m[:3, 3] = offset
````

## 4.4 Cambios en `main_gui.py`

````diff
--- main_gui.py.bak12
+++ main_gui.py
@@ -21,4 +21,5 @@
 from bridge import Bridge
 from ifc_dialog import IfcExportDialog
+from viewer_tools import DxfDialog, ViewerTools
 from core_processing import ASPRS_GROUND, IFC_CATEGORY, PointCloudProcessor
 
@@ -42,5 +43,5 @@
 COLOR_NON_GROUND = (0.20, 0.65, 0.25)
 # Paleta por código ASPRS (clasificación automática): 2 suelo, 6 estructura, 5 vegetación
-CLASS_COLORS = {1: (0.55, 0.55, 0.55), 2: COLOR_GROUND, 5: COLOR_NON_GROUND, 6: (0.90, 0.30, 0.20)}
+CLASS_COLORS = {1: (0.55, 0.55, 0.55), 2: COLOR_GROUND, 5: COLOR_NON_GROUND, 6: (0.90, 0.30, 0.20), 11: (0.45, 0.45, 0.52)}
 
 
@@ -141,4 +142,12 @@
         self.btn_bear.clicked.connect(self.on_bearings)
         f1b.addRow(self.btn_bear)
+        self.btn_road = QPushButton("Separar vía de acceso del terreno")
+        self.btn_road.setToolTip("Plano con peralte por tramo junto al tablero; reclasifica a clase 11 (camino)")
+        self.btn_road.clicked.connect(self.on_road)
+        f1b.addRow(self.btn_road)
+        self.btn_dxf = QPushButton("Exportar líneas a AutoCAD (DXF)")
+        self.btn_dxf.setToolTip("Eje, sección, aristas de los elementos, curvas de nivel, cotas y medidas; en UTM o local")
+        self.btn_dxf.clicked.connect(self.on_export_dxf)
+        f1b.addRow(self.btn_dxf)
         self.btn_ifc = QPushButton("Exportar IFC (IFC2X3, IFC4, IFC4X3)")
         self.btn_ifc.clicked.connect(self.on_export_ifc)
@@ -379,5 +388,6 @@
             self.plotter.remove_actor(a)
         pal = {"losa": "#4a86d8", "viga": "#d8703b", "baranda": "#3bd87a", "estribo": "#9a9a9a",
-               "riel": "#3bd87a", "poste": "#f0e040", "muro": "#b08cd8", "apoyo": "#e04040"}
+               "riel": "#3bd87a", "poste": "#f0e040", "muro": "#b08cd8", "apoyo": "#e04040",
+               "via": "#6e6e78"}
         for i, s in enumerate(self.proc.solids):
             faces = np.hstack([np.full((len(s.triangles), 1), 3), s.triangles]).ravel()
@@ -425,4 +435,36 @@
                   f"({'medido' if st['height_measured'] else 'supuesto'}) | cerrados {st['watertight']}")
         self._draw_solids()
+
+    def on_road(self):
+        self._say("Separando la vía de acceso del terreno ...")
+        self._run(self.proc.build_road, self._road_done)
+
+    def _road_done(self, st):
+        self._say(f"Vía: {st['road_points']:,} puntos pasan a clase 11")
+        for k, m in st["sides"].items():
+            self._say(f"  {k}: {m['length_m']} m largo x {m['width_m']} m ancho (t {m['t_left_m']} a {m['t_right_m']}), "
+                      f"peralte {m['cross_slope_pct']} %")
+        self._draw_solids()
+
+    def on_export_dxf(self):
+        epsg = 0
+        if self.proc.crs is not None:
+            try:
+                epsg = int(self.proc.crs.to_epsg() or 0)
+            except Exception:
+                epsg = 0
+        dlg = DxfDialog(self, epsg, self.proc.mesh is not None, os.path.join(HERE, "lineas.dxf"))
+        if dlg.exec() != DxfDialog.Accepted:
+            return
+        cfg = dlg.config()
+        path = cfg.pop("path")
+        self._say(f"Exportando DXF ({cfg['coords']}) a {path} ...")
+        self._run(self.proc.export_dxf, self._dxf_done, path, **cfg)
+
+    def _dxf_done(self, r):
+        self._say(f"DXF: {r['file']} | {r['coords']} | EPSG {r['epsg']} | {r['polylines']} polilíneas, "
+                  f"{r['points']} cotas, {r['faces3d']} caras | capas {r['layers']}")
+        for a in r["aux"]:
+            self._say(f"  auxiliar: {a}")
 
     def on_export_ifc(self):
@@ -584,5 +626,6 @@
                     "tree": self._cmd_tree, "visibility": self._cmd_visibility,
                     "select": self._cmd_select, "axes": self._cmd_axes,
-                    "screenshot": self._cmd_screenshot}
+                    "screenshot": self._cmd_screenshot, "sectionbox": self._cmd_sectionbox,
+                    "annot": self._cmd_annot}
             task = {"load": self._cmd_load, "classify": self._cmd_classify,
                     "surface": self._cmd_surface, "smooth": self._cmd_smooth,
@@ -592,5 +635,6 @@
                     "abutments": self._cmd_abutments, "railings": self._cmd_railings,
                     "wings": self._cmd_wings, "bearings": self._cmd_bearings,
-                    "terrain": self._cmd_terrain}
+                    "road": self._cmd_road, "terrain": self._cmd_terrain,
+                    "export_dxf": self._cmd_export_dxf}
             if req.cmd in sync:
                 req.finish(True, result=_jsonable(sync[req.cmd](req.args)))
@@ -714,4 +758,57 @@
         self.on_wings()
 
+    def _cmd_export_dxf(self, a):
+        path = os.path.expanduser(str(a.get("path", "")))
+        if not path.lower().endswith(".dxf"):
+            raise ValueError("path debe terminar en .dxf")
+        kw = {k: a[k] for k in ("coords", "epsg", "edge_angle", "contour_interval", "terrain_mesh", "kinds") if k in a}
+        self._run(self.proc.export_dxf, self._dxf_done, path, **kw)
+
+    def _cmd_sectionbox(self, a):
+        """on, frame (puente|mundo), bounds [s0,s1,t0,t1,z0,z1] (null = sin cambio), show, preset
+        (all|transversal|longitudinal|planta) con pos y thick."""
+        t = self.tools
+        if "drag" in a:                  # {face, dx, dy[, x, y]}: arrastre simulado de un asa
+            g = a["drag"]
+            return t.drag_face(g["face"], g.get("dx", 0), g.get("dy", 0), g.get("x"), g.get("y"))
+        if "handles" in a:               # posición en pantalla de cada asa
+            return {k: t._to_display(c).round(1).tolist() for k, (c, _) in t.face_centers().items()}
+        if "preset" in a:
+            if "frame" in a:
+                t.set_box(frame=a["frame"])
+            return t.preset(a["preset"], a.get("pos"), float(a.get("thick", 1.0)))
+        if a.get("on") and all(sp.value() == 0 for sp in t.spins.values()):
+            t.reset_bounds()
+        return t.set_box(on=a.get("on"), bounds=a.get("bounds"), frame=a.get("frame"), show=a.get("show"))
+
+    def _cmd_annot(self, a):
+        """mode (consulta|cota|medida|ninguna); click [x,y]; point o a+b (reales si real=true, si no
+        locales); clear; csv path."""
+        t, o = self.tools, self.proc.origin
+        loc = (lambda p: np.asarray(p, float) - (o if a.get("real") else 0))
+        if "face" in a:                  # cara de la caja que fija el modo "cara"
+            t.cb_face.setCurrentIndex(t.cb_face.findData(a["face"]))
+        if "mode" in a:
+            t.set_mode(a["mode"])
+        if "click" in a:
+            x, y = a["click"][:2]
+            p = t.pick_world(int(x), int(y))
+            if p is None:
+                raise ValueError("sin geometría bajo ese píxel")
+            t.at_point(p)
+        if "point" in a:
+            t.at_point(loc(a["point"]))
+        if "a" in a and "b" in a:
+            self.proc.add_annotation("medida", a=loc(a["a"]), b=loc(a["b"]), text=a.get("text", ""))
+            t.refresh_annotations()
+        if a.get("clear"):
+            t.clear_annotations()
+        if "csv" in a:
+            t.export_csv(a["csv"])
+        return {"mode": t.mode, "info": t.lbl_info.text(), "annotations": list(self.proc.annotations)}
+
+    def _cmd_road(self, a):
+        self.on_road()
+
     def _cmd_bearings(self, a):
         self.on_bearings()
@@ -770,4 +867,5 @@
         "muro": ("Subestructura", "Estribos", "Muros de retorno"),
         "apoyo": ("Subestructura", "Estribos", "Apoyos"),
+        "via": ("Vía de acceso",),
     }
     HIGHLIGHT = "#ff4fd8"
@@ -810,4 +908,11 @@
         self.addDockWidget(Qt.RightDockWidgetArea, dock)
         self._install_picker()
+        self.tools = ViewerTools(self)
+        dock2 = QDockWidget("Caja de secciones, cotas y medidas", self)
+        dock2.setWidget(self.tools.build_panel())
+        dock2.setMinimumWidth(300)
+        self.addDockWidget(Qt.RightDockWidgetArea, dock2)
+        self.tabifyDockWidget(dock, dock2)
+        dock.raise_()
 
     def _request_render(self):
@@ -980,4 +1085,6 @@
             return
         add = bool(obj.GetControlKey() or obj.GetShiftKey())
+        if self.tools.handle_click(x, y, add):       # cota, medida o consulta activas
+            return
         self._picker.Pick(x, y, 0, self.plotter.renderer)
         act = self._picker.GetActor()
````

# 5. Comandos del puente de control

Uso: `python bridge_cli.py <comando> [json]` con la GUI abierta (lee `.bridge.json`).

| Comando | Argumentos principales |
|---|---|
| `status`, `log`, `view`, `screenshot` | estado, registro, vista y captura del visor |
| `load` | `path` |
| `autoclass`, `structure`, `section`, `elements` | sin argumentos |
| `abutments`, `railings`, `wings`, `bearings` | sin argumentos |
| `road` | sin argumentos; antes de `terrain` |
| `terrain` | `voxel`, `max_edge`, `clip_structure`, `outlier_cell`, `outlier_tol`, `roi_margin` |
| `export_ifc` | `path`, `config` con `versions`, `epsg`, `georef`, `coords` utm o local, `validate`, `scope` |
| `export_dxf` | `path`, `coords` utm o local, `epsg`, `edge_angle`, `contour_interval`, `terrain_mesh`, `kinds` |
| `tree`, `visibility`, `select`, `axes` | árbol, visibilidad, selección y ejes |
| `sectionbox` | `on`, `frame` puente o mundo, `bounds` [s0,s1,t0,t1,z0,z1], `show`, `preset` (all, transversal, longitudinal, planta) con `pos` y `thick`, `drag` {face,dx,dy}, `handles` |
| `annot` | `mode` (consulta, cota, medida, cara, ninguna), `face`, `click` [x,y], `point`, `a` y `b`, `real`, `clear`, `csv` |

Ejemplos:

```bash
python bridge_cli.py road
python bridge_cli.py export_dxf '{"path":"C:/salida/lineas.dxf","coords":"utm","epsg":32718}'
python bridge_cli.py sectionbox '{"preset":"transversal","pos":0,"thick":1.0}'
python bridge_cli.py sectionbox '{"drag":{"face":"s1","dx":-60,"dy":0}}'
python bridge_cli.py annot '{"mode":"medida","a":[677990,5603120,31.6],"b":[678010,5603140,33.0],"real":true}'
python bridge_cli.py export_ifc '{"path":"C:/salida/m.ifc","config":{"epsg":32718,"coords":"local","validate":true}}'
```

# 6. Resultados de las pruebas y pendientes

Puente Mafil N2, 89.5 M puntos:

- Vía: 3,832,496 puntos a clase 11. Vía final de 33 m x 11.6 m con peralte 5.36 %; vía inicial de 42 m x 11.2 m con peralte 5.11 %. Sólidos cerrados de 12.7 y 15.2 m3.
- DXF: 2,182 polilíneas en UTM; la cota de prueba (677980, 5603130, 32.0) salió idéntica; `.prj` WGS84 UTM 18S.
- IFC: UTM y local en IFC2X3, IFC4 e IFC4X3 con 0 incidencias en el validador. La unidad de longitud pasó de milímetro a metro (corrección de un error previo: los IFC anteriores salían 1000 veces más chicos).
- Caja de secciones: arrastre de asas y fijar con clic verificados con eventos simulados del interactor.

Pendientes y límites:

- Sin probar: abrir el DXF en AutoCAD real y el IFC en un visor externo; la caja de secciones con ratón real.
- La caja de secciones no tiene modo "invertir" (VTK recorta por intersección de planos).
- Espesor de la vía (0.10 m) supuesto; el borde de la vía puede incluir berma si está en el mismo plano.
- Detección de apoyos: falta ajustar `extract_bearings` con el criterio que defina el usuario.
- Quedan picos de vegetación en el terreno lejos de la obra.
- El DXF no guarda el CRS por sí mismo: se asigna en AutoCAD con `MAPCSASSIGN` o `GEO` usando el `.prj` y el `_georef.txt`.
