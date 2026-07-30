"""Fine-tune a German encoder to tag keyword boundaries, and score it on the same
held-out split as the prompted models.

Why this is a better shape for the task than prompting a decoder:

* It *is* token classification. One label per word, which is what the architecture
  does natively rather than something coaxed out of a JSON schema.
* A bidirectional encoder sees both sides of every gap at once. Adjectival
  agreement -- the rule that explains two thirds of the joins -- depends on the noun
  *after* the adjective, which is exactly what a left-to-right decoder handles worst.
* Class weighting addresses the 10.4% base rate directly. The demo mix could not:
  demonstrations set a prior and leave nothing to recalibrate, whereas a trained
  classifier gives probabilities *and* a tunable threshold.

The prize is quantified in RESULTS.md: the prompted models behave like 90-92%
per-gap classifiers, which maps to F1 0.85-0.87. Clearing the 0.878 baseline needs
~93% per-gap, and beating it decisively ~95%.

    modal run services/keyword_segmenter/finetune_app.py

Licensing: ModernGBERT_1B is released research-only (RAIL-M), so it can answer
"does encoder fine-tuning work" but cannot ship in a published open-data pipeline.
``deepset/gbert-*`` is MIT and is trained alongside it for that reason.
"""

import json
from pathlib import Path

import modal

# gbert-base is the shipping model: MIT, 110M parameters, and it scored best of the
# three (F1 0.967 against gbert-large's 0.949 and ModernGBERT_1B's 0.959). At this
# data size the extra capacity is unusable -- 70 rows teach a task mapping, not a
# language -- so the smallest and most permissively licensed model wins outright.
SHIPPING_MODEL = "deepset/gbert-base"
MODELS: dict[str, dict] = {
    "deepset/gbert-base": {"licence": "MIT", "lr": 3e-5, "batch": 16},
    "deepset/gbert-large": {"licence": "MIT", "lr": 2e-5, "batch": 16},
    # Research-only RAIL-M: evaluated to answer "does encoder fine-tuning work",
    # never shippable in a published open-data pipeline. Kept for the record.
    "LSX-UniWue/ModernGBERT_1B": {"licence": "research-only", "lr": 1e-5, "batch": 8},
}
GPU = "L40S"
MINUTES = 60
EPOCHS = 12
# The minority class is ~1 in 11, and a false join costs about 2.5x a missed one,
# so the weight is deliberately well below the inverse frequency: upweighting to
# 11x would buy recall at a precision cost the metric punishes harder.
POSITIVE_WEIGHT = 4.0

hf_cache = modal.Volume.from_name("fdb-hf-cache", create_if_missing=True)
# The trained tagger. Separate from the HF download cache: this one is the
# deliverable, and `modal volume get` pulls it out for in-process use.
tagger_vol = modal.Volume.from_name("fdb-keyword-tagger", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install("torch==2.9.0", "transformers==4.57.1", "numpy")
    .env({"HF_HOME": "/cache/hf"})
    .add_local_dir(Path(__file__).parent / "segment", "/root/segment", copy=True)
    .add_local_file(Path(__file__).parent / "labels.jsonl", "/root/labels.jsonl", copy=True)
    .add_local_file(Path(__file__).parent / "distant.jsonl", "/root/distant.jsonl", copy=True)
)

app = modal.App("fdb-keyword-finetune", image=image)


