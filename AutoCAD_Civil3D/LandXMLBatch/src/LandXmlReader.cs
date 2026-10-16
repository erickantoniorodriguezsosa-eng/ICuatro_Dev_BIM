using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Xml.Linq;

namespace ICuatro.LandXmlBatch
{
    /// <summary>Plan coordinate. LandXML stores "northing easting [elevation]"; X = easting, Y = northing.</summary>
    public struct XY
    {
        public double X, Y;
        public XY(double x, double y) { X = x; Y = y; }
    }

    public enum GeomKind { Line, Curve, Spiral }

    public class GeomElement
    {
        public GeomKind Kind;
        public XY Start, End;
        public XY? Center, PI;
        public double Radius;          // Curve radius
        public bool Clockwise;
        public double Length;
        public string SpiralType;      // LandXML spiType
    }

    public enum PviKind { Pvi, ParaCurve, UnsymParaCurve, CircCurve }

    public class PviData
    {
        public PviKind Kind;
        public double Station, Elevation;
        public double Length, LengthIn, LengthOut, Radius;
    }

    public class ProfileData
    {
        public string Name;
        public List<PviData> Pvis = new List<PviData>();
    }

    public class AlignmentData
    {
        public string Name;
        public string Description;
        public double StartStation;
        public List<GeomElement> Elements = new List<GeomElement>();
        public List<ProfileData> Profiles = new List<ProfileData>();
        public List<string> Warnings = new List<string>();
    }

    public class LandXmlContent
    {
        public string FilePath;
        public List<string> SurfaceNames = new List<string>();
        public List<AlignmentData> Alignments = new List<AlignmentData>();
    }

    /// <summary>Namespace-agnostic LandXML 1.0/1.1/1.2 reader for surfaces and alignments.</summary>
    public static class LandXmlReader
    {
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        public static LandXmlContent Read(string path)
        {
            var doc = XDocument.Load(path);
            var result = new LandXmlContent { FilePath = path };
            var root = doc.Root;
            if (root == null || root.Name.LocalName != "LandXML")
                throw new FormatException("El archivo no es LandXML (raiz distinta de <LandXML>).");

            foreach (var s in Children(root, "Surfaces").SelectMany(x => Children(x, "Surface")))
            {
                string name = Attr(s, "name");
                if (!string.IsNullOrWhiteSpace(name)) result.SurfaceNames.Add(name);
            }

            foreach (var a in Children(root, "Alignments").SelectMany(x => Children(x, "Alignment")))
                result.Alignments.Add(ReadAlignment(a));

            return result;
        }

        static AlignmentData ReadAlignment(XElement a)
        {
            var al = new AlignmentData
            {
                Name = Attr(a, "name") ?? "Alineamiento",
                Description = Attr(a, "desc"),
                StartStation = Num(Attr(a, "staStart"), 0.0),
            };

            var cg = Children(a, "CoordGeom").FirstOrDefault();
            if (cg != null)
            {
                foreach (var e in cg.Elements())
                {
                    switch (e.Name.LocalName)
                    {
                        case "Line":
                            al.Elements.Add(new GeomElement
                            {
                                Kind = GeomKind.Line,
                                Start = Pt(Child(e, "Start")),
                                End = Pt(Child(e, "End")),
                            });
                            break;
                        case "Curve":
                            al.Elements.Add(ReadCurve(e));
                            break;
                        case "Spiral":
                            al.Elements.Add(new GeomElement
                            {
                                Kind = GeomKind.Spiral,
                                Start = Pt(Child(e, "Start")),
                                End = Pt(Child(e, "End")),
                                PI = OptPt(Child(e, "PI")),
                                Length = Num(Attr(e, "length"), 0.0),
                                Clockwise = IsCw(Attr(e, "rot")),
                                SpiralType = Attr(e, "spiType") ?? "clothoid",
                            });
                            break;
                        case "IrregularLine":
                        case "Chain":
                            al.Warnings.Add($"Elemento <{e.Name.LocalName}> no soportado; omitido.");
                            break;
                    }
                }
            }

            if (Children(a, "StaEquation").Any())
                al.Warnings.Add("Ecuaciones de estacion (StaEquation) no se importan.");

            foreach (var prof in Children(a, "Profile"))
            {
                foreach (var pa in Children(prof, "ProfAlign"))
                {
                    var pd = new ProfileData { Name = Attr(pa, "name") ?? Attr(prof, "name") ?? "Perfil" };
                    foreach (var p in pa.Elements())
                    {
                        var v = Numbers(p.Value);
                        if (v.Length < 2) continue;
                        var pvi = new PviData { Station = v[0], Elevation = v[1] };
                        switch (p.Name.LocalName)
                        {
                            case "PVI": pvi.Kind = PviKind.Pvi; break;
                            case "ParaCurve": pvi.Kind = PviKind.ParaCurve; pvi.Length = Num(Attr(p, "length"), 0.0); break;
                            case "UnsymParaCurve":
                                pvi.Kind = PviKind.UnsymParaCurve;
                                pvi.LengthIn = Num(Attr(p, "lengthIn"), 0.0);
                                pvi.LengthOut = Num(Attr(p, "lengthOut"), 0.0);
                                break;
                            case "CircCurve": pvi.Kind = PviKind.CircCurve; pvi.Radius = Num(Attr(p, "radius"), 0.0); break;
                            default: continue;
                        }
                        pd.Pvis.Add(pvi);
                    }
                    if (pd.Pvis.Count >= 2) al.Profiles.Add(pd);
                    else al.Warnings.Add($"Perfil '{pd.Name}' con menos de 2 PVI; omitido.");
                }
            }
            return al;
        }

