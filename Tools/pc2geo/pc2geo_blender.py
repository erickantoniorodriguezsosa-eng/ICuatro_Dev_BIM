bl_info = {"name": "PC2Geo - nube de puntos a geometria", "author": "ICuatro", "version": (0, 2, 0), "blender": (4, 0, 0),
           "location": "Vista 3D > Barra lateral (N) > PC2Geo", "description": "Ajusta geometria parametrica de un puente de vigas a una nube de puntos", "category": "Import-Export"}
import bpy, os, sys, time, importlib
import numpy as np
from bpy.props import StringProperty, FloatProperty, FloatVectorProperty, BoolProperty

_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else r"C:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\Tools\pc2geo"
if _DIR not in sys.path: sys.path.insert(0, _DIR)
import pc2geo as G
importlib.reload(G)

def _mesh_from(name, V, F, mat=None, colors=None):
    me = bpy.data.meshes.new(name); me.from_pydata([tuple(map(float, v)) for v in V], [], [tuple(int(i) for i in f) for f in F]); me.update()
    if colors is not None:
        ca = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT"); col = np.column_stack((colors.astype(np.float32) / 255.0, np.ones(len(colors), np.float32)))
        ca.data.foreach_set("color", col.ravel())
    if mat: me.materials.append(mat)
    return me

def _mat(name, rgb):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name); m.diffuse_color = (*rgb, 1)
    m.use_nodes = True
    bs = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"); bs.inputs["Base Color"].default_value = (*rgb, 1)
    return m

def run_pipeline(props, report):
    t0 = time.time(); org = tuple(props.origin)
    P, C = G.load_las(bpy.path.abspath(props.filepath), props.voxel, org)
    report.append(f"nube: {len(P)} puntos (voxel {props.voxel} m), origen {org}")
    hag = G.ground_height(P.astype(np.float64))
    cur = bpy.context.scene.cursor.location
    seed = (cur.x, cur.y, props.angle, props.largo, props.ancho)
    fr = G.refine_frame(P.astype(np.float64), hag, seed)
    if fr is None or not fr['rails']:
        report.append("FALLO: no se hallaron las dos barandas. Acerca el cursor 3D al centro del puente (<6 m) y el angulo (<5 grados)."); return None
    report.append(f"eje {fr['ang']:.2f} deg, centro {fr['c'].round(2)}, largo {fr['A1']-fr['A0']:.1f} m, barandas a {fr['rails'][1]-fr['rails'][0]:.2f} m, plano sd {fr['sd']:.3f}")
    ms = G.measure_section(P.astype(np.float64), fr); rl = G.measure_rails(P.astype(np.float64), fr, fr['rails'])
    bx = G.build_boxes(fr, ms, rl, props.ancho_poste); res = G.residuals(P.astype(np.float64), fr, bx)
    report.append(f"cajas {len(bx)}; mediana {res['med']*100:.1f} cm (inliers {res['inl']*100:.0f}%: mediana {res['med_in']*100:.1f} cm, p95 {res['p95_in']*100:.1f}); <3 cm {res['w3']*100:.0f}%, <10 cm {res['w10']*100:.0f}%")
    report.append(f"losa de {ms['losa_top']-ms['losa_bot']:.2f} m de espesor, {len(ms['webs'])} vigas, fondo {ms['fondo']:.2f} m, postes {len(rl['izq']['posts'])}/{len(rl['der']['posts'])}")
    col = bpy.data.collections.get("PC2GEO") or bpy.data.collections.new("PC2GEO")
    if "PC2GEO" not in [c.name for c in bpy.context.scene.collection.children]: bpy.context.scene.collection.children.link(col)
    for o in list(col.objects): bpy.data.objects.remove(o)
    groups = {"Tablero y vigas": [b for b in bx if b[0].startswith(("Losa", "Alma", "Ala"))],
              "Baranda": [b for b in bx if b[0].startswith(("Bordillo", "Riel", "Poste"))]}
    cols = {"Tablero y vigas": (0.55, 0.58, 0.65), "Baranda": (0.85, 0.2, 0.2)}
    for nm, boxes in groups.items():
        if not boxes: continue
        V, F = G.boxes_to_mesh(boxes, fr); me = _mesh_from(nm + " (PC2Geo)", V, F, _mat("PC2GEO_" + nm, cols[nm]))
        ob = bpy.data.objects.new(nm + " (PC2Geo)", me); col.objects.link(ob)
    if props.terreno:
        Vt, Ft, Ct = G.terrain_tin(P.astype(np.float64), C, hag, fr)
        mt = bpy.data.materials.get("PC2GEO_terreno") or bpy.data.materials.new("PC2GEO_terreno"); mt.use_nodes = True
        nt = mt.node_tree; nt.nodes.clear(); o = nt.nodes.new("ShaderNodeOutputMaterial"); b = nt.nodes.new("ShaderNodeBsdfPrincipled"); a = nt.nodes.new("ShaderNodeVertexColor"); a.layer_name = "Col"
        nt.links.new(a.outputs["Color"], b.inputs["Base Color"]); nt.links.new(b.outputs[0], o.inputs[0]); b.inputs["Roughness"].default_value = 0.9
        me = _mesh_from("Terreno TIN (PC2Geo)", Vt, Ft, mt, Ct); ob = bpy.data.objects.new("Terreno TIN (PC2Geo)", me); col.objects.link(ob)
        report.append(f"terreno TIN: {len(Vt)} vertices, {len(Ft)} triangulos")
    report.append(f"tiempo total {time.time()-t0:.0f} s")
    return fr

