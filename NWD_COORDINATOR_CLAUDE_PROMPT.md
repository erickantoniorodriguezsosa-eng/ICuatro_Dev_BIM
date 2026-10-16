# PROMPT MAESTRO — EXTENSIÓN pyRevit PARA DETECCIÓN, ANÁLISIS Y COORDINACIÓN VERTICAL CONTRA SUPERFICIES NWD

Actúa como un **arquitecto de software senior especializado en Autodesk Revit API, pyRevit, IronPython/Python, Navisworks API, geometría computacional, BIM y coordinación de modelos**.

Trabajarás directamente sobre mi proyecto abierto actualmente en VSCode.

Tu objetivo NO es solamente analizar ni proponer código.

Debes:

1. inspeccionar el proyecto existente;
2. comprender su arquitectura;
3. validar técnicamente las capacidades reales de Revit 2022 respecto de archivos NWD/NWC vinculados como Coordination Models;
4. diseñar la arquitectura;
5. implementar las modificaciones;
6. probarlas;
7. corregir errores;
8. mantener compatibilidad con Revit 2022;
9. evitar bloquear o congelar Revit;
10. documentar brevemente las decisiones técnicas importantes.

---

# 1. OBJETIVO GENERAL

Necesito modificar y ampliar una extensión existente desarrollada mediante **pyRevit para Revit 2022** cuya función principal será utilizar uno o varios archivos **NWD/NWC vinculados como modelos de coordinación** como referencia geométrica.

La extensión debe intentar:

- detectar automáticamente los NWD/NWC vinculados;
- identificar el elemento `CoordinationModel` correspondiente dentro de Revit;
- obtener toda la información accesible desde Revit API;
- investigar si es posible acceder a la geometría contenida en el modelo de coordinación;
- identificar superficies relevantes;
- reconstruir o consultar una superficie geométrica;
- seleccionar elementos Revit;
- calcular la distancia vertical entre dichos elementos y la superficie proveniente del NWD;
- visualizar el análisis;
- y posteriormente permitir ajustar verticalmente los elementos Revit respecto de esa superficie.

La finalidad es utilizar la superficie proveniente del NWD como **referencia topográfica o de coordinación vertical**.

IMPORTANTE:

La extensión ya posee una estructura y puede contener funciones existentes. Debes **reutilizar, adaptar y ampliar** dicha arquitectura cuando sea posible, evitando destruir funcionalidades que ya estén operativas.

---

# 2. REGLA CRÍTICA DE DESARROLLO

NO debes asumir que Revit API permite acceder directamente a toda la geometría interna de un archivo NWD.

ANTES de implementar el motor geométrico debes investigar dentro del SDK/API disponible y determinar qué información puede obtener realmente Revit 2022 desde:

- `CoordinationModel`;
- geometría del elemento;
- bounding box;
- transforms;
- gráficos;
- referencias;
- selección;
- intersección;
- raycasting;
- APIs de Navisworks;
- otras APIs Autodesk disponibles.

Debes clasificar las capacidades encontradas como:

- SOPORTADO DIRECTAMENTE POR REVIT API;
- SOPORTADO PARCIALMENTE;
- REQUIERE NAVISWORKS API;
- REQUIERE EXPORTACIÓN/PROCESAMIENTO INTERMEDIO;
- NO SOPORTADO.

No inventes métodos de Revit API.

No escribas llamadas ficticias.

Si Revit 2022 no permite enumerar internamente toda la geometría del NWD, debes implementar la mejor arquitectura posible para sortear esa limitación.

---

# 3. ARQUITECTURA PREFERIDA

Quiero que estudies una arquitectura en capas.

## CAPA A — REVIT / pyREVIT

Responsable de:

- interfaz;
- selección;
- lectura de elementos Revit;
- detección de Coordination Models;
- visualización;
- análisis;
- aplicación de desplazamientos;
- transacciones.

