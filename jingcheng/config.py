from __future__ import annotations
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tomllib


def expand(value: str) -> str:
    def variable(match):
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(f"Environment variable {name} is not set")
        return os.environ[name]
    return os.path.expanduser(re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", variable, value))


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError(f"Invalid job/asset ID: {value!r}")
    return value


def relative_name(value: str) -> str:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or "\\" in value or ":" in value or any(x in {"", ".", ".."} for x in value.split("/")):
        raise ValueError(f"Expected a safe relative asset path: {value!r}")
    return p.as_posix()


class Config:
    def __init__(self, filename=None):
        default = Path(__file__).resolve().parents[1] / "config/local.toml"
        self.filename = Path(filename or os.environ.get("JINGCHENG_CONFIG", default)).expanduser().resolve()
        with self.filename.open("rb") as f:
            self.data = tomllib.load(f)
        if self.data.get("schema_version") != 1:
            raise ValueError("Unsupported config schema_version")
        self.root = self.path("project_root")

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, dotted: str, required=True) -> Path | None:
        value = self.get(dotted)
        if not value:
            if not required:
                return None
            raise ValueError(f"Missing config path: {dotted}")
        p = Path(expand(str(value)))
        return (p if p.is_absolute() else self.filename.parent / p).resolve()

    def tool(self, name: str) -> str:
        value = expand(self.get(f"tools.{name}", name))
        if "/" in value or "\\" in value:
            p = Path(value)
            return str((p if p.is_absolute() else self.filename.parent / p).resolve())
        return shutil.which(value) or value

    def asset(self, name: str) -> Path:
        root = self.path("paths.assets")
        p = (root / relative_name(name)).resolve()
        if not p.is_relative_to(root):
            raise ValueError("Asset symlink leaves configured asset root")
        return p

    def job(self, name: str) -> Path:
        return self.path("paths.runs") / safe_id(name)

    def media(self, reference: str) -> Path:
        """Portable references point into configured private assets or job outputs."""
        kind, sep, name = reference.partition(":")
        if not sep or kind not in {"asset", "run"}:
            raise ValueError("Media reference must be asset:relative/path or run:relative/path")
        root = self.path("paths.assets" if kind == "asset" else "paths.runs")
        path = (root / relative_name(name)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Media symlink leaves configured root")
        return path
