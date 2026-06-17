"""Tests for CathedralBridge manifest integration methods."""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from cathedral_constraint_field import CathedralBridge, CompletenessManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bridge() -> CathedralBridge:
    return CathedralBridge(api_key="cathedral_test_key", agent_id="test-agent")


def _sealed_manifest(n: int = 3) -> CompletenessManifest:
    m = CompletenessManifest("corpus-v1")
    for i in range(n):
        m.add(f"item_{i:04d}", category="doc")
    m.heartbeat()
    m.seal()
    return m


# ---------------------------------------------------------------------------
# store_manifest
# ---------------------------------------------------------------------------

class TestStoreManifest:
    def test_store_posts_new_memory(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest()

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.side_effect = [
                {"memories": []},   # _find_manifest_memory_id search → empty
                {"id": "mem_123"},  # POST /memories
            ]
            result = bridge.store_manifest(manifest)

        assert result == {"id": "mem_123"}
        post_call = mock_req.call_args_list[1]
        method, path, payload, _ = post_call[0]
        assert method == "POST"
        assert path == "/memories"
        assert payload["category"] == "completeness"
        assert "merkle_root" in payload["content"]
        assert "COMPLETENESS_MANIFEST:test-agent:corpus-v1" in payload["content"]

    def test_store_patches_existing_memory(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest()
        sentinel = "COMPLETENESS_MANIFEST:test-agent:corpus-v1"

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.side_effect = [
                {"memories": [{"id": "mem_existing", "content": sentinel + "\n{}"}]},
                {"id": "mem_existing"},  # PATCH /memories/mem_existing
            ]
            bridge.store_manifest(manifest)

        patch_call = mock_req.call_args_list[1]
        method, path, _, _ = patch_call[0]
        assert method == "PATCH"
        assert path == "/memories/mem_existing"

    def test_store_raises_if_not_sealed(self):
        bridge = _make_bridge()
        manifest = CompletenessManifest("unsealed")
        manifest.add("item")
        with pytest.raises(ValueError, match="sealed"):
            bridge.store_manifest(manifest)

    def test_store_raises_if_chain_broken(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest()
        manifest.entries[0].item = "TAMPERED"  # break the chain
        with pytest.raises(ValueError, match="hash chain is broken"):
            bridge.store_manifest(manifest)

    def test_stored_content_has_correct_fields(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest(5)

        captured_payload = {}

        def _fake_request(method, path, payload, api_key):
            if method == "POST":
                captured_payload.update(payload)
                return {"id": "mem_new"}
            return {"memories": []}

        with patch("cathedral_constraint_field.cathedral_bridge._request", side_effect=_fake_request):
            bridge.store_manifest(manifest)

        content = captured_payload["content"]
        sentinel_line, json_part = content.split("\n", 1)
        assert sentinel_line == "COMPLETENESS_MANIFEST:test-agent:corpus-v1"
        data = json.loads(json_part)
        assert data["manifest_id"] == "corpus-v1"
        assert data["agent_id"] == "test-agent"
        assert data["entries"] == 5
        assert len(data["merkle_root"]) == 64  # hex SHA-256
        assert len(data["chain_tip"]) == 64


# ---------------------------------------------------------------------------
# load_manifest_root
# ---------------------------------------------------------------------------

class TestLoadManifestRoot:
    def test_returns_none_when_not_found(self):
        bridge = _make_bridge()
        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.return_value = {"memories": []}
            result = bridge.load_manifest_root("corpus-v1")
        assert result is None

    def test_returns_dict_when_found(self):
        bridge = _make_bridge()
        sentinel = "COMPLETENESS_MANIFEST:test-agent:corpus-v1"
        stored_data = {
            "manifest_id": "corpus-v1",
            "agent_id": "test-agent",
            "entries": 5,
            "merkle_root": "a" * 64,
            "chain_tip": "b" * 64,
            "heartbeat_tip": "c" * 64,
            "heartbeat_count": 1,
        }
        mem_content = f"{sentinel}\n{json.dumps(stored_data)}"

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.side_effect = [
                {"memories": [{"id": "mem_123", "content": mem_content}]},
                {"memory": {"content": mem_content}},
            ]
            result = bridge.load_manifest_root("corpus-v1")

        assert result is not None
        assert result["manifest_id"] == "corpus-v1"
        assert result["entries"] == 5
        assert result["merkle_root"] == "a" * 64

    def test_ignores_memory_with_wrong_sentinel(self):
        bridge = _make_bridge()
        wrong_sentinel = "COMPLETENESS_MANIFEST:other-agent:corpus-v1"
        mem_content = f"{wrong_sentinel}\n{{}}"

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.side_effect = [
                {"memories": []},  # search returns nothing matching
            ]
            result = bridge.load_manifest_root("corpus-v1")

        assert result is None

    def test_roundtrip_store_and_load(self):
        """store_manifest followed by load_manifest_root returns equivalent data."""
        bridge = _make_bridge()
        manifest = _sealed_manifest(4)

        stored_content: dict = {}

        def _fake_request(method, path, payload, api_key):
            if method == "GET" and path.startswith("/memories?"):
                if stored_content:
                    sentinel = "COMPLETENESS_MANIFEST:test-agent:corpus-v1"
                    return {"memories": [{"id": "mem_1", "content": stored_content["c"]}]}
                return {"memories": []}
            if method == "POST" and path == "/memories":
                stored_content["c"] = payload["content"]
                return {"id": "mem_1"}
            if method == "GET" and path == "/memories/mem_1":
                return {"memory": {"content": stored_content["c"]}}
            return {}

        with patch("cathedral_constraint_field.cathedral_bridge._request", side_effect=_fake_request):
            bridge.store_manifest(manifest)
            loaded = bridge.load_manifest_root("corpus-v1")

        assert loaded is not None
        assert loaded["merkle_root"] == manifest._merkle.root
        assert loaded["entries"] == 4


# ---------------------------------------------------------------------------
# snapshot_manifest
# ---------------------------------------------------------------------------

class TestSnapshotManifest:
    def test_snapshot_includes_manifest_root_in_note(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest()

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.return_value = {"snapshot_id": "snap_1"}
            bridge.snapshot_manifest(manifest)

        mock_req.assert_called_once()
        method, path, payload, _ = mock_req.call_args[0]
        assert method == "POST"
        assert path == "/snapshot"
        note = payload["note"]
        assert "completeness_manifest:corpus-v1" in note
        assert "agent:test-agent" in note
        assert manifest._merkle.root[:16] in note

    def test_snapshot_raises_if_not_sealed(self):
        bridge = _make_bridge()
        manifest = CompletenessManifest("unsealed")
        manifest.add("item")
        with pytest.raises(ValueError, match="sealed"):
            bridge.snapshot_manifest(manifest)

    def test_snapshot_passes_through_note(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest()

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.return_value = {}
            bridge.snapshot_manifest(manifest, note="gdpr-erasure-run-2026-06-17")

        _, _, payload, _ = mock_req.call_args[0]
        assert "gdpr-erasure-run-2026-06-17" in payload["note"]

    def test_snapshot_entry_count_in_note(self):
        bridge = _make_bridge()
        manifest = _sealed_manifest(7)

        with patch("cathedral_constraint_field.cathedral_bridge._request") as mock_req:
            mock_req.return_value = {}
            bridge.snapshot_manifest(manifest)

        _, _, payload, _ = mock_req.call_args[0]
        assert "entries=7" in payload["note"]
