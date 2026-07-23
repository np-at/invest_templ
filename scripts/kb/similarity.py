"""Lexical (and optional embedding) text similarity for surfacing semantic
conflict / duplicate CANDIDATES that exact-normalized matching cannot see
(methodology Bucket 3, item 7b).

The DEFAULT path is pure stdlib — a blend of word-token and character-trigram
Jaccard. It catches reordering, morphology, and shared-vocabulary paraphrase
(e.g. "John F. Kennedy" vs "John Kennedy", "was born in" vs "born in"). It
CANNOT catch disjoint-vocabulary synonymy ("JFK" vs "President Kennedy") — that
is the reconciler agent's LLM judgment (tier b), or the optional embedding
backend (tier c, `model2vec` — numpy-tier, no torch).

Everything here is READ-ONLY scoring; nothing is written and nothing is linked.
O(n^2) over live claims is fine at template scale; MinHash/LSH blocking is the
documented scale upgrade.
"""
from __future__ import annotations

import re

from . import events as E

# Default thresholds for surfacing a candidate pair: subject AND predicate must
# both clear their bar (objects are scored, not gated — their similarity is what
# separates a duplicate from a conflict).
SUBJ_SIM = 0.6
# Keep PRED_SIM at ~0.5 or above. Distinct functional predicates on the same
# subject are the risky false positive: `date_of_birth` vs `date_of_death` score
# ~0.4 here (correctly excluded); lowering the bar would start pulling in such
# birth/death-style near-twins as bogus conflicts. Output is advisory anyway.
PRED_SIM = 0.5
OBJ_DUP_SIM = 0.8   # object similarity >= this => likely the SAME value (dup)

_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def _word_tokens(text: str) -> set[str]:
    return {t for t in _SPLIT.split(E.normalize(text)) if t}


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    s = f" {E.normalize(text)} "
    if len(s) <= n:
        return {s.strip()} if s.strip() else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def token_set(text: str) -> set[str]:
    """Word tokens plus char-trigrams — robust to short strings and morphology."""
    return _word_tokens(text) | _char_ngrams(text)


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(a: str, b: str) -> float:
    """Blended lexical similarity in [0, 1]. Exactly 1.0 iff normalized-equal."""
    if E.normalize(a) == E.normalize(b):
        return 1.0
    return jaccard(token_set(a), token_set(b))


# --------------------------------------------------------------- embedding (opt)

_MODEL = None
_MODEL_NAME = "minishlab/potion-base-8M"


def _load_model():
    """Lazily load model2vec. Imported INSIDE the function so the default
    lexical path never touches the dependency (keeps the zero-dep promise)."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from model2vec import StaticModel
    except ImportError as e:
        raise SystemExit(
            "The --embed backend needs the optional 'model2vec' package "
            "(numpy-tier, no torch). Install it with:\n"
            "    pip install model2vec\n"
            "The default lexical backend requires no dependencies."
        ) from e
    try:
        _MODEL = StaticModel.from_pretrained(_MODEL_NAME)
    except Exception as e:  # missing cached weights, no network, corrupt cache…
        raise SystemExit(
            f"Could not load embedding model {_MODEL_NAME!r}: {e}\n"
            "It downloads on first use; ensure network access (or a warm cache), "
            "or omit --embed to use the zero-dependency lexical backend."
        ) from e
    return _MODEL


def embed_texts(texts) -> dict:
    """Return {text: vector} for the unique inputs, via model2vec (lazy)."""
    model = _load_model()
    uniq = sorted(set(texts))
    vecs = model.encode(uniq)
    return {t: list(map(float, v)) for t, v in zip(uniq, vecs)}


def cosine(u, v) -> float:
    import math
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (nu * nv)))
