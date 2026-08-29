# Demo script — 10 minutes

Everything below is measured on the seeded month, not aspirational. Numbers in
**bold** are what the system actually produces; if one doesn't match on the day,
say so rather than talking past it.

## Before they arrive

```bash
make demo        # clean slate, seed, eval, and one full run (~6 minutes)
```

Then open `http://localhost:5173` and leave it on the **Runs** list.

Two things to check while it runs:

- The eval table printed **precision 1.0000, 0 false positives**. If it didn't,
  stop and find out why before the meeting.
- The run finished at `awaiting_human` with **185** in the queue.

**One honest caveat to have ready.** With no `ANTHROPIC_API_KEY` set, Tier 3
runs the *stub* adjudicator — a deterministic placeholder, not a model. The UI
says so in a banner on every affected view, and the run row records it. The
graph, cost ceiling, audit chain, interrupts and replay are all real; only the
judgement is not. If they ask about Tier 3 quality, the honest answer is *"we
haven't measured it yet — the harness is built and the number is one API key
away."* Do not narrate the stub's rationales as if a model wrote them.

---

## 1. "Here's a month of bank lines and a ledger." *(1 min)*

Point at the header strip: **1,400 bank lines, 1,776 open ledger entries**.

> This is one month for a property management company — rent receipts, supplier
> payments, bank fees — against their open receivables and payables. Nothing
> here is a toy: it includes partial payments, batched remittances, FX drift,
> duplicate amounts on the same day, and a genuine unmatched line that *should*
> never be matched.

## 2. "Rules clear 82% with zero AI cost." *(2 min)*

Open the run. Point at the **tier breakdown**.

> **Tier 0 clears 660 lines — 55%** — on an exact reference, exact amount, and a
> two-day window. **Tier 1 clears another 328 — 32%** — on payer, amount and a
> seven-day window. That's **988 of 1,200, with zero model calls and zero cost.**
>
> This matters more than the AI does. If the model were the first thing you
> reached for, you'd be paying to answer questions arithmetic already settled.

Then, the number that should reassure them:

> Across all 1,200 labelled lines, precision is **1.0000** with **zero false
> positives**. Not one wrong match committed automatically.

## 3. "Retrieval finds candidates for the rest — from this client's own history." *(1 min)*

Point at the progress rows: `tier2_candidates`, **candidates offered 2,120**,
**retrieval calls recorded 424**.

> The 212 that survive the rules go to retrieval: amount and date windows, a
> bounded subset search for batched settlements, and hybrid keyword-plus-vector
> search over both the open ledger and this client's previously resolved
> matches.
>
> Recall at ten candidates is **0.9875** on everything reachable without
> history. That's the ceiling on everything downstream — a candidate we never
> surface is one the model can never choose — so we measure it separately.

Note the `truncated subset searches 7` chip.

> When a search hits its bound we say so. "No combination exists" and "I stopped
> looking" are different answers, and only one of them should let a line be
> closed.

## 4. "The model only adjudicates what's genuinely ambiguous." *(1 min)*

Point at `tier3_adjudicate`: **calls 212**, not 1,200.

> Roughly **one line in six** reaches the model. At list rates for Sonnet that's
> about **$0.90 per 1,200 lines** — under a dollar to reconcile a month.

## 5. "Here's one it got right, and why." *(1 min)*

Open the **exception queue**. Find a `subset` candidate — a batched remittance.

> One credit settles six invoices. Nothing in the amount matches any single
> invoice, so the system assembled the combination: same managing agent, same
> issue date, sums exactly. Every candidate carries where it came from —
> `counterparty_window`, `hybrid_open`, `resolved_pair` — so a reviewer can see
> *why* it's being offered, not just that it is.

## 6. "Here's one it refused to guess on." **Dwell here.** *(2 min)*

Find an item whose recommendation is `insufficient_evidence`.

> Two open items fit this payment equally well and nothing separates them. The
> system could have picked one and been right half the time. It refused.
>
> That's the product. A reconciliation tool that guesses is worse than no tool,
> because a wrong match becomes a journal entry somebody has to find and unwind
> six weeks later. We tuned the thresholds so that committing a wrong match is
> the expensive failure and escalating a correct one is the cheap one — and the
> eval reports the false-positive count as its headline, not precision, because
> "99.5% precise" sounds fine right up until you ask how many wrong journal
> entries that is.

