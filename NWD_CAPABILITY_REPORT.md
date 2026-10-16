# NWD CAPABILITY REPORT

Qué puede y qué no puede hacer Revit 2022 con un NWD vinculado como Modelo
de Coordinación, y qué arquitectura se deriva de ello.

Todo lo que sigue está **medido en este equipo contra el modelo real**, no
tomado de documentación. Cada cifra viene de una ejecución cuya fecha se
indica.

| | |
|---|---|
| Revit | 2022, build 22.0.2.392 |
| Navisworks | Manage 2026 (API 23.0.0.0) y Manage 2025 |
| Modelo | `1B-Z5-INB-TOP-V13.T1-MFD-000-0-SuperficiesExistentes.nwd`, 358 MB |
| Documento Revit | `1B-Z5-PRD-ILU-V13.T1-M3D-001-W-IluminacionProyectada` |
| Fecha de las mediciones | 2026-09-03 |

---

## 1. Clasificación de capacidades

| Capacidad | Veredicto | Evidencia |
|---|---|---|
| Detectar los Coordination Models del documento | **SOPORTADO** | `FilteredElementCollector` por `OST_Coordination_Model` (-2000982) |
| Identidad del vínculo (Id, nombre, tipo) | **SOPORTADO** | Es un `DirectShape` + `DirectShapeType` |
| Ruta del archivo NWD | **PARCIAL** | `GetExternalFileReference()` **falla**; la ruta está en un parámetro del tipo |
| Caja envolvente del vínculo | **SOPORTADO** | `get_BoundingBox(vista)`; con `None` devuelve `null` |
| Geometría interna (malla del NWD) | **NO SOPORTADO** | Ver §2 |
| Jerarquía de objetos del NWD | **NO SOPORTADO** | Revit no crea ni subcategorías |
| Selección de un subelemento del NWD | **NO SOPORTADO** | No hay `Element` que seleccionar |
| Triángulos, vértices, propiedades | **REQUIERE NAVISWORKS API** | Ver §3 |
| Transformación NWD → Revit | **DERIVABLE** | Ver §4 |

---

## 2. Por qué Revit no da la geometría

Diagnóstico ejecutado sobre el vínculo (Id 600763):

```
Clase .NET        Autodesk.Revit.DB.DirectShape
Tipo              Autodesk.Revit.DB.DirectShapeType
Categoría         'Modelo de coordinación' (-2000982) = OST_Coordination_Model
Subcategorías     ninguna
get_BoundingBox(None)          None
get_BoundingBox(vista activa)  11599,98 x 21915,92 x 67,62 m
get_Geometry() sin vista       1 x GeometryInstance
get_Geometry() con vista       1 x GeometryInstance + 1 x Solid
GeometryInstance.GetInstanceGeometry()   VACÍO (0 objetos)
```

El único `Solid` que aparece tiene **6 caras** y sus medidas coinciden con
la caja envolvente: es el volumen marcador que Revit dibuja, no contenido
del NWD. Un levantamiento topográfico de 12 × 22 km serían cientos de miles
de triángulos, no seis caras.

Se recorrieron los 107 miembros públicos de `DirectShape` buscando una vía
alternativa. `HasExternalGeometry` y `GetExternallyTaggedGeometry()` suenan
prometedores, pero pertenecen a la API de *geometría etiquetada
externamente* (la que usa Rhino.Inside para asociar geometría propia a un
DirectShape), no a un lector del NWD.

**Conclusión:** la malla la dibuja el motor gráfico de Navisworks embebido
en Revit, fuera de la base de datos del documento. No es una limitación
que se pueda rodear con más código de Revit. Cualquier diseño que dependa
de leerla desde la API de Revit fracasa.

El archivo tampoco se puede parsear por fuera: empieza por
`#LcUStream-V1.1 binary double release C Lichunk-008`, contenedor
propietario comprimido sin especificación pública.

---

## 3. El puente Navisworks: VALIDADO

La única vía a los triángulos es el puente COM de Navisworks
(`ToInwOaPath` → `Fragments()` → `GenerateSimplePrimitives`). La API .NET
da el árbol de `ModelItem` y las propiedades, pero **no** la malla.

