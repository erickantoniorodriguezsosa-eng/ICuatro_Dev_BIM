# ICuatro Extensions

- Extensión pyRevit para Revit 2022: NWD Coordinator, Crear Tubería/Ducto y Elementos BIM desde CAD.
- El motor `Revit/pyRevit/ICuatro.extension/lib/nwdcoord` usa IFC preferentemente y Navisworks como respaldo mediante los binarios de `Revit/pyRevit/ICuatro.extension/bin`.
- Usar `.venv` solo para pruebas aisladas; la API de Revit debe validarse dentro de Revit 2022.
- Consultar `NWD_CAPABILITY_REPORT.md` solo para tareas NWD y respetar .NET Framework 4.8 para integraciones Autodesk.
- Mantener cambios pequeños, reportar bloqueos y ejecutar únicamente pruebas pertinentes.

## Entrada unica Claude + Codex

- Si la solicitud del usuario comienza con `ORQUESTAR:`, usar el texto restante como objetivo exacto y ejecutar `AI_Automations/ai-team/Invoke-AiTeam.ps1 -Task <objetivo>` desde la raiz.
- No implementar la tarea por separado antes ni despues: el orquestador controla analisis, codigo, revision, correcciones, pruebas y aplicacion local.
- Entregar al usuario el estado final y la ruta de artefactos de la corrida. Si el estado es `application_pending_revit_close`, explicar que debe cerrar Revit 2022 para completar la instalacion del DLL.
- Los prompts internos del orquestador no son nuevas solicitudes `ORQUESTAR:` y no deben iniciar otro flujo anidado.

