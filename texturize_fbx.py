import bpy
import os

# Rutas
input_fbx = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno.fbx"
output_fbx = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno_PBR.fbx"
tex_dir = r"C:\Users\erick.rodriguez\Downloads\Susan"

color_path = os.path.join(tex_dir, "Ortofoto_Muro.jpg")
rough_path = os.path.join(tex_dir, "Ortofoto_Muro_Roughness.jpg")
normal_path = os.path.join(tex_dir, "Ortofoto_Muro_Normal.jpg")

# Limpiar escena
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Importar FBX
bpy.ops.import_scene.fbx(filepath=input_fbx)

# Crear Material PBR
mat = bpy.data.materials.new(name="MuroPBR")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links

for n in nodes:
    nodes.remove(n)

out_node = nodes.new('ShaderNodeOutputMaterial')
out_node.location = (300, 0)

bsdf = nodes.new('ShaderNodeBsdfPrincipled')
bsdf.location = (0, 0)
links.new(bsdf.outputs['BSDF'], out_node.inputs['Surface'])

def load_tex(path, name, is_color=True):
    if not os.path.exists(path): 
        print(f"Textura no encontrada: {path}")
        return None
    tex = nodes.new('ShaderNodeTexImage')
    img = bpy.data.images.load(path)
    tex.image = img
    tex.name = name
    if not is_color:
        try:
            tex.image.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass
    return tex

tex_color = load_tex(color_path, "Color", True)
if tex_color:
    tex_color.location = (-400, 200)
    links.new(tex_color.outputs['Color'], bsdf.inputs['Base Color'])

tex_rough = load_tex(rough_path, "Roughness", False)
if tex_rough:
    tex_rough.location = (-400, -100)
    links.new(tex_rough.outputs['Color'], bsdf.inputs['Roughness'])

tex_normal = load_tex(normal_path, "Normal", False)
if tex_normal:
    tex_normal.location = (-600, -400)
    norm_map = nodes.new('ShaderNodeNormalMap')
    norm_map.location = (-300, -400)
    links.new(tex_normal.outputs['Color'], norm_map.inputs['Color'])
    links.new(norm_map.outputs['Normal'], bsdf.inputs['Normal'])

# Asignar material a todos los objetos importados que sean mallas
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH':
        if len(obj.data.materials) == 0:
            obj.data.materials.append(mat)
        else:
            for i in range(len(obj.data.materials)):
                obj.data.materials[i] = mat

# Asegurarse de que el modo objeto esté activo
if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

# Exportar FBX
bpy.ops.export_scene.fbx(
    filepath=output_fbx,
    use_selection=False,
    path_mode='COPY',
    embed_textures=True
)

# Guardar un archivo BLEND por si acaso
blend_path = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno_PBR.blend"
bpy.ops.wm.save_as_mainfile(filepath=blend_path)

print(f"EXPORTED_PBR_FBX: {output_fbx}")
print(f"SAVED_BLEND: {blend_path}")

