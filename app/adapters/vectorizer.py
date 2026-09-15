"""
Adaptadores de vectorización (raster -> SVG).

Mismo patrón que background_remover.py: una interfaz común, varias
implementaciones intercambiables por configuración.
"""
from abc import ABC, abstractmethod
import io
import subprocess
import tempfile
import os

import cv2
import numpy as np


class Vectorizer(ABC):
    """Contrato que debe cumplir cualquier motor de vectorización."""

    @abstractmethod
    def vectorize(self, image_bytes: bytes) -> str:
        """Recibe un PNG (con o sin alfa) y devuelve un string SVG."""
        raise NotImplementedError


class ContourVectorizer(Vectorizer):
    """
    Motor local basado en cuantización de color (k-means) + contornos de
    OpenCV. No requiere binarios externos ni licencias, corre en cualquier
    servidor con Python.

    Es el motor de arranque recomendado para el MVP: cubre bien logos,
    dibujos y gráficos de pocos colores. Para mayor fidelidad de curvas,
    la ruta de mejora natural es reemplazarlo por VTracerVectorizer o
    PotraceVectorizer (mismo contrato, un solo import cambia).
    """

    def __init__(self, n_colors: int = 6, min_area: float = 12.0,
                 simplify_epsilon: float = 0.6):
        self.n_colors = n_colors
        self.min_area = min_area
        self.simplify_epsilon = simplify_epsilon

    def vectorize(self, image_bytes: bytes) -> str:
        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        bgra = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
        if bgra is None:
            raise ValueError("No se pudo decodificar la imagen de entrada")

        if bgra.shape[2] == 4:
            alpha = bgra[:, :, 3]
            bgr = bgra[:, :, :3]
        else:
            bgr = bgra
            alpha = np.full(bgr.shape[:2], 255, dtype=np.uint8)

        h, w = bgr.shape[:2]
        mask = alpha > 10

        if mask.sum() == 0:
            return self._empty_svg(w, h)

        pixels = bgr[mask].reshape(-1, 3).astype(np.float32)
        k = min(self.n_colors, max(1, len(np.unique(pixels.astype(int), axis=0))))

        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, labels, centers = cv2.kmeans(
            pixels, k, None, criteria, 5, cv2.KMEANS_PP_CENTERS
        )
        centers = centers.astype(np.uint8)

        full_labels = np.full((h, w), -1, dtype=np.int32)
        full_labels[mask] = labels.flatten()

        path_elements = []
        for cluster_id in range(k):
            color_mask = np.where(full_labels == cluster_id, 255, 0).astype(np.uint8)
            if color_mask.sum() == 0:
                continue

            color_mask = cv2.morphologyEx(
                color_mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8)
            )
            contours, _ = cv2.findContours(
                color_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
            )

            b, g, r = centers[cluster_id]
            hex_color = f"#{r:02x}{g:02x}{b:02x}"

            path_data = []
            for cnt in contours:
                if cv2.contourArea(cnt) < self.min_area:
                    continue
                approx = cv2.approxPolyDP(cnt, self.simplify_epsilon, True)
                pts = approx.reshape(-1, 2)
                if len(pts) < 3:
                    continue
                d = "M " + " L ".join(f"{p[0]},{p[1]}" for p in pts) + " Z"
                path_data.append(d)

            if path_data:
                combined = " ".join(path_data)
                path_elements.append(
                    f'<path d="{combined}" fill="{hex_color}" fill-rule="evenodd"/>'
                )

        svg_body = "".join(path_elements)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">{svg_body}</svg>'
        )

    @staticmethod
    def _empty_svg(w: int, h: int) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}"></svg>'
        )


