# De nube de puntos a geometría IFC: proceso documentado

Fecha: 2026-10-09. Caso de prueba: Puente Mafil N2 (89.5 M de puntos, UTM 18S, EPSG:32718).
Código: `LAS_Terrain_App/` (`autoclass.py`, `train_autoclass.py`, `reconstruct.py`, `core_processing.py`, `ifc_export.py`, `main_gui.py`, `bridge.py`).

## 1. Qué problema resuelve

Una nube de puntos de un puente mezcla terreno, vegetación y elementos constructivos. El flujo convierte esa nube en elementos IFC separados (losa, vigas, barandas, terreno), pasando por una geometría intermedia en líneas que se puede revisar antes de solidificar.

```
LAS ─► clasificar por forma ─► agrupar estructura ─► eje del puente
    ─► sección apilada ─► líneas 3D ─► separar elementos ─► sólidos ─► IFC
```

Cada etapa se valida con una medida, no solo a ojo (ver sección 4).

## 2. Procedimiento paso a paso

### Paso 0. Preparar y observar
- Python 3.11 (Open3D 0.18 no soporta 3.12+), `vtk==9.3.1` (pyvista 0.44 falla con vtk 9.7).
- Las coordenadas UTM (~1e6 a 1e7) pierden precisión en float32 y desestabilizan algoritmos. Se restan al mínimo de la nube (`origin`) y se trabaja en float64 local. Para volver a coordenadas reales se suma `origin`.
- La GUI expone un puente de control local (`bridge.py`, `bridge_cli.py`): permite cargar, ejecutar cada etapa y tomar capturas de la ventana abierta. Así se verifica visualmente cada resultado.

### Paso 1. Clasificar terreno / estructura / vegetación por forma
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

### Paso 2. Agrupar la estructura
Archivo: `reconstruct.py`, `structure_clusters`.
- Se toman los puntos de clase 6, se voxelizan y se unen los vóxeles a menos de 0.35 m (componentes conectados con `cKDTree.query_pairs`).
- Se descartan grupos de menos de 300 vóxeles: elimina falsos positivos sueltos sin tocar la clasificación.
- Resultado: el grupo 0 es el tablero (36.6 M de puntos); los otros 10 son defensas, luminarias y postes de señal.

### Paso 3. Eje del puente
`reconstruct.py`, `_fit_axis`.
1. PCA en planta del grupo mayor da una dirección inicial. Está sesgada por los extremos esviados.
2. Se afina girando ±8° (paso 0.25°, luego 0.02°): el ángulo bueno maximiza la nitidez del histograma transversal (suma de cuadrados de las cuentas, bins de 5 cm), porque barandas, sardineles y vigas son líneas paralelas al eje.
3. El centro sale de los percentiles 0.5 y 99.5 de los extremos, no de la densidad de puntos.
- Resultado: azimut −84.33° (PCA daba −80.9°; corrección −3.4°), largo 45.2 m, ancho 12.4 m.

### Paso 4. Sección transversal por apilado de cortes
`reconstruct.py`, `extract_section`. En el marco del eje: s (a lo largo), t (transversal), z.

1. **Rasante z(s).** Percentil 99 de z por tramo de 0.5 m en una franja angosta |t| < 0.5 m; polinomio de grado 3 con rechazo iterativo de atípicos. Se resta: `z_rel = z − rasante`.
2. **Extremos esviados.** Puntos del tope de la losa (a ±0.10 m del tope por t); por franja de t se toman percentil 0.5 y 99.5 de s y se ajusta una recta s = a + k·t en cada extremo. Pendientes medidas: 0.563 y 0.534.
3. **Tramo central.** Entre los extremos esviados más 0.5 m de margen; ahí la sección es constante.
4. **Raster.** Cortes de 0.25 m a lo largo del eje; celdas (t, z_rel) de 2 cm. Una celda cuenta una vez por corte (`np.unique` sobre la clave corte-celda).
5. **Umbral.** Se suaviza la frecuencia (gaussiano σ = 0.7 celdas) y se umbrala en 0.15 (la celda tiene punto en al menos el 15 % de los cortes).
6. **Contorno.** Dilatación 3, relleno de huecos, erosión 3, descarte de áreas < 0.05 m², `find_contours` y simplificación Douglas-Peucker a 2 cm.
7. **Líneas 3D.** Cada vértice del contorno se barre a lo largo del eje entre los extremos esviados, sumando la rasante (una estación cada 5 m). Además se dibujan los anillos de ambos extremos. Son las "líneas" de geometría que se revisan antes de solidificar.

