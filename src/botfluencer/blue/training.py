"""Notebook helpers; no training, generation, or activation runs on import."""

import hashlib
import importlib
import json
import math
import random
from pathlib import Path
from typing import Any

from botfluencer.blue.data import (
    prepare_rows,
    private_directory,
    read_jsonl,
    reviewed_rows,
    text_key,
    write_json,
)
from botfluencer.blue.model import Detector, Manifest


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    z = 1.96
    p = successes / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, center - radius), min(1.0, center + radius)


def metrics(labels: list[int], scores: list[float], threshold: float) -> dict[str, Any]:
    if len(labels) != len(scores) or not labels or any(y not in (0, 1) for y in labels):
        raise ValueError("Invalid evaluation labels")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in scores):
        raise ValueError("Invalid evaluation scores")
    tp = sum(y == 1 and p >= threshold for y, p in zip(labels, scores, strict=True))
    fp = sum(y == 0 and p >= threshold for y, p in zip(labels, scores, strict=True))
    positive = sum(labels)
    negative = len(labels) - positive
    return dict(
        positive_count=positive,
        negative_count=negative,
        true_positives=tp,
        false_positives=fp,
        precision=tp / (tp + fp) if tp + fp else 0.0,
        recall=tp / positive if positive else 0.0,
        precision_lower_95=wilson(tp, tp + fp)[0],
        fpr_upper_95=wilson(fp, negative)[1],
    )


def select_threshold(labels: list[int], scores: list[float]) -> float:
    candidates = [0.5 + i * 0.005 for i in range(100)]
    eligible = [(t, metrics(labels, scores, t)) for t in candidates]
    eligible = [
        (t, m) for t, m in eligible if m["false_positives"] == 0 and m["true_positives"] >= 5
    ]
    return max(eligible, key=lambda pair: (pair[1]["recall"], pair[0]))[0] if eligible else 1.0