Ese puente solo funciona **dentro del proceso de Navisworks**. De ahí la
arquitectura elegida, que es la Opción 3 de §3 del prompt maestro y la que
§42 autoriza explícitamente:

```
pyRevit  →  NwdExtractor.exe  →  NavisworksApplication (headless)
                                      ↓ AddPluginAssembly
                                 plugin in-process
                                      ↓ puente COM
                                 triángulos  →  cache binario  →  pyRevit
```

`NavisworksApplication` expone **`AddPluginAssembly`** y
**`ExecuteAddInPlugin`**: el proceso externo carga el plugin desde donde
esté y lo ejecuta dentro de Navisworks. **No hay que instalar nada** en
`Program Files\...\Plugins` (requiere administrador) ni depender de un
bundle en `%APPDATA%`, que fue justamente lo que falló en el intento
anterior. Además aísla a Revit: si Navisworks o el NWD fallan, se cae el
proceso auxiliar y no la sesión del usuario.

### Medición real (2026-09-03)

```
Apertura del NWD de 358 MB          0,6 s
Sondeo del árbol completo           2,0 s
Items totales                       39.130
Items con geometría                 19.691
Clase de todos ellos                "Malla"
com_bridge                          OK
```

---

## 4. Transformación NWD → Revit

Contraste entre la caja del NWD (extractor, metros) y la del vínculo en
Revit (diagnóstico, pies convertidos a metros):

| Eje | NWD mín | Revit mín | Tamaño NWD | Tamaño Revit |
|---|---|---|---|---|
| X | 185.650,6213 | -4.869,4534 | 11.599,9795 | 11.599,9793 |
| Y | 2.838.836,3375 | -12.457,3549 | 21.915,9246 | 21.915,9247 |
| Z | 107,9320 | 107,9321 | 67,6160 | 67,6159 |

Desplazamiento derivado, calculado por separado desde el mínimo y desde el
máximo:

```
dX  -190.520,0747   vs  -190.520,0749    discrepancia  0,00016 m
dY  -2.851.293,6924 vs  -2.851.293,6923  discrepancia  0,00007 m
dZ         0,0001   vs         0,0000    discrepancia  0,00008 m
```

Dos conclusiones, ambas importantes:

1. **Es una traslación pura.** Los tamaños coinciden a 0,2 mm sobre 22 km:
   no hay rotación ni escala. Derivar desde el mínimo o desde el máximo da
   el mismo resultado, lo que lo confirma.
2. **dZ = 0.** La cota del NWD **es** la cota de Revit. Para coordinación
   vertical esto es lo mejor que podía pasar: la Z que se lea de la
   superficie se compara directamente con la Z del elemento de Revit, sin
   más conversión que metros→pies.

El NWD está en coordenadas proyectadas (UTM); Revit las guarda relativas a
su punto base. La traslación se **deriva en tiempo de ejecución**
comparando ambas cajas, así que no hay ninguna constante escrita en el
código. Requisito para que sea fiable: que ambas cajas describan el mismo
modelo, lo que se verifica comparando tamaños antes de aplicarla.

---

## 5. Escala del modelo: el condicionante de diseño

Muestreo real de 120 items (2026-09-03):

```
Items medidos                    120
Triángulos en la muestra         9.572.498
Tiempo                           101,8 s
Media por item                   79.771 triángulos
Velocidad                        94.028 triángulos/s

ESTIMACIÓN PARA EL MODELO COMPLETO
Triángulos                       ~1.570.000.000
Tiempo de extracción             ~16.705 s  (4,6 horas)
```

> **CORRECCIÓN (2026-09-04).** Esta estimación era **falsa**. El total real
> del modelo es **24.468.130 triángulos**, 64 veces menos, medido leyendo el
> IFC de origen (§11). El error vino de extrapolar desde 120 items del NWD
> sin advertir dos cosas: que los primeros items muestreados son justamente
> los más densos, y que Navisworks fragmenta las 313 mallas del IFC en
> 19.691 nodos. La cifra de abajo se conserva para dejar constancia del
> error; **no debe usarse para dimensionar nada**.

