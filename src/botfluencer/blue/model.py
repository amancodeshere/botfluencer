"""Local-only, checksum-verified model releases. ML imports remain optional."""

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from botfluencer.blue.validation import RealValidation


def real_validation_validated(root: Path, manifest: "Manifest") -> bool:
    """Legacy, malformed, unsealed, or synthetic evaluations fail closed."""
    try:
        report_path = root / "real-validation.json"
        if report_path.stat().st_size > 65536:
            return False
        report = RealValidation.model_validate_json(report_path.read_text())
        expected = manifest.files.get("training-provenance.json")
        if expected is None:
            return False
        with (root / "training-provenance.json").open("rb") as handle:
            provenance_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        return (
            report.model_version == manifest.version
            and report.threshold == manifest.threshold
            and report.training_provenance_sha256 == expected == provenance_hash
            and report.permits_automatic_reporting()
        )
    except (OSError, ValueError, TypeError):
        return False


def inspect_release(root: Path) -> dict[str, Any]:
    """Operator diagnostics without loading CUDA or exposing source data."""
    if not (root / "manifest.json").exists():
        return {"status": "missing", "automatic_reporting_validated": False}
    try:
        manifest = Manifest.model_validate_json((root / "manifest.json").read_text())
        manifest.verify(root)
    except (OSError, ValueError, TypeError):
        return {"status": "invalid", "automatic_reporting_validated": False}
    return {
        "status": "verified",
        "model_version": manifest.version,
        "threshold": manifest.threshold,
        "automatic_reporting_validated": real_validation_validated(root, manifest),
    }


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    threshold: float = Field(ge=0.5, le=1)
    temperature: float = Field(gt=0, le=10)
    max_length: int = Field(ge=16, le=512)
    files: dict[str, str]
    task: str = "competitor_vs_npc"
    positive_label: int = 1

    def verify(self, root: Path) -> None:
        if self.task != "competitor_vs_npc" or self.positive_label != 1:
            raise ValueError("Unsupported classifier task/label order")
        for required in ("model.safetensors", "config.json", "tokenizer_config.json"):
            if required not in self.files:
                raise ValueError("Incomplete model manifest")
        for name, expected in self.files.items():
            if Path(name).name != name or len(expected) != 64:
                raise ValueError("Invalid model manifest path/hash")
            with (root / name).open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("Model release checksum mismatch")
        identity = hashlib.sha256(
            json.dumps(
                {
                    "files": self.files,
                    "threshold": self.threshold,
                    "temperature": self.temperature,
                    "max_length": self.max_length,
                    "task": self.task,
                    "positive_label": self.positive_label,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if identity != self.version:
            raise ValueError("Model release version mismatch")


class Detector:
    def __init__(self, root: Path, batch_size: int = 8) -> None:
        self.root = root.resolve()
        self.manifest = Manifest.model_validate_json((self.root / "manifest.json").read_text())
        self.manifest.verify(self.root)
        self.torch: Any = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        if not self.torch.cuda.is_available():
            raise RuntimeError("Blue inference requires the workstation CUDA GPU")
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.root, local_files_only=True, trust_remote_code=False
        )
        self.model = (
            transformers.AutoModelForSequenceClassification.from_pretrained(
                self.root,
                local_files_only=True,
                trust_remote_code=False,
                dtype=self.torch.float32,
                use_safetensors=True,
            )
            .to("cuda")
            .eval()
        )
        self.batch_size = batch_size

    def predict(self, texts: list[str]) -> list[float]:
        probabilities: list[float] = []
        for start in range(0, len(texts), self.batch_size):
            batch = self.tokenizer(
                texts[start : start + self.batch_size],
                padding=True,
                truncation=True,
                max_length=self.manifest.max_length,
                return_tensors="pt",
            ).to("cuda")
            with self.torch.inference_mode():
                values = (self.model(**batch).logits.float() / self.manifest.temperature).softmax(
                    -1
                )[:, 1]
            if not self.torch.isfinite(values).all():
                raise ValueError("Classifier returned non-finite scores")
            probabilities.extend(values.cpu().tolist())
        return probabilities

    def automatic_reporting_validated(self) -> bool:
        return real_validation_validated(self.root, self.manifest)
