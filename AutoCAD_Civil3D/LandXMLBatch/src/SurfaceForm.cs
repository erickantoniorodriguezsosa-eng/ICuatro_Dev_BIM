using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Windows.Forms;

namespace ICuatro.LandXmlBatch
{
    /// <summary>Options dialog for ICSUPLINEAS.</summary>
    public class SurfaceForm : Form
    {
        readonly RadioButton _new = new RadioButton { Text = "Nueva superficie", Checked = true, AutoSize = true };
        readonly RadioButton _existing = new RadioButton { Text = "Agregar a existente", AutoSize = true };
        readonly TextBox _name = new TextBox { Width = 260, Text = "Superficie desde lineas" };
        readonly ComboBox _style = new ComboBox { Width = 260, DropDownStyle = ComboBoxStyle.DropDownList };
        readonly ComboBox _target = new ComboBox { Width = 260, DropDownStyle = ComboBoxStyle.DropDownList };
        readonly ComboBox _use = new ComboBox { Width = 260, DropDownStyle = ComboBoxStyle.DropDownList };
        readonly TextBox _mid = new TextBox { Width = 80, Text = "0.1" };
        readonly TextBox _max = new TextBox { Width = 80, Text = "10" };
        readonly CheckBox _auto = new CheckBox { Text = "Automatico: equilibrar carga segun tamano de seleccion", Checked = true, AutoSize = true };
        readonly TextBox _budget = new TextBox { Width = 100, Text = "1500000" };
        readonly CheckBox _clean = new CheckBox { Text = "Limpiar borde: eliminar triangulos largos y delgados del contorno", Checked = true, AutoSize = true };
        readonly TextBox _edge = new TextBox { Width = 80, Text = "0" };
        readonly CheckBox _flat = new CheckBox { Text = "Minimizar areas planas (curvas de nivel)", Checked = true, AutoSize = true };
        readonly CheckBox _boundary = new CheckBox { Text = "Seleccionar contorno exterior (curva cerrada)", AutoSize = true };

        public SurfaceOptions Options { get; private set; }

        public SurfaceForm(IList<string> surfaceNames, IList<string> styleNames)
        {
            Text = "ICuatro - Superficie desde lineas";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            Font = new Font("Segoe UI", 9f);

            foreach (var s in styleNames) _style.Items.Add(s);
            if (_style.Items.Count > 0) _style.SelectedIndex = 0;
            foreach (var s in surfaceNames) _target.Items.Add(s);
            if (_target.Items.Count > 0) _target.SelectedIndex = 0; else _existing.Enabled = false;

            _use.Items.AddRange(new object[]
            {
                "Lineas de rotura (breaklines)",
                "Curvas de nivel (contornos)",
                "Solo vertices (puntos)",
            });
            _use.SelectedIndex = 0;

            var grid = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, Padding = new Padding(10), Dock = DockStyle.Fill };
            void Row(string label, Control c)
            {
                grid.Controls.Add(new Label { Text = label, AutoSize = true, Anchor = AnchorStyles.Left, Padding = new Padding(0, 5, 8, 0) });
                grid.Controls.Add(c);
            }
            Row("", _new);
            Row("Nombre:", _name);
            Row("Estilo:", _style);
            Row("", _existing);
            Row("Superficie:", _target);
            Row("Usar lineas como:", _use);
            Row("", _auto);
            Row("Limite de puntos:", _budget);
            Row("Tolerancia de curvas (m):", _mid);
            Row("Distancia max. suplementar (m):", _max);
            Row("", _clean);
            Row("Long. max. borde (m, 0 = auto):", _edge);
            Row("", _flat);
            Row("", _boundary);

            var ok = new Button { Text = "Seleccionar objetos >", Width = 160, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "Cancelar", Width = 100, DialogResult = DialogResult.Cancel };
            var buttons = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Dock = DockStyle.Fill };
            buttons.Controls.AddRange(new Control[] { cancel, ok });
            grid.Controls.Add(new Label());
            grid.Controls.Add(buttons);
            Controls.Add(grid);
            AcceptButton = ok;
            CancelButton = cancel;

            _new.CheckedChanged += (s, e) => Toggle();
            _auto.CheckedChanged += (s, e) => Toggle();
            _clean.CheckedChanged += (s, e) => Toggle();
            _use.SelectedIndexChanged += (s, e) => Toggle();
            Toggle();
            ok.Click += OnOk;
        }

        void Toggle()
        {
            _name.Enabled = _style.Enabled = _new.Checked;
            _target.Enabled = !_new.Checked;
            _mid.Enabled = _max.Enabled = !_auto.Checked;
            _edge.Enabled = _clean.Checked;
            _flat.Enabled = _use.SelectedIndex == (int)CurveUse.Contours;
        }

        void OnOk(object sender, EventArgs e)
        {
            bool okMid = double.TryParse(_mid.Text.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var mid) && mid > 0;
            bool okMax = double.TryParse(_max.Text.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var max) && max > 0;
            bool okBudget = long.TryParse(_budget.Text.Replace(".", "").Replace(",", "").Trim(), out var budget) && budget >= 10000;
            bool okEdge = double.TryParse(_edge.Text.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var edge) && edge >= 0;
            string name = _new.Checked ? _name.Text.Trim() : _target.SelectedItem as string;
            string error = !okEdge ? "Longitud maxima de borde invalida (>= 0)." : !okBudget ? "Limite de puntos invalido (>= 10000)." : !okMid ? "Tolerancia invalida (> 0)." : !okMax ? "Distancia maxima invalida (> 0)." : string.IsNullOrEmpty(name) ? "Indique la superficie." : null;
            if (error != null)
            {
                MessageBox.Show(this, error, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                DialogResult = DialogResult.None;
                return;
            }
            Options = new SurfaceOptions
            {
                NewSurface = _new.Checked,
                Name = name,
                StyleName = _style.SelectedItem as string,
                CurveUse = (CurveUse)_use.SelectedIndex,
                MidOrdinate = mid,
                MaxDistance = max,
                OuterBoundary = _boundary.Checked,
                Auto = _auto.Checked,
                PointBudget = budget,
                CleanEdges = _clean.Checked,
                EdgeLength = edge,
                MinimizeFlat = _flat.Checked,
            };
        }
    }
}