~~Extraer el modelo completo es inviable~~: 24,5 millones de triángulos sí
caben, aunque siguen siendo demasiados para tenerlos todos en memoria en
pyRevit de una vez. El recorte espacial se mantiene, pero como comodidad y
no como imposibilidad.

Esto convierte el recorte espacial de optimización en **requisito
arquitectónico**. Densidad media: ~6,2 millones de triángulos por km².

| Zona de trabajo | Triángulos estimados | Extracción estimada |
|---|---|---|
| 200 × 200 m | ~250.000 | ~3 s |
| 500 × 500 m | ~1.550.000 | ~17 s |
| 1 × 1 km | ~6.200.000 | ~66 s |

La caja de recorte se deriva de los elementos de Revit seleccionados más un
margen, se convierte a coordenadas NWD con la traslación de §4, y se pasa
al extractor.

### El recorte tiene que ser a dos niveles

Primer intento, filtrando solo por caja envolvente de item sobre una zona
de 500 × 500 m: descartó 19.674 de 19.691 items, pero los 17 restantes
sumaban **7.130.160 triángulos y 173 MB**. El motivo es que una sola tesela
de topografía puede medir kilómetros, así que "toca la zona" no significa
"está en la zona".

Añadido un segundo filtro que descarta **triángulo a triángulo** por
caja envolvente XY (AABB), la misma zona da:

| | Solo por item | + por triángulo |
|---|---|---|
| Triángulos | 7.130.160 | **882.520** |
| Cache | 173 MB | **21,6 MB** |
| Caja resultante | 4.719 × 9.986 m | **507 × 511 m** |

El tiempo apenas cambia (37 s → 33 s) porque el coste está en teselar, no
en almacenar; lo que baja 8 veces es la memoria, que era el límite real
para pyRevit.

---

## 6. Validación de extremo a extremo

Ejecutado sobre el cache real de 882.520 triángulos (2026-09-03), con el
mismo algoritmo que irá a pyRevit:

```
Lectura del cache binario      0,4 s   (archivo consumido entero, 0 bytes sobrantes)
Construcción de la rejilla XY  1,0 s   (2.128 celdas de 10 m)
Consulta XY -> Z               0,10 ms por punto
Cobertura                      1.538 de 2.000 puntos aleatorios (77%)

(187832,6 , 2855767,8)  ->  Z = 121,5047 m
(187882,6 , 2855817,8)  ->  Z = 121,7786 m
(187712,6 , 2855847,8)  ->  Z = 120,9498 m
```

Las alturas caen dentro del rango del cache (119,06 - 132,95 m), como debe
ser. **La cadena completa del §4 del prompt maestro está demostrada**: NWD →
elementos → geometría → triángulos → índice espacial → consulta XY →
intersección vertical → Z de superficie.

A 0,10 ms por consulta, 1.000 elementos de Revit se resuelven en una
décima de segundo: el requisito de rendimiento de §27 no es un problema.

El 77% de cobertura no es un fallo: los puntos sin superficie caen en
huecos entre teselas, porque el modelo es un corredor y no llena el
rectángulo de su caja. Confirma que el estado "Sin superficie" de §18 es
un caso real y frecuente, no una rama defensiva.

---

## 7. Hallazgo que invalida el modo automático de §8

> **CORRECCIÓN (2026-09-04).** Los nombres **sí existen**: están en los
> ANCESTROS del item, no en la hoja. El árbol real es
> `…ifc > 24-06-07 Z1.pol > TTP (IfcSite) > IfcGeographicElement >
> 500.000 - 505.115 (LEV_ESP)_v2 > Malla`, y cada elemento declara
> `ObjectType: TRIANGULACIÓN`. El filtro por texto **sí sirve**, buscando
> en la cadena de ancestros, que es justo lo que ya hace `Coincide()`.

El prompt maestro (§8, MODO 1) propone detectar superficies buscando
nombres tipo `TOPO`, `TERRENO`, `SURFACE`, `DTM`. La hoja de geometría
tiene el `DisplayName` **vacío** y clase `"Malla"`, así que buscar solo en
ella no encuentra nada.

