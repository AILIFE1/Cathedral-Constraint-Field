"""
Cathedral bridge for RefusalLedger and CompletenessManifest.

Stores ledger/manifest state as Cathedral memories so they survive across sessions.

Usage:
    from cathedral_constraint_field.cathedral_bridge import CathedralBridge

    bridge = CathedralBridge(api_key="cathedral_...", agent_id="my-agent")
    ledger = bridge.load_or_create()          # recover existing or start fresh
    # ... use ledger normally ...
    bridge.save(ledger)                        # persist to Cathedral
    bridge.snapshot(ledger)                   # optional: anchor a snapshot

    # CompletenessManifest:
    manifest.seal()
    bridge.store_manifest(manifest)           # store Merkle root in Cathedral
    bridge.snapshot_manifest(manifest)        # BCH-anchor via Cathedral snapshot
"""

from __future__ import annotations

import json
import os
import time
import urllib.error as _urlerr
import urllib.parse as _urlparse
import urllib.request as _urllib
from typing import TYPE_CHECKING, Optional

from .refusal_ledger import RefusalLedger

if TYPE_CHECKING:
    from .completeness_manifest import CompletenessManifest

_BASE = "https://cathedral-ai.com"
_MEMORY_CATEGORY = "identity"
_MEMORY_IMPORTANCE = 0.9      # 0–1 float; high — identity-critical
_SEARCH_LIMIT = 20
_MAX_RETRIES = 2

# Content sentinels — every stored memory starts with one of these lines so we
# can find them by prefix (Cathedral has no separate name field).
_SENTINEL_PREFIX = "REFUSAL_LEDGER:"
_MANIFEST_SENTINEL_PREFIX = "COMPLETENESS_MANIFEST:"


def _request(method: str, path: str, payload: Optional[dict], api_key: str) -> dict:
    url = f"{_BASE}{path}"
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "cathedral-constraint-field/0.2",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(_MAX_RETRIES + 1):
        req = _urllib.Request(url, data=data, headers=headers, method=method)
        try:
            with _urllib.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except _urlerr.HTTPError as e:
            body = e.read().decode(errors="replace")
            if 400 <= e.code < 500:
                raise RuntimeError(
                    f"Cathedral API {method} {path} → {e.code}: {body}"
                ) from e
            last_exc = RuntimeError(f"Cathedral API {method} {path} → {e.code}: {body}")
        except OSError as e:
            last_exc = e
        if attempt < _MAX_RETRIES:
            time.sleep(1.5 ** attempt)

    raise last_exc


