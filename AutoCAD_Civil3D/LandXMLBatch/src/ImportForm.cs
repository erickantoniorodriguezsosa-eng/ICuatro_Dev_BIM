using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace ICuatro.LandXmlBatch
{
    /// <summary>Batch selection dialog: individual files and/or whole folders.</summary>
    public class ImportForm : Form
    {
        readonly ListBox _list = new ListBox { Dock = DockStyle.Fill, SelectionMode = SelectionMode.MultiExtended, HorizontalScrollbar = true };
        readonly CheckBox _recursive = new CheckBox { Text = "Incluir subcarpetas", Checked = true, AutoSize = true };
        readonly CheckBox _surfaces = new CheckBox { Text = "Superficies (TIN)", Checked = true, AutoSize = true };
        readonly CheckBox _alignments = new CheckBox { Text = "Alineamientos", Checked = true, AutoSize = true };
        readonly CheckBox _profiles = new CheckBox { Text = "Perfiles de diseno (ProfAlign)", Checked = true, AutoSize = true };
        readonly CheckBox _prefix = new CheckBox { Text = "Prefijar nombre con nombre de archivo", AutoSize = true };
        readonly ComboBox _conflict = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 200 };
        readonly Label _count = new Label { AutoSize = true };

        public List<string> Files => _list.Items.Cast<string>().ToList();

        public ImportOptions Options => new ImportOptions
        {
            Surfaces = _surfaces.Checked,
            Alignments = _alignments.Checked,
            Profiles = _alignments.Checked && _profiles.Checked,
            PrefixFileName = _prefix.Checked,
            Conflict = _conflict.SelectedIndex == 1 ? NameConflict.Skip : NameConflict.Rename,
        };

        public ImportForm()
        {
            Text = "ICuatro - Importar LandXML por lotes";
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(640, 480);
            Size = new Size(760, 540);
            Font = new Font("Segoe UI", 9f);

            var btnFiles = Btn("Agregar archivos...", AddFiles);
            var btnFolder = Btn("Agregar carpeta...", AddFolder);
            var btnRemove = Btn("Quitar", (s, e) => { foreach (var i in _list.SelectedItems.Cast<object>().ToList()) _list.Items.Remove(i); UpdateCount(); });
            var btnClear = Btn("Limpiar", (s, e) => { _list.Items.Clear(); UpdateCount(); });

            var side = new FlowLayoutPanel { Dock = DockStyle.Right, FlowDirection = FlowDirection.TopDown, Width = 170, Padding = new Padding(6), WrapContents = false };
            side.Controls.AddRange(new Control[] { btnFiles, btnFolder, _recursive, btnRemove, btnClear });

            _conflict.Items.AddRange(new object[] { "Renombrar (nombre (2))", "Omitir" });
            _conflict.SelectedIndex = 0;
            _alignments.CheckedChanged += (s, e) => _profiles.Enabled = _alignments.Checked;

            var opts = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 92, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(6) };
            opts.Controls.AddRange(new Control[] { _surfaces, _alignments, _profiles, _prefix, new Label { Text = "Nombre existente:", AutoSize = true, Padding = new Padding(0, 4, 0, 0) }, _conflict });

            var ok = new Button { Text = "Importar", DialogResult = DialogResult.OK, Width = 100 };
            var cancel = new Button { Text = "Cancelar", DialogResult = DialogResult.Cancel, Width = 100 };
            var bottom = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 40, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(6) };
            bottom.Controls.AddRange(new Control[] { cancel, ok, _count });
            AcceptButton = ok;
            CancelButton = cancel;
            ok.Click += (s, e) =>
            {
                if (_list.Items.Count == 0 || !(_surfaces.Checked || _alignments.Checked))
                {
                    MessageBox.Show(this, "Agregue al menos un archivo y seleccione que importar.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    DialogResult = DialogResult.None;
                }
            };

            var center = new Panel { Dock = DockStyle.Fill, Padding = new Padding(6) };
            center.Controls.Add(_list);
            _list.AllowDrop = true;
            _list.DragEnter += (s, e) => e.Effect = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
            _list.DragDrop += (s, e) =>
            {
                foreach (var p in (string[])e.Data.GetData(DataFormats.FileDrop))
                    AddPaths(Directory.Exists(p) ? FileCollector.FromFolder(p, _recursive.Checked) : new[] { p });
            };

            Controls.Add(center);
            Controls.Add(side);
            Controls.Add(opts);
            Controls.Add(bottom);
            UpdateCount();
        }

        static Button Btn(string text, EventHandler click)
        {
            var b = new Button { Text = text, Width = 150, Height = 28 };
            b.Click += click;
            return b;
        }

        void AddFiles(object sender, EventArgs e)
        {
            using (var dlg = new OpenFileDialog { Filter = "LandXML (*.xml)|*.xml|Todos (*.*)|*.*", Multiselect = true, Title = "Seleccionar archivos LandXML" })
                if (dlg.ShowDialog(this) == DialogResult.OK) AddPaths(dlg.FileNames);
        }

        void AddFolder(object sender, EventArgs e)
        {
            using (var dlg = new FolderBrowserDialog { Description = "Carpeta con archivos LandXML", UseDescriptionForTitle = true })
                if (dlg.ShowDialog(this) == DialogResult.OK) AddPaths(FileCollector.FromFolder(dlg.SelectedPath, _recursive.Checked));
        }

        void AddPaths(IEnumerable<string> paths)
        {
            var existing = new HashSet<string>(Files, StringComparer.OrdinalIgnoreCase);
            foreach (var p in paths.Select(Path.GetFullPath))
                if (existing.Add(p)) _list.Items.Add(p);
            UpdateCount();
        }

        void UpdateCount() => _count.Text = $"{_list.Items.Count} archivo(s)   ";
    }

    public static class FileCollector
    {
        public static IEnumerable<string> FromFolder(string folder, bool recursive) =>
            Directory.EnumerateFiles(folder, "*.xml", recursive ? SearchOption.AllDirectories : SearchOption.TopDirectoryOnly)
                .OrderBy(f => f, StringComparer.OrdinalIgnoreCase);
    }
}
