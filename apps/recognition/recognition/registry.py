"""Model registry: the licence and integrity gate for every model file.

A model loads only if it is
  1. listed in models/manifest.yaml,
  2. present with a matching SHA-256 (when the manifest has one; a missing hash is refused), and
  3. commercially usable ("allowed" / "licensed"), OR research mode is explicitly enabled outside
     production.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

log = logging.getLogger(__name__)

CommercialUse = Literal["allowed", "licensed", "forbidden"]


class ModelRefused(RuntimeError):
    """A model may not be loaded (licence, integrity or availability)."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    version: str
    task: str
    backend: str
    file: str
    url: str | None
    sha256: str | None
    license: str
    commercial_use: CommercialUse
    embedding_dim: int | None = None
    reference_threshold: float | None = None

    @property
    def model_id(self) -> str:
        return f"{self.name}@{self.version}"


def load_manifest(path: Path) -> dict[str, ModelSpec]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, ModelSpec] = {}
    for m in data.get("models", []):
        spec = ModelSpec(
            name=m["name"],
            version=str(m["version"]),
            task=m["task"],
            backend=m["backend"],
            file=m["file"],
            url=m.get("url"),
            sha256=m.get("sha256"),
            license=m["license"],
            commercial_use=m["commercial_use"],
            embedding_dim=m.get("embedding_dim"),
            reference_threshold=m.get("reference_threshold"),
        )
        if spec.commercial_use not in ("allowed", "licensed", "forbidden"):
            raise ValueError(f"{spec.name}: invalid commercial_use {spec.commercial_use!r}")
        out[spec.name] = spec
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(
    name: str,
    manifest: dict[str, ModelSpec],
    models_dir: Path,
    *,
    app_env: str,
    allow_research: bool,
) -> tuple[ModelSpec, Path]:
    """Returns (spec, path) or raises ModelRefused with a precise reason."""
    spec = manifest.get(name)
    if spec is None:
        raise ModelRefused(f"Model '{name}' is not in the manifest.")
    if spec.commercial_use == "forbidden":
        if app_env == "production":
            raise ModelRefused(
                f"Model '{name}' ({spec.license}) is not licensed for commercial use and cannot "
                "be used in production. Obtain a commercial licence and set commercial_use: licensed."
            )
        if not allow_research:
            raise ModelRefused(
                f"Model '{name}' is non-commercial research only. Set ALLOW_RESEARCH_MODELS=true "
                "to use it for local evaluation (never in production)."
            )
        log.warning("Loading NON-COMMERCIAL model %s for research only (%s).", name, spec.license)
    path = models_dir / spec.file
    if not path.is_file():
        raise ModelRefused(f"Model file missing: {path}. Run: python -m recognition.cli download-models")
    if not spec.sha256:
        raise ModelRefused(f"Model '{name}' has no sha256 in the manifest: refusing unverified weights.")
    actual = sha256_of(path)
    if actual != spec.sha256:
        raise ModelRefused(f"Model '{name}' checksum mismatch (expected {spec.sha256}, got {actual}).")
    return spec, path


def download(spec: ModelSpec, models_dir: Path) -> Path:
    """Downloads a commercially usable model with a known URL and verifies its hash."""
    if spec.commercial_use == "forbidden" or not spec.url:
        raise ModelRefused(f"'{spec.name}' cannot be downloaded automatically ({spec.license}).")
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / spec.file
    if target.is_file() and spec.sha256 and sha256_of(target) == spec.sha256:
        return target
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(spec.url, timeout=120) as r, tmp.open("wb") as f:  # noqa: S310 (https from manifest)
        shutil.copyfileobj(r, f)
    actual = sha256_of(tmp)
    if spec.sha256 and actual != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise ModelRefused(f"Downloaded '{spec.name}' failed checksum verification.")
    tmp.replace(target)
    return target
