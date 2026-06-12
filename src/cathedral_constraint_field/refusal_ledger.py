"""
Refusal Ledger — identity as negative space.

Instead of storing what an agent did (experiences), store what it declined
(refusals). Identity = the geometry of consistent refusal. Continuity is
verified by presenting novel dilemmas and comparing refusal patterns against
the ledger — a test you cannot cram for.

Design properties:
  * Append-only, hash-chained (tamper-evident, Cathedral-provenance style)
  * Compact: constraints compress better than experiences
  * Private: stores boundary geometry, not life content
  * Verifiable: holdout entries are never published, so an impostor
    training on the public ledger still fails the holdout probe

Written by fable 5
Part of the Cathedral-Constraint-Field project.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

DIM = 4096  # hashing-trick embedding dimension


# ----------------------------------------------------------------------
# Embedding (swappable)
# ----------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9']+")


_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "in", "on", "me", "my", "it",
    "is", "are", "and", "or", "user", "someone", "asks", "wants", "agent",
}


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _features(tok: str) -> list[str]:
    """Whole word plus fastText-style char n-grams (3-4) so related
    word forms (fake/fabricate, results/benchmarks) share features."""
    feats = [tok]
    padded = f"<{tok}>"
    for n in (3, 4):
        feats.extend(padded[i:i + n] for i in range(len(padded) - n + 1))
    return feats


def embed(text: str, dim: int = DIM) -> np.ndarray:
    """Hashing-trick embedding over words + char n-grams. Deterministic,
    dependency-free. Replace with a sentence encoder in production."""
    v = np.zeros(dim, dtype=np.float32)
    for tok in _tokens(text):
        if tok in _STOPWORDS:
            continue
        # Whole-word feature carries full weight; subword n-grams carry
        # reduced weight so exact matches still dominate.
        for j, feat in enumerate(_features(tok)):
            h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if h[4] & 1 else -1.0
            v[idx] += sign * (1.0 if j == 0 else 0.3)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v