import bpy
import sys

def main():
    print("--- INICIANDO PRUEBA CON BLENDER ---")
    
    # Limpiar todos los objetos de la escena por defecto
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    
    # Crear un cubo en el centro
    bpy.ops.mesh.primitive_cube_add(size=2, enter_editmode=False, align='WORLD', location=(0, 0, 0))
    
    # Guardar el archivo en la ruta actual
    filepath = "prueba_blender.blend"
    
    try:
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        print(f"--- PRUEBA COMPLETADA: Archivo guardado como {filepath} ---")
    except Exception as e:
        print(f"--- ERROR AL GUARDAR: {e} ---")

if __name__ == "__main__":
    main()
