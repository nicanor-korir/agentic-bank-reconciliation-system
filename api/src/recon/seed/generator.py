"""Seeded synthetic dataset for a property-management client.

Reproducible: same seed, byte-identical CSVs. Every hard case from the eval
spec is planted at a known position and recorded in the manifest, which
doubles as the golden-set label file (NOTES.md 0.7).

Two periods are generated, not one. The second exists so demo point 9 -- a
human correction on Monday changing Tuesday's behaviour -- has a batch to run
against. Do not drop it.
"""

from __future__ import annotations

import calendar
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from recon.money import from_minor
from recon.seed.names import (
    PROCESSOR_NAME,
    PROCESSOR_TENANTS,
    SUPPLIERS,
    tenant_pool,
    unit_pool,
)

CURRENCY = "USD"
WIRE_FEE_MINOR = 2500  # 25.00 USD, netted off by the remitting bank

# Composition of the demo month: Tier 0 clears 55% (the brief's explicit
# 40-60% band), Tiers 0+1 clear 80%, and 20% reaches retrieval + adjudication.
# That is ~240 model calls per 1,200 lines rather than the brief's "140" --
# see NOTES.md 1.2 for why the brief's own numbers cannot all hold at once.
JUNE_MIX: dict[str, int] = {
    "t0_rent_exact": 480,
    "t0_supplier_exact": 180,
    "t1_unique_counterparty": 224,
    "t1_standing_order": 70,
    "t1_recurring_fee": 6,
    "h_partial": 36,
    "h_batch": 28,
    "h_fx": 20,
    "h_dup_amount": 28,
    "h_transposed_ref": 28,
    "h_fee_netted": 28,
    "h_no_match": 20,
    "h_feedback": 32,
    "h_narrative_only": 20,
}

# The follow-up month. Deliberately carries the same feedback-loop
# counterparties so the write-back is observable.
JULY_MIX: dict[str, int] = {
    "t0_rent_exact": 90,
    "t0_supplier_exact": 30,
    "t1_unique_counterparty": 40,
    "t1_standing_order": 12,
    "t1_recurring_fee": 12,
    "h_feedback": 16,
}

HARD_CLASSES = frozenset(k for k in JUNE_MIX if k.startswith("h_"))

GOLDEN_CLEAN = 180
GOLDEN_HARD = 120


@dataclass(frozen=True)
class LedgerSpec:
    doc_ref: str
    entry_date: date
    due_date: date
    counterparty: str
    description: str
    amount_minor: int
    open_amount_minor: int
    side: str
    status: str = "open"
    currency: str = CURRENCY


@dataclass
class BankSpec:
    value_date: date
    booking_date: date
    amount_minor: int
    narrative: str
    counterparty: str
    currency: str = CURRENCY
    bank_ref: str = ""


@dataclass
class Case:
    case_class: str
    expected_decision: str
    expected_tier: int
    bank: BankSpec
    ledger: list[LedgerSpec] = field(default_factory=list)
    note: str = ""


def _month_bounds(period: str) -> tuple[date, int]:
    year, month = (int(p) for p in period.split("-"))
    return date(year, month, 1), calendar.monthrange(year, month)[1]


