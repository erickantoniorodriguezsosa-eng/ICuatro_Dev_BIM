using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using Autodesk.Civil;
using Autodesk.Civil.ApplicationServices;
using Autodesk.Civil.DatabaseServices;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;
using DBObject = Autodesk.AutoCAD.DatabaseServices.DBObject;
using DBPoint = Autodesk.AutoCAD.DatabaseServices.DBPoint;

[assembly: CommandClass(typeof(ICuatro.LandXmlBatch.SurfaceFromCurves))]

namespace ICuatro.LandXmlBatch
{
    /// <summary>
    /// Builds or extends a TIN surface from lines, polylines, arcs, splines, feature lines, points and 3D faces.
    /// Large selections are measured first, tolerances are balanced against a point budget, and data is
    /// added in chunks with automatic rebuild off and a single rebuild at the end.
    /// </summary>
    public class SurfaceFromCurves
    {
        const string Desc = "ICuatro ICSUPLINEAS";
        const int ReadChunk = 2000;          // objects per read transaction
        const int VertexChunk = 250_000;     // points per AddVertices call
        const int OpChunk = 200;             // single-curve operations per transaction
        const double DedupeCell = 0.001;     // 1 mm: identical vertices are merged

        static readonly string[] DxfTypes =
        {
            "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "LWPOLYLINE", "POLYLINE",
            "AECC_FEATURE_LINE", "POINT", "AECC_COGO_POINT", "3DFACE",
        };

        class Selection
        {
            public List<ObjectId> Native = new List<ObjectId>();
            public List<ObjectId> Sampled = new List<ObjectId>();
            public List<Point3dCollection> SampledPts = new List<Point3dCollection>();
            public List<ObjectId> DbPoints = new List<ObjectId>();
            public List<ObjectId> Faces = new List<ObjectId>();
            public List<Point3d> Vertices = new List<Point3d>();
            public LoadStats Stats = new LoadStats();
            public int Flat;
        }

        class CanceledException : System.Exception { }

        [CommandMethod("ICuatro", "ICSUPLINEAS", CommandFlags.Modal)]
        public void Run()
        {
            var doc = AcApp.DocumentManager.MdiActiveDocument;
            var db = doc.Database;
            var ed = doc.Editor;
            var civ = CivilDocument.GetCivilDocument(db);

            List<string> surfaces, styles;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                surfaces = civ.GetSurfaceIds().Cast<ObjectId>()
                    .Select(id => tr.GetObject(id, OpenMode.ForRead)).OfType<TinSurface>()
                    .Select(s => s.Name).OrderBy(n => n).ToList();
                styles = new List<string>();
                foreach (ObjectId id in civ.Styles.SurfaceStyles)
                    styles.Add(((Autodesk.Civil.DatabaseServices.Styles.StyleBase)tr.GetObject(id, OpenMode.ForRead)).Name);
                tr.Commit();
            }

            SurfaceOptions opt;
            using (var form = new SurfaceForm(surfaces, styles))
            {
                if (AcApp.ShowModalDialog(form) != System.Windows.Forms.DialogResult.OK) return;
                opt = form.Options;
            }
            if (opt.NewSurface && surfaces.Contains(opt.Name, StringComparer.OrdinalIgnoreCase))
            {
                ed.WriteMessage($"\nYa existe una superficie '{opt.Name}'. Use 'Agregar a existente' u otro nombre.");
                return;
            }

            var filter = new SelectionFilter(new[] { new TypedValue((int)DxfCode.Start, string.Join(",", DxfTypes)) });
            var sel = ed.GetSelection(new PromptSelectionOptions
            {
                MessageForAdding = "\nSeleccione lineas, polilineas, arcos, splines, lineas caracteristicas, puntos o caras 3D: ",
            }, filter);
            if (sel.Status != PromptStatus.OK) return;

