"""
Orquesta el flujo completo segun el MODO elegido por el usuario:

    foto   -> vectorizar la imagen ORIGINAL (con todo su detalle y fondo)
    logo   -> quitar fondo con IA + vectorizar con curvas limpias
    dibujo -> quitar fondo con IA + vectorizar como line art

No sabe nada de HTTP ni de que proveedor concreto esta detras de cada
adaptador: solo conoce las interfaces y los presets de config.
"""
from dataclasses import dataclass, field

from app.config import get_background_remover, get_modo, get_vectorizer


@dataclass
class ConversionResult:
    png_transparent: bytes
    svg: str
    modo: str = "foto"
    fondo_removido: bool = False
    # Avisos no fatales (p. ej. "la IA no estaba disponible, se uso el
    # metodo clasico"). Sirven para informar al usuario sin romper nada.
    avisos: list = field(default_factory=list)


def convert_image(image_bytes: bytes, mode: str | None = None,
                  remove_background: bool | None = None) -> ConversionResult:
    """
    Ejecuta el pipeline sobre una imagen segun el modo y devuelve el PNG
    transparente y el SVG resultante.

    `mode` elige el preset (foto | logo | dibujo). El parametro
    `remove_background` solo se usa si se pasa explicitamente: permite
    forzar el comportamiento ignorando el preset.
    """
    modo, preset = get_modo(mode)
    avisos: list = []

    # 1) Fondo: el preset del modo decide. En "foto" NO se toca para no
    #    destruir informacion (cielo, sombras, fondo) que luego el
    #    vectorizador necesita para reconstruir la imagen completa.
    if remove_background is None:
        quitar = bool(preset["quitar_fondo"])
    else:
        quitar = bool(remove_background)

    if quitar:
        remover = get_background_remover()
        try:
            fuente = remover.remove_background(image_bytes)
            # El quitafondo puede haber usado el metodo rapido por falta de
            # memoria: el usuario debe saber que el borde es menos preciso.
            if getattr(remover, "degradado", False):
                avisos.append(
                    getattr(remover, "motivo_degradado", "")
                    or "El fondo se quito con el metodo rapido por falta de memoria."
                )
        except Exception as exc:  # noqa: BLE001
            # Ultimo salvavidas: si TODO falla, vectorizamos el original
            # antes que devolver un error al usuario.
            fuente = image_bytes
            quitar = False
            avisos.append(
                f"No se pudo quitar el fondo automaticamente "
                f"({type(exc).__name__}); se vectorizo la imagen original."
            )
    else:
        fuente = image_bytes

    # 2) Vectorizacion con los parametros de calidad del modo.
    vectorizer = get_vectorizer(preset.get("vectorizer"))
    try:
        svg = vectorizer.vectorize(fuente)
        # El motor puede haber reducido la resolucion por falta de memoria:
        # eso baja la calidad y el usuario DEBE saberlo (antes se entregaba
        # en silencio y parecia que la app habia empeorado).
        if getattr(vectorizer, "degradado", False):
            avisos.append(
                getattr(vectorizer, "motivo_degradado", "")
                or "La calidad se redujo por falta de memoria del servidor."
            )
    except Exception as exc:  # noqa: BLE001
        # Red de seguridad final: nunca devolver un error 500 al usuario
        # si al menos uno de los dos motores puede producir un SVG.
        avisos.append(
            f"El motor de vectorizacion principal fallo "
            f"({type(exc).__name__}); se uso el metodo clasico."
        )
        from app.adapters.vectorizer import ContourVectorizer

        svg = ContourVectorizer().vectorize(fuente)

    return ConversionResult(
        png_transparent=fuente,
        svg=svg,
        modo=modo,
        fondo_removido=quitar,
        avisos=avisos,
    )
