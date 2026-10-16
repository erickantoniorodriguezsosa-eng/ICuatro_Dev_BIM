import bpy
import sys

blend_path = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno_PBR.blend"
output_fbx = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno_PBR.fbx"

try:
    bpy.ops.wm.open_mainfile(filepath=blend_path)
except Exception as e:
    print(f"Error abriendo blend: {e}")
    sys.exit(1)

print("Ajustando UVs (Estirar y centrar a los límites de la geometría)...")

for obj in bpy.context.scene.objects:
    if obj.type == 'MESH':
        mesh = obj.data
        
        # Asegurarnos de que tenga un mapa UV
        if not mesh.uv_layers:
            mesh.uv_layers.new(name="UVMap")
            
        uv_layer = mesh.uv_layers.active.data
        
        # Calcular los límites (Bounding Box) reales de los vértices en XY
        min_x = min([v.co.x for v in mesh.vertices])
        max_x = max([v.co.x for v in mesh.vertices])
        min_y = min([v.co.y for v in mesh.vertices])
        max_y = max([v.co.y for v in mesh.vertices])
        
        dx = max_x - min_x
        dy = max_y - min_y
        
        if dx == 0: dx = 0.0001
        if dy == 0: dy = 0.0001
        
        # Asignar coordenadas UV proyectando desde arriba (Top-Down)
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                v_idx = mesh.loops[loop_index].vertex_index
                co = mesh.vertices[v_idx].co
                
                # Mapeo de 0 a 1 (Estirado a los bordes de la imagen)
                u = (co.x - min_x) / dx
                v = (co.y - min_y) / dy
                
                uv_layer[loop_index].uv = (u, v)
        
        print(f"UVs ajustados para el objeto: {obj.name}")

# Guardar el proyecto .blend actualizado
bpy.ops.wm.save_mainfile()

# Asegurarse de estar en modo objeto
if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

# Re-exportar el FBX
print("Exportando FBX...")
bpy.ops.export_scene.fbx(
    filepath=output_fbx,
    use_selection=False,
    path_mode='COPY',
    embed_textures=True
)

print(f"ÉXITO: Guardado en {output_fbx}")