            ObjectIdCollection boundary = null;
            if (opt.OuterBoundary)
            {
                var bsel = ed.GetSelection(new PromptSelectionOptions { MessageForAdding = "\nSeleccione contorno exterior (curva cerrada): " },
                    new SelectionFilter(new[] { new TypedValue((int)DxfCode.Start, "LWPOLYLINE,POLYLINE,CIRCLE,ELLIPSE,SPLINE,AECC_FEATURE_LINE") }));
                if (bsel.Status == PromptStatus.OK) boundary = new ObjectIdCollection(bsel.Value.GetObjectIds());
            }

            var log = new List<string>();
            ObjectId surfId = ObjectId.Null;
            bool? prevAutoRebuild = null;
            try
            {
                var ids = sel.Value.GetObjectIds();
                ed.WriteMessage($"\nAnalizando {ids.Length:N0} objeto(s)... (ESC para cancelar)");
                var s = Analyze(db, ids, opt);
                var plan = LoadPlanner.Plan(s.Stats, opt);
                if (plan.ChordScale > 1.0) Resample(db, s, plan.MidOrdinate * plan.ChordScale);

                ed.WriteMessage($"\nPlan: ~{plan.EstimatedPoints:N0} puntos | tolerancia {plan.MidOrdinate * plan.ChordScale:0.###} m | " +
                                $"suplemento {plan.MaxDistance:0.##} m | bloques de {plan.ChunkSize:N0} objetos.");
                foreach (var n in plan.Notes) ed.WriteMessage("\n  " + n);

                if (s.Flat > 0 && s.Flat == s.Stats.NativeCount + s.Stats.SampledCount && opt.CurveUse != CurveUse.Contours)
                    log.Add($"AVISO: las {s.Flat} curvas estan en Z=0; la superficie saldra plana.");

                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var surf = OpenOrCreate(tr, db, civ, opt);
                    surfId = surf.ObjectId;
                    prevAutoRebuild = opt.NewSurface ? true : surf.AutoRebuild;
                    surf.AutoRebuild = false;
                    if (plan.LevelOfDetail) surf.IsLevelOfDetailEnabled = true;
                    tr.Commit();
                }

                Load(db, surfId, s, plan, opt, boundary, log);
            }
            catch (CanceledException)
            {
                log.Add(surfId.IsNull ? "Cancelado. No se modifico ninguna superficie." : "Cancelado. La superficie contiene solo los datos agregados hasta ahora.");
            }
            catch (System.Exception ex)
            {
                log.Add("ERROR: " + ex.Message);
            }
            finally
            {
                if (!surfId.IsNull) FinishSurface(db, surfId, prevAutoRebuild ?? true, opt, log, ed);
            }

