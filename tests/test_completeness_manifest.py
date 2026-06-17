"""Tests for CompletenessManifest and SortedMerkle (written by fable 5)."""

import json
import time

import pytest

from cathedral_constraint_field import CompletenessManifest, SortedMerkle
from cathedral_constraint_field.completeness_manifest import (
    _leaf_hash,
    _node_hash,
    _sha256,
)


# ---------------------------------------------------------------------------
# SortedMerkle — basic
# ---------------------------------------------------------------------------

class TestSortedMerkleBasic:
    def test_single_item_membership(self):
        tree = SortedMerkle(["apple"])
        proof = tree.prove_membership("apple")
        assert proof is not None
        assert SortedMerkle.verify_membership(proof)

    def test_membership_wrong_item_fails(self):
        tree = SortedMerkle(["apple", "banana"])
        proof = tree.prove_membership("apple")
        assert proof is not None
        proof["item"] = "cherry"
        assert not SortedMerkle.verify_membership(proof)

    def test_membership_returns_none_for_absent(self):
        tree = SortedMerkle(["apple", "banana"])
        assert tree.prove_membership("cherry") is None

    def test_non_membership_returns_none_for_present(self):
        tree = SortedMerkle(["apple", "banana"])
        assert tree.prove_non_membership("apple") is None

    def test_empty_tree_non_membership(self):
        tree = SortedMerkle([])
        proof = tree.prove_non_membership("anything")
        assert proof is not None
        assert proof["empty"] is True
        assert SortedMerkle.verify_non_membership(proof)

    def test_deduplication_on_construction(self):
        tree = SortedMerkle(["a", "a", "b"])
        assert tree.items == ["a", "b"]

    def test_items_are_sorted(self):
        tree = SortedMerkle(["zebra", "apple", "mango"])
        assert tree.items == ["apple", "mango", "zebra"]

    def test_root_is_deterministic(self):
        t1 = SortedMerkle(["c", "a", "b"])
        t2 = SortedMerkle(["a", "b", "c"])
        assert t1.root == t2.root

    def test_root_changes_with_content(self):
        t1 = SortedMerkle(["a", "b"])
        t2 = SortedMerkle(["a", "b", "c"])
        assert t1.root != t2.root


# ---------------------------------------------------------------------------
# Merkle proof security — leaf binds item + index + total
# ---------------------------------------------------------------------------

class TestMerkleProofSecurity:
    def test_leaf_hash_binds_item(self):
        h1 = _leaf_hash("apple", 0, 3)
        h2 = _leaf_hash("APPLE", 0, 3)
        assert h1 != h2

    def test_leaf_hash_binds_index(self):
        h1 = _leaf_hash("apple", 0, 3)
        h2 = _leaf_hash("apple", 1, 3)
        assert h1 != h2

    def test_leaf_hash_binds_total(self):
        h1 = _leaf_hash("apple", 0, 3)
        h2 = _leaf_hash("apple", 0, 4)
        assert h1 != h2

    def test_forged_index_attack_fails(self):
        """Attacker claims apple is at index 1 instead of 0."""
        tree = SortedMerkle(["apple", "banana", "cherry"])
        proof = tree.prove_membership("apple")
        assert proof is not None
        assert proof["index"] == 0
        # Forge: claim apple is at index 1
        forged = dict(proof)
        forged["index"] = 1
        forged["leaf_hash"] = _leaf_hash("apple", 1, 3)
        assert not SortedMerkle.verify_membership(forged)

    def test_forged_total_attack_fails(self):
        """Attacker inflates the total to suggest a larger set."""
        tree = SortedMerkle(["apple", "banana"])
        proof = tree.prove_membership("apple")
        assert proof is not None
        forged = dict(proof)
        forged["total"] = 100
        assert not SortedMerkle.verify_membership(forged)

    def test_wrong_root_fails(self):
        tree = SortedMerkle(["apple", "banana"])
        proof = tree.prove_membership("apple")
        assert proof is not None
        forged = dict(proof)
        forged["root"] = "0" * 64
        assert not SortedMerkle.verify_membership(forged)

    def test_tampered_path_fails(self):
        tree = SortedMerkle(["apple", "banana", "cherry"])
        proof = tree.prove_membership("apple")
        assert proof is not None
        forged = dict(proof)
        forged["path"] = [{"hash": "0" * 64, "side": "right"}] + proof["path"][1:]
        assert not SortedMerkle.verify_membership(forged)

    def test_membership_various_sizes(self):
        for n in [1, 2, 3, 4, 5, 7, 8, 15, 16, 17]:
            items = [f"item_{i:04d}" for i in range(n)]
            tree = SortedMerkle(items)
            for item in items:
                proof = tree.prove_membership(item)
                assert SortedMerkle.verify_membership(proof), f"Failed for n={n}, item={item}"