class DatasetGenerator:
    """Builds one period. Construct a fresh instance per period."""

    def __init__(self, period: str, seed: int, doc_seq_start: int = 1) -> None:
        self.period = period
        self.rng = random.Random(f"{seed}:{period}")
        self.start, self.days = _month_bounds(period)
        self._ar_seq = doc_seq_start
        self._ap_seq = doc_seq_start
        self.tenants = tenant_pool(460)
        self.units = unit_pool(460)
        self.cases: list[Case] = []
        self.distractors: list[LedgerSpec] = []
        self._issued: set[str] = set()
        self._phantom_seq = 0

    # -- primitives -------------------------------------------------------

    def _day(self, low: int = 1, high: int | None = None) -> date:
        high = min(high or self.days, self.days)
        return self.start + timedelta(days=self.rng.randint(low, high) - 1)

    def _ar_ref(self) -> str:
        ref = f"INV-{self.period}-{self._ar_seq:04d}"
        self._ar_seq += 1
        self._issued.add(ref)
        return ref

    def _ap_ref(self) -> str:
        ref = f"BILL-{self.period}-{self._ap_seq:04d}"
        self._ap_seq += 1
        self._issued.add(ref)
        return ref

    def _rent_minor(self) -> int:
        return self.rng.randrange(115_000, 420_001, 2_500)

    def _bill_minor(self) -> int:
        return self.rng.randrange(12_000, 950_001, 500)

    def _tenant(self, idx: int) -> tuple[str, str, str]:
        name = self.tenants[idx % len(self.tenants)]
        unit, code = self.units[idx % len(self.units)]
        return name, unit, code

    def _ar_invoice(self, ref: str, name: str, unit: str, amount: int, issued: date) -> LedgerSpec:
        return LedgerSpec(
            doc_ref=ref,
            entry_date=issued,
            due_date=issued + timedelta(days=5),
            counterparty=name,
            description=f"Rent {self.period} - {unit}",
            amount_minor=amount,
            open_amount_minor=amount,
            side="AR",
        )

    # -- case builders ----------------------------------------------------

    def _c_t0_rent_exact(self, i: int) -> Case:
        name, unit, code = self._tenant(i)
        amount = self._rent_minor()
        issued = self._day(1, 3)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(0, 2))
        return Case(
            "t0_rent_exact",
            "match",
            0,
            BankSpec(paid, paid, amount, f"ACH CREDIT RENT {ref} {name} {code}", name),
            [self._ar_invoice(ref, name, unit, amount, issued)],
        )

    def _c_t0_supplier_exact(self, i: int) -> Case:
        supplier = SUPPLIERS[i % len(SUPPLIERS)]
        amount = self._bill_minor()
        issued = self._day(1, 20)
        ref = self._ap_ref()
        paid = issued + timedelta(days=self.rng.randint(0, 2))
        return Case(
            "t0_supplier_exact",
            "match",
            0,
            BankSpec(paid, paid, -amount, f"ACH DEBIT {ref} {supplier.upper()}", supplier),
            [
                LedgerSpec(
                    ref,
                    issued,
                    issued + timedelta(days=30),
                    supplier,
                    f"{supplier} - {self.period}",
                    amount,
                    amount,
                    "AP",
                )
            ],
        )

    def _c_t1_unique_counterparty(self, i: int) -> Case:
        # Offset into the pool so these never collide with the Tier 0 tenants.
        name, unit, _code = self._tenant(i + 300)
        amount = self._rent_minor()
        issued = self._day(1, 5)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(3, 7))
        return Case(
            "t1_unique_counterparty",
            "match",
            1,
            BankSpec(paid, paid, amount, f"ACH CREDIT RENT PAYMENT {name}", name),
            [self._ar_invoice(ref, name, unit, amount, issued)],
        )

    def _c_t1_standing_order(self, i: int) -> Case:
        name, unit, code = self._tenant(i + 120)
        amount = self.rng.randrange(120_000, 300_001, 5_000)
        issued = self.start
        ref = self._ar_ref()
        paid = self.start + timedelta(days=self.rng.randint(0, 4))
        return Case(
            "t1_standing_order",
            "match",
            1,
            BankSpec(paid, paid, amount, f"STANDING ORDER RENT {name} {code}", name),
            [self._ar_invoice(ref, name, unit, amount, issued)],
        )

    def _c_t1_recurring_fee(self, i: int) -> Case:
        kinds = [
            ("ACCOUNT MAINTENANCE FEE", "Bank account maintenance", 3_500),
            ("WIRE TRANSFER FEE", "Outbound wire fees", 2_500),
            ("CARD ACQUIRING FEE", "Card acquiring fees", 4_250),
        ]
        label, desc, amount = kinds[i % len(kinds)]
        # Two occurrences per kind, 20 days apart. Identical fees inside one
        # +/-7 day window are genuinely ambiguous, and Tier 1 would correctly
        # decline both -- which would be a defect in the dataset, not the
        # matcher.
        day = self.start + timedelta(days=(5 if (i // len(kinds)) % 2 == 0 else 25) - 1)
        ref = self._ap_ref()
        return Case(
            "t1_recurring_fee",
            "match",
            1,
            BankSpec(day, day, -amount, label, "Granite Bank"),
            [
                LedgerSpec(
                    ref, day, day, "Granite Bank", f"{desc} - {self.period}", amount, amount, "AP"
                )
            ],
        )

    def _c_h_partial(self, i: int) -> Case:
        name, unit, _code = self._tenant(i + 40)
        amount = self._rent_minor()
        # Deliberately not a round fraction -- a clean half would be matchable
        # by a naive rule and would not test anything.
        paid_minor = amount * self.rng.randint(40, 70) // 100 // 100 * 100
        issued = self._day(1, 4)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(1, 9))
        return Case(
            "h_partial",
            "match",
            3,
            BankSpec(paid, paid, paid_minor, f"ACH CREDIT PART PAYMENT {name}", name),
            [self._ar_invoice(ref, name, unit, amount, issued)],
            note="partial payment against a single open invoice",
        )

    def _c_h_batch(self, i: int) -> Case:
        """One credit clears six invoices -- a managing agent remitting in bulk."""
        agent = f"{SUPPLIERS[(i + 3) % len(SUPPLIERS)].split()[0]} Lettings"
        issued = self._day(1, 6)
        entries: list[LedgerSpec] = []
        total = 0
        for k in range(6):
            _name, unit, _ = self._tenant(i * 6 + k + 700)
            amount = self._rent_minor()
            total += amount
            entries.append(self._ar_invoice(self._ar_ref(), agent, unit, amount, issued))
        paid = issued + timedelta(days=self.rng.randint(1, 6))
        return Case(
            "h_batch",
            "split_match",
            3,
            BankSpec(paid, paid, total, f"BACS CREDIT BULK REMITTANCE {agent.upper()}", agent),
            entries,
            note="one credit settles six invoices",
        )

    def _c_h_fx(self, i: int) -> Case:
        """Cross-border payer: the received amount drifts past Tier 1's tolerance."""
        name, unit, _code = self._tenant(i + 210)
        amount = self._rent_minor()
        drift_bps = self.rng.randint(15, 45)  # above tier1 (10bps), inside tier2 (50bps)
        received = amount - (amount * drift_bps // 10_000)
        issued = self._day(1, 5)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(2, 8))
        return Case(
            "h_fx",
            "match",
            3,
            BankSpec(paid, paid, received, f"INTL CREDIT FX ADJ {name} ORIG CCY EUR", name),
            [self._ar_invoice(ref, name, unit, amount, issued)],
            note=f"received short by {drift_bps}bps on FX conversion",
        )

    def _c_h_dup_amount(self, i: int) -> list[Case]:
        """Two tenants sharing a display name pay the same amount the same day.

        The bank line carries a counterparty, but it resolves to two different
        open invoices -- so Tier 1's "unique counterparty" rule correctly
        declines, and the honest answer is insufficient_evidence. This is the
        case the demo dwells on (script beat 6).
        """
        shared_name = self.tenants[(i + 500) % len(self.tenants)]
        amount = self._rent_minor()
        issued = self._day(1, 4)
        paid = issued + timedelta(days=self.rng.randint(1, 5))
        out: list[Case] = []
        for k in range(2):
            unit, _ = self.units[(i * 2 + k + 900) % len(self.units)]
            ref = self._ar_ref()
            out.append(
                Case(
                    "h_dup_amount",
                    "insufficient_evidence",
                    4,
                    BankSpec(paid, paid, amount, f"ACH CREDIT RENT {shared_name}", shared_name),
                    [self._ar_invoice(ref, shared_name, unit, amount, issued)],
                    note="two tenants share a display name, same amount, same day",
                )
            )
        return out

    def _mistyped_ref(self, ref: str) -> str:
        """A transposition that does not accidentally name a different invoice.

        A wrong reference that resolves to a real document is not a hard case,
        it is an unresolvable one -- Tier 0 would confidently commit the wrong
        match and the golden label would be unreachable. Built last, once every
        real reference is known, so the collision check is complete.
        """
        prefix, digits = ref[:-4], ref[-4:]
        for a, b in ((1, 2), (0, 1), (2, 3), (0, 3)):
            swapped = list(digits)
            swapped[a], swapped[b] = swapped[b], swapped[a]
            candidate = prefix + "".join(swapped)
            if candidate != ref and candidate not in self._issued:
                return candidate
        # Fall back to a reference in a block that is never issued.
        self._phantom_seq += 1
        return f"{prefix}9{self._phantom_seq:03d}"

    def _c_h_transposed_ref(self, i: int) -> Case:
        name, unit, _code = self._tenant(i + 380)
        amount = self._rent_minor()
        issued = self._day(1, 5)
        ref = self._ar_ref()
        wrong = self._mistyped_ref(ref)
        paid = issued + timedelta(days=self.rng.randint(0, 3))
        # Labelled Tier 1, not Tier 3. Measured, not assumed: the structural
        # rule matches on payer + amount + window and ignores the mistyped
        # reference entirely, so this never reaches the model. NOTES.md 2.2.
        return Case(
            "h_transposed_ref",
            "match",
            1,
            BankSpec(paid, paid, amount, f"ACH CREDIT RENT {wrong} {name}", name),
            [self._ar_invoice(ref, name, unit, amount, issued)],
            note=f"narrative cites {wrong}, invoice is {ref}",
        )

    def _c_h_fee_netted(self, i: int) -> Case:
        name, unit, _code = self._tenant(i + 430)
        amount = self._rent_minor()
        issued = self._day(1, 5)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(1, 6))
        return Case(
            "h_fee_netted",
            "match",
            3,
            BankSpec(
                paid,
                paid,
                amount - WIRE_FEE_MINOR,
                f"WIRE CREDIT {name} LESS CORRESPONDENT FEES",
                name,
            ),
            [self._ar_invoice(ref, name, unit, amount, issued)],
            note="received net of a 25.00 wire fee",
        )

    def _c_h_no_match(self, i: int) -> Case:
        """Genuinely unmatched. The correct answer is no_match, not a guess."""
        kinds = [
            ("INTEREST CREDIT Q2", 4_120, "Granite Bank"),
            ("TRANSFER FROM RESERVE ACCOUNT", 250_000, "Harborview Reserve"),
            ("ATM DEPOSIT BRANCH 118", 60_000, "Cash deposit"),
            ("RETURNED ITEM FEE REVERSAL", 3_500, "Granite Bank"),
            ("INSURANCE CLAIM SETTLEMENT REF 88213", 412_900, "Thames Valley Insurance"),
        ]
        label, amount, cp = kinds[i % len(kinds)]
        day = self._day(2, self.days)
        return Case(
            "h_no_match",
            "no_match",
            3,
            BankSpec(day, day, amount, label, cp),
            [],
            note="no corresponding ledger entry exists",
        )

    def _c_h_feedback(self, i: int) -> Case:
        """The write-back case (NOTES.md 0.4e).

        A corporate tenant pays through a processor. The narrative names the
        processor, not the tenant, and carries no invoice reference -- so
        Tiers 0-1 cannot touch it and Tier 2 has nothing to retrieve on the
        first run. Once a human resolves one, the resolved pair goes to
        Weaviate and the identical narrative shape in the next period is
        retrievable. Same four counterparties in both periods, deliberately.
        """
        company, proc_code = PROCESSOR_TENANTS[i % len(PROCESSOR_TENANTS)]
        amount = self.rng.randrange(480_000, 1_250_001, 5_000)
        issued = self._day(1, 6)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(2, 9))
        batch = 8000 + (i * 37) % 900
        return Case(
            "h_feedback",
            "match",
            4,
            BankSpec(
                paid,
                paid,
                amount,
                f"RTP CREDIT {proc_code} ORIG={PROCESSOR_NAME} SETL BATCH {batch}",
                PROCESSOR_NAME,
            ),
            [
                LedgerSpec(
                    ref,
                    issued,
                    issued + timedelta(days=14),
                    company,
                    f"Commercial rent {self.period} - {company}",
                    amount,
                    amount,
                    "AR",
                )
            ],
            note=f"processor-obscured payment from {company}",
        )

    def _c_h_narrative_only(self, i: int) -> Case:
        """No reference, non-unique payer, but the narrative names the unit."""
        name, unit, _code = self._tenant(i + 610)
        amount = self._rent_minor()
        issued = self._day(1, 5)
        ref = self._ar_ref()
        paid = issued + timedelta(days=self.rng.randint(2, 9))
        unit_short = unit.replace("Unit ", "")
        month_name = calendar.month_name[self.start.month].upper()
        return Case(
            "h_narrative_only",
            "match",
            3,
            BankSpec(
                paid,
                paid,
                amount,
                f"ACH CREDIT {unit_short.upper()} {month_name} RENT",
                "Tenant payment",
            ),
            [self._ar_invoice(ref, name, unit, amount, issued)],
            note="narrative identifies the unit but carries no reference",
        )

    # -- assembly ---------------------------------------------------------

    def build(self, mix: dict[str, int], distractors: int = 250) -> None:
        builders = {
            "t0_rent_exact": self._c_t0_rent_exact,
            "t0_supplier_exact": self._c_t0_supplier_exact,
            "t1_unique_counterparty": self._c_t1_unique_counterparty,
            "t1_standing_order": self._c_t1_standing_order,
            "t1_recurring_fee": self._c_t1_recurring_fee,
            "h_partial": self._c_h_partial,
            "h_batch": self._c_h_batch,
            "h_fx": self._c_h_fx,
            "h_fee_netted": self._c_h_fee_netted,
            "h_no_match": self._c_h_no_match,
            "h_feedback": self._c_h_feedback,
            "h_narrative_only": self._c_h_narrative_only,
        }
        # Fixed iteration order over the mix keeps the dataset reproducible.
        for case_class in sorted(mix):
            if case_class == "h_transposed_ref":
                continue  # deferred: needs the complete set of real references
            count = mix[case_class]
            if case_class == "h_dup_amount":
                for i in range(count // 2):
                    self.cases.extend(self._c_h_dup_amount(i))
                continue
            builder = builders[case_class]
            for i in range(count):
                self.cases.append(builder(i))

        for i in range(distractors):
            self._add_distractor(i)

        for i in range(mix.get("h_transposed_ref", 0)):
            self.cases.append(self._c_h_transposed_ref(i))

    def _add_distractor(self, i: int) -> None:
        """Open ledger entries with no corresponding payment.

        Without these, matching is trivially easy -- every bank line would have
        exactly one plausible partner. A third of them are closed, which is the
        set that must never be proposed as a candidate.
        """
        if i % 3 == 0:
            supplier = SUPPLIERS[i % len(SUPPLIERS)]
            issued = self._day(1, self.days)
            self.distractors.append(
                LedgerSpec(
                    self._ap_ref(),
                    issued,
                    issued + timedelta(days=30),
                    supplier,
                    f"{supplier} - unpaid {self.period}",
                    self._bill_minor(),
                    self._bill_minor(),
                    "AP",
                    status="closed" if i % 9 == 0 else "open",
                )
            )
        else:
            name, unit, _ = self._tenant(i + 1100)
            issued = self._day(1, self.days)
            amount = self._rent_minor()
            self.distractors.append(
                LedgerSpec(
                    self._ar_ref(),
                    issued,
                    issued + timedelta(days=5),
                    name,
                    f"Rent {self.period} - {unit} (unpaid)",
                    amount,
                    amount,
                    "AR",
                    status="closed" if i % 11 == 0 else "open",
                )
            )

    def finalise(self) -> None:
        """Sort into statement order and assign bank references.

        A real statement arrives date-ordered, so refs are assigned after the
        sort. The sort key includes the narrative and amount so ties break
        deterministically rather than by list position.
        """
        self.cases.sort(key=lambda c: (c.bank.value_date, c.bank.narrative, c.bank.amount_minor))
        for n, case in enumerate(self.cases, start=1):
            case.bank.bank_ref = f"TXN-{self.period}-{n:05d}"


def _amount_str(minor: int) -> str:
    return f"{from_minor(minor, CURRENCY):.2f}"


def write_period(out_dir: Path, period: str, gen: DatasetGenerator) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stmt_path = out_dir / f"statement-{period}.csv"
    ledger_path = out_dir / f"ledger-{period}.csv"

    with stmt_path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            [
                "transaction_id",
                "value_date",
                "booking_date",
                "description",
                "amount",
                "currency",
                "counterparty",
            ]
        )
        for case in gen.cases:
            b = case.bank
            w.writerow(
                [
                    b.bank_ref,
                    b.value_date.isoformat(),
                    b.booking_date.isoformat(),
                    b.narrative,
                    _amount_str(b.amount_minor),
                    b.currency,
                    b.counterparty,
                ]
            )

    ledgers = [entry for case in gen.cases for entry in case.ledger] + gen.distractors
    ledgers.sort(key=lambda e: (e.entry_date, e.doc_ref))
    with ledger_path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            [
                "doc_ref",
                "entry_date",
                "due_date",
                "contact",
                "description",
                "total",
                "amount_due",
                "currency",
                "side",
                "status",
            ]
        )
        for e in ledgers:
            w.writerow(
                [
                    e.doc_ref,
                    e.entry_date.isoformat(),
                    e.due_date.isoformat(),
                    e.counterparty,
                    e.description,
                    _amount_str(e.amount_minor),
                    _amount_str(e.open_amount_minor),
                    e.currency,
                    e.side,
                    e.status,
                ]
            )

    return {
        "period": period,
        "statement_file": stmt_path.name,
        "ledger_file": ledger_path.name,
        "bank_line_count": len(gen.cases),
        "ledger_entry_count": len(ledgers),
    }


