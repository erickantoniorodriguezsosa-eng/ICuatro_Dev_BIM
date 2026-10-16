using System;
using System.IO;
using ICuatro.LandXmlBatch;

// Minimal checks for the Civil 3D-independent LandXML parser.
static class Program
{
    static int failures;

    static void Check(bool ok, string what)
    {
        Console.WriteLine((ok ? "OK   " : "FAIL ") + what);
        if (!ok) failures++;
    }

    static bool Near(double a, double b) => Math.Abs(a - b) < 1e-6;

    static void CleanerTests()
    {
        // 10x10 grid of unit squares (200 triangles) plus a fan of 10 long triangles to a far point below.
        var v = new System.Collections.Generic.List<XY>();
        var t = new System.Collections.Generic.List<int>();
        for (int y = 0; y <= 10; y++) for (int x = 0; x <= 10; x++) v.Add(new XY(x, y));
        int Id(int x, int y) => y * 11 + x;
        for (int y = 0; y < 10; y++) for (int x = 0; x < 10; x++)
        {
            t.AddRange(new[] { Id(x, y), Id(x + 1, y), Id(x + 1, y + 1) });
            t.AddRange(new[] { Id(x, y), Id(x + 1, y + 1), Id(x, y + 1) });
        }
        v.Add(new XY(5, -20));
        int far = v.Count - 1;
        for (int x = 0; x < 10; x++) t.AddRange(new[] { Id(x + 1, 0), Id(x, 0), far });

        var r = TinCleaner.Peel(v, t);
        Check(!r.Aborted && r.Removed == 10, $"abanico exterior eliminado ({r.Removed} de {r.Total}, umbral {r.Threshold:0.##})");
        Check(r.EdgesToDelete.Count == 11, $"11 lineas a borrar ({r.EdgesToDelete.Count})");
        Check(r.EdgesToDelete.TrueForAll(e => Math.Abs(e.A.X - 5) < 1e-9 && Math.Abs(e.A.Y + 20) < 1e-9 || Math.Abs(e.B.X - 5) < 1e-9 && Math.Abs(e.B.Y + 20) < 1e-9),
              "solo se borran lineas del punto lejano");

        var clean = TinCleaner.Peel(v, t.GetRange(0, 600));
        Check(clean.Removed == 0, "malla sana no se toca");

        var tooSmall = TinCleaner.Peel(v, t, threshold: 0.5);
        Check(tooSmall.Aborted && tooSmall.EdgesToDelete.Count == 0, "umbral absurdo aborta sin borrar");
    }

    static void PlannerTests()
    {
        var opt = new SurfaceOptions();

        var small = LoadPlanner.Plan(new LoadStats { NativeCount = 50, NativeVertices = 2000, NativeLength = 5000, Width = 500, Height = 400 }, opt);
        Check(small.Notes.Count == 0 && small.WeedDistance == 0 && !small.LevelOfDetail, "plan pequeno sin ajustes");
        Check(small.MaxDistance >= 1 && small.MaxDistance <= 50 && small.MidOrdinate >= 0.01, "parametros automaticos en rango");

        var longLines = LoadPlanner.Plan(new LoadStats { NativeCount = 20000, NativeVertices = 400_000, NativeLength = 1e8, Width = 20000, Height = 15000 }, opt);
        Check(longLines.EstimatedPoints <= opt.PointBudget && longLines.MaxDistance > 50, $"suplemento subido ({longLines.MaxDistance:0.#} m, ~{longLines.EstimatedPoints:N0} pts)");
        Check(longLines.ChunkSize == 2500, "bloques de NativeCount/8");

        var dense = LoadPlanner.Plan(new LoadStats { NativeCount = 5000, NativeVertices = 6_000_000, NativeLength = 3e6, Width = 5000, Height = 5000 }, opt);
        Check(dense.WeedDistance > 0 && dense.EstimatedPoints <= opt.PointBudget, $"depuracion activada ({dense.WeedDistance:0.###} m)");

        var arcs = LoadPlanner.Plan(new LoadStats { SampledCount = 1000, SampledPoints = 50_000, Width = 100, Height = 100 }, opt);
        Check(arcs.SampledAsVertices, "muchos arcos agrupados como vertices");

        var pts = LoadPlanner.Plan(new LoadStats { OtherPoints = 5_000_000, Width = 1000, Height = 1000 },
                                   new SurfaceOptions { CurveUse = CurveUse.Vertices });
        Check(pts.GridCell > 0 && pts.EstimatedPoints <= opt.PointBudget, $"vertices reducidos por grilla ({pts.GridCell:0.###} m)");
    }

    static int Main(string[] args)
    {
        string sample = args.Length > 0 ? args[0] : Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "samples", "muestra.xml");
        var c = LandXmlReader.Read(sample);

        Check(c.SurfaceNames.Count == 1 && c.SurfaceNames[0] == "TN", "superficie TN");
        Check(c.Alignments.Count == 1, "un alineamiento");
        var al = c.Alignments[0];
        Check(al.Name == "Eje 1" && Near(al.StartStation, 1000), "nombre y staStart");
        Check(al.Elements.Count == 3, "tres elementos");

        var line = al.Elements[0];
        Check(Near(line.End.X, 100) && Near(line.End.Y, 0), "N E -> X=E, Y=N");

        var c1 = al.Elements[1];
        var m1 = LandXmlReader.ArcMidPoint(c1);
        double k = 50 - 50 / Math.Sqrt(2);
        Check(Near(m1.X, 100 + 50 / Math.Sqrt(2)) && Near(m1.Y, k), $"punto medio arco ccw ({m1.X:0.###},{m1.Y:0.###})");
        var t1 = LandXmlReader.ArcStartTangent(c1);
        Check(Near(t1.X, 1) && Near(t1.Y, 0), "tangente inicial arco ccw");

        var c2 = al.Elements[2];
        Check(c2.Center.HasValue && Near(c2.Center.Value.X, 200) && Near(c2.Center.Value.Y, 50), $"centro desde cuerda cw ({c2.Center?.X:0.###},{c2.Center?.Y:0.###})");
        var t2 = LandXmlReader.ArcStartTangent(c2);
        Check(Near(t2.X, 0) && Near(t2.Y, 1), "tangente inicial arco cw continua");

        Check(al.Profiles.Count == 1 && al.Profiles[0].Pvis.Count == 5, "perfil con 5 PVI");
        var p = al.Profiles[0].Pvis;
        Check(p[1].Kind == PviKind.ParaCurve && Near(p[1].Length, 40), "ParaCurve");
        Check(p[2].Kind == PviKind.UnsymParaCurve && Near(p[2].LengthIn, 10) && Near(p[2].LengthOut, 20), "UnsymParaCurve");
        Check(p[3].Kind == PviKind.CircCurve && Near(p[3].Radius, 500), "CircCurve");

        PlannerTests();
        CleanerTests();

        Console.WriteLine(failures == 0 ? "TODO OK" : $"{failures} fallo(s)");
        return failures == 0 ? 0 : 1;
    }
}
