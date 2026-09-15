import uuid
import base64
import os

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import MAX_UPLOAD_SIZE_MB, ALLOWED_ORIGINS, MODOS, MODO_POR_DEFECTO
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
    modo: str = MODO_POR_DEFECTO
    fondo_removido: bool = False
    avisos: list[str] = []


class ModoInfo(BaseModel):
    id: str
    etiqueta: str
    descripcion: str
    quitar_fondo: bool


@app.get("/")
def root():
    """Raiz: da una respuesta util en vez de un 404 (Render, monitoreo)."""
    return {
        "service": "Vectorize MVP API",
        "status": "ok",
        "docs": "/docs",
        "health": "/api/health",
        "modos": list(MODOS.keys()),
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/mem")
def mem():
    """
    Diagnostico de memoria: muestra que ve el proceso sobre sus propios
    limites. Sirve para confirmar si el plan free (512 MB) es realmente el
    techo y si /proc/meminfo reporta el host o el contenedor: si MemAvailable
    sale muy por encima del limite del cgroup, la guarda basada en meminfo no
    puede evitar un OOM y hay que decidir el tamano por umbral fijo.
    """
    info: dict[str, object] = {}

    def leer(path: str) -> str | None:
        try:
            with open(path, "r") as fh:
                return fh.read().strip()
        except Exception:  # noqa: BLE001
            return None

    for clave, path in (
        ("meminfo_MemAvailable", "/proc/meminfo"),
        ("cgroup_v2_max", "/sys/fs/cgroup/memory.max"),
        ("cgroup_v2_current", "/sys/fs/cgroup/memory.current"),
        ("cgroup_v1_limit", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        ("cgroup_v1_usage", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        valor = leer(path)
        if clave == "meminfo_MemAvailable" and valor:
            for linea in valor.splitlines():
                if linea.startswith("MemAvailable:"):
                    info["meminfo_MemAvailable_MB"] = round(
                        int(linea.split()[1]) / 1024, 1
                    )
                    break
            continue
        if valor:
            info[clave] = valor

    import os as _os
    info["rss_MB"] = round(
        int(leer("/proc/self/status").split("VmRSS:")[1].split()[0]) / 1024, 1
        if leer("/proc/self/status") and "VmRSS:" in (leer("/proc/self/status") or "")
        else 0,
        1,
    )
    info["pid"] = _os.getpid()
    return info


@app.get("/api/modos", response_model=list[ModoInfo])
def listar_modos():
    """Describe los modos disponibles para que el frontend se arme solo."""
    return [
        ModoInfo(
            id=clave,
            etiqueta=preset["etiqueta"],
            descripcion=preset["descripcion"],
            quitar_fondo=bool(preset["quitar_fondo"]),
        )
        for clave, preset in MODOS.items()
    ]


@app.post("/api/convert", response_model=ConvertResponse)
async def convert(file: UploadFile = File(...), mode: str = Form(MODO_POR_DEFECTO)):
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Formato no soportado. Usa JPG, PNG o WEBP.")

    image_bytes = await file.read()
    size_mb = len(image_bytes) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        raise HTTPException(400, f"La imagen supera el limite de {MAX_UPLOAD_SIZE_MB}MB")

    try:
        result = convert_image(image_bytes, mode=mode)
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
        modo=result.modo,
        fondo_removido=result.fondo_removido,
        avisos=result.avisos,
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