**Resultado.** Un contorno cerrado de 129 vértices con losa y pendiente transversal, 4 vigas en I y 2 barandas. Mediana de 3.0 cm, p90 de 5.2 cm; 87 % de los puntos a menos de 5 cm del contorno.

### Paso 5. Separar elementos
`reconstruct.py`, `separate_elements`. Reglas sobre la máscara (t, z_rel):
- **Tope de losa ZT(t):** recta robusta sobre la zona central |t| < 4.3 m (la losa tiene pendiente transversal).
- **Baranda:** material a más de 0.45 m sobre ZT(t).
- **Viga:** columnas cuyo punto más bajo está a más de 0.9 m bajo ZT. El intradós de la losa se interpola entre las columnas de solo losa; todo lo que cuelga bajo ese intradós es viga (ala superior baja, alma y bulbo).
- **Losa:** el resto, incluidos los sardineles bajos.
- Componentes conexas por región; los polígonos salen en orden antihorario.
- Resultado: 1 losa, 4 vigas, 2 barandas.

### Paso 6. Sólidos
`reconstruct.py`, `build_solids`. Cada polígono se extruye entre los extremos esviados, sobre la rasante, con 9 estaciones. Las tapas se triangulan con `mapbox_earcut`. La orientación se comprueba con el signo del volumen.
- Los 7 sólidos son cerrados (watertight).
- Volúmenes: losa 171.7 m³, vigas 18.4 a 18.8 m³, barandas 6.1 y 6.6 m³. Total 258.8 m³.
- Las definiciones difieren de las de la metodología previa (la losa allí incluye alas superiores; las vigas se medían bajo la losa), por eso las cifras no son comparables uno a uno.

### Paso 7. Estribos
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

### Paso 8. Barandas por componentes (rieles y postes)
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

### Paso 9. Exportar IFC
`ifc_export.py`, `export_elements_ifc` (IFC2X3, IFC4, IFC4X3).
- IFC4 / IFC4X3: `IfcTriangulatedFaceSet` (cerrado), con `IfcProjectedCRS` e `IfcMapConversion` si hay EPSG.
- IFC2X3: `IfcFacetedBrep` para sólidos cerrados; exige `OwnerHistory` (persona, organización y aplicación).
- Clases: `IfcSlab`, `IfcBeam`, `IfcRailing` (rieles), `IfcMember` (postes), `IfcWall` (estribos); terreno como `IfcGeographicElement` TERRAIN (IFC4+) o `IfcBuildingElementProxy` (IFC2X3).
- Propiedades: conteo de vértices y triángulos y volumen (`Pset_Open3D`).
- Verificado: los 3 archivos se reabren con IfcOpenShell. Con el modelo completo son 75 elementos (1 losa, 4 vigas, 6 rieles, 62 postes, 2 estribos) y los 75 generan geometría en cada versión (3 162 triángulos).

### Paso 10. Organización IFC según buildingSMART
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

