"""
Punto unico de configuracion para elegir que motor usa cada etapa del
pipeline. Este es el unico archivo que hay que tocar para cambiar de
proveedor: nada mas en la aplicacion conoce el nombre de un proveedor
concreto.
"""
import os

from app.adapters.background_remover import (
    BackgroundRemover,
    FloodFillBackgroundRemover,
    PhotoRoomBackgroundRemover,
    RemoveBgBackgroundRemover,
)
from app.adapters.vectorizer import (
    Vectorizer,
    ContourVectorizer,
    VTracerVectorizer,
    PotraceVectorizer,
)

# Valores posibles: "floodfill" (local, gratis) | "photoroom" | "removebg"
BACKGROUND_REMOVER_ENGINE = os.getenv("BACKGROUND_REMOVER_ENGINE", "floodfill")

# Valores posibles: "contour" (local, gratis) | "vtracer" | "potrace"
VECTORIZER_ENGINE = os.getenv("VECTORIZER_ENGINE", "contour")

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
RESULT_TTL_HOURS = int(os.getenv("RESULT_TTL_HOURS", "24"))

# CORS: lista separada por comas de origenes permitidos. "*" = abierto.
# En produccion, poner el dominio del frontend:
#   ALLOWED_ORIGINS=https://catrehack.github.io,https://tudominio.shop
_allowed = os.getenv("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _allowed == "*" else [o.strip() for o in _allowed.split(",") if o.strip()]


def get_background_remover() -> BackgroundRemover:
    if BACKGROUND_REMOVER_ENGINE == "photoroom":
        api_key = os.environ["PHOTOROOM_API_KEY"]
        return PhotoRoomBackgroundRemover(api_key)
    if BACKGROUND_REMOVER_ENGINE == "removebg":
        api_key = os.environ["REMOVEBG_API_KEY"]
        return RemoveBgBackgroundRemover(api_key)
    return FloodFillBackgroundRemover()


def get_vectorizer() -> Vectorizer:
    if VECTORIZER_ENGINE == "vtracer":
        return VTracerVectorizer()
    if VECTORIZER_ENGINE == "potrace":
        return PotraceVectorizer()
    return ContourVectorizer()
