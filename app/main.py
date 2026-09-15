import uuid
import base64
import os

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import MAX_UPLOAD_SIZE_MB, ALLOWED_ORIGINS
from app.services.pipeline import convert_image
from app.storage import get_storage

app = FastAPI(title="Vectorize MVP API", version="0.1.0")

# En produccion se restringe a los dominios reales del frontend via
# la variable de entorno ALLOWED_ORIGINS (ver config.py). Por defecto "*"
# para no bloquear nada durante el desarrollo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = get_storage()


class ConvertResponse(BaseModel):
    job_id: str
    svg_base64: str
    png_base64: str


@app.get("/")
def root():
    """Raiz: da una respuesta util en vez de un 404 (Render, monitoreo)."""
    return {
        "service": "Vectorize MVP API",
        "status": "ok",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/convert", response_model=ConvertResponse)
async def convert(file: UploadFile = File(...)):
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Formato no soportado. Usa JPG, PNG o WEBP.")

    image_bytes = await file.read()
    size_mb = len(image_bytes) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        raise HTTPException(400, f"La imagen supera el limite de {MAX_UPLOAD_SIZE_MB}MB")

    try:
        result = convert_image(image_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(500, "Error procesando la imagen. Prueba con otra imagen.")

    job_id = str(uuid.uuid4())
    storage.save(f"{job_id}/result.svg", result.svg.encode("utf-8"))
    storage.save(f"{job_id}/result.png", result.png_transparent)

    return ConvertResponse(
        job_id=job_id,
        svg_base64=base64.b64encode(result.svg.encode("utf-8")).decode(),
        png_base64=base64.b64encode(result.png_transparent).decode(),
    )


@app.get("/api/download/{job_id}/{filetype}")
def download(job_id: str, filetype: str):
    if filetype not in ("svg", "png"):
        raise HTTPException(400, "Tipo de archivo invalido")
    # Evitar path traversal en job_id
    if os.sep in job_id or ".." in job_id:
        raise HTTPException(400, "job_id invalido")
    try:
        data = storage.load(f"{job_id}/result.{filetype}")
    except FileNotFoundError:
        raise HTTPException(404, "Resultado no encontrado")

    media_type = "image/svg+xml" if filetype == "svg" else "image/png"
    headers = {"Content-Disposition": f'attachment; filename="resultado.{filetype}"'}
    return Response(content=data, media_type=media_type, headers=headers)