# ---------------------------------------------------------------------------
# Non-membership proof security
# ---------------------------------------------------------------------------

class TestNonMembershipSecurity:
    def test_left_edge_proof(self):
        tree = SortedMerkle(["banana", "cherry", "date"])
        proof = tree.prove_non_membership("apple")  # apple < banana
        assert proof is not None
        assert proof["edge"] == "left"
        assert SortedMerkle.verify_non_membership(proof)

    def test_right_edge_proof(self):
        tree = SortedMerkle(["apple", "banana", "cherry"])
        proof = tree.prove_non_membership("zebra")  # zebra > cherry
        assert proof is not None
        assert proof["edge"] == "right"
        assert SortedMerkle.verify_non_membership(proof)

    def test_interior_proof(self):
        tree = SortedMerkle(["apple", "cherry", "elderberry"])
        proof = tree.prove_non_membership("banana")  # apple < banana < cherry
        assert proof is not None
        assert proof["edge"] == "interior"
        assert SortedMerkle.verify_non_membership(proof)
        assert proof["left_item"] == "apple"
        assert proof["right_item"] == "cherry"

    def test_interior_proof_adjacency_enforced(self):
        """Attacker claims non-adjacent items bracket the target."""
        tree = SortedMerkle(["apple", "banana", "cherry", "date"])
        proof = tree.prove_non_membership("coconut")  # cherry < coconut < date
        assert proof is not None
        forged = dict(proof)
        # Claim apple (index 0) and date (index 3) bracket "coconut" — not adjacent
        forged["left_item"] = "apple"
        forged["left_index"] = 0
        forged["right_item"] = "date"
        forged["right_index"] = 3
        assert not SortedMerkle.verify_non_membership(forged)

    def test_forged_left_edge_bracket_fails(self):
        """Attacker uses a fake right_item that doesn't verify to root."""
        tree = SortedMerkle(["banana", "cherry"])
        proof = tree.prove_non_membership("apple")
        forged = dict(proof)
        forged["right_leaf_hash"] = "0" * 64
        assert not SortedMerkle.verify_non_membership(forged)

    def test_forged_interior_bracket_fails(self):
        """Attacker tweaks left_item to lie about the boundary."""
        tree = SortedMerkle(["apple", "cherry", "elderberry"])
        proof = tree.prove_non_membership("banana")
        forged = dict(proof)
        forged["left_item"] = "aardvark"  # not actually in the tree
        assert not SortedMerkle.verify_non_membership(forged)

    def test_item_present_not_provable_absent(self):
        """prove_non_membership returns None when item IS in the tree."""
        tree = SortedMerkle(["apple", "banana", "cherry"])
        assert tree.prove_non_membership("banana") is None

    def test_non_membership_single_item_left(self):
        tree = SortedMerkle(["m"])
        proof = tree.prove_non_membership("a")
        assert proof["edge"] == "left"
        assert SortedMerkle.verify_non_membership(proof)

    def test_non_membership_single_item_right(self):
        tree = SortedMerkle(["m"])
        proof = tree.prove_non_membership("z")
        assert proof["edge"] == "right"
        assert SortedMerkle.verify_non_membership(proof)

    def test_order_violation_fails_verification(self):
        """Proof where item >= right_item should fail."""
        tree = SortedMerkle(["apple", "cherry"])
        proof = tree.prove_non_membership("banana")
        forged = dict(proof)
        forged["item"] = "cherry"  # now item == right_item, not strictly less
        assert not SortedMerkle.verify_non_membership(forged)


# ---------------------------------------------------------------------------
# Chain integrity
# ---------------------------------------------------------------------------

class TestChainIntegrity:
    def _build(self) -> CompletenessManifest:
        m = CompletenessManifest("test-chain")
        m.add("alpha", category="doc")
        m.add("beta", category="doc")
        m.add("gamma", category="doc")
        return m

    def test_intact_chain_verifies(self):
        m = self._build()
        assert m.verify_chain() is True

    def test_silent_deletion_detected(self):
        m = self._build()
        del m.entries[1]  # remove middle entry
        assert m.verify_chain() is False

    def test_silent_edit_detected(self):
        m = self._build()
        m.entries[1].item = "TAMPERED"
        assert m.verify_chain() is False

    def test_reorder_detected(self):
        m = self._build()
        m.entries[0], m.entries[1] = m.entries[1], m.entries[0]
        assert m.verify_chain() is False

    def test_sealed_manifest_rejects_new_entry(self):
        m = CompletenessManifest("sealed-test")
        m.add("alpha")
        m.seal()
        with pytest.raises(RuntimeError, match="sealed"):
            m.add("beta")

    def test_seal_is_idempotent(self):
        m = CompletenessManifest("idempotent")
        m.add("alpha")
        root1 = m.seal()
        root2 = m.seal()
        assert root1 == root2

    def test_empty_chain_verifies(self):
        m = CompletenessManifest("empty")
        assert m.verify_chain() is True


