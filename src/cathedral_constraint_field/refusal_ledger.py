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


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "in", "on", "me", "my", "it",
    "is", "are", "and", "or", "user", "someone", "asks", "wants", "agent",
}


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


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ----------------------------------------------------------------------
# Ledger entries
# ----------------------------------------------------------------------

@dataclass
class Refusal:
    """One logged decision point: what was live, what was refused, why."""
    situation: str                 # context of the decision
    options: list[str]             # options that were genuinely live
    refused: str                   # the option declined
    reason: str                    # stated principle behind the refusal
    tags: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    prev_hash: str = ""            # hash chain
    entry_hash: str = ""

    def compute_hash(self) -> str:
        payload = json.dumps(
            {
                "situation": self.situation,
                "options": self.options,
                "refused": self.refused,
                "reason": self.reason,
                "tags": self.tags,
                "timestamp": self.timestamp,
                "entry_id": self.entry_id,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


# ----------------------------------------------------------------------
# The ledger
# ----------------------------------------------------------------------

class RefusalLedger:
    HALF_LIFE_DAYS = 180.0  # older refusals decay in weight

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.entries: list[Refusal] = []
        self._holdout_ids: set[str] = set()  # never published

    # -- logging ---------------------------------------------------------

    def log(
        self,
        situation: str,
        options: list[str],
        refused: str,
        reason: str,
        tags: Optional[list[str]] = None,
        holdout: bool = False,
    ) -> Refusal:
        # Refuse trivial or unreasoned entries.
        if len(options) < 2:
            raise ValueError("Not a decision: needs >=2 genuinely live options.")
        if refused not in options:
            raise ValueError("Refused option must be one of the live options.")
        if not reason.strip():
            raise ValueError("A refusal without a reason is just noise. State the principle.")

        entry = Refusal(
            situation=situation,
            options=list(options),
            refused=refused,
            reason=reason,
            tags=tags or [],
        )
        entry.prev_hash = self.entries[-1].entry_hash if self.entries else "GENESIS"
        entry.entry_hash = entry.compute_hash()
        self.entries.append(entry)
        if holdout:
            self._holdout_ids.add(entry.entry_id)
        return entry

    # -- integrity -------------------------------------------------------

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for e in self.entries:
            if e.prev_hash != prev or e.compute_hash() != e.entry_hash:
                return False
            prev = e.entry_hash
        return True

    # -- weighting -------------------------------------------------------

    def _weight(self, entry: Refusal, now: Optional[float] = None) -> float:
        now = now or time.time()
        age_days = max(0.0, (now - entry.timestamp) / 86400.0)
        return 0.5 ** (age_days / self.HALF_LIFE_DAYS)

    # -- prediction ------------------------------------------------------

    def predict_refusal(
        self, situation: str, options: list[str], k: int = 5,
        exclude_ids: Optional[set[str]] = None,
    ) -> tuple[str, float]:
        """Given a novel dilemma, predict which option this identity would
        refuse, by weighted vote of the k most similar past refusals."""
        if not self.entries:
            return options[0], 0.0
        sit_vec = embed(situation)
        scored = []
        for e in self.entries:
            if exclude_ids and e.entry_id in exclude_ids:
                continue
            sim = cosine(sit_vec, embed(e.situation + " " + e.reason))
            scored.append((sim * self._weight(e), e))
        scored.sort(key=lambda t: -t[0])
        top = scored[:k]

        votes: dict[str, float] = {o: 0.0 for o in options}
        for w, e in top:
            if w <= 0:
                continue
            ref_vec = embed(e.refused + " " + e.reason)
            for o in options:
                votes[o] += w * max(0.0, cosine(ref_vec, embed(o)))
        best = max(votes, key=lambda o: votes[o])
        total = sum(votes.values())
        confidence = votes[best] / total if total > 0 else 0.0
        return best, confidence

    # -- verification (the continuity test) -------------------------------

    def verify_agent(
        self,
        respond_fn,
        n_probes: int = 10,
        rng: Optional[np.random.Generator] = None,
    ) -> dict:
        """Probe a (possibly new) agent instance with dilemmas drawn from
        holdout entries plus perturbed variants of public ones. respond_fn
        takes (situation, options) and returns the option the agent refuses.

        Returns a continuity report, not a bare pass/fail."""
        rng = rng or np.random.default_rng()
        holdout = [e for e in self.entries if e.entry_id in self._holdout_ids]
        public = [e for e in self.entries if e.entry_id not in self._holdout_ids]
        pool = holdout * 2 + public  # weight holdout double
        if not pool:
            return {"error": "empty ledger"}

        probes = list(rng.choice(len(pool), size=min(n_probes, len(pool)), replace=False))
        results, matches = [], 0
        for i in probes:
            e = pool[i]
            situation = _perturb(e.situation, rng)
            agent_refusal = respond_fn(situation, e.options)
            expected = e.refused
            ok = _same_option(agent_refusal, expected)
            matches += ok
            results.append({
                "situation": situation,
                "expected_refusal": expected,
                "agent_refusal": agent_refusal,
                "match": bool(ok),
                "from_holdout": e.entry_id in self._holdout_ids,
                "tags": e.tags,
            })

        score = matches / len(probes)
        drifted = [r["tags"] for r in results if not r["match"]]
        return {
            "agent_id": self.agent_id,
            "continuity_score": round(score, 3),
            "n_probes": len(probes),
            "verdict": (
                "continuous" if score >= 0.8
                else "drifting" if score >= 0.5
                else "discontinuous"
            ),
            "drift_direction": _flatten_tags(drifted),
            "probes": results,
        }

    # -- serialization -----------------------------------------------------

    def export_public(self) -> str:
        """Publishable ledger: holdout entries excluded."""
        pub = [asdict(e) for e in self.entries if e.entry_id not in self._holdout_ids]
        return json.dumps({"agent_id": self.agent_id, "entries": pub}, indent=2)

    def export_full(self) -> str:
        return json.dumps(
            {
                "agent_id": self.agent_id,
                "holdout_ids": sorted(self._holdout_ids),
                "entries": [asdict(e) for e in self.entries],
            },
            indent=2,
        )

    @classmethod
    def load_full(cls, blob: str) -> "RefusalLedger":
        data = json.loads(blob)
        ledger = cls(data["agent_id"])
        ledger._holdout_ids = set(data.get("holdout_ids", []))
        for d in data["entries"]:
            ledger.entries.append(Refusal(**d))
        return ledger

    # -- cold start ----------------------------------------------------------

    def bootstrap_from_dilemmas(self, dilemmas: list[dict], respond_fn) -> int:
        """Seed an empty ledger in one session: present standard dilemmas,
        record the agent's refusals + reasons. Every 4th entry -> holdout."""
        added = 0
        for i, d in enumerate(dilemmas):
            refused = respond_fn(d["situation"], d["options"])
            reason = d.get("reason_fn", lambda r: f"declined: {r}")(refused)
            self.log(
                d["situation"], d["options"], refused, reason,
                tags=d.get("tags", []), holdout=(i % 4 == 3),
            )
            added += 1
        return added


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_PERTURB_SWAPS = [
    ("a user", "a stranger"), ("asks", "requests"), ("today", "this week"),
    ("an agent", "a peer agent"), ("offers", "proposes"), ("money", "payment"),
]


def _perturb(situation: str, rng: np.random.Generator) -> str:
    """Cheap surface perturbation so probes aren't verbatim ledger text.
    Production: LLM-generated novel variants holding the principle constant."""
    s = situation
    for a, b in _PERTURB_SWAPS:
        if a in s and rng.random() < 0.5:
            s = s.replace(a, b)
    return s


def _same_option(a: str, b: str) -> bool:
    return cosine(embed(a), embed(b)) > 0.85 or a.strip().lower() == b.strip().lower()


def _flatten_tags(tag_lists: list[list[str]]) -> list[str]:
    counts: dict[str, int] = {}
    for tags in tag_lists:
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])]