## CAPA B — MOTOR NWD

Responsable de obtener del NWD:

- jerarquía de objetos;
- ModelItem;
- geometría;
- vértices;
- triángulos;
- bounding boxes;
- transformaciones;
- propiedades;
- categorías;
- nombres;
- archivos origen;
- capas cuando existan.

Si Revit API no permite obtener esta información, evaluar:

### OPCIÓN 1
Navisworks .NET API.

### OPCIÓN 2
Navisworks COM API.

### OPCIÓN 3
Proceso auxiliar externo.

### OPCIÓN 4
Exportación de una representación geométrica intermedia.

### OPCIÓN 5
Generación de cache geométrico asociado al NWD.

No descartes una arquitectura híbrida Revit + Navisworks si es necesaria.

---

# 4. OBJETIVO FUNDAMENTAL

Necesito llegar conceptualmente a:

```text
NWD
 ↓
Elementos internos
 ↓
Geometría
 ↓
Triángulos / superficies
 ↓
Índice espacial
 ↓
Consulta XY
 ↓
Intersección vertical
 ↓
Z superficie
 ↓
Comparación con elemento Revit
 ↓
DZ
 ↓
Ajuste vertical
```

---

# 5. DETECCIÓN DE NWD/NWC

Crear o adaptar un módulo:

```text
CoordinationModelDetector
```

Debe detectar automáticamente todos los modelos de coordinación existentes en el documento Revit.

Por cada modelo obtener, cuando sea posible:

```text
ElementId
Name
FilePath
FileName
Type
Transform
Translation
Rotation
BoundingBox
Visibility
Workset
Pin status
```

Crear una representación interna parecida a:

```python
CoordinationModelInfo
```

No guardar solamente el nombre.

Debemos mantener el `ElementId` y la transformación correspondiente.

---

# 6. TRANSFORMACIONES

Este punto es extremadamente importante.

Toda geometría proveniente del NWD debe convertirse correctamente al sistema de coordenadas de Revit.

Debes estudiar:

```text
NWD local coordinates
Navisworks model coordinates
CoordinationModel Transform
Revit internal coordinates
Project Base Point
Survey Point
Shared Coordinates
```

No asumir que las coordenadas del NWD coinciden directamente con Revit.

Crear funciones equivalentes conceptualmente a:

```python
nwd_to_revit(point)
revit_to_nwd(point)
```

Centralizar estas conversiones.

NO repartir correcciones de coordenadas por distintas partes del código.

---

# 7. EXPLORADOR DE CONTENIDO NWD

Quiero una herramienta que permita explorar el modelo de coordinación.

Interfaz aproximada:

```text
MODELO NWD

▼ Modelo Coordinación 01

    ▼ Carreteras
        ▼ Terreno
        ▼ Pavimento
        ▼ Soleras

    ▼ Drenaje

    ▼ Estructuras

    ▼ Paisajismo
```

Si Navisworks API permite obtener la jerarquía `ModelItem`, utilizarla.

Permitir buscar elementos mediante:

```text
nombre
categoría
propiedad
archivo fuente
layer
ItemType
ModelItem
ClassDisplayName
```

---

# 8. IDENTIFICACIÓN DE SUPERFICIES

Necesitamos detectar qué geometrías podrían representar terreno o superficies útiles para coordinación.

Crear o adaptar un módulo:

```text
SurfaceDetector
```

Permitir tres modos.

## MODO 1 — AUTOMÁTICO

Buscar nombres relacionados con:

```text
TOPO
TOPOGRAFIA
TERRAIN
TERRENO
SURFACE
SUPERFICIE
TIN
GROUND
DTM
DEM
PAVIMENTO
ROAD
VIALIDAD
```

El listado debe ser configurable.

## MODO 2 — MANUAL

Permitir navegar la estructura NWD y seleccionar uno o más conjuntos de elementos.

## MODO 3 — SELECCIÓN GRÁFICA

