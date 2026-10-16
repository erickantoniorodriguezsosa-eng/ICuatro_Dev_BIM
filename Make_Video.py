import cv2
import os
import glob
import numpy as np

image_folder = r'c:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\Frames_Ad'
video_name = r'c:\OD\OneDrive - Ingeniería Cuatro S.A\Documentos\_00.PRUEBAS.DYNAMO\ICuatro_Dev_BIM\Publicidad_BIM.mp4'

images = [img for img in glob.glob(os.path.join(image_folder, "Frame_*.jpg"))]
images.sort()

if not images:
    print("No hay imágenes para procesar.")
    exit()

def read_img_unicode(path):
    stream = open(path, "rb")
    bytes = bytearray(stream.read())
    numpyarray = np.asarray(bytes, dtype=np.uint8)
    return cv2.imdecode(numpyarray, cv2.IMREAD_UNCHANGED)

frame = read_img_unicode(images[0])
height, width, layers = frame.shape

fourcc = cv2.VideoWriter_fourcc(*'mp4v') # Codec para MP4
video = cv2.VideoWriter(video_name, fourcc, 24, (width, height)) # 24 FPS

for image in images:
    video.write(read_img_unicode(image))

video.release()
print(f"Video guardado exitosamente en: {video_name}")