### Paso 11. Vistas, ejes y selección de elementos
`main_gui.py` (panel "Elementos y vista").
- **Nube original:** capa propia (`cloud_raw`) con los puntos del LAS tal como se cargaron, con su color RGB si el archivo lo trae (si no, por elevación). No cambia al clasificar; al clasificar se oculta automáticamente (son los mismos puntos) y se puede volver a mostrar. Casilla "Mostrar nube original".
- **Nube clasificada y grupos:** capa aparte (`cloud`), casilla "Mostrar nube clasificada".
- **Ejes:** "Ejes con coordenadas reales" dibuja una caja con Este, Norte y cota reales (local + origen), y "Triedro de orientación" muestra u oculta el triedro del rincón.
- **Árbol de elementos** con la misma organización que el IFC (Superestructura, Tablero, Elementos de superficie y seguridad, Subestructura, Terreno). Cada elemento y cada grupo tiene casilla para mostrarlo u ocultarlo (tri-estado en los grupos). "Referencias (no IFC)" agrupa las líneas de sección y el eje.
- **Selección con clic** en el modelo: clic selecciona un elemento, Ctrl o Mayús + clic suma o quita, clic en el vacío limpia. La selección se resalta en magenta y se refleja en el árbol; también se puede elegir desde el árbol.
- **Botones:** Ocultar seleccionados, Aislar seleccionados, Mostrar todo, Quitar selección.
- Un arrastre (rotar la cámara) no cuenta como clic: se ignora si el cursor se movió más de 3 px entre pulsar y soltar.

### Paso 12. Ventana de exportación a IFC
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

### Paso 13. Muros de retorno, apoyos, filtro de árboles y terreno depurado
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

## 3. Errores encontrados y cómo se resolvieron

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

## 4. Cómo validar cada etapa

| Etapa | Medida |
|---|---|
| Clasificación | Acierto, recall y precisión por clase en bloques espaciales no vistos |
| Grupos | Tamaño de grupos y captura con cada grupo en un color |
| Eje | Paralelismo con los bordes en la vista en planta; ángulo vs. PCA |
| Rasante | Residuo del ajuste por tramo (objetivo: centímetros o menos) |
| Sección | Distancia de los puntos al contorno (mediana, p90, % a menos de 5 cm) y la imagen apilada con el contorno encima |
| Sólidos | `is_watertight`, volumen positivo |
| IFC | Reabrir con IfcOpenShell y generar la geometría de cada elemento |

## 5. Cómo ejecutarlo

Desde la GUI (`LAS_Terrain_App\.venv_las\Scripts\python.exe main_gui.py`) en este orden: Cargar archivo, Clasificar (forma), Agrupar estructura y estimar eje, Extraer sección y líneas, Separar elementos y generar sólidos, Detallar barandas (rieles y postes), Reconstruir estribos, Exportar IFC.

Por el puente (con la GUI abierta): `python bridge_cli.py <comando> [json]`, con `load`, `autoclass`, `structure`, `section`, `elements`, `railings`, `abutments`, `export_ifc`, `tree`, `visibility`, `select`, `axes`, `view {raw, cloud}`, `screenshot`, `view`, `status`.

Para reentrenar con otra nube clasificada: `python train_autoclass.py <clasificado.las> [--voxel 0.15]`.

## 6. Limitaciones y pendientes

- El modelo hereda los errores de la v5. Corregir esas etiquetas (puente 2, muros laterales, estribos) y reentrenar mejora el resultado.
- La sección se supone constante en todo el vano; no captura diafragmas ni cambios de sección. La mejora es calcular secciones por tramo.
- Barandas: los postes son prismas rectangulares (la planta real es un perfil en I) y faltan placas base y anclajes; los rieles se modelan macizos (los tubos reales son huecos).
- Estribos: espesor del espaldar supuesto (0.30 m), base plana, sin muros de retorno ni muros laterales; el estribo inicial tiene solo 22 % de planaridad a 3 cm.
- Faltan diafragmas, apoyos, luminarias y vegetación como elementos.
- Los extremos de las vigas se toman iguales a los de la losa; la metodología previa midió diferencias de hasta 0.9 m en el residuo de ajuste.
- La sección y los sólidos solo se probaron en un puente; para otra tipología hay que revisar los umbrales de `separate_elements`.
- Faltan `IfcClassification`, materiales y tipos (`IfcTypeObject`) en el IFC; la estructura de puente solo existe en IFC4X3 (IFC4 e IFC2X3 usan ensamblajes).
- La captura de ventana de Qt no incluye el visor 3D (OpenGL): para verificar el modelo se usa la captura del visor.
