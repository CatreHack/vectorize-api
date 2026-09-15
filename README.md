# Vectorize API — Backend

Backend FastAPI del vectorizador de imágenes ("Traza").
Convierte JPG/PNG/WEBP en SVG vectorial + PNG transparente.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Info del servicio |
| GET | `/api/health` | Health check (usado por Render) |
| POST | `/api/convert` | Sube imagen (`file`), devuelve `job_id` + `svg_base64` + `png_base64` |
| GET | `/api/download/{job_id}/{svg\|png}` | Descarga el resultado |

## Correr en local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Probar:

```bash
curl -F "file=@/ruta/a/tu/logo.png" http://localhost:8000/api/convert
```

## Motores (sin configuración = gratis, local)

- **Fondo:** `FloodFillBackgroundRemover` — flood-fill desde las 4 esquinas.
  Ideal para fondos uniformes (logos, firmas, dibujos).
- **Vectorización:** `ContourVectorizer` — cuantización de color (k-means) +
  contornos de OpenCV.

Todo se elige desde `app/config.py`; el resto del código no conoce proveedores.

## Motores de producción (opcionales)

```bash
pip install vtracer
export VECTORIZER_ENGINE=vtracer

export BACKGROUND_REMOVER_ENGINE=photoroom
export PHOTOROOM_API_KEY=tu_api_key

export STORAGE_ENGINE=r2
export R2_ACCOUNT_ID=... R2_ACCESS_KEY=... R2_SECRET_KEY=... R2_BUCKET=...
```

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `BACKGROUND_REMOVER_ENGINE` | `floodfill` | `floodfill` \| `photoroom` \| `removebg` |
| `VECTORIZER_ENGINE` | `contour` | `contour` \| `vtracer` \| `potrace` |
| `MAX_UPLOAD_SIZE_MB` | `10` | Límite de subida |
| `STORAGE_DIR` | `./storage` | Carpeta de resultados (disco local) |
| `ALLOWED_ORIGINS` | `*` | CORS: orígenes separados por coma |
| `RESULT_TTL_HOURS` | `24` | Retención de resultados |

## Despliegue en Render

1. New → Web Service → conectar el repo `CatreHack/vectorize-api`.
2. Runtime **Python 3**, plan **Free**.
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Health check: `/api/health`

`render.yaml` ya trae esta configuración (Blueprint).

> **Nota:** en el plan gratuito el servicio se duerme tras ~15 min sin uso.
> La primera petición después tarda ~30-50 s en despertar. El disco es
> efímero: los resultados guardados en `./storage` se pierden al reiniciar
> (por eso el frontend descarga vía base64 y no depende del disco).
