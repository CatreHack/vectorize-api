"""
Adaptadores de remoción de fondo.

Todos implementan la misma interfaz (BackgroundRemover), así que el resto
de la aplicación nunca depende de un proveedor concreto. Cambiar de motor
es cuestión de una línea en config.py, no de reescribir código.
"""
from abc import ABC, abstractmethod
import io

import cv2
import numpy as np
from PIL import Image


class BackgroundRemover(ABC):
    """Contrato que debe cumplir cualquier motor de remoción de fondo."""

    @abstractmethod
    def remove_background(self, image_bytes: bytes) -> bytes:
        """
        Recibe una imagen (bytes, cualquier formato soportado por PIL/OpenCV)
        y devuelve un PNG en bytes con canal alfa (fondo transparente).
        """
        raise NotImplementedError


class FloodFillBackgroundRemover(BackgroundRemover):
    """
    Motor local, sin costo y sin dependencias externas.

    Asume que el fondo es de color relativamente uniforme (caso típico de
    logos, dibujos escaneados y firmas sobre papel/fondo liso) y lo elimina
    haciendo flood-fill desde las cuatro esquinas de la imagen.

    No sirve para fotos con fondos complejos, pero es exactamente el motor
    correcto para el alcance del MVP (logos, dibujos, firmas, gráficos
    simples) y no tiene costo variable por imagen.
    """

    def __init__(self, tolerance: int = 20):
        self.tolerance = tolerance

    def remove_background(self, image_bytes: bytes) -> bytes:
        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar la imagen de entrada")

        h, w = img.shape[:2]
        bg_mask = np.zeros((h, w), dtype=np.uint8)

        corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
        for row, col in corners:
            ff_mask = np.zeros((h + 2, w + 2), np.uint8)
            # Se pasa una copia propia por iteracion: cv2.floodFill modifica
            # la imagen in-place y reutilizar el buffer entre esquinas puede
            # contaminar la mascara del flood-fill siguiente.
            img_work = img.copy()
            cv2.floodFill(
                img_work,
                ff_mask,
                (col, row),
                0,
                loDiff=(self.tolerance,) * 3,
                upDiff=(self.tolerance,) * 3,
                flags=4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8),
            )
            bg_mask = cv2.bitwise_or(bg_mask, ff_mask[1:-1, 1:-1])

        # Suavizar bordes de la máscara para evitar artefactos duros
        bg_mask = cv2.morphologyEx(
            bg_mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
        )

        alpha = np.where(bg_mask > 0, 0, 255).astype(np.uint8)
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = alpha

        ok, buf = cv2.imencode(".png", bgra)
        if not ok:
            raise RuntimeError("No se pudo codificar el PNG de salida")
        return buf.tobytes()


class RembgBackgroundRemover(BackgroundRemover):
    """
    Motor con IA (red neuronal U^2-Net) via la libreria `rembg`.

    A diferencia del flood-fill (que adivina por color desde las esquinas),
    este motor ENTIENDE que hay una persona/objeto en la imagen y lo separa
    del fondo, incluso con fondos complejos (playa, ciudad, interior),
    pelo suelto, bordes difusos o tonos claros parecidos al fondo.

    Modelos disponibles (se descargan una sola vez y quedan cacheados):
      - "u2netp"  ~4.7 MB  -> liviano, entra comodo en el plan free de Render
      - "u2net"   ~176 MB  -> maxima calidad, requiere mas RAM
      - "isnet-general-use" -> muy buena calidad, tamano intermedio

    Nunca deja la peticion sin respuesta: si la IA no esta disponible
    (modelo no descargado, sin memoria, etc.) cae al flood-fill clasico.
    """

    def __init__(self, model_name: str = "u2netp", fallback: bool = True):
        self.model_name = model_name
        self.fallback = fallback

    def _remove_with_ai(self, image_bytes: bytes) -> bytes:
        from rembg import remove, new_session  # import local: pesa mucho

        # Reutilizar la sesion entre peticiones evita recargar el modelo
        # (que es lo caro) en cada conversion.
        session = _get_rembg_session(self.model_name)
        salida = remove(image_bytes, session=session)
        if not salida:
            raise RuntimeError("rembg devolvio una imagen vacia")
        return salida

    def remove_background(self, image_bytes: bytes) -> bytes:
        try:
            return self._remove_with_ai(image_bytes)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de IA cae al clasico
            if not self.fallback:
                raise
            print(f"[rembg] fallo ({type(exc).__name__}: {exc}); usando flood-fill")
            return FloodFillBackgroundRemover().remove_background(image_bytes)


# Cache de sesiones de rembg: el modelo se carga una vez por proceso.
_REMBG_SESSIONS: dict = {}


def _get_rembg_session(model_name: str):
    """Devuelve (creando si hace falta) la sesion de rembg para un modelo."""
    if model_name not in _REMBG_SESSIONS:
        from rembg import new_session

        _REMBG_SESSIONS[model_name] = new_session(model_name)
    return _REMBG_SESSIONS[model_name]


class PhotoRoomBackgroundRemover(BackgroundRemover):
    """
    Adaptador de producción para PhotoRoom API (~$0.01-0.02 por imagen).
    Requiere PHOTOROOM_API_KEY. Se activa solo cambiando la config;
    el resto del sistema no cambia.
    """

    ENDPOINT = "https://sdk.photoroom.com/v1/segment"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def remove_background(self, image_bytes: bytes) -> bytes:
        import requests  # import local: solo se necesita si se usa este adaptador

        response = requests.post(
            self.ENDPOINT,
            headers={"x-api-key": self.api_key},
            files={"image_file": ("image.png", image_bytes)},
            timeout=30,
        )
        response.raise_for_status()
        return response.content


class RemoveBgBackgroundRemover(BackgroundRemover):
    """Adaptador de producción para remove.bg. Requiere REMOVEBG_API_KEY."""

    ENDPOINT = "https://api.remove.bg/v1.0/removebg"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def remove_background(self, image_bytes: bytes) -> bytes:
        import requests

        response = requests.post(
            self.ENDPOINT,
            headers={"X-Api-Key": self.api_key},
            files={"image_file": ("image.png", image_bytes)},
            data={"size": "auto"},
            timeout=30,
        )
        response.raise_for_status()
        return response.content
