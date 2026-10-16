import numpy as np
from PIL import Image, ImageFilter
import sys
import os

def generate_pbr_maps(image_path, output_dir=None, max_size=8192):
    print(f"Abriendo imagen: {image_path}")
    Image.MAX_IMAGE_PIXELS = None
    try:
        img = Image.open(image_path)
    except Exception as e:
        print(f"Error al abrir la imagen: {e}")
        return

    # Redimensionar si es muy grande (Navisworks y PBR no necesitan 26K)
    if max(img.size) > max_size:
        print(f"La imagen es muy grande {img.size}, redimensionando a max {max_size}px...")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        print(f"Nuevo tamaño: {img.size}")
    
    # Asegurar modo RGB
    if img.mode != 'RGB':
        img = img.convert('RGB')
        
    print("Convirtiendo a escala de grises...")
    gray_img = img.convert('L')
    gray_arr = np.array(gray_img, dtype=np.float32) / 255.0

    if output_dir is None:
        output_dir = os.path.dirname(image_path)
    base_name = os.path.splitext(os.path.basename(image_path))[0]

    # --- 1. Displacement Map ---
    print("Generando mapa de Desplazamiento (Displacement)...")
    # Para desplazamiento usamos escala de grises directa o con contraste
    # Podríamos aplicar un ligero desenfoque para suavizar ruido
    disp_img = gray_img.filter(ImageFilter.GaussianBlur(radius=1.0))
    disp_path = os.path.join(output_dir, f"{base_name}_Displacement.jpg")
    disp_img.save(disp_path, quality=90)

    # --- 2. Roughness Map ---
    print("Generando mapa de Rugosidad (Roughness)...")
    # Invertimos el gris y limitamos el contraste
    # Valores oscuros en el original serán más rugosos (claros en roughness)
    rough_arr = 1.0 - gray_arr
    # Comprimir rango para que no haya áreas 100% lisas o 100% mate
    rough_arr = np.clip(rough_arr * 0.6 + 0.3, 0, 1)
    rough_img = Image.fromarray((rough_arr * 255).astype(np.uint8), mode='L')
    rough_path = os.path.join(output_dir, f"{base_name}_Roughness.jpg")
    rough_img.save(rough_path, quality=90)

    # --- 3. Normal Map ---
    print("Generando mapa de Normales (Normal)...")
    # Usamos operador Sobel
    strength = 2.0
    
    # Sobel X
    sobel_x = np.zeros_like(gray_arr)
    sobel_x[:, 1:-1] = (gray_arr[:, 2:] - gray_arr[:, :-2]) * strength
    
    # Sobel Y (DirectX: +Y es abajo. Imagen: fila 0 está arriba. Así que Y positivo hacia abajo)
    sobel_y = np.zeros_like(gray_arr)
    sobel_y[1:-1, :] = (gray_arr[2:, :] - gray_arr[:-2, :]) * strength
    
    # Z (hacia arriba)
    sobel_z = np.ones_like(gray_arr)
    
    # Normalizar longitud del vector (x,y,z)
    length = np.sqrt(sobel_x**2 + sobel_y**2 + sobel_z**2)
    
    norm_x = sobel_x / length
    norm_y = sobel_y / length
    norm_z = sobel_z / length
    
    # Mapear de [-1, 1] a [0, 255]
    r = ((norm_x + 1.0) * 0.5 * 255).astype(np.uint8)
    g = ((norm_y + 1.0) * 0.5 * 255).astype(np.uint8)
    b = (norm_z * 255).astype(np.uint8)
    
    normal_arr = np.stack([r, g, b], axis=2)
    normal_img = Image.fromarray(normal_arr, mode='RGB')
    normal_path = os.path.join(output_dir, f"{base_name}_Normal.jpg")
    normal_img.save(normal_path, quality=90)

    print("¡Mapas generados con éxito!")
    print(f" - {disp_path}")
    print(f" - {rough_path}")
    print(f" - {normal_path}")

if __name__ == '__main__':
    img_path = r"C:\Users\erick.rodriguez\Downloads\Susan\Ortofoto_Muro.jpg"
    generate_pbr_maps(img_path)
