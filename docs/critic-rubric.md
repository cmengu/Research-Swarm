# Critic rubric (v0.3.0 draft)

> **Superseded by [`docs/spec/06-validator-and-critic.md`](spec/06-validator-and-critic.md).** This is the decision asset that produced that spec section, kept for its reasoning — which is fuller than the spec's summary of it. Where the two disagree, **the spec wins**: it resolves several items this document filed as "carried to the spec" (the schema deltas, the lookback floor, the `unconfirmed` rule).

Decision asset for tickets [#7](https://github.com/cmengu/Research-Swarm/issues/7) and [#23](https://github.com/cmengu/Research-Swarm/issues/23). The concrete checklist the cross-family critic (Codex) judges an `issue.json` against, and what the orchestrator does with the verdict.

Binds to the `critic_report` block defined in [issue.json schema v0.1.0](schema/README.md) (#3). **This document is also the degradation registry** — see [the register](#the-register). The contract in [`state/thesis.json`](../state/thesis.json) (#5) is one entry in it, not a second home for the rule.

> **v0.3.0 adds the degradation contract** (#23): the register itself, the three-part admission test, the render-at-the-absence rule, and [continuity across stubs](#continuity-across-stubs). Nothing in v0.2.0 is overturned — the two degradations it declared are unchanged, and two more (stale calendar, failed beat) are lifted in from #18 and #6, which had been declaring them in their own docs.
>
> **v0.2.0 superseded v0.1.0's sorting principle.** v0.1.0 made blocking mean *mechanically checkable* and judgment *always advisory*. Two grilling decisions overturned that — see [What changed from v0.1.0](#what-changed-from-v010). Its receipt rule, `not_run` handling, degradation exemptions and edit-don't-regenerate loop survive intact.

## What the critic sees

The critic reads five things. This is the load-bearing decision of the rubric: a critic holding only the finished digest cannot audit an *absence*, because the absence was removed from the artifact it's reading. Widening the input set is what turns "you missed a story" from unanswerable into a diff.

| Input | Why the critic needs it |
|---|---|
| `issues/<this>.json` | The artifact under judgment. |
| `runs/<run_id>/findings/*.json` | Each researcher's raw output. Lets the critic catch what the **manager dropped** — the receipt rule's only source of receipts. |
| `issues/<previous>.json` | Continuity: open threads carried forward, `cycles_quiet` honest, coverage window joins up. |
| `state/watchlist.json` | Every tracked entity accounted for. |
| `state/thesis.json` | Whether `thesis_impact` is honest, and which belief slots are dormant (exemptions). |

The critic has **no web access**. It cannot catch what all six researchers missed — only what the pipeline found and then lost. That boundary is deliberate: web access would double the run, burn subscription quota on searching rather than judging, and open a prompt-injection surface on an unattended run. Named as a known gap, not an oversight.

**Orchestrator requirement:** researcher findings must be persisted per run at `runs/<run_id>/findings/<beat>.json`. Nothing needed this before — only the manager read them, in-process. Feeds the researcher output contract (#6), which now serves two readers.

## Two gates

Structural checks are decidable by a script with perfect accuracy, for free, in milliseconds. Judgment is not. Asking an LLM to count fields pays a probabilistic system to do a deterministic job — and it will miss an empty section inconsistently, which is worse than not checking. So the gates are separate, and only the second one is Codex.

```
manager draft
     │
     ▼
┌──────────────────────┐  fail   ┌──────────────────────────┐
│ stage 1: validator   ├────────►│ back to manager (2 max)  │
│ deterministic, free  │         │ exhausted → failed stub  │
└──────────┬───────────┘         └──────────────────────────┘
           │ structurally valid
           ▼
┌──────────────────────┐ blocked ┌──────────────────────────┐
│ stage 2: codex critic├────────►│ fix or rebut (2 max)     │
│ judgment only        │         │ exhausted → banner       │
└──────────┬───────────┘         └──────────────────────────┘
           │ pass / pass_with_advisories
           ▼
        publish
```

Budgets are **separate — 2 retries each**. They fail for unrelated reasons, and a trivial JSON slip must never starve the critic of the budget it needed for substance. Worst case is bounded at 4 manager calls.

## Stage 1 — the validator (deterministic)

Runs on every cycle before Codex is invoked. No judgment, no model call, no retry consumed from the critic's budget.

| Check | Test | Manager's repair |
|---|---|---|
| `uncited_claim` | A factual assertion in `summary`, `headline`, or `elsewhere_on_frontier` with no entry in its `sources[]`. | Add the source object, or delete the claim. |
| `malformed_source` | A `sources[]` entry missing `url`, `publisher`, `tier`, or `published_at`, or `tier` outside `primary｜trade｜aggregator`. | Complete the source object. |
| `dangling_entity` | An `entity_id` referenced in `headline`, `themes_and_signals`, or `quiet_this_cycle` appearing in neither `watchlist` nor `new_on_radar`. | Add the entity or fix the reference. |
| `unaccounted_watchlist_entity` | A tracked entity in neither `watchlist` nor `quiet_this_cycle`. Every tracked entity is accounted for each cycle — covered or explicitly quiet. | Move it to `quiet_this_cycle`. |
| `empty_section` | A required section is empty **and** no declared degradation explains it (see below). | Populate it. |
| `derived_stats_mismatch` | `stats` disagrees with the arrays it summarizes. | Orchestrator recomputes — `stats` is derived, so this should be unreachable; if it fires, it's a bug, not an edit. |

**On exhaustion** (still invalid after 2 retries): publish a **failed-run stub** — `run.status: "failed"`, `failure.stage: "validation"`, same schema, empty sections (CAPTURE #16). *Not* a banner: "flagged issue > missing issue" assumes there is a readable issue to flag, and a structurally invalid file is one the dashboard cannot render. An unrenderable file, flagged, is still unrenderable.

### `empty_section` vs declared degradation — the registry

An empty section blocks **unless** a declared degradation explains it. This reconciles CAPTURE #8 ("empty section = blocking") with the thesis contract from #5, and needs no special-casing — "is this slot's stance null?" is itself a mechanical check.

**This section is the registry** (#23). It is the one file both gates read — the validator checks `empty_section` against it, the critic honours its exemptions — so the list and the thing enforcing the list cannot drift apart. Other docs **reference** this table; they never declare locally. A degradation declared anywhere else does not exist.

#### What a degradation is

> **A degradation explains an absence inside a valid issue. A stub says there is no valid issue.**

They sit on opposite sides of the validator: a degradation *prevents* a block, a stub is what remains *after* a block wins and cannot be resolved. Same word "failed", opposite sides of the line — a failed **beat** is a degradation (the other five still rendered a real issue); a failed **validator** is a stub (nothing renders). The rubric already assumed this in ruling exhaustion to a stub rather than a banner: a degradation is a rendering decision, and a stub is the admission that nothing renders.

#### Admission test — all three must hold

1. **A required section or field would be empty or absent.** If nothing is missing, there is nothing to exempt.
2. **The system can detect the cause mechanically**, from facts the orchestrator holds itself. This keeps every degradation inside the free deterministic gate and off the critic's judgment budget.
3. **The honest render is an explained absence, not a halt.** The reader is better served by a published issue saying why something is missing than by no issue.

Test 2 is the load-bearing one, and it is what a **model's self-report cannot satisfy**. A degradation whose trigger is "did the agent remember to confess" fails silently on exactly the run where it matters — and it would grant an *exemption from blocking* on that basis, so the less reliably the failure is reported, the more easily an unexplained absence passes as declared. An absence the system cannot explain to itself is a bug, not a degradation.

#### Where a degradation renders

**At the point of the absence, in the reader's path — not only in a footer.**

The reader's risk is never "not knowing something failed". It is **reading a thin section and concluding it is a fact about the world** — a quiet week for dealmaking rather than a dead M&A beat. An absence that doesn't look like an absence misleads the reader about a fact, which is the critic's own blocking bar. Machine fields (`beats_failed`, `source_tier_counts`) serve the audit trail and the critic; the inline marker serves the reader. A degradation that cannot name where it renders has not been thought through.

#### The register

| Degradation | Trigger (mechanical) | Renders (reader-facing, at the absence) | `kind` |
|---|---|---|---|
| **Unseeded thesis slot** | belief slot's `stance` is `null` | `No thesis seeded — facts only`, in place of `research_angle` / `why_we_care` | `thesis_unseeded` |
| **Genuinely quiet cycle** | every tracked entity present in `quiet_this_cycle` | the entity listed under `quiet_this_cycle` | `quiet_cycle` |
| **Stale calendar** | no window verified in N cycles / `verified_at` absent | `conference calendar stale — surge disabled`, marker in every issue | `calendar_stale` |
| **Failed beat** | beat present in `sources_and_method.beats_failed` | inline marker in each section the beat would have fed, e.g. *"M&A coverage unavailable this cycle — beat failed"* — **not** only the `beats_failed` entry | `beat_failed` |

Exemptions are **scoped to what the trigger explains**, not blanket: an unseeded slot exempts that slot's angle, not every empty angle; a failed beat exempts the sections that beat fed, not the whole issue.

#### Ruled out (deliberately not degradations)

- **Dead source tier** — fails test 2. The system cannot distinguish "FDA published nothing" from "FDA was unreachable"; both are an empty result set. The only witness is the researcher's own `errors[]`, a model self-report. So it surfaces as an **advisory** (`kind: source_unreachable`) that publishes visibly and grants **no exemption**. A required section that is empty with only a self-report to explain it **blocks** — "we don't know why this is empty" is exactly when blocking is right.
- **Retry-exhausted validator** — not a degradation but a **stub**; see the definition above.
- **First-ever run** — nothing is empty, so test 1 fails. The digest renders in full; the queue honestly shows no slip history because none exists; `stats.previous_issue: null` is true. See [continuity across stubs](#continuity-across-stubs) — run #1 is simply the empty case of the backwards search, needing no declaration, assertion or bootstrap flag.

New degradations must be declared in the register above to earn an exemption, and must pass all three tests. An undeclared empty section blocks.

### Continuity across stubs

Three checks compare against a previous issue: `thread_dropped`, `continuity_break` (both advisory), and the catalyst queue's `first_expected_window`-vs-previous-snapshot check (**blocking** — the system's only tamper-evidence rule, #17).

**They bind to the most recent issue that actually carries the thing being compared — never to the positionally-previous issue.** A stub is transparent to continuity: it published no snapshot and covered no window, so it cannot be a join point. `first_expected_window` compares against the latest snapshot found searching backwards; `cycles_quiet` and the coverage window join across stubs.

This is not a nicety. If "previous issue" meant positionally previous, **a single failed run would launder the invariant**: the stub carries no snapshot, so the run after it has no baseline, and a `first_expected_window` contradicting the issue two back would sail through unchecked. Every failed run would be an amnesty for the one check that makes the prediction record tamper-evident. The backwards search closes that hole and makes run #1 fall out for free — the search returns nothing, the check skips, no special case. The only requirement is that an empty search result is tolerated rather than an error.

Run #1 needs no protection from this check anyway: it *creates* every value the check guards. What protects those values is the separate rule that each requires a `window_source` citation. The check only ever protected the chain **between** issues.

## Stage 2 — the critic (judgment)

### The sorting principle

> **A finding blocks when a reader would be misled about a fact. Everything else is advisory.**

Blocking is reserved for harm: the digest asserts something its own sources don't support. Advisory covers *true but weaker than it should be* — thin sourcing, an unargued angle, an uncovered beat. Advisories publish visibly and never halt the line; the retry loop is expensive and is spent on falsehood, not polish.

This principle deliberately lets the critic block on judgment. The objection to that — an unwinnable argument deadlocks the loop — is real, and is answered by the [rebuttal channel](#the-rebuttal-channel) rather than by disarming the critic.

### Blocking findings

| `kind` | Test | Manager's repair |
|---|---|---|
| `provenance_stale` | A claim presented as new rests on a source whose `published_at` predates the coverage window — recycled old news wearing a fresh date. | Re-date the claim, reframe as background, or drop it. |
| `overclaim` | The `summary` or `headline` asserts more than its cited sources support — a hedged "reportedly exploring" rendered as "will acquire". | Soften to what the source says, or source the stronger claim. |
| `aggregator_only` | A material claim's only support is `tier: aggregator`, with no primary or trade corroboration (CAPTURE #9). | Find primary/trade support, or cut. |
| `dropped_story` | A researcher found a story the manager cut, and it changes the picture. **Receipt required** — see below. | Cover it, or move the entity to `quiet_this_cycle` with the omission stated. |
| `thesis_impact_false` | A `research_angle` declares `thesis_impact: confirms` while its own text argues the belief is wrong (or vice versa) — the self-evolution engine fed a false signal. | Correct the enum, or the text. |

`thesis_impact_false` earns its place because that enum is not decoration: accumulated `challenges` mechanically trigger a logged thesis revision (#3). A miscoded impact silently corrupts the worldview the whole product is built on, and no reader could detect it.

### The receipt rule (`dropped_story`)

Completeness is the critic's reason to exist, but "you missed something" is judgment. So it blocks **only when the critic shows the receipt**.

A `dropped_story` finding is **well-formed** — and therefore blocking — only if it carries a `source` object with:

- a resolvable `url` that appears in `runs/<run_id>/findings/*.json` (i.e. a researcher *actually found it*),
- `tier` ∈ `primary｜trade` (an aggregator alone is not enough),
- `published_at` inside `issue.coverage_window`,
- and that URL cited **nowhere** in the issue.

The orchestrator validates this shape. A `dropped_story` finding **without** a well-formed receipt is **downgraded to advisory automatically** — it does not block, and does not consume a retry. The orchestrator never judges whether the story *matters*, only whether the finding is well-formed enough to act on. Materiality stays the critic's call; actionability stays mechanical.

### Advisory findings

Published on the issue, never gate it, never enter the retry payload.

| `kind` | Meaning |
|---|---|
| `thin_sourcing` | Single-source claim, no independent confirmation. |
| `coverage_gap` | In-scope area unaddressed, no receipt to prove a specific story was dropped. |
| `weak_angle` | Research Angle restates facts without arguing against the thesis. |
| `thesis_unseeded` | Angle absent because the belief slot is dormant (#5). |
| `paywalled_primary` | Fact rests on secondary coverage of a paywalled primary (CAPTURE #9). |
| `unverifiable_claim` | Critic doubts a cited claim but cannot show it exceeds the source — doubt, not a demonstrated falsehood. |
| `stale_open_thread` | A `developing` item unchanged for several cycles. |
| `source_unreachable` | A researcher reported a source or tier unreachable in `errors[]` (#6). Publishes visibly; grants **no exemption** — see [Ruled out](#ruled-out-deliberately-not-degradations). |
| `calendar_stale` | No conference window verified in N cycles — surge disabled (#18). |
| `thread_dropped` | An `open_thread` in the previous issue silently absent from this one. |
| `continuity_break` | `cycles_quiet` doesn't increment honestly, or the coverage window doesn't join the previous issue's. |

Continuity findings are advisory by design: they describe incoherence *over time*, not a reader being misled *today*.

## Verdict contract

The critic emits `critic_report.verdict`. It describes the critic's judgment only — never the orchestrator's outcome (that's `run.status`).

| `verdict` | Meaning | Orchestrator action |
|---|---|---|
| `pass` | No findings. | Publish. |
| `pass_with_advisories` | Advisories only. | Publish. |
| `blocked` | ≥1 well-formed blocking finding, retries remain. | Retry (see loop). |
| `not_run` | Critic unavailable. | Publish, `run.status: published_uncritiqued` + banner. |

Malformed verdict, or unparseable critic output, is treated as `not_run` with `reason: "unparseable critic output"` — a broken critic must not silently become a passing one.

## The retry loop

**2 retries** against the critic (CAPTURE #8), separate from the validator's. On `blocked`, the manager receives exactly:

1. its own prior `issue.json`, and
2. `blocking_findings[]`.

It **edits** that draft rather than regenerating it, so sections that already passed cannot silently mutate between rounds. Researchers are **not** re-run — no new web calls, no new facts; the manager works with what it has, plus whatever a receipt hands it.

Advisory findings are **withheld from the retry payload** — they are the record, not a to-do list. This keeps the published `critic_report` an accurate description of the issue that actually shipped.

### The rebuttal channel

A manager forced to comply with a false finding silently deletes a true story, and the deletion leaves no trace in the issue. A manager free to overrule its own auditor makes the cross-family design theatre. So: **rebut once, critic adjudicates, then comply.**

- **Retry 1** — the manager either fixes the finding, or files a `rebuttal`: a sourced argument for why the finding is wrong. It may not silently ignore it.
- **The critic re-judges** each rebuttal on its next pass and marks it `withdrawn` or `reaffirmed`. The critic has the final say.
- **Retry 2** — the manager must comply with every `reaffirmed` finding.
- **On exhaustion** — publish with `run.status: published_with_unresolved_findings`, a reader-visible banner, and **both the finding and the rebuttal printed** in `critic_report`. A genuine dispute between two model families is information the reader should have, not something either side gets to settle silently.

## Run status

| `run.status` | Cause |
|---|---|
| `published` | `pass` or `pass_with_advisories`. |
| `published_uncritiqued` | Critic unavailable — digest is good, unvetted, and says so. |
| `published_with_unresolved_findings` | Blocking findings survived retry 2. |
| `failed` | An earlier stage died, or validation exhausted — stub issue, same schema, empty sections, `failure.stage` names where (CAPTURE #16). |

A missing critic is **not** a failed run. The digest exists and is worth reading; the gap is banner-visible rather than silent — the same rule as the thesis: a missing piece bends the output, it never kills the run.

## Schema deltas this requires

Schema v0.1.0 gives findings the shape `{kind, where, note}`. This rubric needs three additions, filed as v0.1.1 (#3 is closed; these ride with the spec compilation):

```jsonc
critic_report: {
  verdict, retries_used,
  blocking_findings[]: {
    kind, where, note,
    source?:   { url, publisher, tier, published_at },  // REQUIRED when kind == "dropped_story"
    rebuttal?: {
      text,
      sources[],
      adjudication: "withdrawn" | "reaffirmed"          // set by the critic, never the manager
    }
  },
  advisory_findings[]: { kind, where, note },
  validator_report: {                                    // stage 1's record
    passed: bool,
    retries_used: number,
    findings[]: { kind, where, note }
  }
}
```

Plus `run.status` gains `published_uncritiqued` and `published_with_unresolved_findings`, and `failure.stage` accepts `"validation"`.

## What changed from v0.1.0

| | v0.1.0 | v0.2.0 |
|---|---|---|
| Sorting principle | Blocking = mechanically checkable; judgment always advisory. | Blocking = the reader would be misled about a fact; judgment can block. |
| Critic inputs | `issue.json` alone. | + raw findings, previous issue, watchlist, thesis. |
| Structural checks | Codex judged them. | Deterministic validator, before Codex. |
| Disagreement | Not addressed. | Rebuttal channel; critic adjudicates. |
| Retry budget | 2, shared. | 2 per gate, separate. |

Two things forced the change. First, v0.1.0's receipt rule was **unimplementable as written**: it required the critic to produce a source URL cited nowhere in the issue, while giving the critic nothing to read but the issue. Receipts have to come from somewhere — now, the researchers' raw findings. Second, once structural checks move to a validator, "blocking = mechanically checkable" would leave the critic with *no blocking power at all* except `dropped_story` — a pure commenter. That may be defensible, but it wasn't what v0.1.0 intended, and it isn't what was chosen.

v0.1.0's core objection — that a judgment gate deadlocks on unwinnable arguments — was not dismissed. The rebuttal channel answers it directly: disagreement is bounded (one round), adjudicated (by the critic), and published (both sides).

## Carried to the spec

- Exact prose of the two reader-facing banners (uncritiqued / unresolved findings) — a dashboard concern (#8).
- Whether `unverifiable_claim` advisories should accumulate per entity and eventually force a review.
- Wiring `critic_report.advisory_findings[].kind` to the researcher prompts (#6) so recurring advisories tighten the prompts over time.
- Researcher findings persistence at `runs/<run_id>/findings/<beat>.json` — the output contract in #6 now has two readers, the manager and the critic.
