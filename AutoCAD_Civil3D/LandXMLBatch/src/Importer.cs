using System;
using System.Collections.Generic;
using System.IO;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.Civil;
using Autodesk.Civil.ApplicationServices;
using CivilAlignment = Autodesk.Civil.DatabaseServices.Alignment;
using CivilProfile = Autodesk.Civil.DatabaseServices.Profile;
using CivilSurface = Autodesk.Civil.DatabaseServices.Surface;
using Autodesk.Civil.DatabaseServices;

namespace ICuatro.LandXmlBatch
{
    public enum NameConflict { Rename, Skip }

    public class ImportOptions
    {
        public bool Surfaces = true;
        public bool Alignments = true;
        public bool Profiles = true;
        public bool PrefixFileName = false;
        public NameConflict Conflict = NameConflict.Rename;
    }

    public class ImportReport
    {
        public int Files, FilesFailed, Surfaces, Alignments, Profiles, Skipped;
        public List<string> Log = new List<string>();
        public void Info(string m) => Log.Add(m);
    }

    /// <summary>Creates Civil 3D objects from LandXML files in the active drawing.</summary>
    public class Importer
    {
        const double Tol = 1e-4;
        readonly Database _db;
        readonly CivilDocument _civ;
        readonly ImportOptions _opt;
        readonly ImportReport _rep;
        readonly HashSet<string> _surfaceNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        readonly HashSet<string> _alignNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        public Importer(Database db, ImportOptions opt, ImportReport rep)
        {
            _db = db;
            _civ = CivilDocument.GetCivilDocument(db);
            _opt = opt;
            _rep = rep;
            LoadExistingNames();
        }

        void LoadExistingNames()
        {
            using (var tr = _db.TransactionManager.StartTransaction())
            {
                foreach (ObjectId id in _civ.GetSurfaceIds())
                    _surfaceNames.Add(((CivilSurface)tr.GetObject(id, OpenMode.ForRead)).Name);
                foreach (ObjectId id in _civ.GetAlignmentIds())
                    _alignNames.Add(((CivilAlignment)tr.GetObject(id, OpenMode.ForRead)).Name);
                tr.Commit();
            }
        }

        public void ImportFile(string path)
        {
            _rep.Files++;
            string file = Path.GetFileName(path);
            _rep.Info($"--- {file}");
            LandXmlContent content;
            try { content = LandXmlReader.Read(path); }
            catch (Exception ex)
            {
                _rep.FilesFailed++;
                _rep.Info($"  ERROR lectura: {ex.Message}");
                return;
            }

            string prefix = _opt.PrefixFileName ? Path.GetFileNameWithoutExtension(path) + " - " : "";

            if (_opt.Surfaces)
                foreach (var sName in content.SurfaceNames)
                    ImportSurface(path, sName, prefix + sName);

            if (_opt.Alignments)
                foreach (var al in content.Alignments)
                    ImportAlignment(al, prefix + al.Name);

            if (content.SurfaceNames.Count == 0 && content.Alignments.Count == 0)
                _rep.Info("  Sin superficies ni alineamientos.");
        }

        void ImportSurface(string path, string xmlName, string wanted)
        {
            string name = ResolveName(wanted, _surfaceNames);
            if (name == null) { _rep.Skipped++; _rep.Info($"  Superficie '{wanted}' ya existe; omitida."); return; }
            try
            {
                using (var tr = _db.TransactionManager.StartTransaction())
                {
                    TinSurface.CreateFromLandXML(_db, name, path, xmlName);
                    tr.Commit();
                }
                _surfaceNames.Add(name);
                _rep.Surfaces++;
                _rep.Info($"  Superficie: {name}");
            }
            catch (Exception ex) { _rep.Info($"  ERROR superficie '{xmlName}': {ex.Message}"); }
        }

        void ImportAlignment(AlignmentData data, string wanted)
        {
            string name = ResolveName(wanted, _alignNames);
            if (name == null) { _rep.Skipped++; _rep.Info($"  Alineamiento '{wanted}' ya existe; omitido."); return; }
            if (data.Elements.Count == 0) { _rep.Info($"  Alineamiento '{wanted}' sin geometria; omitido."); return; }

            foreach (var w in data.Warnings) _rep.Info($"  AVISO '{wanted}': {w}");
            try
            {
                using (var tr = _db.TransactionManager.StartTransaction())
                {
                    ObjectId styleId = _civ.Styles.AlignmentStyles[0];
                    ObjectId labelsId = _civ.Styles.LabelSetStyles.AlignmentLabelSetStyles[0];
                    ObjectId alId = CivilAlignment.Create(_civ, name, ObjectId.Null, _db.Clayer, styleId, labelsId);
                    var al = (CivilAlignment)tr.GetObject(alId, OpenMode.ForWrite);
                    if (!string.IsNullOrEmpty(data.Description)) al.Description = data.Description;

                    int built = BuildGeometry(al, data, name);
                    if (built == 0) throw new InvalidOperationException("ningun elemento geometrico pudo crearse");
                    al.ReferencePointStation = data.StartStation;

                    int profiles = 0;
                    if (_opt.Profiles)
                        foreach (var p in data.Profiles)
                            if (BuildProfile(tr, al, p, name)) profiles++;

                    tr.Commit();
                    _alignNames.Add(name);
                    _rep.Alignments++;
                    _rep.Profiles += profiles;
                    _rep.Info($"  Alineamiento: {name} ({built}/{data.Elements.Count} elementos, {profiles} perfiles)");
                }
            }
            catch (Exception ex) { _rep.Info($"  ERROR alineamiento '{wanted}': {ex.Message}"); }
        }

