# TAREA PARA CLAUDE — EXTENDER RECORRIDO CINEMATOGRÁFICO DE AUTOPISTA EN BLENDER

## 1. OBJETIVO GENERAL

Debes revisar la escena actual del proyecto Blender y mejorar la animación existente de la autopista.

El objetivo principal es crear una presentación aérea cinematográfica que recorra prácticamente toda la extensión visible/modelada de la autopista.

La prioridad visual debe ser la infraestructura vial completa y no los vehículos individuales.

Debes mantener y aprovechar todo lo que actualmente funciona en la escena.

No reconstruyas elementos que ya funcionan correctamente.

Antes de modificar cualquier objeto, cámara, animación o material, analiza la estructura existente del proyecto.

---

# 2. REGLAS PRINCIPALES

Prioridades:

1. Mostrar toda o prácticamente toda la extensión de la autopista.
2. Crear un vuelo cinematográfico suave.
3. Mostrar tránsito activo durante el recorrido.
4. Utilizar los vehículos existentes como base.
5. Evitar primeros planos excesivos de vehículos.
6. Mantener movimientos naturales.
7. No destruir configuraciones actuales que ya funcionan.

La autopista debe ser siempre el elemento protagonista.

---

# 3. ANALIZAR PRIMERO LA ESCENA ACTUAL

Antes de realizar cambios:

- inspecciona todas las colecciones;
- identifica cámaras existentes;
- identifica animaciones existentes;
- identifica keyframes;
- identifica curvas de animación;
- identifica constraints;
- identifica objetos de autopista;
- identifica carriles;
- identifica vehículos;
- identifica materiales;
- identifica luces;
- identifica elementos vinculados a la cámara;
- identifica scripts existentes;
- identifica Geometry Nodes si existen;
- identifica paths o curvas empleadas para movimiento.

Localiza específicamente:

```text
auto_01
```

y localiza el modelo de:

```text
camion
```

Si el nombre del camión no coincide exactamente con `camion`, identifica cuál objeto corresponde visualmente al camión existente.

No crear nuevos modelos de vehículos desde cero si estos modelos ya existen.

---

# 4. RESPALDO ANTES DE MODIFICAR

Antes de aplicar modificaciones importantes:

- conserva la estructura original;
- evita borrar objetos originales;
- evita eliminar animaciones existentes;
- evita sobrescribir materiales originales innecesariamente.

Si necesitas modificar sistemas existentes, crea primero una copia o una forma segura de recuperación.

---

# 5. EXTENDER LA ESCENA

La animación actual debe extenderse considerablemente.

No concentres el recorrido únicamente en una zona.

La cámara debe recorrer toda o prácticamente toda la autopista disponible dentro del modelo.

Debes identificar:

- inicio del trazado;
- final del trazado;
- principales curvas;
- rectas;
- enlaces;
- intersecciones;
- estructuras;
- desniveles;
- zonas visualmente relevantes;
- elementos de infraestructura importantes.

Construye el recorrido teniendo en cuenta toda la geometría disponible.

---

# 6. RECORRIDO PRINCIPAL DE CÁMARA

La cámara debe realizar principalmente un recorrido aéreo longitudinal.

La sensación debe ser similar a:

- drone cinematográfico;
- helicóptero estabilizado;
- presentación aérea profesional;
- fly-through de infraestructura BIM;
- visualización cinematográfica de ingeniería vial.

Evita una cámara demasiado cercana al suelo durante periodos largos.

---

# 7. PROPORCIÓN DE TOMAS

Utiliza aproximadamente:

```text
80-90 % del tiempo:
planos generales y medios de la autopista.

10-20 % del tiempo:
aproximaciones breves a vehículos o zonas particulares.
```

No utilizar muchos primeros planos consecutivos.

No convertir el video en una presentación de autos.

Los vehículos son secundarios.

La autopista es el protagonista.

---

# 8. TIPOS DE TOMAS

Alternar de manera natural entre:

## Tomas aéreas altas

Mostrar:

- extensión de la autopista;
- contexto;
- geometría general;
- curvas;
- enlaces;
- tránsito.

## Tomas aéreas medias

Seguir longitudinalmente la autopista.

Mantener suficiente altura para comprender el trazado.

## Travelling lateral

La cámara puede desplazarse paralelamente a la autopista durante algunos sectores.

## Tomas diagonales

Permitir visualizar varios carriles y parte del entorno.

## Elevación progresiva

En determinados sectores:

