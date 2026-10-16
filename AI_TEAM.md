# Orquestador Claude + Codex

Este repositorio incluye un flujo local para repartir una tarea entre Claude Code y Codex CLI sin que ambos editen al mismo tiempo.

## Flujo predeterminado

1. **Claude** descompone la solicitud y define los criterios de aceptacion.
2. **Codex** implementa el plan en el workspace.
3. **Claude** hace una revision de solo lectura.
4. **Codex** corrige los hallazgos, si los hay.
5. El orquestador detecta el componente Autodesk, ejecuta las comprobaciones locales pertinentes y aplica la solucion.

La estrategia `claude-build` invierte los agentes: Codex planifica/revisa y Claude implementa/corrige.

## Uso principal: una sola instruccion

Puede escribir la solicitud tanto en este chat de Codex como en un chat de Claude Code abierto en la raiz del proyecto. Use el mismo prefijo en ambos:

```text
ORQUESTAR: Crear una herramienta que genere tuberias desde lineas CAD y permita elegir el tipo de sistema.
```

`AGENTS.md` y `CLAUDE.md` contienen el mismo disparador. El chat que reciba la solicitud inicia el orquestador y el reparto interno es identico; no es necesario repetirla en el otro chat.

La alternativa directa desde PowerShell es:

Desde PowerShell, en la raiz del proyecto:

```powershell
.\AI_Automations\ai-team\Invoke-AiTeam.ps1 -Task "Corregir el calculo de pendiente sin romper Revit 2022"
```

Esa descripcion se entrega una sola vez. El flujo posterior es automatico: analisis, codificacion, revision cruzada, correcciones, pruebas locales y aplicacion.

Para una tarea del add-in C#:

```powershell
.\AI_Automations\ai-team\Invoke-AiTeam.ps1 `
  -Task "Agregar validacion al crear un ducto" `
  -Strategy claude-build
```

Para preparar una corrida y revisar los prompts sin consumir una sesion de los agentes:

```powershell
.\AI_Automations\ai-team\Invoke-AiTeam.ps1 -Task "Descripcion de la tarea" -PrepareOnly
```

## Parametros principales

- `-Task`: objetivo concreto de la corrida.
- `-Strategy codex-build`: Claude planifica/revisa; Codex implementa/corrige. Es el valor predeterminado.
- `-Strategy claude-build`: Codex planifica/revisa; Claude implementa/corrige.
- `-VerifyCommand`: comprobaciones adicionales opcionales; normalmente no hace falta indicarlas.
- `-ApplicationMode auto`: aplica localmente la solucion aprobada. Es el valor predeterminado.
- `-ApplicationMode none`: analiza, implementa y verifica sin instalar o recargar.
- `-MaxReviewCycles`: cantidad maxima de revisiones; el valor predeterminado es 2.
- `-PrepareOnly`: crea los artefactos iniciales y no llama a ningun modelo.
- `-ExistingPlan`: reutiliza un plan generado anteriormente cuando una corrida se interrumpio despues del analisis.

Ejemplo con dos verificaciones:

```powershell
.\AI_Automations\ai-team\Invoke-AiTeam.ps1 `
  -Task "Ajustar el contacto con terreno" `
  -VerifyCommand @( `
    ".\.venv\Scripts\python.exe -m pytest tests\test_nwd_contact.py", `
    ".\.venv\Scripts\python.exe -m pytest tests\test_nwd_slope.py" `
  )
```

## Automatizacion Autodesk local

El perfil se deduce de los archivos cambiados:

- **Motor NWD/pyRevit:** ejecuta las pruebas aisladas disponibles, valida los XAML modificados, comprueba que `Revit\pyRevit\ICuatro.extension` este registrada y limpia la cache de Revit 2022 cuando Revit esta cerrado.
- **Comandos pyRevit CAD/MEP:** valida los XAML y usa directamente el workspace ya registrado. La comprobacion funcional queda para Revit.
- **Add-in C#:** compila `Release|x64` para .NET Framework 4.8, respalda la instalacion anterior y copia DLL, PDB, manifiesto, icono y puentes en `%AppData%\Autodesk\Revit\Addins\2022`.

Si Revit 2022 esta abierto, pyRevit queda listo para usar el boton **Recargar**. Un add-in C# cargado no se sobrescribe: el estado queda como `application_pending_revit_close` y `application.log` entrega el comando exacto para completar la instalacion una vez cerrado Revit.

## Trazabilidad y seguridad

Cada ejecucion crea `.ai-team/runs/<fecha>-<id>/` con:

- `request.md`: solicitud, estrategia y restricciones del proyecto.
- `01-plan.md`: plan del agente planificador.
- `02-implementation.md`: informe del implementador.
- `changes-*.txt`: archivos agregados, modificados o eliminados segun hashes locales.
- `review-*.md`: revisiones cruzadas y veredicto.
- `fix-*.md`: correcciones solicitadas.
- `verification-*.log`: comprobaciones seleccionadas automaticamente y sus codigos de retorno.
- `application.log`: registro de la aplicacion local.
- `state.json`: estado final legible por otras herramientas.

Los agentes no reciben autorizacion para usar servicios externos, hacer `commit`, `push`, instalar dependencias ni ejecutar acciones destructivas. Las verificaciones e instalaciones pertenecen a perfiles PowerShell controlados; nunca se ejecuta un comando propuesto por un modelo.

El inventario por hashes permite usar el flujo aunque esta carpeta no sea un repositorio Git. Aun asi, se recomienda versionarla antes de tareas importantes para poder revisar y revertir cambios con mayor precision.

## Limites del entorno Revit

Las pruebas de `.venv` validan solamente logica aislada. Las operaciones de la API de Revit y la integracion pyRevit deben verificarse dentro de Revit 2022. El orquestador deja esa validacion marcada como pendiente en el estado si no se entrega un comando automatizado; no afirma que una prueba aislada equivalga a una validacion en Revit.
