"""
Igual patrón que los adaptadores de IA: una interfaz de storage, con una
implementación local en disco para el MVP y el lugar preparado para
enchufar Cloudflare R2 / S3 sin tocar el resto del código.
"""
from abc import ABC, abstractmethod
import os

from app.config import STORAGE_DIR


class Storage(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes) -> str:
        """Guarda `data` bajo `key` y devuelve una URL/ruta accesible."""
        raise NotImplementedError

    @abstractmethod
    def load(self, key: str) -> bytes:
        raise NotImplementedError


class LocalDiskStorage(Storage):
    def __init__(self, base_dir: str = STORAGE_DIR):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        path = os.path.join(self.base_dir, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def load(self, key: str) -> bytes:
        path = os.path.join(self.base_dir, key)
        with open(path, "rb") as f:
            return f.read()


class R2Storage(Storage):
    """
    Esqueleto listo para producción: Cloudflare R2 es compatible con la
    API de S3, así que boto3 funciona igual. Requiere las variables de
    entorno R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET.
    """

    def __init__(self):
        import boto3
        import os as _os

        self.bucket = _os.environ["R2_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{_os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=_os.environ["R2_ACCESS_KEY"],
            aws_secret_access_key=_os.environ["R2_SECRET_KEY"],
        )

    def save(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def load(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()


def get_storage() -> Storage:
    engine = os.getenv("STORAGE_ENGINE", "local")
    if engine == "r2":
        return R2Storage()
    return LocalDiskStorage()
