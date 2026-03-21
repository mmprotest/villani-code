from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from villani_code.utils import ensure_dir, now_stamp


@dataclass
class Checkpoint:
    id: str
    message_index: int
    files: list[str]


class CheckpointManager:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.root = self.repo / ".villani_code" / "checkpoints"
        ensure_dir(self.root)

    def create(self, files: list[Path], message_index: int) -> Checkpoint:
        cid = now_stamp()
        cdir = self.root / cid
        ensure_dir(cdir)
        saved: list[str] = []
        for f in files:
            absf = (self.repo / f).resolve() if not f.is_absolute() else f.resolve()
            if not absf.exists():
                continue
            try:
                rel = absf.relative_to(self.repo)
            except ValueError:
                continue
            out = cdir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(absf, out)
            saved.append(str(rel))
        meta = {"id": cid, "message_index": message_index, "files": saved}
        (cdir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return Checkpoint(id=cid, message_index=message_index, files=saved)

    def list(self) -> list[Checkpoint]:
        cps: list[Checkpoint] = []
        for d in sorted(self.root.glob("*")):
            meta = d / "metadata.json"
            if not meta.exists():
                continue
            obj = json.loads(meta.read_text(encoding="utf-8"))
            cps.append(Checkpoint(**obj))
        return cps

    def rewind(self, checkpoint_id: str) -> Checkpoint:
        cdir = self.root / checkpoint_id
        meta = json.loads((cdir / "metadata.json").read_text(encoding="utf-8"))
        cp = Checkpoint(**meta)
        for rel in cp.files:
            src = cdir / rel
            dst = self.repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return cp
