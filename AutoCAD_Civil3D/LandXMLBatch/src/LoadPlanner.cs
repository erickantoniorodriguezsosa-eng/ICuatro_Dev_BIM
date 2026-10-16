using System;
using System.Collections.Generic;

namespace ICuatro.LandXmlBatch
{
    public enum CurveUse { Breaklines, Contours, Vertices }

    public class SurfaceOptions
    {
        public bool NewSurface = true;
        public string Name = "Superficie desde lineas";
        public string StyleName;
        public CurveUse CurveUse = CurveUse.Breaklines;
        public double MidOrdinate = 0.1;
        public double MaxDistance = 10.0;
        public bool OuterBoundary;
        public bool Auto = true;                 // derive tolerances from selection size
        public long PointBudget = 1_500_000;     // hard ceiling for surface points
        public bool CleanEdges = true;           // peel long/thin triangles from the border after building
        public double EdgeLength;                // max exposed border edge; 0 = automatic
        public bool MinimizeFlat = true;         // contours: minimize flat triangles
    }

    /// <summary>Measured size of a selection, before anything is added to the surface.</summary>
    public class LoadStats
    {
        public int NativeCount;          // lines/polylines/feature lines passed to Civil 3D as objects
        public long NativeVertices;
        public double NativeLength;
        public int SampledCount;         // arcs/circles/ellipses/splines converted to points here
        public long SampledPoints;       // points produced with the initial chord tolerance
        public long OtherPoints;         // AutoCAD points, COGO points, 3D face corners
        public double Width, Height;     // plan extents of the selection
        public double Diagonal => Math.Sqrt(Width * Width + Height * Height);
    }

    public class LoadPlan
    {
        public CurveUse CurveUse;
        public double MidOrdinate, MaxDistance;
        public double WeedDistance, WeedAngle;
        public double ChordScale = 1.0;          // factor applied to MidOrdinate when re-sampling curves
        public bool SampledAsVertices;           // one vertex operation instead of one breakline per curve
        public double GridCell;                  // > 0: thin vertices to one per grid cell
        public int ChunkSize;
        public long EstimatedPoints;
        public bool LevelOfDetail;
        public List<string> Notes = new List<string>();
    }

    /// <summary>Chooses tolerances and batching so the surface stays within a point budget.</summary>
    public static class LoadPlanner
    {
        public const int MaxCurveOperations = 300;
        public const long LevelOfDetailFrom = 500_000;
        const double WeedAngleDeg = 4.0;

        public static LoadPlan Plan(LoadStats s, SurfaceOptions o)
        {
            var p = new LoadPlan { CurveUse = o.CurveUse };
            double diag = Math.Max(s.Diagonal, 1.0);
            long budget = Math.Max(10_000, o.PointBudget);
            bool vertices = o.CurveUse == CurveUse.Vertices;

            p.MidOrdinate = o.Auto ? Clamp(diag / 20000, 0.01, 0.5) : o.MidOrdinate;
            long fixedPts = s.NativeVertices + s.SampledPoints + s.OtherPoints;
            // Supplement breaklines at the average point spacing so triangles stay close to equilateral.
            double avgSpacing = Math.Sqrt(Math.Max(s.Width * s.Height, 1.0) / Math.Max(1, fixedPts));
            p.MaxDistance = o.Auto ? Clamp(avgSpacing, 0.5, 50) : o.MaxDistance;

            // Many arcs/splines as separate breaklines means many operations: merge them as vertices.
            if (!vertices && s.SampledCount > MaxCurveOperations)
            {
                p.SampledAsVertices = true;
                p.Notes.Add($"{s.SampledCount} arcos/splines > {MaxCurveOperations}: se agregan como vertices en una sola operacion.");
            }

            double supplement = vertices ? 0 : s.NativeLength / p.MaxDistance;
            long est = fixedPts + (long)supplement;

            if (est > budget && supplement > 0)
            {
                long room = budget - fixedPts;
                double needed = room > 0 ? s.NativeLength / room : diag;
                if (needed > p.MaxDistance)
                {
                    p.MaxDistance = Math.Max(needed, p.MaxDistance);
                    p.Notes.Add($"Distancia de suplemento subida a {p.MaxDistance:0.##} m.");
                }
                supplement = s.NativeLength / p.MaxDistance;
                est = fixedPts + (long)supplement;
            }

            if (est > budget && s.SampledPoints > 0)
            {
                // Chord sampling count grows ~ 1/sqrt(tolerance).
                double excess = (double)est / budget;
                p.ChordScale = Math.Min(100, excess * excess);
                long reduced = (long)(s.SampledPoints / Math.Sqrt(p.ChordScale));
                est -= s.SampledPoints - reduced;
                p.Notes.Add($"Tolerancia de curvas x{p.ChordScale:0.#} ({p.MidOrdinate * p.ChordScale:0.###} m).");
            }

            if (est > budget && !vertices && s.NativeVertices > 0)
            {
                // Weed native vertices so they fit in the room left by everything else.
                double spacing = s.NativeLength / Math.Max(1, s.NativeVertices);
                long others = est - s.NativeVertices;
                long room = budget - others;
                double ratio = room > 0 ? Math.Min(100, (double)s.NativeVertices / room) : 100;
                p.WeedDistance = spacing * ratio;
                p.WeedAngle = WeedAngleDeg;
                est = others + (long)Math.Floor(s.NativeVertices / ratio);
                p.Notes.Add($"Depuracion de vertices: {p.WeedDistance:0.###} m / {p.WeedAngle:0.#} grados.");
            }

            if (est > budget && (vertices || p.SampledAsVertices))
            {
                double area = Math.Max(s.Width * s.Height, 1.0);
                p.GridCell = Math.Sqrt(area / budget);
                est = Math.Min(est, budget);
                p.Notes.Add($"Vertices reducidos a 1 por celda de {p.GridCell:0.###} m.");
            }

            p.EstimatedPoints = est;
            p.ChunkSize = Math.Max(500, (int)Math.Ceiling(s.NativeCount / 8.0));
            p.LevelOfDetail = est >= LevelOfDetailFrom;
            if (est > budget)
                p.Notes.Add($"AVISO: estimado {est:N0} puntos supera el limite {budget:N0}; revise los datos.");
            return p;
        }

        static double Clamp(double v, double min, double max) => Math.Max(min, Math.Min(max, v));
    }
}
