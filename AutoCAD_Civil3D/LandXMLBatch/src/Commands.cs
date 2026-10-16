using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: CommandClass(typeof(ICuatro.LandXmlBatch.Commands))]

namespace ICuatro.LandXmlBatch
{
    public class Commands
    {
        /// <summary>Dialog: pick files and/or folders, choose content, import.</summary>
        [CommandMethod("ICuatro", "ICLANDXML", CommandFlags.Modal)]
        public void ImportDialog()
        {
            using (var form = new ImportForm())
            {
                if (AcApp.ShowModalDialog(form) != System.Windows.Forms.DialogResult.OK) return;
                Run(form.Files, form.Options);
            }
        }

        /// <summary>Command-line variant for scripts: -ICLANDXML folder recursive.</summary>
        [CommandMethod("ICuatro", "-ICLANDXML", CommandFlags.Modal)]
        public void ImportFolderCommandLine()
        {
            var ed = AcApp.DocumentManager.MdiActiveDocument.Editor;
            var ps = ed.GetString(new PromptStringOptions("\nCarpeta con LandXML: ") { AllowSpaces = true });
            if (ps.Status != PromptStatus.OK) return;
            string folder = ps.StringResult.Trim().Trim('"');
            if (!Directory.Exists(folder)) { ed.WriteMessage($"\nCarpeta no existe: {folder}"); return; }

            var pk = new PromptKeywordOptions("\nIncluir subcarpetas [Si/No] <Si>: ", "Si No") { AllowNone = true };
            var kr = ed.GetKeywords(pk);
            if (kr.Status == PromptStatus.Cancel) return;
            bool recursive = kr.Status != PromptStatus.OK || kr.StringResult != "No";

            Run(FileCollector.FromFolder(folder, recursive).ToList(), new ImportOptions());
        }

        static void Run(List<string> files, ImportOptions opt)
        {
            var doc = AcApp.DocumentManager.MdiActiveDocument;
            var ed = doc.Editor;
            if (files.Count == 0) { ed.WriteMessage("\nNo hay archivos .xml para importar."); return; }

            var rep = new ImportReport();
            var importer = new Importer(doc.Database, opt, rep);
            var pm = new ProgressMeter();
            pm.SetLimit(files.Count);
            pm.Start("Importando LandXML...");
            try
            {
                foreach (var f in files)
                {
                    importer.ImportFile(f);
                    pm.MeterProgress();
                    System.Windows.Forms.Application.DoEvents();
                }
            }
            finally { pm.Stop(); pm.Dispose(); }

            string logPath = Path.Combine(Path.GetTempPath(), $"ICuatro_LandXML_{DateTime.Now:yyyyMMdd_HHmmss}.log");
            try { File.WriteAllLines(logPath, rep.Log); } catch { logPath = null; }

            foreach (var line in rep.Log) ed.WriteMessage("\n" + line);
            ed.WriteMessage($"\n\nLandXML: {rep.Files} archivo(s), {rep.FilesFailed} con error | " +
                            $"{rep.Surfaces} superficie(s), {rep.Alignments} alineamiento(s), {rep.Profiles} perfil(es), {rep.Skipped} omitido(s).");
            if (logPath != null) ed.WriteMessage($"\nLog: {logPath}\n");
            ed.Regen();
        }
    }
}