1. seguir la carretera;
2. aumentar progresivamente la altura;
3. mostrar una vista panorámica;
4. volver gradualmente hacia una altura media.

## Descensos controlados

En algunos puntos de interés puede reducirse la altura.

Nunca realizar descensos bruscos.

---

# 9. ACERCAMIENTOS A VEHÍCULOS

Reducir considerablemente los acercamientos actuales a vehículos.

Realizar pocos acercamientos.

Ejemplo:

```text
Plano general de autopista
↓
Descenso progresivo
↓
Breve seguimiento de auto_01
↓
La cámara se separa
↓
Recupera altura
↓
Continúa recorriendo la autopista
```

El seguimiento individual debe durar poco tiempo.

Después de cada aproximación, volver a mostrar rápidamente la infraestructura completa.

---

# 10. VEHÍCULOS BASE

Utilizar como base:

```text
auto_01
```

y el modelo existente del:

```text
camion
```

Los nuevos vehículos deben ser copias o instancias de esos modelos.

No cambiar la geometría del vehículo base.

No cambiar significativamente sus dimensiones.

No deformarlos.

No crear variantes de carrocería innecesarias.

---

# 11. CREAR MÁS AUTOMÓVILES

Duplicar `auto_01` varias veces.

Crear suficiente cantidad de vehículos para generar sensación de tráfico realista a lo largo de la autopista.

Los autos deben encontrarse distribuidos en numerosos sectores.

Evitar concentrarlos todos en una única zona.

---

# 12. VARIACIÓN DE COLORES

Las copias de `auto_01` deben diferenciarse principalmente por color.

Crear materiales derivados del original sin destruir el material base.

Utilizar colores realistas como:

- blanco;
- negro;
- gris;
- plateado;
- azul;
- rojo;
- azul oscuro;
- gris oscuro;
- otros tonos automotrices moderados.

Evitar colores extremadamente saturados o irreales salvo que visualmente sean necesarios.

---

# 13. CAMIONES

Duplicar también el camión existente.

Distribuir varios camiones a través del proyecto.

Aplicar pequeñas variaciones visuales principalmente mediante color.

No modificar innecesariamente su geometría.

Los camiones deben quedar mezclados con los automóviles de manera natural.

---

# 14. DISTRIBUCIÓN DEL TRÁNSITO

El tránsito no debe parecer generado mediante una cuadrícula perfecta.

Crear una distribución irregular y natural.

Debe existir:

- sectores con poco tránsito;
- sectores con tránsito moderado;
- pequeños grupos de vehículos;
- distancias variables;
- vehículos aislados;
- camiones mezclados con autos.

Evitar espacios idénticos entre todos los vehículos.

---

# 15. SENTIDO DE CIRCULACIÓN

Analiza la orientación de los carriles.

Cada vehículo debe moverse en el sentido correcto de circulación.

Si existen carriles en ambas direcciones, crear tránsito en ambos sentidos.

No permitir vehículos circulando contra el tránsito.

---

# 16. ANIMACIÓN DE LOS VEHÍCULOS

Los vehículos deben mantenerse en movimiento durante el recorrido.

Preferentemente utiliza:

- curvas;
- paths;
- Follow Path;
- constraints;
- drivers;
- Geometry Nodes;

o el sistema existente más apropiado dentro de la escena.

No reemplaces un sistema funcional sin una razón técnica.

---

# 17. MOVIMIENTO REALISTA

Los automóviles deben:

- mantenerse dentro del carril;
- seguir correctamente las curvas;
- evitar movimientos laterales abruptos;
- evitar saltos;
- mantener orientación coherente;
- mantener contacto visual con la carretera;
- respetar pendientes;
- evitar atravesar la geometría.

---

# 18. VELOCIDADES

No utilizar exactamente la misma velocidad para todos los vehículos.

Introducir ligeras variaciones.

Por ejemplo:

```text
Vehículo A → velocidad normal
Vehículo B → ligeramente más rápido
Vehículo C → ligeramente más lento
Camión A → moderadamente más lento
```

Las diferencias deben ser sutiles.

---

# 19. EVITAR COLISIONES

Verificar que durante la animación:

- los autos no se atraviesen;
- no se superpongan;
- no colisionen visualmente;
- no aparezcan dos vehículos ocupando la misma posición;
- no existan cambios de carril absurdos.

Mantener separación razonable.

---

# 20. MOVIMIENTO DE CÁMARA

El movimiento debe ser muy suave.

Revisar:

- keyframes;
- interpolaciones;
- F-Curves;
- easing;
- velocidad angular;
- velocidad lineal.