def train(root: Path, output: Path, *, epochs: int = 3, max_length: int = 256) -> dict[str, Any]:
    """Run manually on gaming-pc. FP32 weights, batch 2, gradient accumulation 4."""
    if not 1 <= epochs <= 10 or not 16 <= max_length <= 512:
        raise ValueError("Invalid training configuration")
    root, output = private_directory(root), private_directory(output)
    if (output / "manifest.json").exists():
        raise ValueError("Use a new immutable output directory for every release")
    rows = prepare_rows(
        read_jsonl(root / "npc.jsonl")
        + read_jsonl(root / "synthetic.jsonl")
        + reviewed_rows(read_jsonl(root / "reviewed-training.jsonl"))
    )
    splits = {
        name: [r for r in rows if r["split"] == name] for name in ("train", "validation", "test")
    }
    for name, subset in splits.items():
        if min(sum(r["label"] == y for r in subset) for y in (0, 1)) < 5:
            raise ValueError(
                f"Need at least five examples of each class in {name}; generate more contexts"
            )
    # Limit training imbalance; keep the full held-out NPC population for honest FPR estimates.
    positives = [row for row in splits["train"] if row["label"] == 1]
    negatives = [row for row in splits["train"] if row["label"] == 0]
    random.Random(42).shuffle(negatives)
    splits["train"] = positives + negatives[: 2 * len(positives)]
    random.Random(42).shuffle(splits["train"])
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    datasets = importlib.import_module("datasets")
    np = importlib.import_module("numpy")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for workstation training")
    transformers.set_seed(42)
    model_id = "microsoft/deberta-v3-small"
    tokenizer = transformers.AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    model = transformers.AutoModelForSequenceClassification.from_pretrained(
        model_id,
        num_labels=2,
        dtype=torch.float32,
        trust_remote_code=False,
        id2label={0: "NPC", 1: "COMPETITOR_STYLE"},
        label2id={"NPC": 0, "COMPETITOR_STYLE": 1},
    )

    def tokenize(batch: Any) -> Any:
        return tokenizer(batch["text"], truncation=True, max_length=max_length)

    encoded = {
        name: datasets.Dataset.from_list(
            [{"text": r["text"], "label": r["label"]} for r in subset]
        ).map(tokenize, batched=True, remove_columns=["text"])
        for name, subset in splits.items()
    }
    arguments = transformers.TrainingArguments(
        output_dir=str(output / "training-checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=2,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=2e-5,
        weight_decay=0.01,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        seed=42,
        data_seed=42,
        report_to="none",
        logging_strategy="no",
        fp16=False,
        bf16=False,
        optim="adamw_torch",
        gradient_checkpointing=True,
        dataloader_num_workers=0,
    )
    trainer = transformers.Trainer(
        model=model,
        args=arguments,
        train_dataset=encoded["train"],
        eval_dataset=encoded["validation"],
        processing_class=tokenizer,
        data_collator=transformers.DataCollatorWithPadding(tokenizer),
        callbacks=[transformers.EarlyStoppingCallback(early_stopping_patience=2)],
    )
    trainer.train()
    val_logits = trainer.predict(encoded["validation"]).predictions
    val_labels = [r["label"] for r in splits["validation"]]

    def probabilities(logits: Any, temperature: float) -> list[float]:
        values = logits / temperature
        values = values - values.max(axis=1, keepdims=True)
        exp = np.exp(values)
        return (exp / exp.sum(axis=1, keepdims=True))[:, 1].tolist()  # type: ignore[no-any-return]

    def nll(temperature: float) -> float:
        p = np.clip(probabilities(val_logits, temperature), 1e-7, 1 - 1e-7)
        y = np.array(val_labels)
        return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())

    temperature = min([0.5, 1.0, 1.5, 2.0, 3.0], key=nll)
    val_scores = probabilities(val_logits, temperature)
    threshold = select_threshold(val_labels, val_scores)
    test_scores = probabilities(trainer.predict(encoded["test"]).predictions, temperature)
    report = {
        "validation": metrics(val_labels, val_scores, threshold),
        "test": metrics([r["label"] for r in splits["test"]], test_scores, threshold),
        "synthetic_positive_labels": True,
        "automatic_reporting_validated": False,
        "split_counts": {name: len(data) for name, data in splits.items()},
    }
    # Compare a cheap text baseline, using the same disjoint splits.
    features = importlib.import_module("sklearn.feature_extraction.text")
    linear = importlib.import_module("sklearn.linear_model")
    vectorizer = features.TfidfVectorizer(ngram_range=(1, 2), max_features=30000, min_df=2)
    baseline = linear.LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
    baseline.fit(
        vectorizer.fit_transform([r["text"] for r in splits["train"]]),
        [r["label"] for r in splits["train"]],
    )
    baseline_val = baseline.predict_proba(
        vectorizer.transform([r["text"] for r in splits["validation"]])
    )[:, 1].tolist()
    baseline_threshold = select_threshold(val_labels, baseline_val)
    baseline_test = baseline.predict_proba(
        vectorizer.transform([r["text"] for r in splits["test"]])
    )[:, 1].tolist()
    report["tfidf_test"] = metrics(
        [r["label"] for r in splits["test"]], baseline_test, baseline_threshold
    )
    trainer.save_model(str(output))
    tokenizer.save_pretrained(output)
    files = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.suffix in {".json", ".safetensors", ".model"}:
            with path.open("rb") as handle:
                files[path.name] = hashlib.file_digest(handle, "sha256").hexdigest()
    identity = dict(
        files=files,
        threshold=threshold,
        temperature=temperature,
        max_length=max_length,
        task="competitor_vs_npc",
        positive_label=1,
    )
    version = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    manifest = Manifest(version=version, **identity)
    write_json(output / "evaluation.json", report)
    used = splits["train"] + splits["validation"]
    write_json(
        output / "training-provenance.json",
        {
            "used_groups": sorted({r["group"] for r in used}),
            "used_text_hashes": sorted({r["text_hash"] for r in used}),
            "dataset_hash": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            "base_model": model_id,
            "seed": 42,
        },
    )
    write_json(output / "manifest.json", manifest.model_dump())
    return dict(model_version=version, output=str(output), threshold=threshold, evaluation=report)


def validate_real(release: Path, labelled_jsonl: Path) -> dict[str, Any]:
    """Separate, externally reviewed labels; never treat synthetic evaluation as real."""
    detector = Detector(release)
    rows = reviewed_rows(read_jsonl(labelled_jsonl))
    if not rows:
        raise ValueError("Only independently reviewed real NPC/player examples are eligible")
    provenance = json.loads((release / "training-provenance.json").read_text())
    used_groups, used_hashes = set(provenance["used_groups"]), set(provenance["used_text_hashes"])
    if any(r["group"] in used_groups or text_key(r["text"]) in used_hashes for r in rows):
        raise ValueError("Real evaluation leaks training/calibration authors or text")
    report = metrics(
        [r["label"] for r in rows],
        detector.predict([r["text"] for r in rows]),
        detector.manifest.threshold,
    )
    report.update(
        model_version=detector.manifest.version, real_labels_only=True, author_disjoint=True
    )
    write_json(release / "real-validation.json", report)
    return report


def activate(release: Path, active: Path) -> None:
    release = private_directory(release)
    manifest = Manifest.model_validate_json((release / "manifest.json").read_text())
    manifest.verify(release)
    private_directory(active.parent)
    if active.exists() and not active.is_symlink():
        raise ValueError("Active path must be absent or a managed symlink")
    temporary = active.with_name(active.name + ".next")
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(release, target_is_directory=True)
    temporary.replace(active)
