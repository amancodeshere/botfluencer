"""Exercise CUDA training and checkpoint recovery using synthetic text only.

Requires the optional training extra. This is an installation diagnostic, not a
bot detector, competition evaluation, or useful fine-tuned model.
"""

import argparse
import json
import math
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--pretrained", action="store_true", help="Download and exercise DeBERTa-v3-small"
    )
    parser.add_argument(
        "--precision", choices=["float32", "bfloat16", "float16"], default="float32"
    )
    args = parser.parse_args()

    import torch
    import transformers
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        BertConfig,
        BertForSequenceClassification,
    )

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable; refusing a silent CPU fallback.")
    torch.manual_seed(42)
    device = torch.device("cuda:0")
    dtype = getattr(torch, args.precision)
    if dtype == torch.bfloat16 and not torch.cuda.is_bf16_supported():
        raise SystemExit("This GPU does not support the requested bfloat16 precision.")
    if args.pretrained:
        model_id = "microsoft/deberta-v3-small"
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(
            model_id, num_labels=2, dtype=torch.float32, trust_remote_code=False
        )
        inputs = tokenizer(
            [
                "A synthetic example about a sunny afternoon.",
                "A synthetic example about a rainy day.",
            ],
            padding=True,
            truncation=True,
            max_length=128,
            return_tensors="pt",
        )
    else:
        model_id = "random-tiny-bert"
        model = BertForSequenceClassification(
            BertConfig(
                vocab_size=128,
                hidden_size=64,
                num_hidden_layers=2,
                num_attention_heads=4,
                intermediate_size=128,
                num_labels=2,
            )
        )
        inputs = {"input_ids": torch.randint(1, 128, (2, 32))}

    inputs = {key: value.to(device) for key, value in inputs.items()}
    labels = torch.tensor([0, 1], device=device)
    model.to(device)
    if any(not torch.isfinite(parameter).all() for parameter in model.parameters()):
        raise RuntimeError("Model loading produced non-finite parameters.")
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5)
    scaler = torch.amp.GradScaler("cuda", enabled=dtype == torch.float16)
    before = model.classifier.weight.detach().clone()
    losses = []
    model.train()
    for step in range(3):
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type="cuda", dtype=dtype, enabled=dtype != torch.float32):
            loss = model(**inputs, labels=labels).loss
        if not torch.isfinite(loss):
            raise RuntimeError(f"Training produced a non-finite loss at step {step + 1}.")
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
        scaler.step(optimizer)
        scaler.update()
        losses.append(float(loss.detach()))
    if torch.equal(before, model.classifier.weight.detach()):
        raise RuntimeError("Optimizer did not update the classifier weights.")

    model.eval()
    with torch.no_grad():
        expected = model(**inputs).logits.detach().cpu()
    args.output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="synthetic-gpu-check-", dir=args.output_root))
    model.save_pretrained(run_dir / "checkpoint", safe_serialization=True)
    if args.pretrained:
        tokenizer.save_pretrained(run_dir / "checkpoint")
    peak_mib = math.ceil(torch.cuda.max_memory_allocated() / 1024**2)
    del optimizer, model, before, scaler
    torch.cuda.empty_cache()
    restored = AutoModelForSequenceClassification.from_pretrained(
        run_dir / "checkpoint",
        dtype=torch.float32,
        local_files_only=True,
        trust_remote_code=False,
    ).to(device)
    restored.eval()
    with torch.no_grad():
        actual = restored(**inputs).logits.detach().cpu()
    torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-5)
    result = {
        "diagnostic_only": True,
        "model": model_id,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "precision": str(dtype),
        "parameter_dtype": str(restored.classifier.weight.dtype),
        "optimizer_steps": len(losses),
        "losses": losses,
        "peak_allocated_mib": peak_mib,
        "checkpoint_reload_matches": True,
        "run_dir": str(run_dir),
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