```
item        Malla   100000   317,72 x 249,12 x 10,95 m
item        Malla    77070   200,30 x 501,67 x 13,19 m
item        Malla   100000   312,16 x 423,65 x 12,50 m
```

Son teselas de topografía de unos 200-400 m de lado. El criterio operativo
sigue siendo **espacial** por rendimiento —recortar por zona evita leer
millones de triángulos que no interesan—, pero el filtro nominal está
disponible y es útil cuando un NWD mezcla topografía con otras disciplinas.

---

## 8. Arquitectura seleccionada

```
CAPA A — pyRevit / Revit
  Detección de Coordination Models, selección de elementos, análisis
  vertical, previsualización, transacciones, interfaz.

CAPA B — Puente Navisworks (proceso externo)
  NwdExtractor.exe + plugin in-process. Sondeo y extracción por zona.
  Se comunica por archivos: cache binario + informe de texto.

CAPA C — Motor geométrico (pyRevit, en memoria)
  NWDSurface, rejilla XY, consulta XY→Z por interpolación baricéntrica.
```

Decisiones y su motivo:

| Decisión | Motivo |
|---|---|
| Proceso externo, no plugin instalado | No requiere administrador; aísla Revit; fue el fallo del intento anterior |
| Cache binario, no JSON | Millones de dobles; con `Buffer.BlockCopy` se leen en bloque |
| Recorte espacial obligatorio | §5-6: el modelo completo es inviable |
| Traslación derivada en ejecución | §4: sin constantes escritas en el código |
| Filtro espacial como criterio principal | §7: los items no tienen nombre |
| `lib/` en vez de `modules/` dentro del pushbutton | Es la convención que pyRevit añade al `sys.path` |
| Índices de triángulo globales en el cache (NWCACHE2) | Reindexar 2,6 M de valores en IronPython sería lento; el extractor ya los desplaza en C# |
| Rejilla XY con lado automático | 2,17 m para la zona medida: 0,015 ms por consulta frente a 0,10 ms con celda fija de 10 m |

---

## 9. Riesgos abiertos

- **Licencia de Navisworks.** `NavisworksApplication` consume una licencia
  de Manage/Simulate. Verificado que funciona en este equipo; en otro
  puesto sin Navisworks instalado, el puente no está disponible y la
  herramienta debe degradar con un mensaje claro, no fallar.
- **Coincidencia de modelos.** La traslación derivada supone que el NWD
  abierto es el mismo que está vinculado. Se comprueba contrastando
  tamaños de caja antes de aplicarla.
- **Sin rotación.** Medido que aquí no la hay. Si otro vínculo llegara
  girado, el método de derivación por cajas no lo detectaría correctamente:
  habría que compararlo con `Transform` del vínculo.
- ~~**Tope de 100.000 triángulos por item.**~~ **DESCARTADO** — ver §10.

---

## 10. Auditoría del tope de 100.000 triángulos: descartado

Ocho de cada catorce items devolvían exactamente 100.000 triángulos, cifra
demasiado redonda para ser casual. Si `GenerateSimplePrimitives` estuviera
truncando la malla, las cotas leídas de esas teselas serían incompletas y
toda la coordinación vertical quedaría en entredicho.

La comprobación no depende de creer ninguna cifra: se contrasta la caja que
**declara** Navisworks para el item con la caja que ocupan los triángulos
**realmente extraídos**. Una malla cortada daría una caja más pequeña.

```
idx  triángulos  fragmentos   caja declarada          caja extraída           desajuste
 1     100.000      1.625     317,72x249,12x10,95     317,72x249,12x10,95      0,0000
 3     100.000      1.939     312,16x423,65x12,50     312,16x423,65x12,50      0,0000
 4     100.000      4.509     344,70x362,87x19,71     344,70x362,87x19,71      0,0000
 5      99.323      4.685     319,80x435,37x16,37     319,80x435,37x16,37      0,0000
 9     100.000      5.248     329,19x236,37x 8,68     329,19x236,37x 8,68      0,0000
13         567          2      23,79x 20,62x 0,40      23,79x 20,62x 0,40      0,0000

items revisados            14
múltiplos de 100.000        8
con malla truncada          0
```