## 7. Approve one. The graph resumes. *(1 min)*

Select the top candidate on one item, **Approve**, then **Submit**.

> The graph was holding at an interrupt with its state checkpointed in Postgres.
> It resumes from that checkpoint — nothing is recomputed — commits the match,
> and records the reviewer's decision as a *new* row that supersedes the
> escalation. The escalation is still there.

Click **Audit** on that line.

> Append-only. Nothing is ever edited or deleted — the database rejects both.
> You can see what the system thought, what the reviewer overruled, and when.

## 8. "That correction is now training data." *(1 min)*

Point at the `written_back` count in the submit summary.

> Every approval is written back into retrieval — keyed on the **counterparty**,
> not the invoice, because this month's invoice is closed by next month and the
> payer is not.
>
> The eval measures exactly this. There is a class of payment that arrives
> through a processor: the narrative names the processor, carries no invoice
> reference, and the amount is short by a processing fee. Nothing can reach it.
> Recall on next month's batch is **0.0000** before any correction and
> **1.0000** after 32 of them. That's not a story about the system improving —
> it's a number in the eval report that runs every time.

## 9. `make replay` — identical decisions *(1 min)*

```bash
make replay RUN_ID=<the run id>
```

> **1,015 of 1,015 decisions identical.**
>
> And note what we're *not* claiming. We don't pretend the model is
> deterministic — it isn't. Every model call and every retrieval query is
> recorded and keyed by a hash of its exact request. Replay re-executes the
> deterministic tiers for real, because that's where replay bugs actually hide,
> and serves the external calls from the recording. If the input changed, the
> hash misses and **replay fails loudly with a non-zero exit** rather than
> quietly producing a slightly different answer.

Optional, if they're technical and you have a minute:

```bash
docker compose kill api && docker compose up -d api
make continue RUN_ID=<the run id>
```

> Killed mid-run, resumed from the checkpoint, no work recomputed and no
> duplicate decisions.

## 10. The eval table *(1 min)*

```bash
make eval
```

| | Auto-matched | Precision | False positives |
|---|---|---|---|
| Tier 0 only | 660 / 1200 — 55.0% | 1.0000 | **0** |
| Tiers 0–1 | 988 / 1200 — 82.3% | 1.0000 | **0** |

> Three hundred labelled lines, deliberately enriched with the hard cases, plus
> every one of the 1,200. The regression gate fails the build on a single false
> positive, independently of precision.

---

## Questions to expect

**"What happens when it's wrong?"**
It escalates. The whole design is built so that being unsure is cheap and being
wrong is expensive. Zero false positives across 1,200 lines is the evidence, and
the false-positive count is the eval's headline number.

**"Does it touch our ledger?"**
No. It writes only to its own tables. No journal is posted, no invoice closed.
The output is a proposed match with a rationale and an audit trail; applying it
is a separate, human-authorised step.

**"What if the model changes underneath us?"**
The model version, prompt version, config and git SHA are on the run row, and
every model call is recorded. A newer model doesn't silently change history —
replaying an old run reproduces it exactly, and a changed prompt is a different
call, not a silent reuse of an answer to a different question.

**"How much does it cost?"**
About **$0.90 per 1,200 lines** at list rates, before prompt caching. There's a
per-run ceiling that halts the graph rather than overspending — demonstrable by
setting it to zero.

**"How long to run it on our data?"**
The ingest is a parser swap: CSV today, CAMT.053 XML included, both producing
identical rows. The real work is the first month of labelling, which is what
makes the eval mean anything.

## If something goes wrong

- **Queue is empty** — the run is still working. Check the progress rows.
- **Retrieval unavailable** — the eval still runs and says so. Windows-only
  recall is **0.9635**; you lose the history-dependent cases.
- **Replay says DIVERGED** — read it aloud. It names the field and both sides.
  It's supposed to be able to fail; that's why it's worth showing.
- **Anything shows a `dirty` commit marker** — the run came from an uncommitted
  tree. Say so if asked; don't claim reproducibility you can't back.
