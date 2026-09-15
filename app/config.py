"""
Punto unico de configuracion para elegir que motor usa cada etapa del
pipeline. Este es el unico archivo que hay que tocar para cambiar de
proveedor: nada mas en la aplicacion conoce el nombre de un proveedor
concreto.

Aqui viven tambien los PRESETS POR MODO: la logica de negocio que decide
si a una imagen se le quita el fondo y con cuanta fidelidad se vectoriza.

    foto   -> NO se quita el fondo. Se vectoriza TODO el detalle.
    logo   -> SI se quita el fondo. Curvas limpias y pocos colores.
    dibujo -> SI se quita el fondo. Trazos nitidos como line art.
"""
import os

from app.adapters.background_remover import (
    BackgroundRemover,
    FloodFillBackgroundRemover,
    SmartBackgroundRemover,
    RembgBackgroundRemover,
    PhotoRoomBackgroundRemover,
    RemoveBgBackgroundRemover,
)
from app.adapters.vectorizer import (
    Vectorizer,
    ContourVectorizer,
    VTracerVectorizer,
    PotraceVectorizer,
)

# Valores posibles: "rembg" (IA, local, gratis) | "floodfill" (local) |
#                   "photoroom" | "removebg" (APIs de pago)
BACKGROUND_REMOVER_ENGINE = os.getenv("BACKGROUND_REMOVER_ENGINE", "rembg")

# Valores posibles: "vtracer" (alta fidelidad) | "contour" (basico) | "potrace"
VECTORIZER_ENGINE = os.getenv("VECTORIZER_ENGINE", "vtracer")

# Modelo de IA de rembg. "u2netp" (~4.7 MB) es el liviano que entra sin
# problemas en el plan free de Render; "u2net" (~176 MB) da mas calidad
# pero exige mas memoria. Se puede subir sin tocar codigo con la env var
# REMBG_MODEL=u2net.
REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp")

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
RESULT_TTL_HOURS = int(os.getenv("RESULT_TTL_HOURS", "24"))

# CORS: lista separada por comas de origenes permitidos. "*" = abierto.
# En produccion, poner el dominio del frontend:
#   ALLOWED_ORIGINS=https://catrehack.github.io,https://tudominio.shop
_allowed = os.getenv("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _allowed == "*" else [o.strip() for o in _allowed.split(",") if o.strip()]


# ---------------------------------------------------------------------------
# Presets por modo
# ---------------------------------------------------------------------------
# Cada modo define: si se quita el fondo y con que motores/tunings se procesa.
#
# Sobre los parametros de vtracer:
#   color_precision 1-8  : mas alto = mas colores y detalle (8 = foto)
#   filter_speckle       : descarta manchas menores a N px (limpia ruido)
#   path_precision       : decimales de las coordenadas (8 = curvas suaves)
#   corner_threshold     : angulo minimo para respetar una esquina dura
#   layer_difference     : umbral de separacion entre capas de color
#   mode                 : "spline" (curvas) | "polygon" (recto)
MODOS = {
    "foto": {
        "etiqueta": "Foto",
        "descripcion": "Fotos y retratos. Conserva todo el detalle y el fondo.",
        "quitar_fondo": False,
        # Maxima fidelidad: 8 bits por canal (~16M colores), sin descartar
        # manchas (filter_speckle=2 solo elimina ruido de compresion),
        # umbral de capa bajo para no perder degradados suaves.
        "vectorizer": {
            "color_precision": 8,
            "filter_speckle": 2,
            "path_precision": 8,
            "corner_threshold": 60,
            "layer_difference": 8,
            "mode": "spline",
        },
    },
    "logo": {
        "etiqueta": "Logo",
        "descripcion": "Logos y marcas. Quita el fondo y deja curvas limpias.",
        "quitar_fondo": True,
        # Colores planos: basta con 6 bits; se descartan manchas pequenas
        # (>=16 px) para que el logo quede limpio, y el umbral de capa alto
        # agrupa tonos parecidos en un mismo color plano.
        "vectorizer": {
            "color_precision": 6,
            "filter_speckle": 16,
            "path_precision": 3,
            "corner_threshold": 60,
            "layer_difference": 48,
            "mode": "spline",
        },
    },
    "dibujo": {
        "etiqueta": "Dibujo",
        "descripcion": "Ilustraciones y line art. Trazos nitidos, fondo limpio.",
        "quitar_fondo": True,
        # Trazos finos: poca precision de color (4 bits, tonos planos),
        # se conservan detalles pequenos (>=4 px) y la esquina se respeta
        # a partir de 45 grados para no redondear el line art.
        "vectorizer": {
            "colormode": "binary",
            "color_precision": 4,
            "filter_speckle": 4,
            "path_precision": 4,
            "corner_threshold": 45,
            "layer_difference": 32,
            "mode": "spline",
        },
    },
}

MODO_POR_DEFECTO = "foto"


def get_modo(mode: str | None) -> tuple[str, dict]:
    """
    Normaliza y valida el modo pedido. Devuelve (nombre_modo, preset).

    Si el modo no existe se usa el de por defecto en vez de fallar: es
    preferible dar un resultado razonable que un error al usuario.
    """
    if mode:
        clave = str(mode).strip().lower()
        # Aceptar variantes comunes sin tildes/plurales
        alias = {
            "photo": "foto", "fotos": "foto", "photograph": "foto",
            "logos": "logo", "marca": "logo", "brand": "logo",
            "drawing": "dibujo", "dibujos": "dibujo", "lineart": "dibujo",
            "ilustracion": "dibujo",
        }
        clave = alias.get(clave, clave)
        if clave in MODOS:
            return clave, MODOS[clave]
    return MODO_POR_DEFECTO, MODOS[MODO_POR_DEFECTO]


def get_background_remover() -> BackgroundRemover:
    if BACKGROUND_REMOVER_ENGINE == "photoroom":
        api_key = os.environ["PHOTOROOM_API_KEY"]
        return PhotoRoomBackgroundRemover(api_key)
    if BACKGROUND_REMOVER_ENGINE == "removebg":
        api_key = os.environ["REMOVEBG_API_KEY"]
        return RemoveBgBackgroundRemover(api_key)
    if BACKGROUND_REMOVER_ENGINE == "floodfill":
        return FloodFillBackgroundRemover()
    if BACKGROUND_REMOVER_ENGINE == "smart":
        return SmartBackgroundRemover()
    # Por defecto: el motor de IA cuando el entorno lo permite (plan con
    # RAM suficiente / USE_AI=true) y, si no, el motor algoritmico
    # GrabCut. RembgBackgroundRemover ya decide esto internamente, de modo
    # que en el plan free el prototipo responde siempre y el dia que se
    # escale basta con poner USE_AI=true (sin tocar codigo).
    return RembgBackgroundRemover(model_name=REMBG_MODEL)


def get_vectorizer(parametros: dict | None = None) -> Vectorizer:
    """Crea el vectorizador, opcionalmente con los parametros de un modo."""
    parametros = parametros or {}
    if VECTORIZER_ENGINE == "contour":
        return ContourVectorizer()
    if VECTORIZER_ENGINE == "potrace":
        return PotraceVectorizer()
    if VECTORIZER_ENGINE == "vtracer":
        return VTracerVectorizer(**parametros)
    # Motor por defecto: alta fidelidad
    return VTracerVectorizer(**parametros)
