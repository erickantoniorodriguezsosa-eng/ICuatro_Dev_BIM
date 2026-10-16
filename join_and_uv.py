import bpy
import sys

blend_path = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno_PBR.blend"
output_fbx = r"C:\Users\erick.rodriguez\Downloads\Susan\terreno_PBR.fbx"

try:
    bpy.ops.wm.open_mainfile(filepath=blend_path)
except Exception as e:
    print(f"Error abriendo blend: {e}")
    sys.exit(1)

# Asegurar modo objeto
if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

# Seleccionar solo mallas
bpy.ops.object.select_all(action='DESELECT')
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']

if not meshes:
    print("No se encontraron mallas.")
    sys.exit(0)

print(f"Se encontraron {len(meshes)} partes de malla. Preparando para unir...")

# Seleccionar todas las mallas
for obj in meshes:
    obj.select_set(True)

bpy.context.view_layer.objects.active = meshes[0]

# Aplicar transformaciones (Escala, Rotación, Posición) para que el cálculo sea global y real
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# Unir todas las mallas en un solo objeto
bpy.ops.object.join()
merged_obj = bpy.context.active_object
merged_obj.name = "Terreno_Unido"
print("Mallas unidas exitosamente.")

mesh = merged_obj.data

# Asegurar UV map
if not mesh.uv_layers:
    mesh.uv_layers.new(name="UVMap")
uv_layer = mesh.uv_layers.active.data

# Calcular límites globales del objeto único
print("Calculando proyección Top-Down global...")
min_x = min([v.co.x for v in mesh.vertices])
max_x = max([v.co.x for v in mesh.vertices])
min_y = min([v.co.y for v in mesh.vertices])
max_y = max([v.co.y for v in mesh.vertices])

dx = max_x - min_x
dy = max_y - min_y

if dx == 0: dx = 0.0001
if dy == 0: dy = 0.0001

# Asignar coordenadas UV proyectando de 0 a 1 según la caja global
for poly in mesh.polygons:
    for loop_index in poly.loop_indices:
        v_idx = mesh.loops[loop_index].vertex_index
        co = mesh.vertices[v_idx].co
        
        # Mapeo global
        u = (co.x - min_x) / dx
        v = (co.y - min_y) / dy
        
        uv_layer[loop_index].uv = (u, v)

print("Coordenadas UV globales aplicadas.")

# Guardar y Exportar
bpy.ops.wm.save_mainfile()
print("Exportando nuevo FBX consolidado...")

bpy.ops.export_scene.fbx(
    filepath=output_fbx,
    use_selection=False,
    path_mode='COPY',
    embed_textures=True
)

print(f"COMPLETADO: {output_fbx}")

