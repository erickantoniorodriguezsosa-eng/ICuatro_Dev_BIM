using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using Autodesk.Civil.DatabaseServices;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;
using Entity = Autodesk.AutoCAD.DatabaseServices.Entity;
using CivilSection = Autodesk.Civil.DatabaseServices.Section;
using CivilSurface = Autodesk.Civil.DatabaseServices.Surface;

[assembly: CommandClass(typeof(ICuatro.LandXmlBatch.ZShift))]

namespace ICuatro.LandXmlBatch
{
    /// <summary>Model space entity summary used by the ICMOVERZ filters.</summary>
    public class EntityInfo
    {
        public ObjectId Id;
        public string Layer;
        public string Type;
        public string Color;
        public bool HasZ;
        public double ZMin, ZMax;
    }

    public class ZShiftResult
    {
        public int Moved, Locked, Unsupported, Failed;
        public List<string> Log = new List<string>();
    }

    /// <summary>
    /// Selects model space objects by layer, type, color and elevation range and raises or lowers them in Z.
    /// CogoPoints change Elevation, TIN surfaces use RaiseSurface, everything else is moved with a Z displacement.
    /// Planar Civil 3D objects (alignments, profiles, views, labels...) are skipped.
    /// </summary>
    public class ZShift
    {
        [CommandMethod("ICuatro", "ICMOVERZ", CommandFlags.Modal | CommandFlags.UsePickSet | CommandFlags.Redraw)]
        public void Dialog()
        {
            var doc = AcApp.DocumentManager.MdiActiveDocument;
            var ed = doc.Editor;

            var pre = ed.SelectImplied();
            var preIds = pre.Status == PromptStatus.OK ? pre.Value.GetObjectIds() : new ObjectId[0];

            ed.WriteMessage("\nLeyendo espacio modelo...");
            var infos = Scan(doc.Database);
            if (infos.Count == 0) { ed.WriteMessage("\nEl espacio modelo no contiene objetos."); return; }

            ZShiftForm.Request req;
            using (var form = new ZShiftForm(infos, preIds.Length))
            {
                if (AcApp.ShowModalDialog(form) != System.Windows.Forms.DialogResult.OK) return;
                req = form.Result;
            }

            HashSet<ObjectId> scope = null;
            if (req.Scope == ZShiftForm.ScopeKind.PreSelection) scope = new HashSet<ObjectId>(preIds);
            else if (req.Scope == ZShiftForm.ScopeKind.Pick)
            {
                var sel = ed.GetSelection(new PromptSelectionOptions { MessageForAdding = "\nSeleccione area de trabajo (se aplicaran los filtros): " });
                if (sel.Status != PromptStatus.OK) return;
                scope = new HashSet<ObjectId>(sel.Value.GetObjectIds());
            }

            var ids = infos.Where(i => (scope == null || scope.Contains(i.Id)) && req.Matches(i)).Select(i => i.Id).ToArray();
            if (ids.Length == 0) { ed.WriteMessage("\nNingun objeto cumple los filtros."); return; }

            if (req.SelectOnly)
            {
                ed.SetImpliedSelection(ids);
                ed.WriteMessage($"\n{ids.Length:N0} objeto(s) seleccionados.");
                return;
            }
            Report(ed, Move(doc.Database, ids, req.DeltaZ), req.DeltaZ);
        }

        /// <summary>Command-line variant: select objects, type the Z offset.</summary>
        [CommandMethod("ICuatro", "-ICMOVERZ", CommandFlags.Modal | CommandFlags.UsePickSet)]
        public void CommandLine()
        {
            var doc = AcApp.DocumentManager.MdiActiveDocument;
            var ed = doc.Editor;
            var sel = ed.GetSelection(new PromptSelectionOptions { MessageForAdding = "\nSeleccione objetos a desplazar en Z: " });
            if (sel.Status != PromptStatus.OK) return;
            var pd = ed.GetDouble(new PromptDoubleOptions("\nDesplazamiento Z (+ sube, - baja): ") { AllowZero = false });
            if (pd.Status != PromptStatus.OK) return;
            Report(ed, Move(doc.Database, sel.Value.GetObjectIds(), pd.Value), pd.Value);
        }

        public static List<EntityInfo> Scan(Database db)
        {
            var list = new List<EntityInfo>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                foreach (ObjectId id in ms)
                {
                    if (!(tr.GetObject(id, OpenMode.ForRead) is Entity ent)) continue;
                    var info = new EntityInfo { Id = id, Layer = ent.Layer, Type = id.ObjectClass.DxfName, Color = ent.Color.ToString() };
                    try
                    {
                        var ext = ent.GeometricExtents;
                        info.ZMin = ext.MinPoint.Z;
                        info.ZMax = ext.MaxPoint.Z;
                        info.HasZ = true;
                    }
                    catch { }
                    list.Add(info);
                }
                tr.Commit();
            }
            return list;
        }

        static bool IsPlanar(Entity e) =>
            e is Alignment || e is Profile || e is ProfileView || e is SectionView || e is SampleLine ||
            e is CivilSection || e is Corridor || e is Label || e is Parcel || e is Assembly || e is Subassembly ||
            e is ViewFrame || e is MatchLine || e is Viewport;

        public static ZShiftResult Move(Database db, IList<ObjectId> ids, double dz)
        {
            var r = new ZShiftResult();
            var m = Matrix3d.Displacement(new Vector3d(0, 0, dz));
            var lockedLayers = new Dictionary<ObjectId, bool>();
            var pm = new ProgressMeter();
            pm.SetLimit(ids.Count);
            pm.Start("Desplazando en Z...");
            try
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    foreach (var id in ids)
                    {
                        pm.MeterProgress();
                        if (id.IsErased || !(tr.GetObject(id, OpenMode.ForRead) is Entity ent)) continue;

                        if (!lockedLayers.TryGetValue(ent.LayerId, out bool locked))
                            lockedLayers[ent.LayerId] = locked = ((LayerTableRecord)tr.GetObject(ent.LayerId, OpenMode.ForRead)).IsLocked;
                        if (locked) { r.Locked++; continue; }
                        if (IsPlanar(ent) || (ent is CivilSurface && !(ent is TinSurface))) { r.Unsupported++; continue; }

                        try
                        {
                            ent.UpgradeOpen();
                            if (ent is CogoPoint cp) cp.Elevation += dz;
                            else if (ent is TinSurface ts) ts.RaiseSurface(dz);
                            else ent.TransformBy(m);
                            r.Moved++;
                        }
                        catch (System.Exception ex)
                        {
                            r.Failed++;
                            if (r.Log.Count < 20) r.Log.Add($"{id.ObjectClass.DxfName} {ent.Handle}: {ex.Message}");
                        }
                    }
                    tr.Commit();
                }
            }
            finally { pm.Stop(); pm.Dispose(); }
            return r;
        }

        static void Report(Editor ed, ZShiftResult r, double dz)
        {
            foreach (var line in r.Log) ed.WriteMessage("\n  " + line);
            ed.WriteMessage($"\nZ {(dz > 0 ? "+" : "")}{dz:0.###} m: {r.Moved:N0} desplazado(s)" +
                            (r.Locked > 0 ? $", {r.Locked:N0} en capa bloqueada" : "") +
                            (r.Unsupported > 0 ? $", {r.Unsupported:N0} no aplica (alineamientos, perfiles, vistas, etiquetas...)" : "") +
                            (r.Failed > 0 ? $", {r.Failed:N0} con error" : "") + ".");
            ed.Regen();
        }
    }
}