Investigar si es posible seleccionar/pickear gráficamente una geometría del modelo de coordinación desde Revit.

Si Revit API no proporciona identificación directa del subelemento NWD seleccionado, documentarlo y utilizar una estrategia alternativa.

---

# 9. EXTRACCIÓN DE GEOMETRÍA

Para cada objeto seleccionado intentar obtener:

```text
vertices
triangles
faces
meshes
bounding boxes
transforms
```

La representación interna preferida debe ser:

```python
Triangle(
    p1,
    p2,
    p3,
    normal,
    bbox
)
```

o una estructura equivalente eficiente.

---

# 10. SUPERFICIE VIRTUAL

Construir una representación en memoria denominada:

```text
NWDSurface
```

que contenga:

```text
triangles
global_bbox
spatial_index
source_items
source_model
transform
```

La superficie NO debe convertirse automáticamente en geometría Revit.

Debe permanecer preferentemente como geometría temporal de cálculo.

---

# 11. ÍNDICE ESPACIAL

Un NWD puede contener millones de triángulos.

NO recorrer todos los triángulos para cada elemento Revit.

Crear un índice espacial.

Evaluar alternativas compatibles con Revit 2022 + pyRevit:

```text
XY Grid
Uniform Grid
Quadtree
BVH
R-Tree
```

Preferencia inicial:

```text
XY Grid
```

por simplicidad, robustez y facilidad de implementación.

La idea es que:

```text
element XY
    ↓
grid cell
    ↓
candidate triangles
```

reduzca enormemente las operaciones.

---

# 12. CONSULTA DE ALTURA

Crear un motor:

```text
SurfaceHeightSolver
```

Entrada:

```python
X
Y
```

Salida:

```python
Z
```

Lógica:

```text
punto XY

↓

buscar triángulos candidatos

↓

verificar Point-In-Triangle en XY

↓

interpolar Z del triángulo

↓

obtener superficie
```

También estudiar la alternativa:

```text
ray vertical
```

desde una posición superior:

```text
(X,Y,+Z)
```

dirección:

```text
(0,0,-1)
```

y detectar la primera intersección.

---

# 13. MÚLTIPLES SUPERFICIES EN LA MISMA XY

Puede existir:

```text
terreno
puente
túnel
pavimento
estructura
cubierta
```

en la misma coordenada XY.

Por tanto:

```text
XY → múltiples Z
```

El motor debe devolver:

```python
[
    Z1,
    Z2,
    Z3
]
```

Permitir estrategias:

```text
Z más alta
Z más baja
Z más cercana al elemento
primera intersección descendente
primera intersección ascendente
```

---

# 14. SELECCIÓN DE ELEMENTOS REVIT

Crear o adaptar un módulo:

```text
RevitElementSelector
```

Debe permitir:

## selección gráfica

```text
PickObjects
```

## selección actual

Usar elementos seleccionados previamente en Revit.

## selección mediante ElementId

Permitir ingresar:

```text
123456
123457
123458
```

La búsqueda por ID debe ser EXACTA.

No realizar búsqueda parcial.

## selección por familia

```text
FamilyName
TypeName
Category
```

## selección múltiple

Debe soportar:

```text
1 elemento
100 elementos
1000+ elementos
```

sin cambiar la lógica.

---

# 15. PREVISUALIZACIÓN Y RESALTADO

La extensión debe mantener o agregar la posibilidad de previsualizar y resaltar elementos.

Antes de modificar el modelo:

```text
PREVISUALIZAR
```

Debe permitir distinguir:

```text
elementos OK
elementos que deben subir
elementos que deben bajar
elementos sin superficie
elementos con error
```

Investigar métodos compatibles con Revit 2022:

```text
OverrideGraphicSettings
TemporaryViewMode
Selection.SetElementIds
DirectContext3D
TemporaryGraphics
```

Utilizar solamente APIs realmente disponibles en Revit 2022.

