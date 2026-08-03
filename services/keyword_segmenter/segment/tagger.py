"""Segment a ``keywords`` value with the fine-tuned tagger, in process.

This replaces the prompting service entirely. No vLLM, no Modal, no GPU, no cold
start: ``deepset/gbert-base`` is 110M parameters and runs on CPU fast enough to do
the whole column in a couple of minutes. It is the reason the approach is worth
shipping at all -- see RESULTS.md for the measurement, but in short, five prompted
decoders up to 12B all lost to "never join anything" (F1 0.878) and this scores
**0.968**, and 0.906 on the rows whose keywords run three tokens or longer.

The model is not in the repository -- 440MB of weights do not belong in git. Train
and fetch it:

    modal run services/keyword_segmenter/finetune_app.py::train
    modal volume get fdb-keyword-tagger keyword_tagger models/

The output contract is unchanged from the prompted version, deliberately: every
keyword is a contiguous span of the source, so invention and omission stay
unrepresentable rather than merely tested for.
"""

from __future__ import annotations

from pathlib import Path

from segment.postprocess import glue_function_words
from segment.tokens import joins_to_sizes, terms, tokenize

DEFAULT_PATH = Path("models/keyword_tagger")
# The held-out threshold curve is flat from 0.05 to 0.5 (F1 0.980 to 0.967), which
# is what a well-separated classifier looks like -- unlike the prompted models,
# whose curves climbed monotonically towards "never join". 0.5 is therefore the
# honest default: no threshold was fitted on the test split to obtain it.
DEFAULT_THRESHOLD = 0.5
INSIDE = 1


class KeywordTagger:
    """Word-level boundary tagger. Load once, call many times."""

    def __init__(
        self,
        path: str | Path = DEFAULT_PATH,
        threshold: float = DEFAULT_THRESHOLD,
        device: str = "cpu",
        batch_size: int = 32,
    ):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"no tagger at {path}. Train and fetch it:\n"
                "  modal run services/keyword_segmenter/finetune_app.py::train\n"
                "  modal volume get fdb-keyword-tagger keyword_tagger models/"
            )
        self._torch = torch
        self.tok = AutoTokenizer.from_pretrained(str(path))
        self.model = AutoModelForTokenClassification.from_pretrained(str(path)).to(device)
        self.model.eval()
        self.device = device
        self.threshold = threshold
        self.batch_size = batch_size

    def _probabilities(self, batch: list[list[str]]) -> list[list[float]]:
        """P(inside) for each word of each row."""
        enc = self.tok(
            batch,
            is_split_into_words=True,
            truncation=True,
            max_length=256,
            padding=True,
            return_tensors="pt",
        )
        with self._torch.no_grad():
            logits = self.model(**{k: v.to(self.device) for k, v in enc.items()}).logits
        out = []
        for i in range(len(batch)):
            probs, seen = [], set()
            for pos, word in enumerate(enc.word_ids(i)):
                # First subword of each word carries the label, matching training.
                if word is not None and word not in seen:
                    seen.add(word)
                    probs.append(self._torch.softmax(logits[i, pos], -1)[INSIDE].item())
            out.append(probs)
        return out

    def segment(self, values: list[str]) -> list[dict]:
        """Segment many ``keywords`` values. Returns one result per input, in order."""
        tokenised = [tokenize(v) for v in values]
        results: list[dict | None] = [None] * len(values)

        # Rows with nothing to decide never reach the model.
        todo = []
        for i, tokens in enumerate(tokenised):
            if len(tokens) < 2:
                results[i] = {"terms": tokens, "group_sizes": [1] * len(tokens)}
            else:
                todo.append(i)

        for start in range(0, len(todo), self.batch_size):
            chunk = todo[start : start + self.batch_size]
            for i, probs in zip(chunk, self._probabilities([tokenised[i] for i in chunk])):
                tokens = tokenised[i]
                # Word 0 always begins a keyword, so the gaps are words 1..n-1.
                joins = [p >= self.threshold for p in probs[1 : len(tokens)]]
                # Truncation would leave the tail unlabelled; treat any missing
                # decision as "new keyword", which is the safe direction.
                joins += [False] * (len(tokens) - 1 - len(joins))
                sizes = joins_to_sizes(glue_function_words(tokens, joins))
                results[i] = {"terms": terms(tokens, sizes), "group_sizes": sizes}

        return [r for r in results if r is not None]

    def __call__(self, keywords: str) -> dict:
        return self.segment([keywords])[0]