Priorizar interpolaciones suaves.

Evitar:

- aceleraciones instantáneas;
- frenadas repentinas;
- cambios bruscos de dirección;
- rotaciones rápidas;
- saltos de cámara.

---

# 21. ANTICIPAR LAS CURVAS

Cuando la autopista cambie de dirección:

la cámara debe anticipar progresivamente la curva.

No esperar hasta encontrarse directamente sobre la curva para rotar.

La cámara debe comenzar suavemente a orientar la vista hacia la siguiente parte del recorrido.

---

# 22. TRANSICIONES

Las transiciones entre movimientos deben ser suaves.

Ejemplo:

```text
Vuelo longitudinal
↓
giro progresivo
↓
vuelo diagonal
↓
elevación
↓
plano panorámico
↓
descenso progresivo
↓
continuación longitudinal
```

Debe sentirse como un único viaje continuo.

---

# 23. CORTES DE CÁMARA

Reducir cortes innecesarios.

Siempre que sea posible utilizar movimientos continuos.

Cuando un corte sea necesario, hacerlo entre tomas visualmente compatibles.

Ejemplo:

```text
Movimiento hacia adelante
CUT
Movimiento hacia adelante
```

Evitar:

```text
Movimiento hacia adelante
CUT
Movimiento opuesto completamente diferente
```

---

# 24. CONECTAR TOMAS

Los cortes deben tener continuidad espacial y visual.

Mantener coherencia de:

- orientación;
- velocidad;
- dirección;
- altura;
- composición.

El espectador debe entender que sigue recorriendo la misma autopista.

---

# 25. COMPOSICIÓN

Durante los planos generales:

mantener la carretera como elemento dominante.

Utilizar elementos del entorno para aportar profundidad.

Los vehículos deben servir para:

- aportar escala;
- mostrar funcionamiento;
- introducir movimiento;
- dar realismo.

No permitir que un vehículo ocupe gran parte de la pantalla durante largos periodos.

---

# 26. MOSTRAR VARIOS VEHÍCULOS SIMULTÁNEAMENTE

Siempre que sea posible, durante el recorrido aéreo mostrar simultáneamente:

- varios autos;
- uno o más camiones;
- vehículos en distintos carriles;
- tránsito en diferentes direcciones.

Esto debe aumentar la sensación de actividad.

---

# 27. EVITAR REPETICIÓN VISUAL

Aunque los vehículos provengan de los mismos modelos, evitar que parezcan copiados idénticamente.

Variar:

- color;
- posición;
- separación;
- velocidad;
- momento de entrada;
- ubicación dentro del recorrido.

No modificar innecesariamente la geometría.

---

# 28. DURACIÓN Y RITMO

Extender el recorrido lo suficiente como para mostrar el proyecto completo.

No acelerar excesivamente la cámara únicamente para cubrir mayor distancia.

Si es necesario:

aumentar la duración total de la animación.

Priorizar calidad y claridad del recorrido.

---

# 29. FOTOGRAFÍA Y SENSACIÓN VISUAL

Buscar una sensación de:

```text
visualización BIM profesional
+
ingeniería vial
+
cinematografía aérea
+
presentación comercial
```

El video debe permitir comprender el proyecto y al mismo tiempo resultar visualmente atractivo.

---

# 30. NO MODIFICAR INNECESARIAMENTE

No modificar sin necesidad:

- geometría principal de la autopista;
- vegetación existente;
- estructuras;
- iluminación existente funcional;
- materiales de infraestructura;
- entorno;
- vehículos originales.

Realiza cambios únicamente cuando aporten a la animación.

---

# 31. OPTIMIZACIÓN DE VEHÍCULOS

Si agregar muchos vehículos afecta significativamente el rendimiento:

priorizar:

```text
Linked Duplicates
```

o:

```text
Instances
```

antes que copias completamente independientes.

Los materiales pueden variar sin duplicar innecesariamente toda la geometría.

Optimizar memoria y rendimiento.

---

# 32. OPTIMIZACIÓN DE ESCENA

Antes de aumentar excesivamente la complejidad:

analizar:

- cantidad de polígonos;
- instancias;
- materiales;
- modifiers;
- Geometry Nodes;
- simulaciones;
- resolución;
- render engine.

No generar elementos innecesarios.

---

# 33. SI EXISTEN SCRIPTS

Si la escena utiliza Python para:

- generar vehículos;
- animarlos;
- crear cámaras;
- gestionar rutas;

analiza primero el script.

