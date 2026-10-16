using System;
using System.Collections.Generic;
using System.Linq;

namespace ICuatro.LandXmlBatch
{
    /// <summary>
    /// Peels bad triangles from the outside of a TIN: triangles whose exposed edge is too long (bridges across
    /// concave gaps) or that are thin slivers along the border. Works inward only from the hull, so interior
    /// triangles are never removed unless they become exposed by a removed neighbour.
    /// </summary>
    public static class TinCleaner
    {
        public class Result
        {
            public int Total, Removed;
            public double Threshold, MedianEdge;
            public bool Aborted;
            public List<(XY A, XY B)> EdgesToDelete = new List<(XY, XY)>();
        }

        /// <param name="verts">Vertex plan coordinates.</param>
        /// <param name="tris">Vertex indices, three per triangle.</param>
        /// <param name="threshold">Maximum exposed edge length; &lt;= 0 computes it from the edge statistics.</param>
        /// <param name="sliverDeg">Border triangles with a smaller minimum angle are removed when their long edge is exposed.</param>
        /// <param name="maxFraction">Safety stop: abort if more than this share of triangles would go.</param>
        public static Result Peel(IReadOnlyList<XY> verts, IReadOnlyList<int> tris, double threshold = 0, double sliverDeg = 3, double maxFraction = 0.4)
        {
            int n = tris.Count / 3;
            var r = new Result { Total = n };
            if (n == 0) return r;

            // Neighbour across edge k (vertices k, k+1) of triangle t, or -1 on the hull.
            var nb = new int[n * 3];
            var open = new Dictionary<long, int>(n * 2);
            for (int t = 0; t < n; t++)
                for (int k = 0; k < 3; k++)
                {
                    nb[t * 3 + k] = -1;
                    long key = EdgeKey(tris[t * 3 + k], tris[t * 3 + (k + 1) % 3]);
                    if (open.TryGetValue(key, out int slot))
                    {
                        nb[t * 3 + k] = slot / 3;
                        nb[slot] = t;
                        open.Remove(key);
                    }
                    else open[key] = t * 3 + k;
                }

            r.MedianEdge = EdgeStats(verts, tris, n, out double q1, out double q3);
            r.Threshold = threshold > 0 ? threshold : Math.Max(2 * r.MedianEdge, q3 + 3 * (q3 - q1));

            var removed = new bool[n];
            var queue = new Queue<int>();
            for (int t = 0; t < n; t++)
                if (nb[t * 3] < 0 || nb[t * 3 + 1] < 0 || nb[t * 3 + 2] < 0) queue.Enqueue(t);

            double sliverCos = Math.Cos(sliverDeg * Math.PI / 180);
            while (queue.Count > 0)
            {
                int t = queue.Dequeue();
                if (removed[t] || !ShouldRemove(verts, tris, nb, removed, t, r.Threshold, r.MedianEdge, sliverCos)) continue;

                removed[t] = true;
                if (++r.Removed > maxFraction * n)
                {
                    r.Aborted = true;
                    r.EdgesToDelete.Clear();
                    return r;
                }
                for (int k = 0; k < 3; k++)
                {
                    int o = nb[t * 3 + k];
                    if (o >= 0 && !removed[o]) queue.Enqueue(o);
                }
            }

            // Deleting these lines removes exactly the peeled triangles: each is on the hull or between two peeled triangles.
            for (int t = 0; t < n; t++)
            {
                if (!removed[t]) continue;
                for (int k = 0; k < 3; k++)
                {
                    int o = nb[t * 3 + k];
                    if (o < 0 || (removed[o] && t < o))
                        r.EdgesToDelete.Add((verts[tris[t * 3 + k]], verts[tris[t * 3 + (k + 1) % 3]]));
                }
            }
            return r;
        }

        static bool ShouldRemove(IReadOnlyList<XY> v, IReadOnlyList<int> tris, int[] nb, bool[] removed, int t,
                                 double threshold, double median, double sliverCos)
        {
            double longestExposed = 0, longest = 0;
            var len = new double[3];
            for (int k = 0; k < 3; k++)
            {
                len[k] = Dist(v[tris[t * 3 + k]], v[tris[t * 3 + (k + 1) % 3]]);
                longest = Math.Max(longest, len[k]);
                int o = nb[t * 3 + k];
                if (o < 0 || removed[o]) longestExposed = Math.Max(longestExposed, len[k]);
            }
            if (longestExposed <= 0) return false;
            if (longestExposed > threshold) return true;

            // Thin border sliver: its long side is on the outside and it is not a tiny triangle.
            return longestExposed >= longest - 1e-9 && longestExposed > median && MaxCos(len) > sliverCos;
        }

        /// <summary>Cosine of the smallest angle (largest cosine) from side lengths.</summary>
        static double MaxCos(double[] l)
        {
            double best = -1;
            for (int k = 0; k < 3; k++)
            {
                double a = l[k], b = l[(k + 1) % 3], c = l[(k + 2) % 3];   // angle opposite side a
                if (b <= 0 || c <= 0) return 1;
                best = Math.Max(best, (b * b + c * c - a * a) / (2 * b * c));
            }
            return best;
        }

        static double EdgeStats(IReadOnlyList<XY> v, IReadOnlyList<int> tris, int n, out double q1, out double q3)
        {
            int stride = Math.Max(1, n / 100_000);
            var lengths = new List<double>();
            for (int t = 0; t < n; t += stride)
                for (int k = 0; k < 3; k++)
                    lengths.Add(Dist(v[tris[t * 3 + k]], v[tris[t * 3 + (k + 1) % 3]]));
            lengths.Sort();
            q1 = lengths[lengths.Count / 4];
            q3 = lengths[lengths.Count * 3 / 4];
            return lengths[lengths.Count / 2];
        }

        static long EdgeKey(int a, int b) => a < b ? ((long)a << 32) | (uint)b : ((long)b << 32) | (uint)a;

        static double Dist(XY a, XY b) => Math.Sqrt((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y));
    }
}