Al seleccionar una fila de resultados:

```text
seleccionar elemento en Revit
zoom al elemento
resaltarlo
```

Crear una función conceptual similar a:

```text
ShowElementInRevit(ElementId)
```

---

# 16. PUNTO DE REFERENCIA DEL ELEMENTO REVIT

Para cada elemento necesitamos calcular correctamente su punto vertical de referencia.

Crear o adaptar:

```text
ElementReferencePointResolver
```

Debe permitir utilizar:

```text
LocationPoint.Z
LocationCurve
BoundingBox.Min.Z
BoundingBox.Max.Z
BoundingBox.Center.Z
punto de inserción
base geométrica
```

El modo principal para coordinación topográfica debe poder configurarse como:

```text
BASE DEL ELEMENTO
```

preferentemente:

```python
BoundingBox.Min.Z
```

cuando sea apropiado.

Pero no asumir que siempre será correcto.

---

# 17. ANÁLISIS DE DISTANCIA

Para cada elemento seleccionado calcular:

```text
X elemento
Y elemento
Z referencia elemento
Z superficie NWD
DZ
```

Convención:

```python
DZ = Z_surface - Z_element
```

Ejemplo:

```text
Elemento
Z = 543.10

Superficie
Z = 542.85

DZ = -0.25 m
```

Significa:

```text
bajar elemento 0.25 m
```

---

# 18. RESULTADOS

Crear una tabla como:

```text
ID      Familia       Tipo       Z elemento    Z NWD     DZ
----------------------------------------------------------------
12345   Poste         Tipo A     543.10        542.85   -0.25
12346   Poste         Tipo A     544.20        544.35   +0.15
```

Agregar estado:

```text
OK
Subir
Bajar
Sin superficie
Fuera del rango
Error
```

---

# 19. TOLERANCIA

Agregar tolerancia configurable.

Ejemplo:

```text
Tolerance = 0.02 m
```

Si:

```python
abs(DZ) <= tolerance
```

marcar:

```text
OK
```

---

# 20. APLICACIÓN AUTOMÁTICA

Agregar botón:

```text
APLICAR NIVELACIÓN
```

Proceso conceptual:

```text
START TRANSACTION

for element in elements:

    displacement = DZ

    ElementTransformUtils.MoveElement(
        document,
        element.Id,
        XYZ(0,0,displacement)
    )

COMMIT
```

Antes de mover verificar:

```text
Pinned
Group
Host
Constraints
Worksharing
ReadOnly
Design Options
```

Si no puede moverse:

```text
registrar error
continuar con los demás
```

No cancelar automáticamente toda la operación por un elemento problemático.

---

# 21. AJUSTE MANUAL POSTERIOR

Después de aplicar la nivelación automática necesito poder modificar manualmente uno o varios elementos.

Agregar o mantener:

```text
AJUSTE MANUAL
```

Input:

```text
+0.10
-0.05
+0.25
```

Mover exclusivamente en:

```text
Z
```

---

# 22. OFFSETS

Agregar offset respecto de superficie.

Ejemplo:

```text
Offset = +0.30 m
```

Resultado:

```python
TargetZ = SurfaceZ + Offset
```

Esto debe permitir casos como:

```text
postes
cámaras
luminarias
equipos
elementos enterrados
elementos elevados
```

---

# 23. MODO ANÁLISIS

La herramienta debe permitir realizar:

```text
ANÁLISIS
```

sin modificar nada.

Separar claramente:

```text
ANALIZAR
```

de:

```text
APLICAR
```

---

# 24. INTERFAZ

Crear una interfaz profesional con WPF si la arquitectura actual del proyecto lo permite.

Diseño aproximado:

```text
┌───────────────────────────────────────────────────────┐
│ COORDINACIÓN VERTICAL NWD                            │
├───────────────────────────────────────────────────────┤
│ MODELO NWD                                           │
│ [ CoordinationModel01.nwd ▼ ]                       │
│                                                       │
│ SUPERFICIE                                           │
│ [ Detectar automáticamente ]                         │
│ [ Seleccionar objetos NWD ]                          │
│                                                       │
│ ELEMENTOS REVIT                                      │
│ [ Selección Revit ]                                  │
│ [ Buscar por ID ]                                    │
│ [ Buscar por Familia ]                               │
│                                                       │
│ REFERENCIA                                           │
│ (•) Base elemento                                    │
│ ( ) Punto inserción                                  │
│ ( ) Centro                                           │
│                                                       │
│ OFFSET                                               │
│ [ +0.000 m ]                                         │
│                                                       │
│ TOLERANCIA                                           │
│ [ 0.020 m ]                                          │
│                                                       │
│ [ ANALIZAR ]                                         │
├───────────────────────────────────────────────────────┤
│ ID       FAMILIA        Z REVIT      Z NWD      DZ   │
│ 12345    Poste          543.10       542.85    -0.25 │
│ 12346    Poste          544.20       544.35    +0.15 │
├───────────────────────────────────────────────────────┤
│ [ PREVISUALIZAR ]                                    │
│ [ APLICAR NIVELACIÓN ]                               │
│                                                       │
│ Ajuste manual                                        │
│ [ +0.000 ] [ APLICAR ]                              │
└───────────────────────────────────────────────────────┘
```

---

# 25. CACHE GEOMÉTRICO

La extracción geométrica del NWD puede ser costosa.

Crear un sistema de cache:

```text
NWD file

↓

extract geometry

↓

build surface

↓

save cache
```

Ejemplo:

```text
CoordinationModel01.nwd

↓

CoordinationModel01.nwdcache
```

El cache puede contener:

```text
vertices
triangles
bounding boxes
spatial index
metadata
file timestamp
file size
transform
```

---

# 26. INVALIDACIÓN DE CACHE

Si cambia:

```text
NWD modification date
NWD file size
CoordinationModel Transform
NWD path
```

invalidar automáticamente el cache.

---

# 27. RENDIMIENTO

Este requisito es crítico.

NO quiero:

```text
freeze Revit
```

Evitar bucles innecesarios.

Implementar:

```text
spatial index
geometry cache
batch processing
progress feedback
cancel button
```

Para procesos largos considerar:

```text
ExternalEvent
Idling
chunk processing
```

si fuese necesario.

No ejecutar cálculos extremadamente costosos directamente en la UI principal sin estrategia.

---

# 28. UNIDADES

Revit trabaja internamente en pies.

Toda operación debe respetar:

```text
Internal Revit units
```

La UI puede mostrar:

```text
metros
milímetros
```

pero realizar conversiones centralizadas mediante:

```text
UnitUtils
```

y métodos disponibles específicamente en Revit 2022.

No mezclar pies y metros.

---

# 29. LOGGING

Crear:

```text
logger
```

Registrar:

```text
archivo NWD
ElementId CoordinationModel
transformación
objetos NWD analizados
triángulos obtenidos
tiempo extracción
tiempo generación índice
elementos Revit analizados
elementos OK
elementos subir
elementos bajar
errores
```

No imprimir millones de líneas.

Utilizar logging resumido.

---

# 30. MANEJO DE ERRORES

La extensión no debe cerrar Revit ante errores.

Utilizar manejo robusto:

```python
try:
    ...
except Exception as ex:
    ...
```

pero NO ocultar excepciones silenciosamente.

Registrar:

```text
Exception type
Message
Context
ElementId
```

---

# 31. COMPATIBILIDAD

Objetivo principal:

```text
Revit 2022
```

Por tanto:

NO utilizar APIs exclusivas de Revit 2024/2025/2026 sin crear compatibilidad.

Verificar específicamente cada API antes de utilizarla.

---

# 32. pyREVIT

Mantener estructura compatible con pyRevit.

