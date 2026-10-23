"""Local-only, checksum-verified model releases. ML imports remain optional."""

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
        path = self.root / "real-validation.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text())
        # Synthetic evaluation never activates automatic reports.
        return bool(
            data.get("model_version") == self.manifest.version
            and data.get("real_labels_only") is True
            and data.get("author_disjoint") is True
            and data.get("positive_count", 0) >= 100
            and data.get("negative_count", 0) >= 400
            and data.get("precision_lower_95", 0) >= 0.95
            and data.get("fpr_upper_95", 1) <= 0.01
        )