class PC2GEO_Props(bpy.types.PropertyGroup):
    filepath: StringProperty(name="Nube (LAS)", subtype='FILE_PATH', default="")
    origin: FloatVectorProperty(name="Origen local", size=3, default=(0.0, 0.0, 0.0), subtype='XYZ')
    voxel: FloatProperty(name="Voxel (m)", default=0.05, min=0.01, max=0.5)
    angle: FloatProperty(name="Angulo aprox (grados)", default=90.0)
    largo: FloatProperty(name="Largo aprox (m)", default=40.0, min=5)
    ancho: FloatProperty(name="Ancho aprox (m)", default=12.0, min=3)
    ancho_poste: FloatProperty(name="Ancho de poste (m)", default=0.06, min=0.02, max=0.4)
    terreno: BoolProperty(name="Generar terreno TIN", default=True)

class PC2GEO_OT_run(bpy.types.Operator):
    bl_idname = "pc2geo.run"; bl_label = "Generar geometria"; bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        rep = []; props = context.scene.pc2geo
        try: fr = run_pipeline(props, rep)
        except Exception as ex:
            import traceback; rep.append("ERROR: " + str(ex)); rep.append(traceback.format_exc())
        txt = bpy.data.texts.get("PC2GEO_reporte") or bpy.data.texts.new("PC2GEO_reporte"); txt.clear(); txt.write("\n".join(rep))
        self.report({'INFO'}, rep[-1] if rep else "listo"); return {'FINISHED'}

class PC2GEO_PT_panel(bpy.types.Panel):
    bl_label = "PC2Geo"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = "PC2Geo"
    def draw(self, context):
        l = self.layout; p = context.scene.pc2geo
        for k in ("filepath", "origin", "voxel", "angle", "largo", "ancho", "ancho_poste", "terreno"): l.prop(p, k)
        l.label(text="Semilla: cursor 3D en el centro del puente")
        l.operator("pc2geo.run", icon='MOD_BUILD')

_classes = (PC2GEO_Props, PC2GEO_OT_run, PC2GEO_PT_panel)
def register():
    for c in _classes:
        try: bpy.utils.unregister_class(c)
        except Exception: pass
        bpy.utils.register_class(c)
    bpy.types.Scene.pc2geo = bpy.props.PointerProperty(type=PC2GEO_Props)
def unregister():
    for c in reversed(_classes): bpy.utils.unregister_class(c)
    del bpy.types.Scene.pc2geo
if __name__ == "__main__": register()
