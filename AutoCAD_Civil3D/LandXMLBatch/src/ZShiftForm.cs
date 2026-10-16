using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace ICuatro.LandXmlBatch
{
    /// <summary>Filter and offset dialog for ICMOVERZ.</summary>
    public class ZShiftForm : Form
    {
        public enum ScopeKind { ModelSpace, PreSelection, Pick }

        public class Request
        {
            public ScopeKind Scope;
            public HashSet<string> Layers, Types, Colors;
            public bool UseZRange;
            public double ZMin, ZMax;
            public double DeltaZ;
            public bool SelectOnly;

            public bool Matches(EntityInfo i) =>
                Layers.Contains(i.Layer) && Types.Contains(i.Type) &&
                (Colors == null || Colors.Contains(i.Color)) &&
                (!UseZRange || (i.HasZ && i.ZMax >= ZMin && i.ZMin <= ZMax));
        }

        static readonly Dictionary<string, string> Friendly = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["LINE"] = "Linea", ["LWPOLYLINE"] = "Polilinea", ["POLYLINE"] = "Polilinea 2D/3D", ["ARC"] = "Arco",
            ["CIRCLE"] = "Circulo", ["SPLINE"] = "Spline", ["ELLIPSE"] = "Elipse", ["POINT"] = "Punto",
            ["INSERT"] = "Bloque", ["TEXT"] = "Texto", ["MTEXT"] = "Texto multilinea", ["HATCH"] = "Sombreado",
            ["3DFACE"] = "Cara 3D", ["3DSOLID"] = "Solido 3D", ["MESH"] = "Malla", ["DIMENSION"] = "Cota",
            ["AECC_COGO_POINT"] = "Punto COGO", ["AECC_TIN_SURFACE"] = "Superficie TIN", ["AECC_FEATURE_LINE"] = "Linea caracteristica",
            ["AECC_PIPE"] = "Tuberia", ["AECC_STRUCTURE"] = "Estructura", ["AECC_PRESSURE_PIPE"] = "Tuberia a presion",
            ["AECC_SURVEY_FIGURE"] = "Figura de levantamiento",
        };

        readonly List<EntityInfo> _infos;
        readonly RadioButton _scopeAll = new RadioButton { Text = "Todo el espacio modelo", Checked = true, AutoSize = true };
        readonly RadioButton _scopePre = new RadioButton { AutoSize = true };
        readonly RadioButton _scopePick = new RadioButton { Text = "Seleccionar area en pantalla", AutoSize = true };
        readonly CheckedListBox _layers = new CheckedListBox { Width = 300, Height = 220, CheckOnClick = true, IntegralHeight = false };
        readonly CheckedListBox _types = new CheckedListBox { Width = 300, Height = 220, CheckOnClick = true, IntegralHeight = false };
        readonly TextBox _pattern = new TextBox { Width = 170 };
        readonly CheckBox _useColor = new CheckBox { Text = "Color:", AutoSize = true };
        readonly ComboBox _color = new ComboBox { Width = 170, DropDownStyle = ComboBoxStyle.DropDownList, Enabled = false };
        readonly CheckBox _useZ = new CheckBox { Text = "Rango Z (m):", AutoSize = true };
        readonly TextBox _zMin = new TextBox { Width = 80, Enabled = false };
        readonly TextBox _zMax = new TextBox { Width = 80, Enabled = false };
        readonly RadioButton _up = new RadioButton { Text = "Subir", Checked = true, AutoSize = true };
        readonly RadioButton _down = new RadioButton { Text = "Bajar", AutoSize = true };
        readonly TextBox _dz = new TextBox { Width = 80, Text = "1.000" };
        readonly Label _count = new Label { AutoSize = true, Font = new Font("Segoe UI", 9f, FontStyle.Bold), Padding = new Padding(0, 6, 0, 0) };

        public Request Result { get; private set; }

        public ZShiftForm(List<EntityInfo> infos, int preSelected)
        {
            _infos = infos;
            Text = "ICuatro - Desplazar en Z";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            Font = new Font("Segoe UI", 9f);

            _scopePre.Text = $"Solo seleccion previa ({preSelected:N0} objetos)";
            _scopePre.Enabled = preSelected > 0;
            if (preSelected > 0) _scopePre.Checked = true;

            foreach (var g in infos.GroupBy(i => i.Layer).OrderBy(g => g.Key, StringComparer.OrdinalIgnoreCase))
                _layers.Items.Add(new Item(g.Key, $"{g.Key}  ({g.Count():N0})"), true);
            foreach (var g in infos.GroupBy(i => i.Type).OrderBy(g => Label(g.Key)))
                _types.Items.Add(new Item(g.Key, $"{Label(g.Key)}  ({g.Count():N0})"), true);
            foreach (var g in infos.GroupBy(i => i.Color).OrderByDescending(g => g.Count()))
                _color.Items.Add(new Item(g.Key, $"{g.Key}  ({g.Count():N0})"));
            if (_color.Items.Count > 0) _color.SelectedIndex = 0;

            var zs = infos.Where(i => i.HasZ).ToList();
            if (zs.Count > 0)
            {
                _zMin.Text = zs.Min(i => i.ZMin).ToString("0.###", CultureInfo.InvariantCulture);
                _zMax.Text = zs.Max(i => i.ZMax).ToString("0.###", CultureInfo.InvariantCulture);
            }

            var root = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, Padding = new Padding(10) };

            var scope = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, AutoSize = true };
            scope.Controls.AddRange(new Control[] { _scopeAll, _scopePre, _scopePick });
            root.Controls.Add(Group("Alcance", scope), 0, 0);
            root.SetColumnSpan(root.GetControlFromPosition(0, 0), 2);

            var layerBox = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true };
            var patternRow = new FlowLayoutPanel { AutoSize = true, WrapContents = false };
            var mark = new Button { Text = "Marcar", Width = 60 };
            var only = new Button { Text = "Solo", Width = 50 };
            patternRow.Controls.AddRange(new Control[] { _pattern, mark, only });
            layerBox.Controls.Add(new Label { Text = "Filtro (* y ?, varios con ;):", AutoSize = true });
            layerBox.Controls.Add(patternRow);
            layerBox.Controls.Add(_layers);
            layerBox.Controls.Add(AllNone(_layers));
            root.Controls.Add(Group("Capas", layerBox), 0, 1);

            var typeBox = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true };
            typeBox.Controls.Add(new Label { Text = "Tipos de objeto:", AutoSize = true, Padding = new Padding(0, 0, 0, 33) });
            typeBox.Controls.Add(_types);
            typeBox.Controls.Add(AllNone(_types));
            root.Controls.Add(Group("Tipos", typeBox), 1, 1);

            var extra = new TableLayoutPanel { ColumnCount = 4, AutoSize = true };
            extra.Controls.Add(_useColor); extra.Controls.Add(_color); extra.SetColumnSpan(_color, 3);
            extra.Controls.Add(_useZ); extra.Controls.Add(_zMin);
            extra.Controls.Add(new Label { Text = "a", AutoSize = true, Padding = new Padding(0, 5, 0, 0) }); extra.Controls.Add(_zMax);
            root.Controls.Add(Group("Otras caracteristicas", extra), 0, 2);

            var move = new FlowLayoutPanel { AutoSize = true, WrapContents = false };
            move.Controls.AddRange(new Control[] { _up, _down, new Label { Text = "Distancia (m):", AutoSize = true, Padding = new Padding(8, 5, 0, 0) }, _dz });
            root.Controls.Add(Group("Desplazamiento", move), 1, 2);

            var countBtn = new Button { Text = "Contar", Width = 80 };
            var countRow = new FlowLayoutPanel { AutoSize = true };
            countRow.Controls.AddRange(new Control[] { countBtn, _count });
            root.Controls.Add(countRow, 0, 3);

            var ok = new Button { Text = "Desplazar", Width = 100, DialogResult = DialogResult.OK };
            var sel = new Button { Text = "Solo seleccionar", Width = 120, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "Cancelar", Width = 90, DialogResult = DialogResult.Cancel };
            var buttons = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Dock = DockStyle.Fill };
            buttons.Controls.AddRange(new Control[] { cancel, ok, sel });
            root.Controls.Add(buttons, 1, 3);

            Controls.Add(root);
            AcceptButton = ok;
            CancelButton = cancel;

            _useColor.CheckedChanged += (s, e) => _color.Enabled = _useColor.Checked;
            _useZ.CheckedChanged += (s, e) => _zMin.Enabled = _zMax.Enabled = _useZ.Checked;
            mark.Click += (s, e) => MarkLayers(false);
            only.Click += (s, e) => MarkLayers(true);
            countBtn.Click += (s, e) => UpdateCount();
            ok.Click += (s, e) => Finish(false);
            sel.Click += (s, e) => Finish(true);
            UpdateCount();
        }

        static string Label(string dxf) => Friendly.TryGetValue(dxf, out var f) ? $"{f} [{dxf}]" : dxf;

        static GroupBox Group(string title, Control content)
        {
            var g = new GroupBox { Text = title, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Padding = new Padding(8), Dock = DockStyle.Fill };
            content.Dock = DockStyle.Fill;
            g.Controls.Add(content);
            return g;
        }

        static Control AllNone(CheckedListBox list)
        {
            var p = new FlowLayoutPanel { AutoSize = true };
            var all = new Button { Text = "Todos", Width = 70 };
            var none = new Button { Text = "Ninguno", Width = 70 };
            var inv = new Button { Text = "Invertir", Width = 70 };
            all.Click += (s, e) => { for (int i = 0; i < list.Items.Count; i++) list.SetItemChecked(i, true); };
            none.Click += (s, e) => { for (int i = 0; i < list.Items.Count; i++) list.SetItemChecked(i, false); };
            inv.Click += (s, e) => { for (int i = 0; i < list.Items.Count; i++) list.SetItemChecked(i, !list.GetItemChecked(i)); };
            p.Controls.AddRange(new Control[] { all, none, inv });
            return p;
        }

        void MarkLayers(bool exclusive)
        {
            var rx = _pattern.Text.Split(';').Select(t => t.Trim()).Where(t => t.Length > 0)
                .Select(t => new Regex("^" + Regex.Escape(t).Replace(@"\*", ".*").Replace(@"\?", ".") + "$", RegexOptions.IgnoreCase)).ToList();
            if (rx.Count == 0) return;
            for (int i = 0; i < _layers.Items.Count; i++)
            {
                bool hit = rx.Any(r => r.IsMatch(((Item)_layers.Items[i]).Key));
                if (hit) _layers.SetItemChecked(i, true);
                else if (exclusive) _layers.SetItemChecked(i, false);
            }
            UpdateCount();
        }

        static HashSet<string> Checked(CheckedListBox list) =>
            new HashSet<string>(list.CheckedItems.Cast<Item>().Select(i => i.Key));

        static bool TryNum(TextBox t, out double v) =>
            double.TryParse(t.Text.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out v);

        Request Build(out string error)
        {
            error = null;
            var r = new Request
            {
                Scope = _scopePre.Checked ? ScopeKind.PreSelection : _scopePick.Checked ? ScopeKind.Pick : ScopeKind.ModelSpace,
                Layers = Checked(_layers),
                Types = Checked(_types),
                Colors = _useColor.Checked && _color.SelectedItem is Item c ? new HashSet<string> { c.Key } : null,
                UseZRange = _useZ.Checked,
            };
            if (r.UseZRange)
            {
                if (!TryNum(_zMin, out r.ZMin) || !TryNum(_zMax, out r.ZMax) || r.ZMin > r.ZMax) error = "Rango Z invalido.";
            }
            if (TryNum(_dz, out double d) && d > 0) r.DeltaZ = _up.Checked ? d : -d;
            else if (error == null) error = "Distancia invalida (> 0).";
            return r;
        }

        void UpdateCount()
        {
            var r = Build(out _);
            int n = _infos.Count(r.Matches);
            _count.Text = $"{n:N0} objeto(s) en espacio modelo cumplen los filtros";
        }

        void Finish(bool selectOnly)
        {
            var r = Build(out string error);
            if (error == null && (r.Layers.Count == 0 || r.Types.Count == 0)) error = "Marque al menos una capa y un tipo.";
            if (error != null)
            {
                MessageBox.Show(this, error, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                DialogResult = DialogResult.None;
                return;
            }
            r.SelectOnly = selectOnly;
            Result = r;
        }

        class Item
        {
            public readonly string Key;
            readonly string _text;
            public Item(string key, string text) { Key = key; _text = text; }
            public override string ToString() => _text;
        }
    }
}
