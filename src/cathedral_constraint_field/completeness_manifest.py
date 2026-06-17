"""
CompletenessManifest — provable non-membership / training-data manifests.

Transforms absence into a verifiable proof by combining:
  * Append-only hash chains (tamper-evident, Cathedral-provenance style)
  * Sorted Merkle trees  (non-membership proofs via sorted adjacency)
  * Time-anchored heartbeat commitments (epoch-bound existence proofs)

Design properties:
  * Leaf hashes bind item + index + total set size (forged-index attacks fail)
  * Non-membership proofs use authenticated sorted adjacency
  * Sealed manifests reject new entries
  * Heartbeat chain provides temporal provenance

Written to be a sibling to RefusalLedger: same append-only, hash-chained
integrity philosophy, different proof primitive.

Written by fable 5
Part of the Cathedral-Constraint-Field project.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Low-level hashing helpers
# ---------------------------------------------------------------------------

def _sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _leaf_hash(item: str, index: int, total: int) -> str:
    """Leaf hash binds item + position + set size — prevents index forgery."""
    payload = json.dumps(
        {"item": item, "index": index, "total": total}, sort_keys=True
    )
    return _sha256(payload)


def _node_hash(left: str, right: str) -> str:
    return _sha256(left + right)


# ---------------------------------------------------------------------------
# Sorted Merkle tree
# ---------------------------------------------------------------------------

class SortedMerkle:
    """
    A sorted Merkle tree over a frozen set of string items.

    Sorting enables non-membership proofs: to prove X ∉ S, present the
    adjacent pair (a, b) with a < X < b and Merkle proofs for both.
    Edge cases: X < min(S) or X > max(S) use the first/last leaf.

    Leaf hash formula:  SHA-256(json({"item": item, "index": i, "total": n}))
    Node hash formula:  SHA-256(left_hash + right_hash)
    Odd levels:         last leaf is paired with itself.
    """

    def __init__(self, items: list[str]) -> None:
        self._items: list[str] = sorted(set(items))
        n = len(self._items)
        if n == 0:
            self._root = _sha256("EMPTY_SORTED_MERKLE")
            self._tree: list[list[str]] = []
            return
        leaves = [_leaf_hash(item, i, n) for i, item in enumerate(self._items)]
        self._tree = [leaves]
        current = leaves
        while len(current) > 1:
            next_level: list[str] = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else current[i]
                next_level.append(_node_hash(left, right))
            current = next_level
            self._tree.append(current)
        self._root = self._tree[-1][0]

    @property
    def root(self) -> str:
        return self._root

    @property
    def items(self) -> list[str]:
        return list(self._items)

    def _proof_path(self, index: int) -> list[dict]:
        """Sibling hashes needed to reconstruct the root from leaf[index]."""
        path: list[dict] = []
        i = index
        for level in self._tree[:-1]:
            if i % 2 == 0:
                sibling_i = i + 1 if i + 1 < len(level) else i
                side = "right"
            else:
                sibling_i = i - 1
                side = "left"
            path.append({"hash": level[sibling_i], "side": side})
            i //= 2
        return path

    def prove_membership(self, item: str) -> Optional[dict]:
        """Return a Merkle membership proof, or None if item is not in the tree."""
        if item not in self._items:
            return None
        index = self._items.index(item)
        n = len(self._items)
        return {
            "type": "membership",
            "item": item,
            "index": index,
            "total": n,
            "leaf_hash": _leaf_hash(item, index, n),
            "path": self._proof_path(index),
            "root": self._root,
        }

    def prove_non_membership(self, item: str) -> Optional[dict]:
        """
        Return a non-membership proof for item, or None if item IS in the tree.

        Proof types:
          edge="left"     — item < all entries; proves first leaf is smallest
          edge="right"    — item > all entries; proves last leaf is largest
          edge="interior" — item falls between two adjacent leaves
        """
        if item in self._items:
            return None
        n = len(self._items)
        if n == 0:
            return {
                "type": "non_membership",
                "item": item,
                "empty": True,
                "root": self._root,
            }

        pos = bisect.bisect_left(self._items, item)

        if pos == 0:
            right_item = self._items[0]
            return {
                "type": "non_membership",
                "item": item,
                "edge": "left",
                "right_item": right_item,
                "right_index": 0,
                "right_leaf_hash": _leaf_hash(right_item, 0, n),
                "right_path": self._proof_path(0),
                "total": n,
                "root": self._root,
            }
        elif pos == n:
            left_item = self._items[-1]
            left_idx = n - 1
            return {
                "type": "non_membership",
                "item": item,
                "edge": "right",
                "left_item": left_item,
                "left_index": left_idx,
                "left_leaf_hash": _leaf_hash(left_item, left_idx, n),
                "left_path": self._proof_path(left_idx),
                "total": n,
                "root": self._root,
            }
        else:
            left_item = self._items[pos - 1]
            right_item = self._items[pos]
            left_idx = pos - 1
            right_idx = pos
            return {
                "type": "non_membership",
                "item": item,
                "edge": "interior",
                "left_item": left_item,
                "left_index": left_idx,
                "left_leaf_hash": _leaf_hash(left_item, left_idx, n),
                "left_path": self._proof_path(left_idx),
                "right_item": right_item,
                "right_index": right_idx,
                "right_leaf_hash": _leaf_hash(right_item, right_idx, n),
                "right_path": self._proof_path(right_idx),
                "total": n,
                "root": self._root,
            }

    @staticmethod
    def verify_membership(proof: dict) -> bool:
        """Verify a membership proof against its claimed root."""
        try:
            item = proof["item"]
            index = proof["index"]
            total = proof["total"]
            claimed_root = proof["root"]
            if proof["leaf_hash"] != _leaf_hash(item, index, total):
                return False
            current = proof["leaf_hash"]
            i = index
            for step in proof["path"]:
                if step["side"] == "right":
                    current = _node_hash(current, step["hash"])
                else:
                    current = _node_hash(step["hash"], current)
                i //= 2
            return current == claimed_root
        except (KeyError, TypeError):
            return False

    @staticmethod
    def verify_non_membership(proof: dict) -> bool:
        """Verify a non-membership proof."""
        try:
            item = proof["item"]
            claimed_root = proof["root"]

            if proof.get("empty"):
                return True

            total = proof["total"]
            edge = proof.get("edge")

            def _verify_leaf(it: str, idx: int, leaf_hash: str, path: list) -> bool:
                if leaf_hash != _leaf_hash(it, idx, total):
                    return False
                current = leaf_hash
                i = idx
                for step in path:
                    if step["side"] == "right":
                        current = _node_hash(current, step["hash"])
                    else:
                        current = _node_hash(step["hash"], current)
                    i //= 2
                return current == claimed_root

            if edge == "left":
                right_item = proof["right_item"]
                if not (item < right_item):
                    return False
                if proof["right_index"] != 0:
                    return False
                return _verify_leaf(
                    right_item, 0, proof["right_leaf_hash"], proof["right_path"]
                )

            elif edge == "right":
                left_item = proof["left_item"]
                if not (item > left_item):
                    return False
                if proof["left_index"] != total - 1:
                    return False
                return _verify_leaf(
                    left_item, total - 1, proof["left_leaf_hash"], proof["left_path"]
                )

            elif edge == "interior":
                left_item = proof["left_item"]
                right_item = proof["right_item"]
                left_idx = proof["left_index"]
                right_idx = proof["right_index"]
                if not (left_item < item < right_item):
                    return False
                if right_idx != left_idx + 1:
                    return False
                left_ok = _verify_leaf(
                    left_item, left_idx, proof["left_leaf_hash"], proof["left_path"]
                )
                right_ok = _verify_leaf(
                    right_item, right_idx, proof["right_leaf_hash"], proof["right_path"]
                )
                return left_ok and right_ok

            return False
        except (KeyError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Manifest entries
# ---------------------------------------------------------------------------

@dataclass
class ManifestEntry:
    item: str
    category: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    prev_hash: str = ""
    entry_hash: str = ""

    def compute_hash(self) -> str:
        payload = json.dumps(
            {
                "item": self.item,
                "category": self.category,
                "metadata": self.metadata,
                "timestamp": self.timestamp,
                "entry_id": self.entry_id,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
        )
        return _sha256(payload)


@dataclass
class Heartbeat:
    epoch: int
    timestamp: float
    commitment_root: str
    chain_tip: str
    prev_heartbeat_hash: str
    heartbeat_hash: str = ""

    def compute_hash(self) -> str:
        payload = json.dumps(
            {
                "epoch": self.epoch,
                "timestamp": self.timestamp,
                "commitment_root": self.commitment_root,
                "chain_tip": self.chain_tip,
                "prev_heartbeat_hash": self.prev_heartbeat_hash,
            },
            sort_keys=True,
        )
        return _sha256(payload)


# ---------------------------------------------------------------------------
# CompletenessManifest
# ---------------------------------------------------------------------------

class CompletenessManifest:
    """
    An append-only manifest that proves what it contains — and what it does not.

    Usage pattern:
      1. ``add()`` items as they are processed
      2. ``heartbeat()`` periodically to anchor the state in time
      3. ``seal()`` to finalise the Merkle tree
      4. ``prove_non_membership()`` to generate absence proofs
      5. ``verify_non_membership()`` / ``SortedMerkle.verify_non_membership()``
         to check proofs without the manifest present
    """

    def __init__(self, manifest_id: str) -> None:
        self.manifest_id = manifest_id
        self.entries: list[ManifestEntry] = []
        self.heartbeats: list[Heartbeat] = []
        self._sealed = False
        self._merkle: Optional[SortedMerkle] = None

    # -- mutation ------------------------------------------------------------

    def add(
        self,
        item: str,
        category: str = "",
        metadata: Optional[dict] = None,
    ) -> ManifestEntry:
        """Append an item to the manifest. Raises if already sealed."""
        if self._sealed:
            raise RuntimeError("Manifest is sealed; no new entries allowed.")
        entry = ManifestEntry(
            item=item,
            category=category,
            metadata=metadata or {},
        )
        entry.prev_hash = self.entries[-1].entry_hash if self.entries else "GENESIS"
        entry.entry_hash = entry.compute_hash()
        self.entries.append(entry)
        self._merkle = None  # invalidate cached tree
        return entry

    def seal(self) -> str:
        """Seal the manifest, build the Merkle tree, and return the root."""
        if not self._sealed:
            self._sealed = True
            self._merkle = SortedMerkle([e.item for e in self.entries])
        return self._merkle.root  # type: ignore[union-attr]

    def heartbeat(self) -> Heartbeat:
        """Record a time-anchored commitment against the current state."""
        if self._merkle is None:
            self._merkle = SortedMerkle([e.item for e in self.entries])
        commitment_root = self._merkle.root
        chain_tip = self.entries[-1].entry_hash if self.entries else "EMPTY"
        prev_hb_hash = (
            self.heartbeats[-1].heartbeat_hash if self.heartbeats else "GENESIS"
        )
        hb = Heartbeat(
            epoch=len(self.heartbeats),
            timestamp=time.time(),
            commitment_root=commitment_root,
            chain_tip=chain_tip,
            prev_heartbeat_hash=prev_hb_hash,
        )
        hb.heartbeat_hash = hb.compute_hash()
        self.heartbeats.append(hb)
        return hb

    # -- integrity -----------------------------------------------------------

    def verify_chain(self) -> bool:
        """Verify the append-only entry chain has not been tampered with."""
        prev = "GENESIS"
        for e in self.entries:
            if e.prev_hash != prev or e.compute_hash() != e.entry_hash:
                return False
            prev = e.entry_hash
        return True

    def verify_heartbeat_chain(self) -> bool:
        """Verify the heartbeat chain has not been tampered with."""
        prev = "GENESIS"
        for hb in self.heartbeats:
            if hb.prev_heartbeat_hash != prev or hb.compute_hash() != hb.heartbeat_hash:
                return False
            prev = hb.heartbeat_hash
        return True

    # -- proofs --------------------------------------------------------------

    def _ensure_merkle(self) -> SortedMerkle:
        if self._merkle is None:
            self._merkle = SortedMerkle([e.item for e in self.entries])
        return self._merkle

    def prove_non_membership(self, item: str) -> Optional[dict]:
        """Return a non-membership proof, or None if item is in the manifest."""
        return self._ensure_merkle().prove_non_membership(item)

    def prove_membership(self, item: str) -> Optional[dict]:
        """Return a membership proof, or None if item is not in the manifest."""
        return self._ensure_merkle().prove_membership(item)

    # -- serialization -------------------------------------------------------

    def export(self) -> str:
        """Export the full manifest as JSON."""
        return json.dumps(
            {
                "manifest_id": self.manifest_id,
                "sealed": self._sealed,
                "entries": [asdict(e) for e in self.entries],
                "heartbeats": [asdict(hb) for hb in self.heartbeats],
            },
            indent=2,
        )

    @classmethod
    def load(cls, blob: str) -> "CompletenessManifest":
        """Reconstruct a manifest from an exported JSON string."""
        data = json.loads(blob)
        m = cls(data["manifest_id"])
        for d in data["entries"]:
            m.entries.append(ManifestEntry(**d))
        for d in data["heartbeats"]:
            m.heartbeats.append(Heartbeat(**d))
        if data.get("sealed"):
            m._sealed = True
            m._merkle = SortedMerkle([e.item for e in m.entries])
        return m
