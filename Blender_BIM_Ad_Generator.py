import bpy
import bmesh
import random
import math

def clean_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()

def create_terrain():
    # Crear un plano para el valle
    bpy.ops.mesh.primitive_grid_add(size=150, x_subdivisions=80, y_subdivisions=80)
    terrain = bpy.context.active_object
    terrain.name = "Terrain_Valley"
    
    # Modificador Displace para las montañas
    mod = terrain.modifiers.new("Displace", 'DISPLACE')
    tex = bpy.data.textures.new("Terrain_Tex", 'CLOUDS')
    tex.noise_scale = 20.0
    mod.texture = tex
    mod.strength = 12.0
    
    bpy.ops.object.shade_smooth()
    
    # Material del terreno (tierra/pasto)
    mat = bpy.data.materials.new(name="Ground_Material")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (0.05, 0.2, 0.05, 1.0)
    terrain.data.materials.append(mat)
    return terrain

def create_river():
    # Plano del río
    bpy.ops.mesh.primitive_plane_add(size=160)
    river = bpy.context.active_object
    river.name = "River_Water"
    river.location.z = 2.0 # Nivel del agua
    
    # Material del agua
    mat = bpy.data.materials.new(name="Water_Material")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (0.0, 0.3, 0.6, 1.0)
        if 'Transmission Weight' in bsdf.inputs: # 4.0+
            bsdf.inputs['Transmission Weight'].default_value = 0.9
        elif 'Transmission' in bsdf.inputs: # 3.x
            bsdf.inputs['Transmission'].default_value = 0.9
        bsdf.inputs['Roughness'].default_value = 0.1
    river.data.materials.append(mat)
    return river

def create_bridge():
    # Crear la base del puente
    bpy.ops.mesh.primitive_cube_add(size=1)
    bridge_deck = bpy.context.active_object
    bridge_deck.name = "Bridge_Deck"
    bridge_deck.scale = (100, 8, 0.5)
    bridge_deck.location = (0, 0, 15)
    
    # Crear pilares del puente
    for i in range(-3, 4):
        bpy.ops.mesh.primitive_cylinder_add(radius=1.5, depth=20)
        pillar = bpy.context.active_object
        pillar.name = f"Bridge_Pillar_{i}"
        pillar.location = (i * 25, 0, 5)
        
    # Material BIM (Efecto estructura digital)
    mat = bpy.data.materials.new(name="BIM_Concrete")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (0.8, 0.8, 0.8, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.4
    bridge_deck.data.materials.append(mat)

def setup_camera_animation():
    # Crear Cámara
    bpy.ops.object.camera_add(location=(-60, -40, 40), rotation=(math.radians(60), 0, math.radians(-45)))
    cam = bpy.context.active_object
    cam.name = "Cinematic_Camera"
    bpy.context.scene.camera = cam
    
    # Animar la cámara para un flythrough
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 200
    
    # Keyframe inicial
    cam.location = (-70, -50, 50)
    cam.keyframe_insert(data_path="location", frame=1)
    
    # Keyframe final (sobrevolando el puente)
    cam.location = (40, 20, 20)
    cam.keyframe_insert(data_path="location", frame=200)
    
    # Luces
    bpy.ops.object.light_add(type='SUN', rotation=(math.radians(45), math.radians(45), 0))
    sun = bpy.context.active_object
    sun.data.energy = 6.0

if __name__ == "__main__":
    clean_scene()
    terrain = create_terrain()
    river = create_river()
    create_bridge()
    setup_camera_animation()
    print("Escena con Río, Puente BIM y Animación generada.")
