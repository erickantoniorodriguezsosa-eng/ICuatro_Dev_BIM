import bpy
import bmesh
import math

def clean_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()

def setup_cycles_engine():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    # Configuración de calidad
    scene.cycles.samples = 128
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.fps = 24
    scene.render.use_motion_blur = True

def setup_cinematic_lighting():
    # Cielo físico (Nishita) para realismo
    world = bpy.context.scene.world
    world.use_nodes = True
    node_tree = world.node_tree
    node_tree.nodes.clear()
    
    node_sky = node_tree.nodes.new(type="ShaderNodeTexSky")
    # node_sky.sky_type = 'NISHITA'
    node_sky.sun_elevation = math.radians(25) # Atardecer/Amanecer
    node_sky.sun_rotation = math.radians(135)
    
    node_bg = node_tree.nodes.new(type="ShaderNodeBackground")
    node_out = node_tree.nodes.new(type="ShaderNodeOutputWorld")
    
    node_tree.links.new(node_sky.outputs[0], node_bg.inputs[0])
    node_tree.links.new(node_bg.outputs[0], node_out.inputs[0])
    
    # Niebla volumétrica para atmósfera cinematográfica
    bpy.ops.mesh.primitive_cube_add(size=500)
    fog = bpy.context.active_object
    fog.name = "Atmosphere_Fog"
    fog.display_type = 'BOUNDS'
    
    mat = bpy.data.materials.new(name="Volumetric_Fog")
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    node_vol = mat.node_tree.nodes.new(type="ShaderNodeVolumeScatter")
    node_vol.inputs['Density'].default_value = 0.002
    node_vol.inputs['Anisotropy'].default_value = 0.7
    node_out = mat.node_tree.nodes.new(type="ShaderNodeOutputMaterial")
    mat.node_tree.links.new(node_vol.outputs[0], node_out.inputs[1]) # Link to Volume
    fog.data.materials.append(mat)

def create_photorealistic_river():
    bpy.ops.mesh.primitive_plane_add(size=300)
    river = bpy.context.active_object
    river.name = "River"
    
    # Material procedural de agua realista
    mat = bpy.data.materials.new(name="Water_Real")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs['Base Color'].default_value = (0.01, 0.05, 0.1, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.02
    if 'Transmission' in bsdf.inputs:
        bsdf.inputs['Transmission'].default_value = 1.0 # 3.x
    elif 'Transmission Weight' in bsdf.inputs:
        bsdf.inputs['Transmission Weight'].default_value = 1.0 # 4.x
        
    bsdf.inputs['IOR'].default_value = 1.333
    
    # Olas con ruido
    noise = nodes.new(type="ShaderNodeTexNoise")
    noise.inputs['Scale'].default_value = 25.0
    bump = nodes.new(type="ShaderNodeBump")
    bump.inputs['Distance'].default_value = 0.1
    bump.inputs['Strength'].default_value = 0.2
    
    links.new(noise.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    
    river.data.materials.append(mat)

def create_bim_bridge():
    # Tablero principal
    bpy.ops.mesh.primitive_cube_add(size=1)
    bridge = bpy.context.active_object
    bridge.name = "Bridge_Structure"
    bridge.scale = (200, 12, 1)
    bridge.location = (0, 0, 20)
    
    # Pilares
    for i in range(-2, 3):
        bpy.ops.mesh.primitive_cylinder_add(radius=2, depth=25)
        pilar = bpy.context.active_object
        pilar.name = f"Pilar_{i}"
        pilar.location = (i * 40, 0, 8)
        # Parent to bridge for unified material
        pilar.parent = bridge
    
    # Material Híbrido (Holograma BIM -> Concreto Real)
    mat = bpy.data.materials.new(name="BIM_Transition")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    
    # Concreto
    bsdf.inputs['Base Color'].default_value = (0.3, 0.3, 0.3, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.8
    
    # Mezcla de emisión (Holograma BIM)
    mix = nodes.new(type="ShaderNodeMixShader")
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs['Color'].default_value = (0.0, 0.5, 1.0, 1.0)
    emission.inputs['Strength'].default_value = 5.0
    
    # Wireframe effect
    wire = nodes.new(type="ShaderNodeWireframe")
    
    # Output
    out = nodes.get("Material Output")
    links.new(bsdf.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(wire.outputs[0], mix.inputs[0])
    links.new(mix.outputs[0], out.inputs[0])
    
    # Animar la transición del Wireframe a sólido
    mix.inputs[0].default_value = 1.0 # 100% BIM Wireframe
    mix.inputs[0].keyframe_insert(data_path="default_value", frame=50)
    mix.inputs[0].default_value = 0.0 # 100% Concreto
    mix.inputs[0].keyframe_insert(data_path="default_value", frame=120)
    
    bridge.data.materials.append(mat)
    for child in bridge.children:
        child.data.materials.append(mat)

def setup_camera():
    bpy.ops.object.camera_add(location=(-80, -30, 10), rotation=(math.radians(80), 0, math.radians(-30)))
    cam = bpy.context.active_object
    cam.name = "Cinematic_Cam"
    bpy.context.scene.camera = cam
    
    # Animación fluida sobre el agua
    cam.keyframe_insert(data_path="location", frame=1)
    cam.location = (40, 20, 15)
    cam.keyframe_insert(data_path="location", frame=250)
    bpy.context.scene.frame_end = 250

def add_placeholders():
    # Dron Placeholder
    bpy.ops.mesh.primitive_monkey_add(size=2, location=(-20, -10, 25))
    drone = bpy.context.active_object
    drone.name = "PLACEHOLDER_Drone_Aqui"
    
    # Ingeniero Placeholder
    bpy.ops.mesh.primitive_cube_add(size=3, location=(-100, -50, 5))
    eng = bpy.context.active_object
    eng.name = "PLACEHOLDER_Ingeniero_PC"

if __name__ == "__main__":
    clean_scene()
    setup_cycles_engine()
    setup_cinematic_lighting()
    create_photorealistic_river()
    create_bim_bridge()
    setup_camera()
    add_placeholders()
    
    # Guardar el archivo .blend automáticamente
    blend_path = r"c:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\BIM_Cinematic_Production.blend"
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"Proyecto de alta calidad guardado en: {blend_path}")
