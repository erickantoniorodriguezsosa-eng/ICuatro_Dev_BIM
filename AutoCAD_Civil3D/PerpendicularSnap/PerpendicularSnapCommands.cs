using System;

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;

namespace ICuatro.CadTools
{
    /// <summary>
    /// Comando IC_LLEVARAEJE: reubica una curva (Line, Polyline, Polyline2d,
    /// Polyline3d, Arc, Spline o Ellipse) sobre un eje CAD destino del mismo
    /// tipo de entidades, conservando su forma/longitud mediante una
    /// transformacion rigida (traslacion + rotacion) y orientando su cuerda
    /// (inicio-fin) lo mas perpendicular posible al eje en el punto de
    /// contacto.
    /// </summary>
    public class PerpendicularSnapCommands
    {
        private const double Epsilon = 1e-9;

        [CommandMethod("IC_LLEVARAEJE")]
        public void LlevarLineaAEje()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Database db = doc.Database;
            Editor ed = doc.Editor;

            PromptEntityOptions opcionesMovil = new PromptEntityOptions(
                "\nSeleccione la entidad a mover (Line/Polyline/Arc/Spline): ");
            opcionesMovil.SetRejectMessage(
                "\nSolo se aceptan Line, Polyline, Polyline2d, Polyline3d, "
                + "Arc, Spline o Ellipse.");
            opcionesMovil.AddAllowedClass(typeof(Curve), false);
            PromptEntityResult resultMovil = ed.GetEntity(opcionesMovil);
            if (resultMovil.Status != PromptStatus.OK)
            {
                return;
            }

            PromptEntityOptions opcionesEje = new PromptEntityOptions(
                "\nSeleccione el eje destino (Line/Polyline/Arc/Spline): ");
            opcionesEje.SetRejectMessage(
                "\nSolo se aceptan Line, Polyline, Polyline2d, Polyline3d, "
                + "Arc, Spline o Ellipse.");
            opcionesEje.AddAllowedClass(typeof(Curve), false);
            PromptEntityResult resultEje = ed.GetEntity(opcionesEje);
            if (resultEje.Status != PromptStatus.OK)
            {
                return;
            }

            using (Transaction tr = db.TransactionManager.StartTransaction())
            {
                Curve entidadMovil = tr.GetObject(
                    resultMovil.ObjectId, OpenMode.ForWrite) as Curve;
                Curve eje = tr.GetObject(
                    resultEje.ObjectId, OpenMode.ForRead) as Curve;

                if (!EsCurvaSoportada(entidadMovil))
                {
                    ed.WriteMessage(
                        "\nTipo de entidad a mover no soportado.");
                    tr.Commit();
                    return;
                }
                if (!EsCurvaSoportada(eje))
                {
                    ed.WriteMessage("\nTipo de entidad de eje no soportado.");
                    tr.Commit();
                    return;
                }

                Point3d baseVieja = entidadMovil.StartPoint;
                Point3d libreVieja = entidadMovil.EndPoint;
                double cuerda = baseVieja.DistanceTo(libreVieja);
                if (cuerda < Epsilon)
                {
                    ed.WriteMessage(
                        "\nLa entidad a mover no tiene cuerda definida "
                        + "(inicio y fin coinciden); no se puede orientar.");
                    tr.Commit();
                    return;
                }

                // Punto de la entidad mas cercano al eje: ese extremo se fija
                // sobre el eje; el otro conserva la cuerda original.
                Point3d proyBaseVieja = ProyectarSobreEje(eje, baseVieja);
                Point3d proyLibreVieja = ProyectarSobreEje(eje, libreVieja);
                Vector3d ladoBase = baseVieja - proyBaseVieja;
                Vector3d ladoLibre = libreVieja - proyLibreVieja;

                bool baseEsInicio = ladoBase.Length <= ladoLibre.Length;
                Point3d proyBase = baseEsInicio ? proyBaseVieja : proyLibreVieja;
                Vector3d ladoLejano = baseEsInicio ? ladoLibre : ladoBase;

                // Tangente local del eje en el punto de contacto: soporta
                // ejes curvos (Arc/Polyline/Spline), no solo rectas.
                Vector3d dirEje = TangenteEn(eje, proyBase);

                Vector3d direccionLado;
                if (ladoLejano.Length > Epsilon)
                {
                    // Mismo lado del eje que tenia el extremo lejano: evita
                    // que la entidad salte al lado opuesto.
                    direccionLado = (ladoLejano
                        - dirEje * ladoLejano.DotProduct(dirEje)).GetNormal();
                }
                else
                {
                    // Entidad original ya colineal con el eje: no hay lado
                    // que conservar, se usa una perpendicular generica.
                    Vector3d candidato = dirEje.CrossProduct(Vector3d.ZAxis);
                    if (candidato.Length < Epsilon)
                    {
                        candidato = dirEje.CrossProduct(Vector3d.XAxis);
                    }
                    direccionLado = candidato.GetNormal();
                }

                Point3d nuevaBase = proyBase;
                Point3d nuevaLibre = nuevaBase + direccionLado * cuerda;

                Point3d baseVieja2 = baseEsInicio ? baseVieja : libreVieja;
                Point3d libreVieja2 = baseEsInicio ? libreVieja : baseVieja;
                Point3d nuevaBase2 = baseEsInicio ? nuevaBase : nuevaLibre;
                Point3d nuevaLibre2 = baseEsInicio ? nuevaLibre : nuevaBase;

                Matrix3d transformacion = TransformacionRigida(
                    baseVieja2, libreVieja2, nuevaBase2, nuevaLibre2);
                entidadMovil.TransformBy(transformacion);

                tr.Commit();
            }