        int BuildGeometry(CivilAlignment al, AlignmentData data, string name)
        {
            AlignmentEntity prev = null;
            XY prevEnd = default;
            int built = 0;
            foreach (var g in data.Elements)
            {
                bool connected = prev != null && Dist(prevEnd, g.Start) < Tol;
                AlignmentEntity created = null;
                try
                {
                    switch (g.Kind)
                    {
                        case GeomKind.Line:
                            created = connected
                                ? al.Entities.AddFixedLine(prev.EntityId, P3(g.Start), P3(g.End))
                                : al.Entities.AddFixedLine(P3(g.Start), P3(g.End));
                            break;
                        case GeomKind.Curve:
                            if (!g.Center.HasValue) { _rep.Info($"  AVISO '{name}': curva sin centro/radio valido; omitida."); break; }
                            if (connected)
                                created = al.Entities.AddFixedCurve(prev.EntityId, P3(g.Start), P3(LandXmlReader.ArcMidPoint(g)), P3(g.End));
                            else
                            {
                                var t = LandXmlReader.ArcStartTangent(g);
                                created = al.Entities.AddFixedCurve(P3(g.Start), new Vector3d(t.X, t.Y, 0), P3(g.End));
                            }
                            break;
                        case GeomKind.Spiral:
                            if (!connected || !g.PI.HasValue)
                            {
                                _rep.Info($"  AVISO '{name}': espiral sin elemento previo conectado o sin PI; omitida.");
                                break;
                            }
                            created = al.Entities.AddFixedSpiral(prev.EntityId, P3(g.Start), P3(g.PI.Value), P3(g.End), MapSpiral(g.SpiralType));
                            break;
                    }
                }
                catch (Exception ex) { _rep.Info($"  AVISO '{name}': {g.Kind} no creado ({ex.Message})."); }

                if (created != null)
                {
                    prev = created;
                    prevEnd = g.End;
                    built++;
                }
            }
            return built;
        }

        bool BuildProfile(Transaction tr, CivilAlignment al, ProfileData p, string alignName)
        {
            try
            {
                ObjectId styleId = _civ.Styles.ProfileStyles[0];
                ObjectId labelsId = _civ.Styles.LabelSetStyles.ProfileLabelSetStyles[0];
                string pName = UniqueProfileName(tr, al, p.Name);
                ObjectId profId = CivilProfile.CreateByLayout(pName, al.ObjectId, _db.Clayer, styleId, labelsId);
                var prof = (CivilProfile)tr.GetObject(profId, OpenMode.ForWrite);

                // Tangents through all PVIs, then vertical curves on interior PVIs.
                for (int i = 0; i + 1 < p.Pvis.Count; i++)
                    prof.Entities.AddFixedTangent(
                        new Point2d(p.Pvis[i].Station, p.Pvis[i].Elevation),
                        new Point2d(p.Pvis[i + 1].Station, p.Pvis[i + 1].Elevation));

                for (int i = 1; i + 1 < p.Pvis.Count; i++)
                {
                    var v = p.Pvis[i];
                    if (v.Kind == PviKind.Pvi) continue;
                    try
                    {
                        var pvi = prof.PVIs.GetPVIAt(v.Station, v.Elevation);
                        if (v.Kind == PviKind.ParaCurve && v.Length > 0)
                            prof.Entities.AddFreeSymmetricParabolaByPVIAndCurveLength(pvi, v.Length);
                        else if (v.Kind == PviKind.UnsymParaCurve && v.LengthIn > 0 && v.LengthOut > 0)
                            prof.Entities.AddFreeAsymmetricParabolaByPVIAndLengths(pvi, v.LengthIn, v.LengthOut);
                        else if (v.Kind == PviKind.CircCurve && v.Radius > 0)
                            prof.Entities.AddFreeCircularCurveByPVIAndRadius(pvi, v.Radius);
                    }
                    catch (Exception ex)
                    {
                        _rep.Info($"  AVISO perfil '{pName}': curva vertical en est. {v.Station:0.###} no creada ({ex.Message}).");
                    }
                }
                return true;
            }
            catch (Exception ex)
            {
                _rep.Info($"  AVISO '{alignName}': perfil '{p.Name}' no creado ({ex.Message}).");
                return false;
            }
        }

        static string UniqueProfileName(Transaction tr, CivilAlignment al, string wanted)
        {
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (ObjectId id in al.GetProfileIds())
                names.Add(((CivilProfile)tr.GetObject(id, OpenMode.ForRead)).Name);
            string n = wanted;
            for (int i = 2; names.Contains(n); i++) n = $"{wanted} ({i})";
            return n;
        }

        string ResolveName(string wanted, HashSet<string> existing)
        {
            if (!existing.Contains(wanted)) return wanted;
            if (_opt.Conflict == NameConflict.Skip) return null;
            string n = wanted;
            for (int i = 2; existing.Contains(n); i++) n = $"{wanted} ({i})";
            return n;
        }

        static SpiralType MapSpiral(string t)
        {
            switch ((t ?? "").ToLowerInvariant())
            {
                case "cubicparabola": return SpiralType.CubicParabola;
                case "bloss": return SpiralType.Bloss;
                case "sinusoid": return SpiralType.Sinusoidal;
                case "sinehalfwave": return SpiralType.SineHalfWave;
                case "biquadratic": return SpiralType.BiQuadratic;
                case "japanesecubic": return SpiralType.JapaneseCubic;
                default: return SpiralType.Clothoid;
            }
        }

        static Point3d P3(XY p) => new Point3d(p.X, p.Y, 0);

        static double Dist(XY a, XY b) => Math.Sqrt((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y));
    }
}