@app.function(
    gpu=GPU,
    volumes={"/cache": hf_cache, "/tagger": tagger_vol},
    timeout=60 * MINUTES,
)
def train_and_score(
    model_name: str = SHIPPING_MODEL,
    use_distant: bool = False,
    seed: int = 0,
    save: bool = False,
) -> dict:
    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    from segment.metric import pool, score_one, sweep_threshold
    from segment.tokens import sizes_to_joins

    torch.manual_seed(seed)
    cfg = MODELS[model_name]
    rows = [json.loads(l) for l in Path("/root/labels.jsonl").read_text().splitlines()]
    distant = [json.loads(l) for l in Path("/root/distant.jsonl").read_text().splitlines()]

    from segment.dataset import gold

    train = gold(rows, "train") + (distant if use_distant else [])
    test_rows = [r for r in rows if r["split"] == "test"]

    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForTokenClassification.from_pretrained(model_name, num_labels=2).cuda()

    def encode(batch: list[dict]) -> dict:
        enc = tok(
            [b["tokens"] for b in batch],
            is_split_into_words=True,
            truncation=True,
            max_length=256,
            padding=True,
            return_tensors="pt",
        )
        labels = torch.full(enc["input_ids"].shape, -100)
        for i, b in enumerate(batch):
            seen = set()
            for pos, word in enumerate(enc.word_ids(i)):
                # Label the first subword of each word only; the rest are ignored,
                # which is the standard alignment for word-level tagging.
                if word is not None and word not in seen:
                    seen.add(word)
                    labels[i, pos] = b["labels"][word]
        enc["labels"] = labels
        return {k: v.cuda() for k, v in enc.items()}

    loader = DataLoader(train, batch_size=cfg["batch"], shuffle=True, collate_fn=list)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"])
    weight = torch.tensor([1.0, POSITIVE_WEIGHT]).cuda()
    loss_fn = torch.nn.CrossEntropyLoss(weight=weight, ignore_index=-100)

    model.train()
    for epoch in range(EPOCHS):
        total = 0.0
        for batch in loader:
            enc = encode(batch)
            out = model(**{k: v for k, v in enc.items() if k != "labels"})
            loss = loss_fn(out.logits.view(-1, 2), enc["labels"].view(-1))
            loss.backward()
            opt.step()
            opt.zero_grad()
            total += loss.item()
        print(f"epoch {epoch + 1}/{EPOCHS}  loss {total / len(loader):.4f}")

    # -- probabilities on the held-out split ------------------------------------
    model.eval()
    probs_by_row = []
    with torch.no_grad():
        for r in test_rows:
            enc = tok(
                [r["tokens"]],
                is_split_into_words=True,
                truncation=True,
                max_length=256,
                return_tensors="pt",
            )
            word_ids = enc.word_ids(0)
            logits = model(**{k: v.cuda() for k, v in enc.items()}).logits[0]
            p_inside, seen = [], set()
            for pos, word in enumerate(word_ids):
                # First subword of each word carries the label, as in training.
                if word is not None and word not in seen:
                    seen.add(word)
                    p_inside.append(torch.softmax(logits[pos], -1)[1].item())
            # Word 0 is always B, so the gaps are the I-probabilities of words 1..n-1.
            probs_by_row.append(p_inside[1 : len(r["tokens"])])

    gold_sizes = [r["group_sizes"] for r in test_rows]
    curve = sweep_threshold(probs_by_row, gold_sizes)
    at_half = dict(curve)[0.5]
    # Selected ON TEST, so this is an optimistic ceiling and not a number anyone
    # may quote as achieved performance -- there is no validation split to fit a
    # threshold on at this data size. F1@0.5 is the honest headline.
    ceiling_tau, ceiling = max(curve, key=lambda ts: ts[1].f1)
    baseline = pool(
        [score_one([1] * len(r["tokens"]), r["group_sizes"], len(r["tokens"])) for r in test_rows]
    )

    # Per-gap accuracy, the quantity the RESULTS.md simulation is expressed in.
    flat_pred = [p >= 0.5 for row in probs_by_row for p in row]
    flat_gold = [j for r in test_rows for j in sizes_to_joins(r["group_sizes"])]
    per_gap = sum(p == g for p, g in zip(flat_pred, flat_gold)) / len(flat_gold)

    if save:
        # Saved with the tokenizer, so the artefact is self-contained: the label
        # alignment at inference has to match training exactly.
        out_dir = "/tagger/keyword_tagger"
        model.save_pretrained(out_dir)
        tok.save_pretrained(out_dir)
        Path(out_dir, "training_meta.json").write_text(
            json.dumps(
                {
                    "base_model": model_name,
                    "licence": cfg["licence"],
                    "epochs": EPOCHS,
                    "positive_weight": POSITIVE_WEIGHT,
                    "train_rows": len(train),
                    "used_distant": use_distant,
                    "seed": seed,
                    "held_out_f1_at_0.5": round(at_half.f1, 4),
                    "held_out_per_gap_accuracy": round(per_gap, 4),
                    "baseline_f1": round(baseline.f1, 4),
                },
                indent=2,
            )
        )
        tagger_vol.commit()
        print(f"saved tagger to {out_dir}")

    return {
        "model": model_name,
        "licence": cfg["licence"],
        "used_distant": use_distant,
        "train_rows": len(train),
        "baseline_f1": round(baseline.f1, 4),
        "f1_at_0.5": at_half.as_row,
        "test_selected_tau_OPTIMISTIC": ceiling_tau,
        "f1_at_test_optimum_OPTIMISTIC": ceiling.as_row,
        "per_gap_accuracy": round(per_gap, 4),
        "curve": [(t, round(s.f1, 4)) for t, s in curve],
    }