            foreach (var l in log) ed.WriteMessage("\n" + l);
            ed.WriteMessage("\n");
            ed.Regen();
        }

        // ---------- Phase 1: measure the selection ----------

        static Selection Analyze(Database db, ObjectId[] ids, SurfaceOptions opt)
        {
            var s = new Selection();
            var ext = new Extents3d();
            bool hasExt = false;

            using (var pm = new Meter("Analizando seleccion...", ids.Length))
            {
                for (int start = 0; start < ids.Length; start += ReadChunk)
                {
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        for (int i = start; i < Math.Min(ids.Length, start + ReadChunk); i++)
                        {
                            var obj = tr.GetObject(ids[i], OpenMode.ForRead);
                            Classify(s, ids[i], obj);
                            if (obj is Autodesk.AutoCAD.DatabaseServices.Entity ent)
                            {
                                try { var e = ent.GeometricExtents; if (hasExt) ext.AddExtents(e); else { ext = e; hasExt = true; } }
                                catch { /* entity without extents */ }
                            }
                            pm.Step();
                        }
                        tr.Commit();
                    }
                }
            }

            if (hasExt)
            {
                s.Stats.Width = ext.MaxPoint.X - ext.MinPoint.X;
                s.Stats.Height = ext.MaxPoint.Y - ext.MinPoint.Y;
            }
            s.Stats.NativeCount = s.Native.Count;
            s.Stats.SampledCount = s.Sampled.Count;

            // Arcs/splines are sampled now so their real point count enters the plan.
            double baseMid = opt.Auto ? Math.Max(0.01, Math.Min(0.5, s.Stats.Diagonal / 20000)) : opt.MidOrdinate;
            Resample(db, s, baseMid);
            return s;
        }

        static void Classify(Selection s, ObjectId id, DBObject obj)
        {
            switch (obj)
            {
                case DBPoint _: s.DbPoints.Add(id); s.Stats.OtherPoints++; return;
                case CogoPoint cp: s.Vertices.Add(cp.Location); s.Stats.OtherPoints++; return;
                case Face _: s.Faces.Add(id); s.Stats.OtherPoints += 4; return;
                case FeatureLine fl:
                    {
                        var pts = fl.GetPoints(FeatureLinePointType.AllPoints);
                        s.Native.Add(id);
                        s.Stats.NativeVertices += pts.Count;
                        s.Stats.NativeLength += PathLength(pts);
                        if (IsFlat(pts)) s.Flat++;
                        return;
                    }
                case Line _:
                case Polyline _:
                case Polyline2d _:
                case Polyline3d _:
                    {
                        var c = (Curve)obj;
                        s.Native.Add(id);
                        s.Stats.NativeVertices += obj is Line ? 2 : obj is Polyline pl ? pl.NumberOfVertices : (long)Math.Round(c.EndParam) + 1;
                        try { s.Stats.NativeLength += c.GetDistanceAtParameter(c.EndParam); } catch { }
                        if (IsFlat(c)) s.Flat++;
                        return;
                    }
                case Curve c2:
                    s.Sampled.Add(id);
                    if (IsFlat(c2)) s.Flat++;
                    return;
            }
        }

        static void Resample(Database db, Selection s, double chord)
        {
            s.SampledPts.Clear();
            s.Stats.SampledPoints = 0;
            if (s.Sampled.Count == 0) return;
            using (var pm = new Meter("Discretizando curvas...", s.Sampled.Count))
            {
                for (int start = 0; start < s.Sampled.Count; start += ReadChunk)
                {
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        for (int i = start; i < Math.Min(s.Sampled.Count, start + ReadChunk); i++)
                        {
                            var pts = Sample(tr, s.Sampled[i], chord);
                            s.SampledPts.Add(pts);
                            s.Stats.SampledPoints += pts.Count;
                            pm.Step();
                        }
                        tr.Commit();
                    }
                }
            }
        }

        // ---------- Phase 2: load in balanced chunks ----------

        static void Load(Database db, ObjectId surfId, Selection s, LoadPlan plan, SurfaceOptions opt, ObjectIdCollection boundary, List<string> log)
        {
            double mid = plan.MidOrdinate * plan.ChordScale;
            bool asVertices = opt.CurveUse == CurveUse.Vertices;
            var pool = new List<Point3d>(s.Vertices);

            if (asVertices)
            {
                AddSampledNative(db, s.Native, mid, pool);
                foreach (var pts in s.SampledPts) pool.AddRange(pts.Cast<Point3d>());
            }
            else
            {
                int ok = 0, failed = 0;
                var rejected = new List<ObjectId>();
                var chunks = Chunks(s.Native, plan.ChunkSize).ToList();
                using (var pm = new Meter(opt.CurveUse == CurveUse.Contours ? "Agregando curvas de nivel..." : "Agregando lineas de rotura...", chunks.Count))
                {
                    foreach (var chunk in chunks)
                    {
                        using (var tr = db.TransactionManager.StartTransaction())
                        {
                            var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                            if (TryAddCurves(surf, new ObjectIdCollection(chunk.ToArray()), mid, plan)) ok += chunk.Count;
                            else rejected.AddRange(chunk);
                            tr.Commit();
                        }
                        pm.Step();
                    }
                }

                // Rejected objects and arcs/splines: one operation per curve while few, else merged as vertices.
                var single = new List<Point3dCollection>(s.SampledPts);
                if (rejected.Count > 0)
                {
                    log.Add($"AVISO: Civil 3D rechazo {rejected.Count:N0} objeto(s) en bloque; se agregan por separado.");
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        foreach (var id in rejected) single.Add(Sample(tr, id, mid));
                        tr.Commit();
                    }
                }

                if (plan.SampledAsVertices || single.Count > LoadPlanner.MaxCurveOperations)
                {
                    foreach (var pts in single) pool.AddRange(pts.Cast<Point3d>());
                    if (single.Count > 0) log.Add($"{single.Count:N0} curva(s) agregadas como vertices.");
                }
                else if (single.Count > 0)
                {
                    using (var pm = new Meter("Agregando curvas...", single.Count))
                    {
                        foreach (var chunk in Chunks(single, OpChunk))
                        {
                            using (var tr = db.TransactionManager.StartTransaction())
                            {
                                var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                                foreach (var pts in chunk)
                                {
                                    if (pts.Count >= 2 && TryAddPoints(surf, pts, mid, plan)) ok++; else failed++;
                                    pm.Step();
                                }
                                tr.Commit();
                            }
                        }
                    }
                }
                log.Add($"{(opt.CurveUse == CurveUse.Contours ? "Curvas de nivel" : "Lineas de rotura")}: {ok:N0} agregadas" +
                        (failed > 0 ? $", {failed:N0} fallidas." : "."));
            }

            AddDrawingObjects(db, surfId, s.DbPoints, plan.ChunkSize, "Puntos AutoCAD", (surf, ids) => surf.DrawingObjectsDefinition.AddFromPoints(ids, Desc), log);
            AddDrawingObjects(db, surfId, s.Faces, plan.ChunkSize, "Caras 3D", (surf, ids) => surf.DrawingObjectsDefinition.AddFrom3DFaces(ids, true, Desc), log);

            if (pool.Count > 0)
            {
                var thinned = Thin(pool, plan.GridCell > 0 ? plan.GridCell : DedupeCell);
                var chunks = Chunks(thinned, VertexChunk).ToList();
                using (var pm = new Meter("Agregando vertices...", chunks.Count))
                {
                    foreach (var chunk in chunks)
                    {
                        using (var tr = db.TransactionManager.StartTransaction())
                        {
                            var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                            surf.AddVertices(new Point3dCollection(chunk.ToArray()));
                            tr.Commit();
                        }
                        pm.Step();
                    }
                }
                log.Add($"Vertices: {thinned.Count:N0}" + (thinned.Count < pool.Count ? $" (de {pool.Count:N0}, depurados)." : "."));
            }

            if (opt.CurveUse == CurveUse.Contours && opt.MinimizeFlat)
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                    try
                    {
                        surf.MinimizeFlatAreas(new SurfaceMinimizeFlatAreaOptions(true, true, true, true));
                        log.Add("Areas planas minimizadas.");
                    }
                    catch (System.Exception ex) { log.Add("AVISO: no se pudieron minimizar areas planas (" + ex.Message + ")."); }
                    tr.Commit();
                }
            }

            if (boundary != null && boundary.Count > 0)
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                    try { surf.BoundariesDefinition.AddBoundaries(boundary, mid, SurfaceBoundaryType.Outer, true); }
                    catch
                    {
                        foreach (ObjectId id in boundary)
                            surf.BoundariesDefinition.AddBoundaries(Sample(tr, id, mid), mid, SurfaceBoundaryType.Outer, true);
                    }
                    tr.Commit();
                }
                log.Add($"Contorno exterior: {boundary.Count} objeto(s).");
            }
        }

        static void AddSampledNative(Database db, List<ObjectId> ids, double chord, List<Point3d> pool)
        {
            using (var pm = new Meter("Leyendo vertices...", ids.Count))
            {
                foreach (var chunk in Chunks(ids, ReadChunk))
                {
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        foreach (var id in chunk) { pool.AddRange(Sample(tr, id, chord).Cast<Point3d>()); pm.Step(); }
                        tr.Commit();
                    }
                }
            }
        }

        static void AddDrawingObjects(Database db, ObjectId surfId, List<ObjectId> ids, int chunkSize, string label,
                                      Action<TinSurface, ObjectIdCollection> add, List<string> log)
        {
            if (ids.Count == 0) return;
            var chunks = Chunks(ids, Math.Max(chunkSize, 5000)).ToList();
            using (var pm = new Meter($"Agregando {label.ToLowerInvariant()}...", chunks.Count))
            {
                foreach (var chunk in chunks)
                {
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        add((TinSurface)tr.GetObject(surfId, OpenMode.ForWrite), new ObjectIdCollection(chunk.ToArray()));
                        tr.Commit();
                    }
                    pm.Step();
                }
            }
            log.Add($"{label}: {ids.Count:N0}.");
        }

        static void FinishSurface(Database db, ObjectId surfId, bool autoRebuild, SurfaceOptions opt, List<string> log, Editor ed)
        {
            try
            {
                ed.WriteMessage("\nReconstruyendo superficie...");
                Rebuild(db, surfId);
                if (opt.CleanEdges && CleanBorder(db, surfId, opt.EdgeLength, log, ed))
                    Rebuild(db, surfId);
            }
            catch (System.Exception ex) { log.Add("ERROR al reconstruir: " + ex.Message); }
            finally
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                    surf.AutoRebuild = autoRebuild;
                    tr.Commit();
                    var gp = surf.GetGeneralProperties();
                    log.Add($"Superficie '{surf.Name}': {gp.NumberOfPoints:N0} puntos.");
                }
            }
        }

        static void Rebuild(Database db, ObjectId surfId)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                ((TinSurface)tr.GetObject(surfId, OpenMode.ForWrite)).Rebuild();
                tr.Commit();
            }
        }

        /// <summary>Deletes TIN lines of long/thin border triangles, working inward from the hull. Returns true if lines were deleted.</summary>
        static bool CleanBorder(Database db, ObjectId surfId, double edgeLength, List<string> log, Editor ed)
        {
            const int MaxTriangles = 4_000_000;
            try
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var surf = (TinSurface)tr.GetObject(surfId, OpenMode.ForWrite);
                    var triangles = surf.GetTriangles(false);
                    if (triangles.Count > MaxTriangles)
                    {
                        log.Add($"AVISO: {triangles.Count:N0} triangulos > {MaxTriangles:N0}; limpieza de borde omitida.");
                        return false;
                    }

                    ed.WriteMessage("\nLimpiando borde de la triangulacion...");
                    var verts = new List<XY>();
                    var index = new Dictionary<(long, long), int>();
                    var tris = new List<int>(triangles.Count * 3);
                    using (var pm = new Meter("Leyendo triangulos...", triangles.Count))
                    {
                        foreach (TinSurfaceTriangle t in triangles)
                        {
                            tris.Add(VertexIndex(t.Vertex1.Location, verts, index));
                            tris.Add(VertexIndex(t.Vertex2.Location, verts, index));
                            tris.Add(VertexIndex(t.Vertex3.Location, verts, index));
                            pm.Step();
                        }
                    }

                    var r = TinCleaner.Peel(verts, tris, edgeLength);
                    if (r.Aborted)
                    {
                        log.Add($"AVISO: limpieza de borde omitida: con {r.Threshold:0.##} m se eliminaria mas del 40% de la superficie. " +
                                "Indique una longitud maxima de borde mayor.");
                        return false;
                    }
                    if (r.EdgesToDelete.Count == 0)
                    {
                        log.Add($"Borde limpio: ningun triangulo exterior supera {r.Threshold:0.##} m.");
                        return false;
                    }

                    var edges = new List<TinSurfaceEdge>(r.EdgesToDelete.Count);
                    int missed = 0;
                    foreach (var (a, b) in r.EdgesToDelete)
                    {
                        var e = FindEdge(surf, a, b);
                        if (e != null) edges.Add(e); else missed++;
                    }
                    if (edges.Count > 0) surf.DeleteLines(edges);
                    tr.Commit();
                    log.Add($"Limpieza de borde: {r.Removed:N0} triangulos exteriores eliminados ({edges.Count:N0} lineas, " +
                            $"umbral {r.Threshold:0.##} m, arista mediana {r.MedianEdge:0.##} m)" +
                            (missed > 0 ? $"; {missed} lineas no localizadas." : "."));
                    return edges.Count > 0;
                }
            }
            catch (CanceledException)
            {
                log.Add("Limpieza de borde cancelada.");
                return false;
            }
        }

        static int VertexIndex(Point3d p, List<XY> verts, Dictionary<(long, long), int> index)
        {
            var key = ((long)Math.Round(p.X * 1000), (long)Math.Round(p.Y * 1000));
            if (!index.TryGetValue(key, out int i))
            {
                i = verts.Count;
                verts.Add(new XY(p.X, p.Y));
                index[key] = i;
            }
            return i;
        }

        /// <summary>TIN edge whose endpoints match a and b, looked up at the edge midpoint.</summary>
        static TinSurfaceEdge FindEdge(TinSurface surf, XY a, XY b)
        {
            try
            {
                var e = surf.FindEdgeAtXY((a.X + b.X) / 2, (a.Y + b.Y) / 2);
                if (e == null) return null;
                var p1 = e.Vertex1.Location;
                var p2 = e.Vertex2.Location;
                bool same = (Near(p1, a) && Near(p2, b)) || (Near(p1, b) && Near(p2, a));
                return same ? e : null;
            }
            catch { return null; }
        }

        static bool Near(Point3d p, XY q) => Math.Abs(p.X - q.X) < 1e-3 && Math.Abs(p.Y - q.Y) < 1e-3;

        static TinSurface OpenOrCreate(Transaction tr, Database db, CivilDocument civ, SurfaceOptions opt)
        {
            if (!opt.NewSurface)
            {
                foreach (ObjectId id in civ.GetSurfaceIds())
                    if (tr.GetObject(id, OpenMode.ForRead) is TinSurface s && string.Equals(s.Name, opt.Name, StringComparison.OrdinalIgnoreCase))
                        return (TinSurface)tr.GetObject(id, OpenMode.ForWrite);
                throw new InvalidOperationException($"Superficie '{opt.Name}' no encontrada.");
            }

            var surf = (TinSurface)tr.GetObject(TinSurface.Create(db, opt.Name), OpenMode.ForWrite);
            if (!string.IsNullOrEmpty(opt.StyleName))
            {
                try { surf.StyleId = civ.Styles.SurfaceStyles[opt.StyleName]; } catch { /* keep default style */ }
            }
            return surf;
        }

        static bool TryAddCurves(TinSurface surf, ObjectIdCollection ids, double mid, LoadPlan plan)
        {
            try
            {
                var op = plan.CurveUse == CurveUse.Contours
                    ? (object)surf.ContoursDefinition.AddContours(ids, mid, plan.MaxDistance, plan.WeedDistance, plan.WeedAngle)
                    : surf.BreaklinesDefinition.AddStandardBreaklines(ids, mid, plan.MaxDistance, plan.WeedDistance, plan.WeedAngle);
                return op != null;
            }
            catch { return false; }
        }

        static bool TryAddPoints(TinSurface surf, Point3dCollection pts, double mid, LoadPlan plan)
        {
            try
            {
                if (plan.CurveUse == CurveUse.Contours)
                    surf.ContoursDefinition.AddContours(pts, mid, plan.MaxDistance, plan.WeedDistance, plan.WeedAngle);
                else
                    surf.BreaklinesDefinition.AddStandardBreaklines(pts, mid, plan.MaxDistance, plan.WeedDistance, plan.WeedAngle);
                return true;
            }
            catch { return false; }
        }

        // ---------- Helpers ----------

        /// <summary>Converts any curve to a 3D point list; arcs and splines are split by chord height.</summary>
        static Point3dCollection Sample(Transaction tr, ObjectId id, double chord)
        {
            var pts = new Point3dCollection();
            var obj = tr.GetObject(id, OpenMode.ForRead);
            if (obj is FeatureLine fl)
            {
                foreach (Point3d p in fl.GetPoints(FeatureLinePointType.AllPoints)) AddPoint(pts, p);
                return pts;
            }
            if (!(obj is Curve c)) return pts;

            try
            {
                var ge = c.GetGeCurve();
                var iv = ge.GetInterval();
                foreach (var p in ge.GetSamplePoints(iv.LowerBound, iv.UpperBound, chord))
                    AddPoint(pts, p.Point);
            }
            catch
            {
                double len = c.GetDistanceAtParameter(c.EndParam);
                int n = Math.Max(2, (int)Math.Ceiling(len / Math.Max(chord * 10, 0.5)));
                for (int i = 0; i <= n; i++)
                    AddPoint(pts, c.GetPointAtDist(Math.Min(len, len * i / n)));
            }
            if (c.Closed && pts.Count > 2 && !pts[0].IsEqualTo(pts[pts.Count - 1]))
                pts.Add(pts[0]);
            return pts;
        }

        /// <summary>Keeps one point per grid cell (first one found).</summary>
        static List<Point3d> Thin(List<Point3d> pts, double cell)
        {
            var seen = new HashSet<(long, long)>();
            var result = new List<Point3d>(pts.Count);
            foreach (var p in pts)
                if (seen.Add(((long)Math.Floor(p.X / cell), (long)Math.Floor(p.Y / cell))))
                    result.Add(p);
            return result;
        }

        static IEnumerable<List<T>> Chunks<T>(IList<T> items, int size)
        {
            for (int i = 0; i < items.Count; i += size)
            {
                var chunk = new List<T>(Math.Min(size, items.Count - i));
                for (int j = i; j < Math.Min(items.Count, i + size); j++) chunk.Add(items[j]);
                yield return chunk;
            }
        }

        static void AddPoint(Point3dCollection pts, Point3d p)
        {
            if (pts.Count == 0 || !pts[pts.Count - 1].IsEqualTo(p)) pts.Add(p);
        }

        static double PathLength(Point3dCollection pts)
        {
            double len = 0;
            for (int i = 1; i < pts.Count; i++) len += pts[i - 1].DistanceTo(pts[i]);
            return len;
        }

        static bool IsFlat(Point3dCollection pts) => pts.Cast<Point3d>().All(p => Math.Abs(p.Z) < 1e-9);

        static bool IsFlat(Curve c)
        {
            try
            {
                var ext = c.GeometricExtents;
                return Math.Abs(ext.MinPoint.Z) < 1e-9 && Math.Abs(ext.MaxPoint.Z) < 1e-9;
            }
            catch { return false; }
        }

        /// <summary>AutoCAD progress bar that also checks ESC.</summary>
        sealed class Meter : IDisposable
        {
            readonly ProgressMeter _pm = new ProgressMeter();
            int _n;

            public Meter(string label, int total)
            {
                _pm.SetLimit(Math.Max(1, total));
                _pm.Start(label);
            }

            public void Step()
            {
                _pm.MeterProgress();
                if ((++_n & 63) == 0)
                {
                    System.Windows.Forms.Application.DoEvents();
                    if (HostApplicationServices.Current.UserBreak()) throw new CanceledException();
                }
            }

            public void Dispose()
            {
                _pm.Stop();
                _pm.Dispose();
            }
        }
    }
}
