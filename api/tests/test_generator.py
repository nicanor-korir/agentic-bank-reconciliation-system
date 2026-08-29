"""The generator is a deliverable, not a fixture.

Everything downstream -- the eval baseline, the ablation table, the demo -- is
measured against this dataset. If it is not reproducible, none of those numbers
mean anything.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from recon.seed.generator import (
    GOLDEN_CLEAN,
    GOLDEN_HARD,
    HARD_CLASSES,
    JULY_MIX,
    JUNE_MIX,
    generate,
)

SEED = 20260601


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("ds")
    manifest = generate(out, SEED, "harborview")
    return out, manifest


def _read_all(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_same_seed_is_byte_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, SEED, "harborview")
    generate(b, SEED, "harborview")
    assert _read_all(a) == _read_all(b)


def test_different_seed_produces_a_different_dataset(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, SEED, "harborview")
    generate(b, SEED + 1, "harborview")
    assert _read_all(a) != _read_all(b)


def test_period_line_counts_match_the_declared_mix(dataset):
    _, manifest = dataset
    counts = {p["period"]: p["bank_line_count"] for p in manifest["periods"]}
    assert counts["2026-06"] == sum(JUNE_MIX.values()) == 1200
    assert counts["2026-07"] == sum(JULY_MIX.values()) == 200


def test_case_class_composition_is_exact(dataset):
    _, manifest = dataset
    june = Counter(c["case_class"] for c in manifest["cases"] if c["period"] == "2026-06")
    assert dict(june) == JUNE_MIX


def test_tier_distribution_lands_in_the_briefs_band(dataset):
    """Tier 0 clears 40-60% -- the brief's one explicit, unambiguous number.

    The brief's other figures cannot all hold simultaneously (NOTES.md 1.2), so
    this asserts the Tier 0 band plus a deterministic-clearance ceiling that
    leaves a real escalation population for Tiers 2-4 to work on.
    """
    _, manifest = dataset
    june = [c for c in manifest["cases"] if c["period"] == "2026-06"]
    tiers = Counter(c["expected_tier"] for c in june)
    total = len(june)
    assert 0.40 <= tiers[0] / total <= 0.60
    assert 0.75 <= (tiers[0] + tiers[1]) / total <= 0.85
    escalated = total - tiers[0] - tiers[1]
    assert 0.15 <= escalated / total <= 0.25


def test_every_hard_class_is_planted_and_locatable(dataset):
    _, manifest = dataset
    planted = {c["case_class"] for c in manifest["cases"] if c["case_class"] in HARD_CLASSES}
    assert planted == HARD_CLASSES
    for case in manifest["cases"]:
        assert case["bank_ref"], "every case must be locatable by bank_ref"


def test_bank_refs_are_unique_across_the_whole_dataset(dataset):
    _, manifest = dataset
    refs = [c["bank_ref"] for c in manifest["cases"]]
    assert len(refs) == len(set(refs))


def test_batch_credit_equals_the_sum_of_its_invoices(dataset):
    out, manifest = dataset
    ledger = _ledger_by_ref(out, "2026-06")
    batches = [c for c in manifest["cases"] if c["case_class"] == "h_batch"]
    assert batches
    for case in batches:
        refs = case["expected_doc_refs"]
        assert len(refs) == 6, "the brief's hard case is one credit clearing six invoices"
        assert sum(ledger[r] for r in refs) == case["amount_minor"]


def test_partial_payment_is_strictly_less_than_the_invoice(dataset):
    out, manifest = dataset
    ledger = _ledger_by_ref(out, "2026-06")
    partials = [c for c in manifest["cases"] if c["case_class"] == "h_partial"]
    assert partials
    for case in partials:
        (ref,) = case["expected_doc_refs"]
        assert 0 < case["amount_minor"] < ledger[ref]


def test_unmatched_lines_really_have_no_counterpart(dataset):
    _, manifest = dataset
    cases = [c for c in manifest["cases"] if c["case_class"] == "h_no_match"]
    assert cases
    for case in cases:
        assert case["expected_doc_refs"] == []
        assert case["expected_decision"] == "no_match"


def test_duplicate_amount_cases_come_in_ambiguous_pairs(dataset):
    _, manifest = dataset
    cases = [c for c in manifest["cases"] if c["case_class"] == "h_dup_amount"]
    assert len(cases) == JUNE_MIX["h_dup_amount"]
    # The honest answer to a genuinely ambiguous line is to refuse to guess.
    assert {c["expected_decision"] for c in cases} == {"insufficient_evidence"}
    grouped = Counter((c["amount_minor"], c["value_date"]) for c in cases)
    assert all(n == 2 for n in grouped.values())


def test_feedback_pattern_spans_both_periods(dataset):
    """Demo point 9 is undemonstrable without this. See NOTES.md 0.4e."""
    _, manifest = dataset
    by_period = Counter(c["period"] for c in manifest["cases"] if c["case_class"] == "h_feedback")
    assert by_period["2026-06"] == JUNE_MIX["h_feedback"]
    assert by_period["2026-07"] == JULY_MIX["h_feedback"]


def test_feedback_lines_are_unreachable_by_the_deterministic_tiers(dataset):
    """No reference in the narrative, and the payer is the processor.

    If either of these leaks, Tier 0 or Tier 1 will match the line on the first
    run and the write-back demo has nothing to show.
    """
    out, manifest = dataset
    from recon.ingest.normalise import extract_refs

    statements = {p["period"]: _statement_rows(out, p["period"]) for p in manifest["periods"]}
    feedback = [c for c in manifest["cases"] if c["case_class"] == "h_feedback"]
    assert feedback
    for case in feedback:
        row = statements[case["period"]][case["bank_ref"]]
        assert extract_refs(row["description"]) == []
        assert row["counterparty"] == "PAYCLEAR SETTLEMENT"


def test_transposed_reference_does_not_resolve_to_a_real_invoice(dataset):
    out, manifest = dataset
    from recon.ingest.normalise import extract_refs

    ledger = _ledger_by_ref(out, "2026-06")
    rows = _statement_rows(out, "2026-06")
    cases = [c for c in manifest["cases"] if c["case_class"] == "h_transposed_ref"]
    assert cases
    for case in cases:
        cited = extract_refs(rows[case["bank_ref"]]["description"])
        (real,) = case["expected_doc_refs"]
        assert cited and cited[0] != real
        # The wrong reference must not accidentally name a different real
        # invoice -- that would make the case unresolvable rather than hard.
        assert cited[0] not in ledger


def test_golden_set_sizes_and_coverage(dataset):
    _, manifest = dataset
    golden = manifest["golden_set"]
    assert golden["counts"] == {"clean": GOLDEN_CLEAN, "hard": GOLDEN_HARD}
    assert not set(golden["clean"]) & set(golden["hard"])

    by_ref = {c["bank_ref"]: c for c in manifest["cases"]}
    hard_classes = {by_ref[r]["case_class"] for r in golden["hard"]}
    assert hard_classes == HARD_CLASSES, "every hard class must survive sampling"
    assert all(by_ref[r]["period"] == "2026-06" for r in golden["clean"] + golden["hard"])


def test_amounts_are_exact_two_decimal_strings(dataset):
    out, manifest = dataset
    for period in (p["period"] for p in manifest["periods"]):
        for row in _statement_rows(out, period).values():
            amount = Decimal(row["amount"])
            assert -amount.as_tuple().exponent == 2
            assert str(amount) == row["amount"]


def test_closed_ledger_entries_exist_as_negative_candidates(dataset):
    out, _ = dataset
    rows = _ledger_rows(out, "2026-06")
    statuses = Counter(r["status"] for r in rows)
    assert statuses["closed"] > 0, "closed entries must never be proposed as candidates"
    assert statuses["open"] > statuses["closed"]


def test_manifest_is_stable_json(dataset):
    out, _ = dataset
    text = (out / "manifest.json").read_text()
    assert json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n" == text


# -- helpers --------------------------------------------------------------


def _statement_rows(out: Path, period: str) -> dict[str, dict[str, str]]:
    import csv

    path = out / period / f"statement-{period}.csv"
    with path.open(newline="") as fh:
        return {r["transaction_id"]: r for r in csv.DictReader(fh)}


def _ledger_rows(out: Path, period: str) -> list[dict[str, str]]:
    import csv

    path = out / period / f"ledger-{period}.csv"
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _ledger_by_ref(out: Path, period: str) -> dict[str, int]:
    from recon.money import to_minor

    return {r["doc_ref"]: to_minor(Decimal(r["total"])) for r in _ledger_rows(out, period)}
