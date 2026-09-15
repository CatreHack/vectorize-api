"""
Orquesta el flujo completo: quitar fondo -> vectorizar -> devolver
resultados. No sabe nada de HTTP ni de qué proveedor concreto está detrás
de cada adaptador — solo conoce las interfaces.
"""
from dataclasses import dataclass

from app.config import get_background_remover, get_vectorizer


@dataclass
class ConversionResult:
    png_transparent: bytes
    svg: str


def convert_image(image_bytes: bytes, remove_background: bool = True) -> ConversionResult:
    """
    Ejecuta el pipeline sobre una imagen y devuelve el PNG transparente
    y el SVG resultante.
    """
    if remove_background:
        remover = get_background_remover()
        png_transparent = remover.remove_background(image_bytes)
    else:
        png_transparent = image_bytes

    vectorizer = get_vectorizer()
    svg = vectorizer.vectorize(png_transparent)

    return ConversionResult(png_transparent=png_transparent, svg=svg)