Ejemplo:

```text
NWDCoordinator.extension

└── NWDCoordinator.tab

    └── Coordination.panel

        └── VerticalCoordinator.pushbutton

            ├── script.py

            ├── ui.xaml

            ├── icon.png

            └── modules
```

Separar responsabilidades.

Ejemplo:

```text
modules/

coordination_model.py
navisworks_bridge.py
geometry.py
surface.py
spatial_index.py
revit_elements.py
vertical_analysis.py
visualization.py
transactions.py
units.py
logging_utils.py
```

No crear un único `script.py` gigantesco.

---

# 33. ARQUITECTURA MULTI-MOTOR

Diseñar desde el inicio una abstracción:

```python
SurfaceProvider
```

con métodos parecidos a:

```python
load()
get_bbox()
get_height(x, y)
get_intersections(x, y)
dispose()
```

Implementar inicialmente:

```text
NWDSurfaceProvider
```

pero dejar preparada la arquitectura para agregar posteriormente:

```text
CADSurfaceProvider
IFCSurfaceProvider
RevitTopographyProvider
Civil3DSurfaceProvider
```

---

# 34. PRUEBAS INICIALES

Crear primero una versión mínima de diagnóstico.

BOTÓN:

```text
TEST NWD
```

Debe mostrar:

```text
Coordination Models encontrados: X

Modelo:
CoordinationModel01.nwd

ElementId:
123456

Transform:
...

BoundingBox:
...

Revit API Geometry:
DISPONIBLE / NO DISPONIBLE

Navisworks bridge:
DISPONIBLE / NO DISPONIBLE
```

Este diagnóstico debe implementarse ANTES del sistema completo.

---

# 35. SEGUNDA PRUEBA

Intentar obtener:

```text
1 objeto NWD
```

y mostrar:

```text
name
type
bbox
properties
geometry status
triangle count
```

---

# 36. TERCERA PRUEBA

Obtener geometría de un conjunto pequeño y construir:

```text
100-1000 triángulos
```

Luego probar:

```text
XY → Z
```

---

# 37. CUARTA PRUEBA

Seleccionar:

```text
1 elemento Revit
```

Calcular:

```text
Z elemento
Z NWD
DZ
```

SIN mover nada.

---

# 38. QUINTA PRUEBA

Mover temporalmente o mediante transacción controlada un único elemento.

Verificar:

```text
movimiento exclusivamente Z
```

---

# 39. ESCALADO

Sólo después de demostrar que funciona con:

```text
1 NWD
1 superficie
1 elemento
```

escalar a:

```text
múltiples NWD
múltiples superficies
miles de elementos
```

---

# 40. INVESTIGAR NAVISWORKS API

Si necesitamos Navisworks API, investiga específicamente clases/conceptos como:

```text
Autodesk.Navisworks.Api.Application
Document
DocumentModels
Model
ModelItem
ModelItemCollection
Geometry
Fragment
Primitive
BoundingBox3D
PropertyCategory
DataProperty
Transform3D
```

Pero comprueba los nombres reales para la versión disponible.

No copies APIs de ejemplos antiguos sin verificarlas.

---

# 41. EXTRACCIÓN DE TRIÁNGULOS NAVISWORKS

Investigar correctamente el mecanismo disponible en la versión utilizada para recorrer primitivas/fragmentos y obtener:

```text
triangles
vertices
normals
```

Si requiere callbacks de primitivas:

crear un módulo específico:

```text
NavisworksGeometryExtractor
```

No mezclar esta lógica con la interfaz pyRevit.

---

# 42. POSIBLE PROCESO EXTERNO

Si cargar assemblies de Navisworks directamente dentro de pyRevit/Revit genera incompatibilidades CLR o conflictos de versiones, NO forzar esa arquitectura.

Crear un proceso externo:

```text
NWDGeometryExtractor.exe
```

o equivalente.