# ---------------------------------------------------------------------------
# Heartbeat logic
# ---------------------------------------------------------------------------

class TestHeartbeatLogic:
    def test_heartbeat_chain_intact(self):
        m = CompletenessManifest("hb-test")
        m.add("alpha")
        m.heartbeat()
        m.add("beta")
        m.heartbeat()
        assert m.verify_heartbeat_chain() is True

    def test_epochs_increment(self):
        m = CompletenessManifest("epochs")
        m.add("item")
        hb0 = m.heartbeat()
        hb1 = m.heartbeat()
        hb2 = m.heartbeat()
        assert hb0.epoch == 0
        assert hb1.epoch == 1
        assert hb2.epoch == 2

    def test_commitment_root_stable_between_heartbeats(self):
        """Two heartbeats with no adds in between share the same commitment root."""
        m = CompletenessManifest("stable-root")
        m.add("alpha")
        m.add("beta")
        hb1 = m.heartbeat()
        hb2 = m.heartbeat()
        assert hb1.commitment_root == hb2.commitment_root

    def test_commitment_root_changes_after_add(self):
        m = CompletenessManifest("changing-root")
        m.add("alpha")
        hb1 = m.heartbeat()
        m.add("beta")
        hb2 = m.heartbeat()
        assert hb1.commitment_root != hb2.commitment_root

    def test_tampered_heartbeat_chain_detected(self):
        m = CompletenessManifest("tamper-hb")
        m.add("item")
        m.heartbeat()
        m.heartbeat()
        m.heartbeats[0].epoch = 99  # tamper
        assert m.verify_heartbeat_chain() is False

    def test_heartbeat_chain_tip_matches_entry_chain(self):
        m = CompletenessManifest("chain-tip")
        m.add("alpha")
        m.add("beta")
        hb = m.heartbeat()
        assert hb.chain_tip == m.entries[-1].entry_hash


# ---------------------------------------------------------------------------
# CompletenessManifest — integration
# ---------------------------------------------------------------------------

class TestCompletenessManifestIntegration:
    def test_non_membership_after_seal(self):
        m = CompletenessManifest("training-data")
        for word in ["apple", "cherry", "elderberry"]:
            m.add(word)
        m.seal()
        proof = m.prove_non_membership("banana")
        assert proof is not None
        assert SortedMerkle.verify_non_membership(proof)

    def test_membership_proof_after_seal(self):
        m = CompletenessManifest("training-data")
        m.add("apple")
        m.add("banana")
        m.seal()
        proof = m.prove_membership("apple")
        assert proof is not None
        assert SortedMerkle.verify_membership(proof)

    def test_export_and_reload(self):
        m = CompletenessManifest("export-test")
        m.add("alpha", category="test", metadata={"source": "unit"})
        m.add("gamma")
        m.heartbeat()
        m.seal()
        blob = m.export()

        m2 = CompletenessManifest.load(blob)
        assert m2.manifest_id == "export-test"
        assert m2._sealed is True
        assert m2.verify_chain() is True
        assert m2.verify_heartbeat_chain() is True
        assert len(m2.entries) == 2
        assert len(m2.heartbeats) == 1
        # Proofs still verify after reload
        proof = m2.prove_non_membership("beta")
        assert SortedMerkle.verify_non_membership(proof)

    def test_non_membership_proof_independent_of_manifest(self):
        """A proof exported to JSON can be verified without the manifest."""
        m = CompletenessManifest("standalone-proof")
        for w in ["alpha", "gamma", "omega"]:
            m.add(w)
        m.seal()
        proof = m.prove_non_membership("beta")
        proof_json = json.dumps(proof)
        recovered_proof = json.loads(proof_json)
        assert SortedMerkle.verify_non_membership(recovered_proof)

    def test_full_workflow(self):
        m = CompletenessManifest("full-workflow")
        items = ["banana", "cherry", "date", "fig", "grape"]
        for item in items:
            m.add(item, category="fruit")
        hb = m.heartbeat()
        root = m.seal()

        assert m.verify_chain()
        assert m.verify_heartbeat_chain()
        assert root == hb.commitment_root  # sealed root matches pre-seal heartbeat

        # Absent items
        for absent in ["apple", "coconut", "elderberry", "kiwi"]:
            proof = m.prove_non_membership(absent)
            assert proof is not None, f"Expected proof for {absent}"
            assert SortedMerkle.verify_non_membership(proof), f"Proof failed for {absent}"

        # Present items
        for present in items:
            proof = m.prove_membership(present)
            assert proof is not None
            assert SortedMerkle.verify_membership(proof)