class VTracerVectorizer(Vectorizer):
    """
    Motor de vectorizacion de alta fidelidad via VTracer.

    Es el motor recomendado: genera curvas Bezier REALES (no poligonos
    rectos), soporta miles de colores y preserva degradados y sombras.
    Con la configuracion adecuada da buenos resultados tanto en logos
    planos como en fotografias complejas.

    Parametros que importan:
      - colormode: "color" (fotos/logos a color) o "binary" (line art)
      - color_precision (1-8): cuantos bits por canal. Mas alto = mas
        colores y mas detalle, pero SVG mas pesado. 8 = fotografia.
      - filter_speckle: descarta manchas de menos de N px (limpia ruido)
      - path_precision: decimales en las coordenadas. 8 = curvas suaves
      - corner_threshold (grados): angulo a partir del cual VTracer
        respeta una esquina dura en vez de suavizarla con curva
      - mode: "spline" (curvas suaves) o "polygon" (recto, mas liviano)
      - layer_difference: umbral para separar capas de color
    """

    def __init__(
        self,
        colormode: str = "color",
        color_precision: int = 6,
        filter_speckle: int = 4,
        path_precision: int = 8,
        corner_threshold: int = 60,
        mode: str = "spline",
        layer_difference: int = 16,
        hierarchical: str = "stacked",
    ):
        self.colormode = colormode
        self.color_precision = color_precision
        self.filter_speckle = filter_speckle
        self.path_precision = path_precision
        self.corner_threshold = corner_threshold
        self.mode = mode
        self.layer_difference = layer_difference
        self.hierarchical = hierarchical

    def vectorize(self, image_bytes: bytes) -> str:
        try:
            return self._vectorize_with_vtracer(image_bytes)
        except Exception as exc:  # noqa: BLE001
            # Salvavidas: si vtracer no esta instalado o falla, se usa el
            # motor de contornos para NO dejar al usuario sin resultado.
            print(f"[vtracer] fallo ({type(exc).__name__}: {exc}); usando contornos")
            return ContourVectorizer().vectorize(image_bytes)

    def _vectorize_with_vtracer(self, image_bytes: bytes) -> str:
        import vtracer

        # vtracer SOLO entiende PNG aunque el archivo lleve otra extension.
        # Por eso hay que decodificar y RECODIFICAR siempre a PNG real:
        # escribir los bytes originales con nombre .png rompe con JPEG
        # (y WebP, BMP...) y produce un 500 al no poder decodificar.
        #
        # Se acota el lado mayor: vtracer reserva memoria proporcional al
        # AREA de la imagen y en 512 MB una foto grande mata el proceso.
        # 1000 px conserva de sobra el detalle visual del vectorizado.
        png_bytes = self._to_png(image_bytes, max_lado=1000)

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "input.png")
            out_path = os.path.join(tmp, "output.svg")
            with open(in_path, "wb") as f:
                f.write(png_bytes)

            vtracer.convert_image_to_svg_py(
                in_path,
                out_path,
                colormode=self.colormode,
                color_precision=self.color_precision,
                filter_speckle=self.filter_speckle,
                path_precision=self.path_precision,
                corner_threshold=self.corner_threshold,
                mode=self.mode,
                layer_difference=self.layer_difference,
                hierarchical=self.hierarchical,
            )
            with open(out_path, "r") as f:
                return f.read()

    @staticmethod
    def _to_png(image_bytes: bytes, max_lado: int | None = None) -> bytes:
        """
        Normaliza cualquier imagen soportada (JPEG, PNG, WebP, BMP, GIF...)
        a PNG real en memoria. Lanza ValueError si no se puede decodificar.

        max_lado: si se indica, la imagen se reduce proporcionalmente para que
        su lado mayor no supere ese valor. vtracer construye estructuras por
        color sobre TODA la imagen, asi que su consumo de memoria crece muy
        rapido con el area: una foto de 1024x1024 puede pasar de los 512 MB
        del plan free y matar el proceso (el usuario veia 502 y luego
        "Failed to fetch"). Reducir antes de vectorizar es la diferencia
        entre un 200 y un crash.
        """
        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(
                "No se pudo decodificar la imagen de entrada "
                "(formato no soportado o archivo corrupto)"
            )

        # Asegurar 3 canales BGR + alfa opcional -> PNG BGRA/RGB valido.
        if img.ndim == 2:  # escala de grises
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGRA)

        if max_lado:
            h, w = img.shape[:2]
            if max(h, w) > max_lado:
                escala = max_lado / float(max(h, w))
                img = cv2.resize(
                    img, (max(1, int(w * escala)), max(1, int(h * escala))),
                    interpolation=cv2.INTER_AREA,
                )

        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise ValueError("No se pudo recodificar la imagen a PNG")
        return buf.tobytes()


class PotraceVectorizer(Vectorizer):
    """
    Adaptador de producción para Potrace. Mejor opción para imágenes
    binarias (firmas, sellos, line art en blanco y negro).

    Requiere el binario `potrace` instalado en el sistema
    (apt install potrace / brew install potrace).
    """

    def __init__(self, threshold: int = 128):
        self.threshold = threshold

    def vectorize(self, image_bytes: bytes) -> str:
        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
        _, binary = cv2.threshold(
            img, self.threshold, 255, cv2.THRESH_BINARY_INV
        )

        with tempfile.TemporaryDirectory() as tmp:
            bmp_path = os.path.join(tmp, "input.bmp")
            svg_path = os.path.join(tmp, "output.svg")
            cv2.imwrite(bmp_path, binary)

            subprocess.run(
                ["potrace", "-s", "-o", svg_path, bmp_path],
                check=True,
                capture_output=True,
            )
            with open(svg_path, "r") as f:
                return f.read()