            ed.WriteMessage(
                "\nEntidad reubicada sobre el eje: forma/longitud original, "
                + "perpendicular al eje.");
        }

        private static bool EsCurvaSoportada(Curve curva)
        {
            return curva is Line
                || curva is Polyline
                || curva is Polyline2d
                || curva is Polyline3d
                || curva is Arc
                || curva is Spline
                || curva is Ellipse;
        }

        /// <summary>
        /// Punto del eje mas cercano a punto, extendiendo el eje mas alla de
        /// sus extremos cuando el tipo de curva lo permite (Line/Arc); si la
        /// extension no esta soportada (p.ej. Polyline), se usa el eje tal
        /// cual esta dibujado.
        /// </summary>
        private static Point3d ProyectarSobreEje(Curve eje, Point3d punto)
        {
            try
            {
                return eje.GetClosestPointTo(punto, true);
            }
            catch
            {
                return eje.GetClosestPointTo(punto, false);
            }
        }

        private static Vector3d TangenteEn(Curve eje, Point3d puntoSobreEje)
        {
            Point3d puntoValido = puntoSobreEje;
            try
            {
                double parametro = eje.GetParameterAtPoint(puntoSobreEje);
                puntoValido = eje.GetPointAtParameter(parametro);
            }
            catch
            {
                puntoValido = eje.GetClosestPointTo(puntoSobreEje, false);
            }
            return eje.GetFirstDerivative(puntoValido).GetNormal();
        }

        /// <summary>
        /// Traslacion + rotacion que lleva baseVieja-libreVieja exactamente
        /// sobre baseNueva-libreNueva (misma distancia en ambos pares), sin
        /// escalar. Rotacion alrededor de baseVieja, luego traslacion de
        /// baseVieja a baseNueva.
        /// </summary>
        private static Matrix3d TransformacionRigida(
            Point3d baseVieja, Point3d libreVieja,
            Point3d baseNueva, Point3d libreNueva)
        {
            Vector3d dirVieja = (libreVieja - baseVieja).GetNormal();
            Vector3d dirNueva = (libreNueva - baseNueva).GetNormal();

            Matrix3d rotacion;
            double producto = dirVieja.DotProduct(dirNueva);
            if (producto > 1.0 - 1e-9)
            {
                rotacion = Matrix3d.Identity;
            }
            else if (producto < -1.0 + 1e-9)
            {
                Vector3d arbitrario = dirVieja.CrossProduct(Vector3d.ZAxis);
                if (arbitrario.Length < Epsilon)
                {
                    arbitrario = dirVieja.CrossProduct(Vector3d.XAxis);
                }
                rotacion = Matrix3d.Rotation(
                    Math.PI, arbitrario.GetNormal(), baseVieja);
            }
            else
            {
                Vector3d ejeRotacion = dirVieja.CrossProduct(dirNueva).GetNormal();
                double angulo = dirVieja.GetAngleTo(dirNueva);
                rotacion = Matrix3d.Rotation(angulo, ejeRotacion, baseVieja);
            }

            Matrix3d traslacion = Matrix3d.Displacement(baseNueva - baseVieja);
            return traslacion * rotacion;
        }
    }
}