Flujo:

```text
pyRevit

↓

solicita análisis

↓

NWDGeometryExtractor

↓

Navisworks API

↓

extrae geometría

↓

genera cache

↓

pyRevit lee cache
```

Este diseño es totalmente aceptable si resulta más robusto.

---

# 43. FORMATO DEL CACHE

Evaluar:

```text
binary
MessagePack
JSON + binary vertices
SQLite
```

Evitar JSON gigante si existen millones de triángulos.

Una estructura razonable sería:

```text
metadata.json
vertices.bin
triangles.bin
spatial_index.bin
```

o SQLite.

---

# 44. SEGURIDAD DEL MODELO REVIT

Ninguna operación destructiva debe ejecutarse durante análisis.

Solamente:

```text
APLICAR NIVELACIÓN
```

puede iniciar modificaciones.

Usar:

```text
Transaction
```

y si resulta conveniente:

```text
TransactionGroup
```

Debe ser posible usar:

```text
UNDO
```

en Revit.

---

# 45. CASOS ESPECIALES

Considerar:

```text
familias hosteadas
familias face-based
familias work-plane based
elementos pinned
grupos
elementos de links
elementos pertenecientes a sistemas
elementos con offsets paramétricos
elementos con nivel asociado
```

No todos deben moverse mediante `MoveElement`.

Crear:

```text
MovementStrategy
```

para decidir la forma correcta de modificar cada tipo de elemento.

---

# 46. INFORMACIÓN QUE QUIERO CONSERVAR

Para cada análisis crear un objeto:

```python
VerticalCoordinationResult
```

con:

```text
ElementId
UniqueId
Category
Family
Type
ReferencePoint
OriginalZ
SurfaceZ
Offset
TargetZ
DeltaZ
Status
MovementPossible
Error
```

---

# 47. FUNCIONES PRINCIPALES

La arquitectura final debería conceptualmente permitir:

```python
detect_coordination_models()
load_nwd_model()
detect_surfaces()
build_surface()
build_spatial_index()
select_revit_elements()
resolve_element_reference_point()
analyze_vertical_distance()
preview_results()
apply_vertical_adjustment()
apply_manual_offset()
```

---

# 48. NO HACER

No:

- inventar APIs;
- generar miles de objetos DirectShape innecesarios;
- convertir todo el NWD a geometría Revit;
- recorrer todos los triángulos por cada elemento;
- mover elementos durante el análisis;
- modificar X/Y;
- asumir coordenadas compartidas;
- asumir que NWD == terreno;
- ejecutar operaciones destructivas sin transacción;
- dejar toda la lógica dentro de `script.py`;
- utilizar exclusivamente nombres de objetos para identificarlos;
- capturar excepciones sin registrarlas;
- bloquear Revit con procesos monolíticos.

---

# 49. METODOLOGÍA DE TRABAJO

Trabaja en este orden.

## ETAPA 1

Analiza el repositorio existente.

Entrega una descripción breve:

```text
estructura encontrada
tecnologías
módulos reutilizables
riesgos
```

## ETAPA 2

Investiga las capacidades reales de:

```text
Revit 2022 CoordinationModel API
```

y del acceso a NWD.

## ETAPA 3

Genera:

```text
NWD_CAPABILITY_REPORT.md
```

con:

```text
qué puede hacer Revit directamente
qué no puede hacer
qué requiere Navisworks API
arquitectura seleccionada
```

## ETAPA 4

Implementa:

```text
TEST NWD
```

## ETAPA 5

Prueba y corrige.

## ETAPA 6

Implementa extracción geométrica mínima.

## ETAPA 7

Implementa `NWDSurface`.

## ETAPA 8

Implementa índice espacial.

## ETAPA 9

Implementa:

```text
XY → Z
```

## ETAPA 10

Implementa selección de elementos Revit.

## ETAPA 11

Implementa análisis vertical.

## ETAPA 12