**Veredicto: no hay truncamiento.** El desajuste es 0,0000 m en los catorce
items, incluidos todos los de 100.000.

La explicación está en la columna de fragmentos: cada tesela se compone de
**miles** de fragmentos (1.625, 1.939, 4.509, 5.248…), y los 100.000
triángulos son la suma de todos ellos. El número redondo viene de cómo el
software que generó el NWD troceó la superficie —en bloques de 100.000
triángulos, con los restos en teselas sueltas de 77.070, 99.323, 72.126,
14.450, 6.330 y 567—, no de un límite de la API.

Consecuencia para el diseño: **ninguna**. La extracción por fragmentos ya
recorre todos, y el filtro `Pertenece()` impide que las instancias que
comparten definición se dupliquen.

---

## 11. El IFC de origen: el camino preferente

El árbol interno del NWD reveló de qué está hecho:

```
…SuperficiesExistentes.nwd
  └ 1B-Z5-INB-TOP-V13.T1-MFD-000-0-SuperficiesExistentes.ifc
      └ 24-06-07 Z1.pol
          └ TTP                      (IfcSite)
              └ IfcGeographicElement (Category)
                  └ 500.000 - 505.115 (LEV_ESP)_v2   ObjectType: TRIANGULACIÓN
                      └ Malla
```

El NWD se generó anexando un IFC, y **ese IFC está en disco**, junto al
NWD: 1,08 GB, esquema **IFC4**, generado por IfcOpenShell 0.7.0 desde
**ISTRAM** (Buhodra Ingeniería). Guarda la topografía como
`IFCTRIANGULATEDFACESET` + `IFCCARTESIANPOINTLIST3D`: malla triangular
explícita en texto plano.

### Medición (2026-09-04)

```
Lectura del archivo completo    7,3 s
Elementos                       6
Facesets                        313
Vértices                        13.521.798
Triángulos                      24.468.130
```

Los primeros facesets miden 100000, 77070, 100000, 100000, 99323, 72126,
100000 y 14450 triángulos: **exactamente los mismos items** que devolvió
el sondeo del NWD. Es la misma geometría.

### Verificación de fidelidad

Extraída la misma zona de 500 m por las dos vías y consultada la cota en
los mismos puntos del mundo:

```
punto (coordenadas NWD)          Z vía NWD    Z vía IFC   diferencia
(187712,6 , 2855847,8)            120,9481     120,9481      0,00 mm
(187808,3 , 2855795,9)            121,1456     121,1456      0,00 mm
(187973,5 , 2855763,0)            122,1057     122,1057      0,00 mm
(187827,7 , 2855805,6)            121,0371     121,0379      0,81 mm
(187892,7 , 2855614,7)            120,9968     120,9968      0,05 mm

9 de 9 puntos coinciden · desviación máxima 0,81 mm
```

Además el IFC da la misma superficie con **un 35% menos de triángulos**
(570.654 frente a 882.520 en esa zona): Navisworks duplica fragmentos que
el IFC no repite.

### Por qué se prefiere

| | IFC | NWD vía Navisworks |
|---|---|---|
| Navisworks instalado | no hace falta | obligatorio |
| Licencia | ninguna | consume una |
| Lectura del modelo completo | 7,3 s | no medible (teselado) |
| Triángulos para la misma zona | 570.654 | 882.520 |
| Dependencia de versión de API | ninguna | alta |

La georreferenciación (`IFCMAPCONVERSION`) viene a cero, con CRS
*"LTM CHILE EL MAÑIO (-72.25)"*, así que el IFC está en coordenadas
locales y Navisworks aplicó al insertarlo un desplazamiento de
dX=185.846,133, dY=2.842.543,394, **dZ=0**. No supone ningún problema: la
transformación se deriva comparando cajas, igual que antes, y sale sola.

**Decisión:** el origen preferente es el IFC cuando está junto al NWD y su
lector está compilado; el puente de Navisworks queda como respaldo para
los NWD sin IFC accesible. Ambos producen el mismo cache `NWCACHE2`, así
que nada por debajo distingue cuál se usó.
