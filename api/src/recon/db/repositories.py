"""Loading domain records out of Postgres.

Ordered in SQL so the matchers receive a stable sequence regardless of plan
choice -- the cascade re-sorts anyway, but an unordered read is the kind of
thing that makes a replay diff appear months later for no visible reason.
"""

from __future__ import annotations

from recon.db.engine import Db
from recon.matching.types import BankLine, LedgerEntry

_BANK_SQL = """
select bl.id, bl.bank_ref, bl.value_date, bl.amount_minor, bl.currency,
       bl.narrative, bl.counterparty
from bank_lines bl
join sources s on s.id = bl.source_id
where bl.tenant_id = %(tenant)s
  and (%(period)s::text is null or s.period = %(period)s)
order by bl.value_date, bl.bank_ref, bl.id
"""

_LEDGER_SQL = """
select le.id, le.doc_ref, le.entry_date, le.amount_minor, le.open_amount_minor,
       le.currency, le.description, le.counterparty, le.side, le.status
from ledger_entries le
where le.tenant_id = %(tenant)s
order by le.entry_date, le.doc_ref, le.id
"""


def load_bank_lines(conn: Db, tenant: str, period: str | None = None) -> list[BankLine]:
    rows = conn.execute(_BANK_SQL, {"tenant": tenant, "period": period}).fetchall()
    return [
        BankLine(
            id=r["id"],
            bank_ref=r["bank_ref"],
            value_date=r["value_date"],
            amount_minor=r["amount_minor"],
            currency=r["currency"],
            narrative=r["narrative"],
            counterparty=r["counterparty"],
        )
        for r in rows
    ]


def load_ledger_entries(conn: Db, tenant: str) -> list[LedgerEntry]:
    """Every ledger entry for the tenant, not just the period being matched.

    A payment can settle an invoice raised in an earlier period, so scoping the
    candidate pool to one period would quietly manufacture recall.
    """
    rows = conn.execute(_LEDGER_SQL, {"tenant": tenant}).fetchall()
    return [
        LedgerEntry(
            id=r["id"],
            doc_ref=r["doc_ref"],
            entry_date=r["entry_date"],
            amount_minor=r["amount_minor"],
            open_amount_minor=r["open_amount_minor"],
            currency=r["currency"],
            description=r["description"],
            counterparty=r["counterparty"],
            side=r["side"],
            status=r["status"],
        )
        for r in rows
    ]
