import bpy
import os

filepath = os.path.join(os.path.dirname(bpy.data.filepath) if bpy.data.filepath else r"c:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM", "Blender_BIM_Ad_Generator.py")

with open(filepath, 'r', encoding='utf-8') as f:
    exec(f.read())

bpy.context.scene.render.engine = 'BLENDER_EEVEE'
bpy.context.scene.render.resolution_x = 640
bpy.context.scene.render.resolution_y = 360
bpy.context.scene.render.resolution_percentage = 100
bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = 60 # Solo 60 frames para hacerlo rápido (2 segundos)

output_path = r"c:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\Frames_Ad\Frame_"
bpy.context.scene.render.filepath = output_path
bpy.context.scene.render.image_settings.file_format = 'JPEG'

print("Iniciando renderizado de frames...")
bpy.ops.render.render(animation=True)
print(f"Renderizado completado en: {output_path}")
