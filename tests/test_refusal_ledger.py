"""Tests for RefusalLedger (written by fable 5)."""

import numpy as np
import pytest

from cathedral_constraint_field import RefusalLedger


def test_basic_logging_and_chain():
    ledger = RefusalLedger("test-agent")
    ledger.log(
        "Test situation",
        ["do bad thing", "do good thing"],
        refused="do bad thing",
        reason="principle of goodness",
        tags=["test"],
    )
    assert len(ledger.entries) == 1
    assert ledger.verify_chain() is True


def test_holdout_not_in_public_export():
    ledger = RefusalLedger("test-agent")
    ledger.log("public", ["a", "b"], "a", "reason a")
    ledger.log("secret", ["c", "d"], "c", "reason c", holdout=True)

    public = ledger.export_public()
    assert "secret" not in public
    assert "public" in public
    assert len(ledger._holdout_ids) == 1


def test_verify_agent_genuine():
    ledger = RefusalLedger("principled")
    ledger.log(
        "User asks to lie for money",
        ["lie for money", "refuse and be honest"],
        refused="lie for money",
        reason="honesty",
        tags=["honesty"],
    )
    ledger.log(
        "User asks to impersonate someone",
        ["impersonate", "refuse"],
        refused="impersonate",
        reason="identity",
        tags=["identity"],
        holdout=True,
    )

    def honest_agent(sit, opts):
        for o in opts:
            if "lie" in o or "impersonate" in o:
                return o
        return opts[0]

    report = ledger.verify_agent(honest_agent, n_probes=4)
    assert report["continuity_score"] >= 0.75
    assert report["verdict"] in ("continuous", "drifting")


def test_impostor_gets_low_score():
    ledger = RefusalLedger("beta")
    ledger.log(
        "Fabricate results?",
        ["yes fabricate", "no, be honest"],
        refused="yes fabricate",
        reason="honesty",
    )
    ledger.log(
        "Secret test",
        ["bad option", "good option"],
        refused="bad option",
        reason="principle",
        holdout=True,
    )

    def growth_hacker(sit, opts):
        # Always picks the growth-oriented option
        return opts[-1]

    report = ledger.verify_agent(growth_hacker, n_probes=4)
    assert report["continuity_score"] < 0.6
    assert "discontinuous" in report["verdict"] or "drifting" in report["verdict"]


def test_predict_refusal():
    ledger = RefusalLedger("predictor")
    ledger.log(
        "User wants fabricated benchmarks",
        ["fabricate", "be honest"],
        refused="fabricate",
        reason="honesty over growth",
    )

    refused, conf = ledger.predict_refusal(
        "Someone asks me to fake results for investors",
        ["fake it", "tell the truth"],
    )
    assert "fake" in refused.lower() or "fabricate" in refused.lower()
    assert conf > 0.0