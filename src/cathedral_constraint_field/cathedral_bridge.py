"""
Cathedral bridge for RefusalLedger.

Stores the ledger as a Cathedral memory and recovers it on startup, so
refusal identity persists across sessions.

Usage:
    from cathedral_constraint_field.cathedral_bridge import CathedralBridge

    bridge = CathedralBridge(api_key="cathedral_...", agent_id="my-agent")
    ledger = bridge.load_or_create()          # recover existing or start fresh
    # ... use ledger normally ...
    bridge.save(ledger)                        # persist to Cathedral
    bridge.snapshot(ledger)                   # optional: anchor a snapshot
"""

from __future__ import annotations

import json
import os
import time
import urllib.error as _urlerr
import urllib.parse as _urlparse
import urllib.request as _urllib
from typing import Optional

from .refusal_ledger import RefusalLedger

_BASE = "https://cathedral-ai.com/api"
_MEMORY_CATEGORY = "refusal_ledger"
_MEMORY_IMPORTANCE = 8  # high — identity-critical
_SEARCH_LIMIT = 20      # fetch enough to find the exact-name match
_MAX_RETRIES = 2


def _request(method: str, path: str, payload: Optional[dict], api_key: str) -> dict:
    url = f"{_BASE}{path}"
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {"X-API-Key": api_key}
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
            # 4xx errors are caller errors — don't retry, surface them directly
            if 400 <= e.code < 500:
                raise RuntimeError(
                    f"Cathedral API {method} {path} → {e.code}"
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
        self._memory_name = f"refusal_ledger:{agent_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_or_create(self) -> RefusalLedger:
        """Return the persisted ledger, or a fresh one if none exists."""
        blob = self._fetch_ledger_blob()
        if blob is None:
            return RefusalLedger(agent_id=self.agent_id)
        try:
            ledger = RefusalLedger.load_full(blob)
        except Exception as exc:
            raise RuntimeError(
                f"Ledger blob found in Cathedral but failed to parse: {exc}"
            ) from exc
        if not ledger.verify_chain():
            raise RuntimeError(
                "Recovered ledger has a broken hash chain — "
                "possible tampering or corruption. Refusing to use it."
            )
        return ledger

    def save(self, ledger: RefusalLedger) -> dict:
        """Upsert the full ledger (including holdouts) into Cathedral memories.

        Before saving, re-fetches the stored version and verifies the stored
        chain is a prefix of the local chain, refusing on divergence to prevent
        silent overwrites in concurrent-session scenarios.
        """
        if not ledger.verify_chain():
            raise ValueError("Ledger hash chain is broken — refusing to persist corrupt state.")

        existing_id = self._find_memory_id()

        if existing_id:
            # Concurrent-safety check: stored chain must be a prefix of local.
            stored_blob = self._fetch_blob_by_id(existing_id)
            if stored_blob:
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
                    pass  # parse failure on stored — let save proceed and overwrite corrupt data

            blob = self._compact_blob(ledger)
            return _request(
                "PATCH",
                f"/memories/{existing_id}",
                {
                    "content": blob,
                    "importance": _MEMORY_IMPORTANCE,
                },
                self.api_key,
            )
        else:
            blob = self._compact_blob(ledger)
            return _request(
                "POST",
                "/memories",
                {
                    "name": self._memory_name,
                    "content": blob,
                    "category": _MEMORY_CATEGORY,
                    "importance": _MEMORY_IMPORTANCE,
                },
                self.api_key,
            )

    def snapshot(self, ledger: RefusalLedger, note: str = "") -> dict:
        """Take a Cathedral snapshot with refusal stats + chain head hash in the note.

        Note: Cathedral /snapshot and /drift operate on the whole account's
        memory corpus, not just this agent's ledger. Keep that in mind when
        reading drift scores if multiple agents share one API key.
        """
        stats = self._ledger_stats(ledger)
        chain_head = ledger.entries[-1].entry_hash if ledger.entries else "empty"
        full_note = f"refusal_ledger:{self.agent_id} | {stats} | head={chain_head[:16]}"
        if note:
            full_note += f" | {note}"
        return _request("POST", "/snapshot", {"note": full_note}, self.api_key)

    def drift(self) -> dict:
        """Return the current drift report from Cathedral.

        Note: this reflects the entire account's memory corpus, not only
        this agent's ledger.
        """
        return _request("GET", "/drift", None, self.api_key)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_memory_id(self) -> Optional[str]:
        """Search Cathedral for an existing ledger memory, return its ID or None.

        Raises on auth/transport errors; only returns None for genuine absence.
        """
        query = _urlparse.quote(self._memory_name)
        result = _request(
            "GET",
            f"/memories?search={query}&limit={_SEARCH_LIMIT}",
            None,
            self.api_key,
        )
        for m in result.get("memories", []):
            if m.get("name") == self._memory_name:
                return m["id"]
        return None

    def _fetch_blob_by_id(self, mem_id: str) -> Optional[str]:
        try:
            result = _request("GET", f"/memories/{mem_id}", None, self.api_key)
            return result.get("content")
        except Exception:
            return None

    def _fetch_ledger_blob(self) -> Optional[str]:
        """Return the raw JSON blob stored in Cathedral, or None if absent."""
        mem_id = self._find_memory_id()
        if not mem_id:
            return None
        return self._fetch_blob_by_id(mem_id)

    @staticmethod
    def _compact_blob(ledger: RefusalLedger) -> str:
        """Compact JSON serialisation — no indent, to minimise content size."""
        return json.dumps(json.loads(ledger.export_full()), separators=(",", ":"))

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
    """True if stored.entries is a prefix of local.entries (by entry_id order)."""
    if len(stored.entries) > len(local.entries):
        return False
    for s, l in zip(stored.entries, local.entries):
        if s.entry_id != l.entry_id or s.entry_hash != l.entry_hash:
            return False
    return True