class CathedralBridge:
    """Thin bridge between a RefusalLedger and the Cathedral memory API."""

    def __init__(self, api_key: Optional[str] = None, agent_id: str = "default"):
        self.api_key = api_key or os.environ.get("CATHEDRAL_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Cathedral API key required. Pass api_key= or set CATHEDRAL_API_KEY."
            )
        self.agent_id = agent_id
        self._sentinel = f"{_SENTINEL_PREFIX}{agent_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_or_create(self) -> RefusalLedger:
        """Return the persisted ledger, or a fresh one if none exists."""
        result = self._fetch_ledger()
        if result is None:
            return RefusalLedger(agent_id=self.agent_id)
        mem_id, blob = result
        try:
            ledger = RefusalLedger.load_full(blob)
        except Exception as exc:
            raise RuntimeError(
                f"Ledger found in Cathedral (id={mem_id}) but failed to parse: {exc}"
            ) from exc
        if not ledger.verify_chain():
            raise RuntimeError(
                f"Recovered ledger (id={mem_id}) has a broken hash chain — "
                "possible tampering or corruption. Refusing to use it."
            )
        return ledger

    def save(self, ledger: RefusalLedger) -> dict:
        """Upsert the full ledger into Cathedral memories.

        Before saving, re-fetches the stored version and checks the stored
        chain is a prefix of the local chain to guard against concurrent overwrites.
        """
        if not ledger.verify_chain():
            raise ValueError("Ledger hash chain is broken — refusing to persist corrupt state.")

        existing = self._fetch_ledger()

        if existing:
            mem_id, stored_blob = existing
            try:
                stored = RefusalLedger.load_full(stored_blob)
                if stored.entries and not _is_prefix(stored, ledger):
                    raise RuntimeError(
                        f"Stored ledger ({len(stored.entries)} entries) diverges from "
                        f"local ({len(ledger.entries)} entries). Resolve the conflict "
                        "manually before saving."
                    )
            except RuntimeError:
                raise
            except Exception:
                pass  # corrupt stored data — let save overwrite it

            return _request(
                "PATCH",
                f"/memories/{mem_id}",
                {"content": self._encode(ledger)},
                self.api_key,
            )
        else:
            return _request(
                "POST",
                "/memories",
                {
                    "content": self._encode(ledger),
                    "category": _MEMORY_CATEGORY,
                    "importance": _MEMORY_IMPORTANCE,
                },
                self.api_key,
            )

    def snapshot(self, ledger: RefusalLedger, note: str = "") -> dict:
        """Take a Cathedral snapshot with refusal stats + chain head hash in the note.

        Note: Cathedral /snapshot and /drift operate on the whole account's
        memory corpus, not just this agent's ledger.
        """
        stats = self._ledger_stats(ledger)
        chain_head = ledger.entries[-1].entry_hash if ledger.entries else "empty"
        full_note = f"refusal_ledger:{self.agent_id} | {stats} | head={chain_head[:16]}"
        if note:
            full_note += f" | {note}"
        return _request("POST", "/snapshot", {"note": full_note}, self.api_key)

    def drift(self) -> dict:
        """Return the current drift report from Cathedral.

        Note: reflects the entire account's memory corpus, not only this agent's ledger.
        """
        return _request("GET", "/drift", None, self.api_key)

    def store_manifest(self, manifest: "CompletenessManifest") -> dict:
        """Persist a sealed manifest's Merkle root + chain tip to Cathedral memory.

        The manifest must already be sealed (``manifest.seal()``). Only the root
        and chain metadata are stored — not the full item list — so the memory is
        compact while still being verifiable against any exported proof.

        Upserts: calling again after adding more entries and re-sealing will
        PATCH the existing memory rather than creating a duplicate.
        """
        from .completeness_manifest import CompletenessManifest as _CM  # avoid circular at runtime

        if not manifest._sealed:
            raise ValueError(
                "Manifest must be sealed before storing. Call manifest.seal() first."
            )
        if not manifest.verify_chain():
            raise ValueError("Manifest hash chain is broken — refusing to persist corrupt state.")

        sentinel = f"{_MANIFEST_SENTINEL_PREFIX}{self.agent_id}:{manifest.manifest_id}"
        root = manifest._merkle.root  # type: ignore[union-attr]
        chain_tip = manifest.entries[-1].entry_hash if manifest.entries else "EMPTY"
        hb_tip = manifest.heartbeats[-1].heartbeat_hash if manifest.heartbeats else "NONE"

        payload = json.dumps(
            {
                "manifest_id": manifest.manifest_id,
                "agent_id": self.agent_id,
                "entries": len(manifest.entries),
                "merkle_root": root,
                "chain_tip": chain_tip,
                "heartbeat_tip": hb_tip,
                "heartbeat_count": len(manifest.heartbeats),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        mem_content = f"{sentinel}\n{payload}"

        existing_id = self._find_manifest_memory_id(manifest.manifest_id)
        if existing_id:
            return _request(
                "PATCH",
                f"/memories/{existing_id}",
                {"content": mem_content},
                self.api_key,
            )
        return _request(
            "POST",
            "/memories",
            {
                "content": mem_content,
                "category": "completeness",
                "importance": 0.85,
            },
            self.api_key,
        )

    def load_manifest_root(self, manifest_id: str) -> Optional[dict]:
        """Retrieve stored manifest metadata from Cathedral.

        Returns a dict with ``merkle_root``, ``chain_tip``, ``entries``, etc.,
        or ``None`` if no manifest with that ID is stored for this agent.
        """
        existing_id = self._find_manifest_memory_id(manifest_id)
        if not existing_id:
            return None
        result = _request("GET", f"/memories/{existing_id}", None, self.api_key)
        content = result.get("memory", {}).get("content", "")
        sentinel = f"{_MANIFEST_SENTINEL_PREFIX}{self.agent_id}:{manifest_id}\n"
        if not content.startswith(sentinel):
            return None
        return json.loads(content[len(sentinel):])

    def snapshot_manifest(self, manifest: "CompletenessManifest", note: str = "") -> dict:
        """Take a Cathedral snapshot that includes the manifest Merkle root in its note.

        Cathedral already BCH-anchors every snapshot via OP_RETURN, so this call
        anchors the manifest root to the Bitcoin Cash chain as a tamper-evident
        provenance record.

        The manifest must be sealed first.
        """
        if not manifest._sealed:
            raise ValueError(
                "Manifest must be sealed before anchoring. Call manifest.seal() first."
            )
        root = manifest._merkle.root  # type: ignore[union-attr]
        chain_tip = manifest.entries[-1].entry_hash if manifest.entries else "EMPTY"
        full_note = (
            f"completeness_manifest:{manifest.manifest_id} | agent:{self.agent_id} | "
            f"entries={len(manifest.entries)} | root={root[:16]} | tip={chain_tip[:16]}"
        )
        if note:
            full_note += f" | {note}"
        return _request("POST", "/snapshot", {"note": full_note}, self.api_key)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_memory_id(self) -> Optional[str]:
        """Search for an existing ledger memory by sentinel prefix. Returns ID or None."""
        query = _urlparse.quote(self._sentinel)
        result = _request(
            "GET",
            f"/memories?search={query}&limit={_SEARCH_LIMIT}",
            None,
            self.api_key,
        )
        for m in result.get("memories", []):
            if m.get("content", "").startswith(self._sentinel + "\n"):
                return m["id"]
        return None

    def _find_manifest_memory_id(self, manifest_id: str) -> Optional[str]:
        """Search for an existing manifest memory by sentinel prefix. Returns ID or None."""
        sentinel = f"{_MANIFEST_SENTINEL_PREFIX}{self.agent_id}:{manifest_id}"
        query = _urlparse.quote(sentinel)
        result = _request(
            "GET",
            f"/memories?search={query}&limit={_SEARCH_LIMIT}",
            None,
            self.api_key,
        )
        for m in result.get("memories", []):
            if m.get("content", "").startswith(sentinel + "\n"):
                return m["id"]
        return None

    def _fetch_ledger(self) -> Optional[tuple[str, str]]:
        """Return (memory_id, json_blob) if a ledger exists, else None."""
        mem_id = self._find_memory_id()
        if not mem_id:
            return None
        result = _request("GET", f"/memories/{mem_id}", None, self.api_key)
        content = result.get("memory", {}).get("content", "")
        blob = self._decode(content)
        if blob is None:
            return None
        return mem_id, blob

    def _encode(self, ledger: RefusalLedger) -> str:
        """Sentinel-prefixed compact JSON for storage."""
        compact = json.dumps(json.loads(ledger.export_full()), separators=(",", ":"))
        return f"{self._sentinel}\n{compact}"

    def _decode(self, content: str) -> Optional[str]:
        """Strip sentinel prefix and return raw JSON, or None if malformed."""
        prefix = self._sentinel + "\n"
        if not content.startswith(prefix):
            return None
        return content[len(prefix):]

    @staticmethod
    def _ledger_stats(ledger: RefusalLedger) -> str:
        n = len(ledger.entries)
        h = len(ledger._holdout_ids)
        chain_ok = ledger.verify_chain()
        tags: dict[str, int] = {}
        for e in ledger.entries:
            for t in e.tags:
                tags[t] = tags.get(t, 0) + 1
        top = sorted(tags, key=lambda k: -tags[k])[:3]
        return (
            f"entries={n} holdouts={h} chain={'ok' if chain_ok else 'BROKEN'} "
            f"top_tags={','.join(top) or 'none'}"
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _is_prefix(stored: RefusalLedger, local: RefusalLedger) -> bool:
    """True if stored.entries is a prefix of local.entries (by entry_id + hash)."""
    if len(stored.entries) > len(local.entries):
        return False
    for s, l in zip(stored.entries, local.entries):
        if s.entry_id != l.entry_id or s.entry_hash != l.entry_hash:
            return False
    return True
