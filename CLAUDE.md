# ICuatro Extensions

- Proyecto pyRevit para Revit 2022 con tres comandos: NWD Coordinator, Crear Tubería/Ducto y Elementos BIM desde CAD.
- `Revit/pyRevit/ICuatro.extension/lib/nwdcoord` contiene el motor NWD/IFC; los extractores C# están compilados en `Revit/pyRevit/ICuatro.extension/bin`.
- `.venv` es CPython para utilidades y pruebas aisladas; no ejecuta pyRevit fuera de Revit.
- Leer solo el documento técnico necesario: `NWD_CAPABILITY_REPORT.md` para NWD validado y `NWD_COORDINATOR_CLAUDE_PROMPT.md` para requisitos de diseño.
- Mantener compatibilidad con Revit 2022 y .NET Framework 4.8; verificar antes de modificar APIs de Autodesk.
- Ser conciso, modificar solo lo solicitado y ejecutar la verificación mínima relevante.

## Entrada unica Claude + Codex

- Si la solicitud del usuario comienza con `ORQUESTAR:`, usar el texto restante como objetivo exacto y ejecutar `AI_Automations/ai-team/Invoke-AiTeam.ps1 -Task <objetivo>` desde la raiz.
- No implementar la tarea por separado antes ni despues: el orquestador controla analisis, codigo, revision, correcciones, pruebas y aplicacion local.
- Entregar al usuario el estado final y la ruta de artefactos de la corrida. Si el estado es `application_pending_revit_close`, explicar que debe cerrar Revit 2022 para completar la instalacion del DLL.
- Los prompts internos del orquestador no son nuevas solicitudes `ORQUESTAR:` y no deben iniciar otro flujo anidado.