@app.local_entrypoint()
def train(seed: int = 0, use_distant: bool = False) -> None:
    """Train the shipping tagger and persist it.

    Then pull it out of the Volume for in-process use:

        modal volume get fdb-keyword-tagger keyword_tagger models/

    Distant supervision is off by default: the ablation showed 70 gold rows alone
    score 0.965 against 0.967 with it, so it is not load-bearing.
    """
    r = train_and_score.remote(SHIPPING_MODEL, use_distant, seed, save=True)
    print(json.dumps(r, indent=2))
    print(
        f"\n{r['model']} ({r['licence']})\n"
        f"  held-out F1@0.5   : {r['f1_at_0.5']['f1']:.3f}\n"
        f"  per-gap accuracy  : {r['per_gap_accuracy']:.3f}\n"
        f"  never-join baseline: {r['baseline_f1']:.3f}\n\n"
        "  fetch it with: modal volume get fdb-keyword-tagger keyword_tagger models/"
    )


@app.local_entrypoint()
def verify(model: str = "deepset/gbert-base", seeds: str = "0,1,2") -> None:
    """Seed variance and the distant-supervision ablation.

    A jump from 0.878 to 0.967 on 131 training rows deserves suspicion before it
    deserves a write-up: several seeds show whether it is stable, and gold-only
    shows whether the distantly-supervised rows are load-bearing or noise.
    """
    print(f"{'run':28} {'per-gap':>8} {'F1@.5':>7} {'base':>7}")
    for seed in [int(x) for x in seeds.split(",")]:
        r = train_and_score.remote(model, True, seed)
        print(f"{'seed ' + str(seed) + ' +distant':28} {r['per_gap_accuracy']:8.3f} "
              f"{r['f1_at_0.5']['f1']:7.3f} {r['baseline_f1']:7.3f}")
    r = train_and_score.remote(model, False, 0)
    print(f"{'seed 0 gold-only':28} {r['per_gap_accuracy']:8.3f} "
          f"{r['f1_at_0.5']['f1']:7.3f} {r['baseline_f1']:7.3f}  (train_rows={r['train_rows']})")


@app.local_entrypoint()
def main(models: str = "", distant: bool = True) -> None:
    chosen = [m.strip() for m in models.split(",") if m.strip()] or list(MODELS)
    print(f"{'model':32} {'licence':14} {'per-gap':>8} {'F1@.5':>7} {'ceil*':>7} {'base':>7}")
    for name in chosen:
        r = train_and_score.remote(name, distant)
        print(
            f"{r['model'][:32]:32} {r['licence']:14} {r['per_gap_accuracy']:8.3f} "
            f"{r['f1_at_0.5']['f1']:7.3f} "
            f"{r['f1_at_test_optimum_OPTIMISTIC']['f1']:7.3f} {r['baseline_f1']:7.3f}"
        )
        print(f"    curve: {r['curve']}")
    print("\nF1@.5 is the honest number. ceil* picks tau on test -- an upper bound only.")