Implementa previsualización.

## ETAPA 13

Implementa movimientos.

## ETAPA 14

Implementa ajustes manuales.

## ETAPA 15

Optimiza rendimiento.

---

# 50. VALIDACIÓN OBLIGATORIA

En cada etapa:

```text
IMPLEMENTAR
↓
REVISAR
↓
PROBAR
↓
IDENTIFICAR ERROR
↓
CORREGIR
↓
VOLVER A PROBAR
```

No considerar una etapa terminada solamente porque el código fue escrito.

---

# 51. USO DEL REPOSITORIO

Tienes autorización para:

```text
leer los archivos del proyecto
crear módulos
modificar archivos existentes
reorganizar código cuando sea necesario
crear archivos de pruebas
crear documentación técnica
ejecutar pruebas posibles desde terminal
revisar logs
corregir errores
```

Antes de reemplazar una arquitectura existente que funcione, verifica si puede reutilizarse.

---

# 52. PROTECCIÓN DEL PROYECTO

No elimines funciones existentes sin verificar primero sus dependencias.

Si haces una modificación estructural importante:

```text
documenta el cambio
mantén compatibilidad cuando sea razonable
```

No destruir funcionalidades existentes para implementar una nueva.

---

# 53. RESULTADO FINAL ESPERADO

Quiero llegar a una herramienta cuyo flujo para el usuario sea:

```text
ABRIR REVIT

↓

NWD COORDINATOR

↓

Seleccionar NWD

↓

Seleccionar superficie

↓

Seleccionar elementos Revit

↓

ANALIZAR

↓

tabla DZ

↓

PREVISUALIZAR

↓

APLICAR NIVELACIÓN

↓

AJUSTE MANUAL si es necesario
```

---

# 54. OBJETIVO A LARGO PLAZO

La arquitectura debe quedar preparada para evolucionar posteriormente hacia una herramienta universal de coordinación geométrica capaz de trabajar con:

```text
NWD
NWC
IFC
CAD
Revit Links
Civil 3D
LandXML
DEM
```

sin tener que reconstruir toda la aplicación.

---

# 55. COMIENZA AHORA

No me entregues inicialmente cientos de líneas de código en el chat.

Comienza inspeccionando el repositorio actual.

Luego:

1. identifica la arquitectura existente;
2. identifica la versión de Python/IronPython utilizada por pyRevit;
3. identifica las dependencias;
4. valida la API disponible para Revit 2022;
5. determina de manera concluyente si Revit puede acceder a la geometría interna del Coordination Model NWD;
6. determina si necesitamos un puente mediante Navisworks API;
7. crea `NWD_CAPABILITY_REPORT.md`;
8. diseña la arquitectura definitiva;
9. implementa el primer prototipo `TEST NWD`;
10. ejecuta las pruebas posibles;
11. corrige los errores encontrados;
12. continúa progresivamente con el motor geométrico.

No te detengas solamente en análisis o recomendaciones.

Debes modificar e implementar el proyecto.

Cuando encuentres una limitación técnica, no abandones el objetivo: selecciona la alternativa arquitectónica más robusta que permita continuar.

Prioridades, en este orden:

```text
1. Correctitud geométrica
2. Compatibilidad Revit 2022
3. Estabilidad de Revit
4. Transformaciones/coordenadas correctas
5. Rendimiento
6. Arquitectura mantenible
7. Interfaz
```

La meta final no es simplemente "leer un NWD".

La meta es construir este puente:

```text
NAVISWORKS / NWD
        ↓
GEOMETRÍA DE SUPERFICIE
        ↓
CONSULTA ESPACIAL XY → Z
        ↓
ELEMENTOS REVIT
        ↓
DISTANCIA VERTICAL DZ
        ↓
PREVISUALIZACIÓN
        ↓
NIVELACIÓN / AJUSTE
```

Empieza ahora analizando el proyecto abierto en VSCode.
