"""
Test de humo REAL del pipeline: genera una imagen de prueba (un logo
simple) y la pasa por convert_image() para verificar que devuelve un SVG
valido y un PNG transparente. No usa mocks.
"""
import io
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, ".")

from app.services.pipeline import convert_image


def make_test_logo() -> bytes:
    """Un circulo rojo con un cuadrado azul sobre fondo blanco uniforme."""
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([80, 80, 320, 320], fill=(220, 40, 40))
    d.rectangle([160, 160, 240, 240], fill=(30, 60, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    src = make_test_logo()
    print(f"Imagen de prueba: {len(src)} bytes (400x400)")

    result = convert_image(src)
    svg = result.svg
    png = result.png_transparent

    print(f"SVG: {len(svg)} bytes")
    print(f"PNG transparente: {len(png)} bytes")

    checks = []

    # 1. El SVG es un SVG bien formado
    ok = svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    checks.append(("SVG bien formado", ok))
    checks.append(("SVG tiene viewBox 400x400", 'viewBox="0 0 400 400"' in svg))
    checks.append(("SVG tiene paths", "<path" in svg))

    # 2. El SVG referencia los colores del logo original
    has_red = any(c in svg.lower() for c in ["#dc2828", "#d82828", "#dc2827", "#e02828"])
    has_blue = any(c in svg.lower() for c in ["#1e3cc8", "#1e3cc9", "#1e3cc7"])
    checks.append(("SVG conserva el rojo del logo", has_red))
    checks.append(("SVG conserva el azul del logo", has_blue))

    # 3. El PNG de salida tiene canal alfa y esquinas transparentes
    out = Image.open(io.BytesIO(png))
    checks.append(("PNG tiene canal alfa", out.mode == "RGBA"))
    arr = np.array(out)
    if arr.shape[2] == 4:
        corners = [arr[0, 0, 3], arr[0, -1, 3], arr[-1, 0, 3], arr[-1, -1, 3]]
        checks.append(("Esquinas transparentes (fondo removido)", all(c == 0 for c in corners)))
        center_alpha = arr[200, 200, 3]
        checks.append(("Centro opaco (logo conservado)", center_alpha > 200))
    checks.append(("PNG decodifica correctamente", out.size == (400, 400)))

    print("\n--- RESULTADOS ---")
    allok = True
    for name, ok in checks:
        print(f"  [{'OK ' if ok else 'FALLA'}] {name}")
        allok = allok and ok

    print(f"\nSVG (primeros 300 chars):\n{svg[:300]}")
    print("\n===> " + ("TODO OK" if allok else "HAY FALLOS"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