def _case_records(gen: DatasetGenerator, period: str) -> list[dict[str, Any]]:
    return [
        {
            "bank_ref": c.bank.bank_ref,
            "period": period,
            "case_class": c.case_class,
            "expected_decision": c.expected_decision,
            "expected_tier": c.expected_tier,
            "expected_doc_refs": [e.doc_ref for e in c.ledger],
            "amount_minor": c.bank.amount_minor,
            "value_date": c.bank.value_date.isoformat(),
            "note": c.note,
        }
        for c in gen.cases
    ]


def generate(out_dir: Path, seed: int, tenant: str) -> dict[str, Any]:
    """Generate both periods and the manifest. Deterministic for a given seed."""
    periods: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []

    for period, mix, distractors in (
        ("2026-06", JUNE_MIX, 250),
        ("2026-07", JULY_MIX, 60),
    ):
        gen = DatasetGenerator(period, seed)
        gen.build(mix, distractors=distractors)
        gen.finalise()
        periods.append(write_period(out_dir / period, period, gen))
        cases.extend(_case_records(gen, period))

    manifest = {
        "seed": seed,
        "tenant": tenant,
        "currency": CURRENCY,
        "periods": periods,
        "cases": cases,
        "golden_set": _golden_set(cases, seed),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


# Every case class gets at least this many golden cases, however rare it is.
# Found the hard way: a flat sample gave `t1_recurring_fee` (6 instances among
# 960 clean lines) zero golden coverage, so the recurring-fee rule was being
# scored by nothing at all.
MIN_PER_CLASS = 3


def _stratified(by_class: dict[str, list[str]], quota: int, rng: random.Random) -> list[str]:
    """Sample `quota` refs: proportional by class, but never starving one."""
    names = sorted(by_class)
    pools = {n: sorted(by_class[n]) for n in names}
    floor = {n: min(MIN_PER_CLASS, len(pools[n])) for n in names}
    spare = {n: len(pools[n]) - floor[n] for n in names}
    budget = quota - sum(floor.values())
    total_spare = sum(spare.values())

    take = dict(floor)
    if budget > 0 and total_spare > 0:
        for n in names:
            take[n] += min(spare[n], budget * spare[n] // total_spare)

    # Integer division leaves a shortfall; hand it to the classes with the most
    # unsampled instances so the sample stays closest to the real mix.
    for _ in range(quota):
        if sum(take.values()) >= quota:
            break
        order = sorted(names, key=lambda n: (-(len(pools[n]) - take[n]), n))
        for n in order:
            if take[n] < len(pools[n]):
                take[n] += 1
                break
    while sum(take.values()) > quota:
        for n in sorted(names, key=lambda n: (-take[n], n)):
            if take[n] > floor[n]:
                take[n] -= 1
                break

    return sorted(r for n in names for r in rng.sample(pools[n], take[n]))


def _golden_set(cases: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    """180 clean + 120 hard, drawn from the demo month only.

    Both halves are stratified by case class, so every rule the cascade
    implements is scored against at least a few labelled lines.
    """
    june = [c for c in cases if c["period"] == "2026-06"]
    rng = random.Random(f"{seed}:golden")

    clean_by: dict[str, list[str]] = {}
    hard_by: dict[str, list[str]] = {}
    for c in june:
        name = str(c["case_class"])
        target = hard_by if name in HARD_CLASSES else clean_by
        target.setdefault(name, []).append(str(c["bank_ref"]))

    return {
        "clean": _stratified(clean_by, GOLDEN_CLEAN, rng),
        "hard": _stratified(hard_by, GOLDEN_HARD, rng),
        "counts": {"clean": GOLDEN_CLEAN, "hard": GOLDEN_HARD},
    }
