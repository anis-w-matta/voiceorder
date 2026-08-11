from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import settings


class AudioStore:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.audio_dir)

    def save(self, data: bytes, ext: str = ".ogg") -> str:
        # Never let a caller-supplied extension introduce path structure:
        # on Windows both separators are live, so ".\evil" would otherwise
        # spread files into directories of the sender's choosing.
        ext = ext.split("\x00")[0].replace("\\", "").replace("/", "").strip()
        if not ext.startswith("."):
            ext = f".{ext}" if ext else ".ogg"
        day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        name = f"{uuid4().hex}{ext}"
        path = self.root / day / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.relative_to(self.root).as_posix()

    def absolute(self, rel_path: str) -> str:
        return str(self.root / rel_path)
