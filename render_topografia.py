import bpy
import os

# Set paths
base_dir = r"C:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\Rock030_8K-PNG"
color_path = os.path.join(base_dir, "Rock030_8K-PNG_Color.png")
rough_path = os.path.join(base_dir, "Rock030_8K-PNG_Roughness.png")
normal_path = os.path.join(base_dir, "Rock030_8K-PNG_NormalGL.png")
disp_path = os.path.join(base_dir, "Rock030_8K-PNG_Displacement.png")

# Clear scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Create a subdivided grid for topography
bpy.ops.mesh.primitive_grid_add(size=10, x_subdivisions=400, y_subdivisions=400, location=(0, 0, 0))
grid = bpy.context.active_object
grid.name = "Topography"

# Unwrap to ensure UVs exist
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
bpy.ops.object.mode_set(mode='OBJECT')

# 1. Base topography using procedural Clouds
tex_terrain = bpy.data.textures.new("TerrainNoise", type='CLOUDS')
tex_terrain.noise_scale = 3.0
mod_disp_terrain = grid.modifiers.new("DisplaceTerrain", type='DISPLACE')
mod_disp_terrain.texture = tex_terrain
mod_disp_terrain.strength = 1.0

# 2. Add Subdivision to handle fine details
mod_subsurf = grid.modifiers.new("Subsurf", type='SUBSURF')
mod_subsurf.levels = 1
mod_subsurf.render_levels = 1

# 3. Micro displacement using the Rock Texture
if os.path.exists(disp_path):
    tex_rock_disp = bpy.data.textures.new("RockDisp", type='IMAGE')
    tex_rock_disp.image = bpy.data.images.load(disp_path)
    mod_disp_micro = grid.modifiers.new("DisplaceMicro", type='DISPLACE')
    mod_disp_micro.texture = tex_rock_disp
    mod_disp_micro.strength = 0.2
    mod_disp_micro.texture_coords = 'UV'

# Add smooth shading
bpy.ops.object.shade_smooth()

# Create material
mat = bpy.data.materials.new(name="RockMaterial")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links

# Clear default nodes
for node in nodes:
    nodes.remove(node)

output_node = nodes.new(type='ShaderNodeOutputMaterial')
output_node.location = (400, 0)

principled_node = nodes.new(type='ShaderNodeBsdfPrincipled')
principled_node.location = (0, 0)
links.new(principled_node.outputs['BSDF'], output_node.inputs['Surface'])

def load_texture(path, name):
    if os.path.exists(path):
        img = bpy.data.images.load(path)
        img_node = nodes.new(type='ShaderNodeTexImage')
        img_node.image = img
        img_node.name = name
        return img_node
    return None

tex_color = load_texture(color_path, "Color")
if tex_color:
    tex_color.location = (-400, 200)
    links.new(tex_color.outputs['Color'], principled_node.inputs['Base Color'])

tex_rough = load_texture(rough_path, "Roughness")
if tex_rough:
    tex_rough.location = (-400, 0)
    tex_rough.image.colorspace_settings.name = 'Non-Color'
    links.new(tex_rough.outputs['Color'], principled_node.inputs['Roughness'])

tex_normal = load_texture(normal_path, "Normal")
if tex_normal:
    tex_normal.location = (-600, -200)
    tex_normal.image.colorspace_settings.name = 'Non-Color'
    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
    normal_map_node.location = (-300, -200)
    links.new(tex_normal.outputs['Color'], normal_map_node.inputs['Color'])
    links.new(normal_map_node.outputs['Normal'], principled_node.inputs['Normal'])

# Assign material to object
if grid.data.materials:
    grid.data.materials[0] = mat
else:
    grid.data.materials.append(mat)

# Setup Camera
bpy.ops.object.camera_add(location=(6, -6, 5), rotation=(0.9, 0, 0.785))
cam = bpy.context.active_object
bpy.context.scene.camera = cam

# Setup Lighting
bpy.ops.object.light_add(type='SUN', location=(5, -5, 10), rotation=(0.5, 0.5, 0))
sun = bpy.context.active_object
sun.data.energy = 3.0

bpy.ops.object.light_add(type='POINT', location=(0, 0, 4))
point = bpy.context.active_object
point.data.energy = 500.0
point.data.color = (0.8, 0.9, 1.0)

# Render settings
scene = bpy.context.scene
try:
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
except TypeError:
    scene.render.engine = 'BLENDER_EEVEE'

scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.resolution_percentage = 100

# Save blend file
blend_path = r"C:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\prueba_topografia.blend"
bpy.ops.wm.save_as_mainfile(filepath=blend_path)

# Render
render_path = r"C:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\resultado_topografia.png"
scene.render.filepath = render_path
bpy.ops.render.render(write_still=True)
print(f"RENDER_COMPLETED: {render_path}")