Preferir mejorar el sistema existente en lugar de crear sistemas paralelos innecesarios.

---

# 34. SI ES NECESARIO CREAR PYTHON

Si es más eficiente automatizar mediante Python, puedes desarrollar un script para:

1. localizar `auto_01`;
2. localizar el camión;
3. generar instancias;
4. generar materiales de diferentes colores;
5. distribuir vehículos;
6. asignarlos a rutas;
7. variar velocidades;
8. crear offsets de animación.

El script debe ser seguro y repetible.

Evitar que al ejecutarlo varias veces genere duplicados infinitamente.

Implementar controles si es necesario.

---

# 35. NO ASUMIR NOMBRES

No inventar nombres de objetos internos.

Busca primero los objetos reales dentro del archivo `.blend`.

Solo después utiliza sus nombres reales en scripts o modificaciones.

---

# 36. METODOLOGÍA DE TRABAJO

Trabaja siguiendo este orden:

## FASE 1 — INSPECCIÓN

Analiza completamente el proyecto.

## FASE 2 — DIAGNÓSTICO

Determina:

- cómo funciona la animación actual;
- cómo están organizados los vehículos;
- cómo funciona la cámara;
- qué partes se pueden reutilizar.

## FASE 3 — PLAN

Define internamente la estrategia de modificación.

## FASE 4 — IMPLEMENTACIÓN

Aplica los cambios.

## FASE 5 — PRUEBA

Reproduce la animación.

## FASE 6 — CORRECCIÓN

Corrige problemas.

## FASE 7 — VALIDACIÓN

Verifica el resultado completo.

---

# 37. PRUEBAS OBLIGATORIAS

Antes de considerar terminada la tarea, comprobar:

### Cámara

- no hay saltos;
- no existen giros violentos;
- no existen aceleraciones bruscas;
- se visualiza la mayor parte de la autopista.

### Vehículos

- permanecen en los carriles;
- no atraviesan la autopista;
- no vuelan;
- no se hunden;
- siguen las curvas;
- mantienen orientación correcta.

### Tráfico

- existen numerosos vehículos;
- están distribuidos;
- presentan variaciones;
- existen autos y camiones;
- existe sensación de tránsito activo.

### Visualización

- la autopista sigue siendo protagonista;
- los acercamientos son pocos;
- el recorrido resulta comprensible;
- existe continuidad visual.

---

# 38. RESULTADO ESPERADO

El resultado final debe percibirse como una presentación aérea profesional de la autopista.

Debe transmitir:

```text
ESCALA
+
CONTINUIDAD
+
TRÁNSITO
+
MOVIMIENTO
+
REALISMO
+
FLUIDEZ
+
CALIDAD CINEMATOGRÁFICA
```

La cámara debe recorrer la infraestructura mientras se observa tráfico activo en distintos sectores.

---

# 39. PRIORIDAD FINAL

Mantener esta prioridad estricta:

```text
1. AUTOPISTA COMPLETA
2. RECORRIDO DE CÁMARA
3. INFRAESTRUCTURA
4. TRÁNSITO EN MOVIMIENTO
5. ENTORNO
6. VEHÍCULOS INDIVIDUALES
```

Nunca invertir estas prioridades.

---

# 40. CRITERIO DE FINALIZACIÓN

No considerar terminada la tarea únicamente porque el script funcione o porque existan keyframes.

La tarea estará terminada cuando:

- el recorrido abarque prácticamente toda la autopista;
- la cámara se mueva suavemente;
- el tránsito esté distribuido;
- `auto_01` sea utilizado como base;
- el camión existente sea utilizado como base;
- existan variantes de color;
- los vehículos estén animados;
- los vehículos respeten sus carriles;
- existan pocos acercamientos;
- las transiciones sean suaves;
- no existan errores visuales evidentes.

---

# 41. REPORTE FINAL A ENTREGAR

Cuando termines, informa:

```text
1. Qué objetos fueron identificados.
2. Qué cámara o cámaras fueron modificadas.
3. Qué sistema de movimiento utilizaste.
4. Cuántos vehículos adicionales fueron creados.
5. Cómo se generaron las variantes de color.
6. Cómo se controlaron las rutas de los vehículos.
7. Cómo se variaron las velocidades.
8. Qué duración final tiene la animación.
9. Qué problemas encontraste.
10. Qué correcciones realizaste.
11. Qué archivos o scripts fueron modificados.
12. Si queda algún elemento que requiera revisión manual.
```

No indiques únicamente que la tarea fue completada.

Documenta claramente los cambios realizados.