        static GeomElement ReadCurve(XElement e)
        {
            var g = new GeomElement
            {
                Kind = GeomKind.Curve,
                Start = Pt(Child(e, "Start")),
                End = Pt(Child(e, "End")),
                Center = OptPt(Child(e, "Center")),
                PI = OptPt(Child(e, "PI")),
                Radius = Num(Attr(e, "radius"), 0.0),
                Length = Num(Attr(e, "length"), 0.0),
                Clockwise = IsCw(Attr(e, "rot")),
            };
            if (g.Center.HasValue && g.Radius <= 0)
                g.Radius = Dist(g.Start, g.Center.Value);
            if (!g.Center.HasValue)
                g.Center = CenterFromChord(g);
            return g;
        }

        /// <summary>Point at the middle of the arc, used to define a fixed 3-point curve.</summary>
        public static XY ArcMidPoint(GeomElement c)
        {
            var ctr = c.Center.Value;
            double r = Dist(c.Start, ctr);
            double a0 = Math.Atan2(c.Start.Y - ctr.Y, c.Start.X - ctr.X);
            double a1 = Math.Atan2(c.End.Y - ctr.Y, c.End.X - ctr.X);
            double sweep = c.Clockwise ? -Norm(a0 - a1) : Norm(a1 - a0);
            double am = a0 + sweep / 2.0;
            return new XY(ctr.X + r * Math.Cos(am), ctr.Y + r * Math.Sin(am));
        }

        /// <summary>Unit tangent direction at arc start.</summary>
        public static XY ArcStartTangent(GeomElement c)
        {
            var ctr = c.Center.Value;
            double dx = c.Start.X - ctr.X, dy = c.Start.Y - ctr.Y;
            double r = Math.Sqrt(dx * dx + dy * dy);
            // CCW tangent = radius vector rotated +90 deg; CW = -90 deg.
            return c.Clockwise ? new XY(dy / r, -dx / r) : new XY(-dy / r, dx / r);
        }

        static XY? CenterFromChord(GeomElement g)
        {
            double r = g.Radius;
            double cx = g.End.X - g.Start.X, cy = g.End.Y - g.Start.Y;
            double chord = Math.Sqrt(cx * cx + cy * cy);
            if (r <= 0 || chord <= 0 || chord > 2 * r + 1e-9) return null;
            double h = Math.Sqrt(Math.Max(0, r * r - chord * chord / 4));
            bool major = g.Length > Math.PI * r;
            // Left normal of the chord holds the center for a minor CCW arc.
            double nx = -cy / chord, ny = cx / chord;
            double side = (g.Clockwise ? -1 : 1) * (major ? -1 : 1);
            return new XY((g.Start.X + g.End.X) / 2 + side * h * nx, (g.Start.Y + g.End.Y) / 2 + side * h * ny);
        }

        static double Norm(double a)
        {
            double twoPi = 2 * Math.PI;
            a %= twoPi;
            if (a <= 1e-12) a += twoPi;
            return a;
        }

        static double Dist(XY a, XY b) => Math.Sqrt((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y));

        static bool IsCw(string rot) => string.Equals(rot, "cw", StringComparison.OrdinalIgnoreCase);

        static IEnumerable<XElement> Children(XElement e, string localName) =>
            e.Elements().Where(x => x.Name.LocalName == localName);

        static XElement Child(XElement e, string localName) => Children(e, localName).FirstOrDefault();

        static string Attr(XElement e, string name) => e.Attribute(name)?.Value;

        static XY Pt(XElement e)
        {
            var p = OptPt(e);
            if (!p.HasValue) throw new FormatException($"Coordenada invalida o ausente en <{e?.Parent?.Name.LocalName}>.");
            return p.Value;
        }

        static XY? OptPt(XElement e)
        {
            if (e == null) return null;
            var v = Numbers(e.Value);
            if (v.Length < 2) return null;
            return new XY(v[1], v[0]); // northing easting -> X=E, Y=N
        }

        static double[] Numbers(string text) =>
            (text ?? "").Split((char[])null, StringSplitOptions.RemoveEmptyEntries)
                .Select(t => double.TryParse(t, NumberStyles.Float, Inv, out var d) ? d : double.NaN)
                .Where(d => !double.IsNaN(d)).ToArray();

        static double Num(string s, double fallback) =>
            double.TryParse(s, NumberStyles.Float, Inv, out var d) ? d : fallback;
    }
}
