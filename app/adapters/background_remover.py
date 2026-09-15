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


class SmartBackgroundRemover(BackgroundRemover):
    """
    Motor ALGORITMICO de alta calidad (sin IA, sin costo, sin RAM extra).

    Sustituye al flood-fill de esquinas, que era el origen de las
    distorsiones: aquel "adivinaba" el fondo por color desde las 4 esquinas
    y se colaba por cualquier zona clara conectada con el borde.

    Este motor usa GrabCut, el algoritmo de segmentacion de OpenCV:
      1. Estima que zona de la imagen es fondo y cual es objeto.
      2. Refina esa estimacion de forma iterativa (modelos de color
         foreground/background + corte de grafo).
      3. Produce una mascara con borde suave (feather) para que el recorte
         no se vea a sierra.

    Consume ~30-50 MB de RAM, asi que corre comodo en el plan free de
    Render (512 MB). Es el motor por defecto del prototipo: nunca falla
    por memoria y da resultados presentables en logos, dibujos, firmas y
    graficos sobre fondo claro.
    """

    def __init__(self, iterations: int = 3, margin_ratio: float = 0.06,
                 feather: int = 3):
        self.iterations = iterations
        self.margin_ratio = margin_ratio
        self.feather = feather

    def _auto_mask(self, img: np.ndarray) -> np.ndarray:
        """
        Construye la 'semilla' inicial de GrabCut (0=fondo seguro,
        1=objeto probable, 2=fondo probable, 3=objeto seguro).

        Estrategia: damos por fondo seguro un marco de borde (donde casi
        siempre esta el fondo) y por objeto probable todo lo que quede
        dentro. Ademas, si las esquinas son de color uniforme, las
        marcamos como fondo seguro reforzando la senal.
        """
        h, w = img.shape[:2]
        mask = np.full((h, w), cv2.GC_PR_FGD, dtype=np.uint8)

        # Marco exterior = fondo seguro.
        # El margen se acota para que NUNCA se coma toda la imagen: en
        # imagenes muy pequenas un 6% podria dejar cero pixeles de objeto
        # y la mascara saldria totalmente transparente. Se garantiza al
        # menos 1 px de objeto en cada eje.
        m = max(1, int(min(h, w) * self.margin_ratio))
        m = min(m, max(0, (min(h, w) - 1) // 2))

        if m > 0:
            mask[:m, :] = cv2.GC_BGD
            mask[-m:, :] = cv2.GC_BGD
            mask[:, :m] = cv2.GC_BGD
            mask[:, -m:] = cv2.GC_BGD

        # Rectangulo de trabajo para la inicializacion de GrabCut
        rect = (m, m, max(1, w - 2 * m), max(1, h - 2 * m))
        return mask, rect

    def remove_background(self, image_bytes: bytes) -> bytes:
        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar la imagen de entrada")

        h, w = img.shape[:2]

        # Trabajar en un tamano acotado: GrabCut es costoso y para pantalla
        # no hace falta mas resolucion. Mantiene proporcion y acelera.
        #
        # OJO: el coste de memoria de GrabCut crece MUY rapido con el area
        # (construye un grafo por pixel + modelos de color). Con 700 px de
        # lado, una imagen de 1024x1024 ya consumia >512 MB y el proceso
        # moria con OOM (el usuario veia 502 y despues "Failed to fetch").
        # 600 px es el punto dulce: comodo en 512 MB y visualmente
        # indistinguible para un vectorizado de pantalla.
        MAX_LADO = 600
        escala = 1.0
        if max(h, w) > MAX_LADO:
            escala = MAX_LADO / float(max(h, w))
            img_work = cv2.resize(
                img, (max(1, int(w * escala)), max(1, int(h * escala))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            img_work = img

        hw, ww = img_work.shape[:2]

        # Guarda de memoria: GrabCut construye un grafo del tamano de la
        # imagen. Si al proceso le queda muy poca RAM, es preferible una
        # mascara por color de esquinas (instantanea, ~0 MB extra) antes
        # que un OOM que mate el proceso y el usuario vea "Failed to fetch".
        if not _hay_ram_para_grabcut():
            print("[grabcut] RAM muy justa; usando mascara por esquinas")
            fg_mask = self._fallback_corner_mask(img_work)
        else:
            # Suavizado leve: ayuda a GrabCut a no engancharse con ruido/JPEG.
            blurred = cv2.bilateralFilter(img_work, 5, 40, 40)

            mask, rect = self._auto_mask(img_work)

            bgd = np.zeros((1, 65), np.float64)
            fgd = np.zeros((1, 65), np.float64)

            try:
                cv2.grabCut(
                    blurred, mask, rect, bgd, fgd,
                    self.iterations, cv2.GC_INIT_WITH_MASK,
                )
                # 0(BGD) y 2(PR_BGD) -> fondo ; 1(FGD) y 3(PR_FGD) -> objeto
                fg_mask = np.where(
                    (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0
                ).astype(np.uint8)
            except cv2.error:
                # Si GrabCut no puede converger, degradamos a umbral de fondo
                # por color de esquinas (mejor que devolver un error).
                fg_mask = self._fallback_corner_mask(img_work)

            del blurred, mask

        # Limpieza morfologica: quita motas sueltas y cierra huecos.
        kernel = np.ones((3, 3), np.uint8)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        # Conservar solo el componente conexo mas grande (el objeto
        # principal), descartando islas de ruido en el fondo.
        num, labels, stats, _ = cv2.connectedComponentsWithStats(fg_mask, 8)
        if num > 2:
            areas = stats[1:, cv2.CC_STAT_AREA]
            if len(areas) > 0:
                idx = int(np.argmax(areas)) + 1
                mayor = stats[idx, cv2.CC_STAT_AREA]
                # Solo descartamos si el mayor domina claramente, para no
                # borrar logos con varias piezas legitimas (letras sueltas).
                if mayor >= 0.55 * float(stats[1:, cv2.CC_STAT_AREA].sum()):
                    fg_mask = np.where(labels == idx, 255, 0).astype(np.uint8)

        # Suavizar el borde (feather) para evitar el corte "a sierra".
        if self.feather > 0:
            k = self.feather * 2 + 1
            fg_mask = cv2.GaussianBlur(fg_mask, (k, k), 0)
            # Realzar el centro del trazo tras el desenfoque.
            fg_mask = cv2.normalize(fg_mask, None, 0, 255, cv2.NORM_MINMAX)

        if escala != 1.0:
            fg_mask = cv2.resize(fg_mask, (w, h), interpolation=cv2.INTER_LINEAR)

        alpha = fg_mask.astype(np.uint8)
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = alpha

        ok, buf = cv2.imencode(".png", bgra)
        if not ok:
            raise RuntimeError("No se pudo codificar el PNG de salida")
        return buf.tobytes()

    def _fallback_corner_mask(self, img: np.ndarray) -> np.ndarray:
        """Respaldo minimo: marca como fondo lo que se parezca a las esquinas."""
        h, w = img.shape[:2]
        corners = np.array(
            [img[0, 0], img[0, w - 1], img[h - 1, 0], img[h - 1, w - 1]],
            dtype=np.float32,
        )
        bg_color = corners.mean(axis=0)
        dist = np.linalg.norm(img.astype(np.float32) - bg_color, axis=2)
        return np.where(dist > 60, 255, 0).astype(np.uint8)


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
        # Guarda de memoria: en el plan free de Render (512 MB) cargar la
        # red neuronal provoca un OOM que MATA el proceso (el usuario ve
        # "Failed to fetch"). Antes de intentarlo siquiera, comprobamos
        # que haya RAM disponible: si no, vamos directo al motor
        # algoritmico sin gastar 30+ segundos muriendo.
        if not _hay_ram_suficiente():
            if not self.fallback:
                raise RuntimeError(
                    "Memoria insuficiente para el motor de IA "
                    "(se requiere un plan con mas RAM)."
                )
            print("[rembg] RAM insuficiente; usando motor algoritmico (GrabCut)")
            return get_fallback_remover().remove_background(image_bytes)

        try:
            return self._remove_with_ai(image_bytes)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de IA cae al clasico
            if not self.fallback:
                raise
            print(f"[rembg] fallo ({type(exc).__name__}: {exc}); usando motor algoritmico")
            return get_fallback_remover().remove_background(image_bytes)


# --- Seleccion automatica entre IA y motor algoritmico --------------------
# El motor de respaldo es un singleton: asi GrabCut no se reinstancia en
# cada peticion y el comportamiento es predecible.
_FALLBACK_REMOVER = None

# RAM minima (MB) que se le exige al proceso para intentar cargar la IA.
# u2netp + onnxruntime + opencv necesitan ~350-450 MB; en 512 MB el
# proceso muere. Se pide un margen para no quedar al limite.
RAM_MINIMA_MB = 1400

# RAM minima (MB) que necesita el motor algoritmico GrabCut. Es mucho mas
# liviano que la IA, pero tampoco es gratis: con imagenes grandes el grafo
# que construye puede pasar de 400 MB. Por debajo de este suelo conviene
# degradar a un metodo aun mas barato antes que morir con OOM.
RAM_MINIMA_GRABCUT_MB = 380


def get_fallback_remover() -> BackgroundRemover:
    """Devuelve el motor algoritmico de respaldo (singleton)."""
    global _FALLBACK_REMOVER
    if _FALLBACK_REMOVER is None:
        _FALLBACK_REMOVER = SmartBackgroundRemover()
    return _FALLBACK_REMOVER


def _hay_ram_suficiente() -> bool:
    """
    Comprueba si el proceso tiene RAM de sobra para cargar la red neuronal.

    Lee el limite de memoria del cgroup (que es lo que Render aplica) y,
    si no esta disponible, cae a la memoria total del sistema. Si no se
    puede determinar nada, se devuelve True para no bloquear la IA en un
    entorno donde si podria funcionar.
    """
    try:
        # Limite del cgroup v2 (Render lo usa)
        for ruta in ("/sys/fs/cgroup/memory.max",
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            try:
                with open(ruta, "r") as fh:
                    valor = fh.read().strip()
                if valor and valor != "max":
                    limite_mb = int(valor) / (1024 * 1024)
                    return limite_mb >= RAM_MINIMA_MB
            except (OSError, ValueError):
                continue

        # Sin cgroup: usar memoria total del sistema
        with open("/proc/meminfo", "r") as fh:
            for linea in fh:
                if linea.startswith("MemTotal:"):
                    total_mb = int(linea.split()[1]) / 1024
                    return total_mb >= RAM_MINIMA_MB
    except Exception:  # noqa: BLE001
        pass
    return True


def _hay_ram_para_grabcut() -> bool:
    """
    Comprueba si al proceso le queda RAM suficiente para GrabCut.

    A diferencia de _hay_ram_suficiente() (que mira el LIMITE del cgroup para
    decidir si cabe la red neuronal), aqui interesa la memoria DISPONIBLE en
    este instante: GrabCut no falla por el limite del plan, falla cuando el
    proceso ya tiene la memoria tomada por otras peticiones o por el modelo.
    """
    try:
        with open("/proc/meminfo", "r") as fh:
            for linea in fh:
                if linea.startswith("MemAvailable:"):
                    disponible_mb = int(linea.split()[1]) / 1024
                    return disponible_mb >= RAM_MINIMA_GRABCUT_MB
    except Exception:  # noqa: BLE001
        pass
    # Si no se puede medir, no bloqueamos el camino normal.
    return True


def _permite_ia() -> bool:
    """
    Interruptor maestro de la IA, controlado por variable de entorno.

    En el plan free el prototipo NO puede cargar la red neuronal, asi que
    por defecto (sin variable) la IA se omite y se usa GrabCut.

    El dia que se pase a un plan con mas RAM, basta con definir en Render:
        USE_AI=true
    y la IA entra en servicio SIN tocar una linea de codigo.
    """
    import os

    valor = os.environ.get("USE_AI", "").strip().lower()
    if valor in ("1", "true", "yes", "si", "on"):
        return True
    if valor in ("0", "false", "no", "off"):
        return False
    # Sin definir: decidir segun la RAM real disponible.
    return _hay_ram_suficiente()


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
