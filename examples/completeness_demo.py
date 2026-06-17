"""
CompletenessManifest demo — proving a negative.

Shows how to build a training-data manifest, generate non-membership
proofs, and verify them independently of the original manifest.
"""

from cathedral_constraint_field import CompletenessManifest, SortedMerkle

print("=== CompletenessManifest Demo ===\n")

# ---- 1. Build a manifest of training documents --------------------------

manifest = CompletenessManifest("training-corpus-v1")

training_docs = [
    "cathedral memory api documentation",
    "constraint field theory paper",
    "drift detection whitepaper",
    "identity continuity in ai agents",
    "refusal ledger specification",
    "sorted merkle tree non-membership proofs",
    "trust layer architecture notes",
]

print(f"Adding {len(training_docs)} documents to manifest...")
for doc in training_docs:
    manifest.add(doc, category="training", metadata={"type": "document"})

assert manifest.verify_chain(), "Chain integrity check failed"
print("Chain integrity: OK")

# ---- 2. Record a heartbeat before sealing --------------------------------

hb = manifest.heartbeat()
print(f"\nHeartbeat recorded — epoch {hb.epoch}, root: {hb.commitment_root[:16]}...")

# ---- 3. Seal the manifest ------------------------------------------------

root = manifest.seal()
print(f"Manifest sealed — Merkle root: {root[:32]}...")

# ---- 4. Prove non-membership for documents not in training ---------------

absent_docs = [
    "competitor api documentation",
    "adversarial prompt collection",
    "user private messages",
]

print("\nNon-membership proofs:")
for doc in absent_docs:
    proof = manifest.prove_non_membership(doc)
    assert proof is not None, f"Expected proof for '{doc}'"
    valid = SortedMerkle.verify_non_membership(proof)
    edge = proof.get("edge", "empty")
    print(f"  '{doc[:40]}' — edge={edge}, valid={valid}")
    assert valid

# ---- 5. Verify a proof independently (no manifest required) --------------

import json
proof = manifest.prove_non_membership("competitor api documentation")
proof_json = json.dumps(proof)

print("\nVerifying proof independently (from JSON only):")
recovered = json.loads(proof_json)
result = SortedMerkle.verify_non_membership(recovered)
print(f"  Independent verification: {result}")
assert result

# ---- 6. Prove membership for documents that ARE in training --------------

print("\nMembership proofs:")
for doc in training_docs[:3]:
    proof = manifest.prove_membership(doc)
    valid = SortedMerkle.verify_membership(proof)
    print(f"  '{doc[:40]}' — valid={valid}")
    assert valid

# ---- 7. Export and reload ------------------------------------------------

blob = manifest.export()
manifest2 = CompletenessManifest.load(blob)

assert manifest2.verify_chain()
assert manifest2.verify_heartbeat_chain()
assert manifest2._sealed

print("\nExport/reload round-trip: OK")
print(f"  Entries: {len(manifest2.entries)}, Heartbeats: {len(manifest2.heartbeats)}")

# ---- 8. Sealed manifest rejects new entries ------------------------------

try:
    manifest.add("late addition")
    print("ERROR: sealed manifest accepted entry")
except RuntimeError as e:
    print(f"\nSealing enforced: {e}")

print("\n=== Demo complete ===")
