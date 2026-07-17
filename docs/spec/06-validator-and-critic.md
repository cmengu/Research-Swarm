# 6. Validator and critic

The two gates between a manager's draft and a published issue. Covers the deterministic validator, the degradation register, the Codex critic's rubric, the receipt rule, retries, and the rebuttal channel.

**Inputs:** the draft `issue.json`, the raw findings corpus, the previous issue, and two state files.
**Outputs:** `critic_report` (embedded in the issue), and the `run.status` that decides how it publishes.

## Two gates, not one

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

Structural checks are decidable by a script with perfect accuracy, for free, in milliseconds. Judgment is not. Asking a model to count fields pays a probabilistic system to do a deterministic job — and it will miss an empty section *inconsistently*, which is worse than not checking. So the gates are separate, and only the second one is Codex.

**Budgets are separate — two retries each.** They fail for unrelated reasons, and a trivial JSON slip must never starve the critic of the budget it needed for substance. Worst case is bounded at four manager calls.

## Stage 1 — the validator

Deterministic, runs every cycle before Codex is invoked. No judgment, no model call, no retry consumed from the critic's budget.

| Check | Test | Manager's repair |
|---|---|---|
| `uncited_claim` | A factual assertion in `summary`, `headline`, a `read_through`, or a `house_view` item with no entry in its `sources[]`. | Add the source object, or delete the claim. |
| `malformed_source` | A `sources[]` entry missing `url`, `publisher`, `tier`, or `published_at`, or `tier` outside `primary｜trade｜aggregator`. | Complete the source object. |
| `dangling_entity` | An `entity_id` referenced anywhere resolving to no `state/entities/` record and carrying no `proposed_entity`. Covers issues, findings, queue items and relation edges alike — the cross-file join check. | Add the entity or fix the reference. |
| `unaccounted_competitor` | A typed competitor in neither `competitors`/an arena nor `quiet_this_cycle`. Every typed competitor is accounted for each cycle — covered, or explicitly quiet. | Move it to `quiet_this_cycle`. |
| `missing_read_through` | A `competitors`, arena, or `house_view` item with no `read_through`, or empty `read_through.text`, or a `relation`/`lens` outside its enum. The admission rule, made mechanical. | Add the read-through, or move the item to a blind spot / dropped-with-receipt. |
| `untyped_competitor` | A `competitors[]` entry whose `relation` is not one of the four program-level relations, or a `platform_threat` placed in `competitors[]` instead of the house view. | Retype it, or route platform threat to the house view. |
| `blind_spot_overflow` | `house_view.blind_spots.ranked` exceeds `cap` with no `overflow` receipt. | Emit the overflow receipt. |
| `landscape_number_unsourced` | A `treatment_landscape` efficacy number whose `efficacy_source.tier` is not `primary`. Stricter than the general bar. | Cite a primary source, or cut the number. |
| `empty_section` | A required section is empty **and** no declared degradation explains it. | Populate it. |
| `derived_stats_mismatch` | `stats` disagrees with the arrays it summarizes. | Orchestrator recomputes — `stats` is derived, so this should be unreachable; if it fires, it's a bug, not an edit. |
| `queue_tamper` | `first_expected_window` differs from the most recent snapshot carrying it; or `expected_window` changed with no new `slip_log` entry; or a status transition carries no source. | Restore the immutable value; append the missing log entry; cite the transition. |

`queue_tamper` is the system's **only tamper-evidence rule**, and the only reason the catalyst queue's predictions are worth anything ([03](03-state-and-governance.md#the-accountability-invariant)).

## The admission rule

The pivot's central new bar, and a clean expression of [determinism before judgment](01-overview.md#3-determinism-before-judgment): **a stated read-through or it doesn't publish** ([#49](https://github.com/cmengu/Research-Swarm/issues/49), [scan model #56](https://github.com/cmengu/Research-Swarm/issues/56)). Every scanned item lands in exactly one of three places, and **nothing is silently omitted** — a ternary receipt:

| Disposition | Destination | Checked by |
|---|---|---|
| **has a read-through** | `competitors[]`, an arena, `newly_discovered[]`, a `house_view` lens | `missing_read_through` — the field is present, `text` non-empty, enum valid |
| **capped blind spot** | `house_view.blind_spots` (N=5 ⚑, ranked) | `blind_spot_overflow` — the cap emits a receipt, never silent |
| **dropped with a receipt** | `quiet_this_cycle.dropped_with_receipt` | the critic's `dropped_story` receipt rule reads the source |

**This is why admission is a *validator* check, not a critic check** — the open question the map named ([#49](https://github.com/cmengu/Research-Swarm/issues/49)). The *presence* of a read-through is mechanically detectable from facts the orchestrator holds, so it belongs to the free deterministic gate and passes [admission test 2](#admission-test--all-three-must-hold). The *quality* of the prose — does it argue, or merely restate? — is judgment, and stays a critic advisory (`weak_read_through`), exactly as `weak_angle` was in v1. The validator checks that the read-through is there; the critic judges whether it earns its place.

**On exhaustion** (still invalid after two retries): publish a **failed-run stub** — `run.status: "failed"`, `failure.stage: "validation"`, same schema, empty sections.

Not a banner. "A flagged issue beats a missing issue" assumes there *is* a readable issue to flag, and a structurally invalid file is one the dashboard cannot render. An unrenderable file, flagged, is still unrenderable.

## The degradation register

An empty section blocks **unless** a declared degradation explains it.

**This section is the registry.** It is the one place both gates read — the validator checks `empty_section` against it, the critic honours its exemptions — so the list and the thing enforcing the list cannot drift apart. Other documents **reference** this table; they never declare locally. **A degradation declared anywhere else does not exist.**

### What a degradation is

> **A degradation explains an absence inside a valid issue. A stub says there is no valid issue.**

They sit on opposite sides of the validator: a degradation *prevents* a block; a stub is what remains *after* a block wins and cannot be resolved. Same word "failed", opposite sides of the line — a failed **aperture** is a degradation (the others still rendered a real issue); a failed **validator** is a stub (nothing renders).

### Admission test — all three must hold

1. **A required section or field would be empty or absent.** If nothing is missing, there is nothing to exempt.
2. **The system can detect the cause mechanically**, from facts the orchestrator holds itself. This keeps every degradation inside the free deterministic gate and off the critic's judgment budget.
3. **The honest render is an explained absence, not a halt.** The reader is better served by a published issue saying why something is missing than by no issue.

**Test 2 is load-bearing, and it is what a model's self-report cannot satisfy.** A degradation whose trigger is "did the agent remember to confess" fails silently on exactly the run where it matters — and it grants an *exemption from blocking* on that basis, so the less reliably the failure is reported, the more easily an unexplained absence passes as declared. An absence the system cannot explain to itself is a bug, not a degradation.

### Where a degradation renders

**At the point of the absence, in the reader's path — not only in a footer.**

The reader's risk is never "not knowing something failed". It is **reading a thin section and concluding it is a fact about the world** — a quiet arena rather than a dead arena scan. An absence that doesn't look like an absence misleads the reader about a fact, which is the critic's own blocking bar.

Machine fields (`beats_failed`, `source_tier_counts`) serve the audit trail and the critic; the inline marker serves the reader. **A degradation that cannot name where it renders has not been thought through.**

### The register

| Degradation | Trigger (mechanical) | Renders (reader-facing, at the absence) | `kind` |
|---|---|---|---|
| **Unseeded thesis slot** | belief slot's `stance` is `null` | `No thesis seeded — facts only`, in place of `research_angle` / `why_we_care` | `thesis_unseeded` |
| **Genuinely quiet cycle** | every typed competitor present in `quiet_this_cycle` | the competitor listed under `quiet_this_cycle` | `quiet_cycle` |
| **Stale calendar** | no window verified in N cycles, or `verified_at` absent | `conference calendar stale — surge disabled`, marker in every issue | `calendar_stale` |
| **Failed aperture** | aperture present in `sources_and_method.apertures_degraded` with status failed | inline marker in each section the aperture would have fed, e.g. *"squamous arena coverage unavailable this cycle — scan failed"* — **not** only the `apertures_degraded` entry | `arena_scan_failed` |
| **Dormant aperture** | an indication with no active arena scan this cycle | a no-op landscape marker on that indication | `arena_scan_dormant` |
| **China-first partial** | a competitor whose registry feed is CDE/chictr (no clean free feed) | low-confidence marker on the competitor, e.g. *"China-first — tracked at low confidence"* | `china_feed_partial` |
| **Stale interest list** | `config/interests.toml` `last_edited` older than the ⚑ 6-month horizon | whole-list `interest list last edited <date>` marker on the digest | `interest_list_stale` |

Exemptions are **scoped to what the trigger explains**, never blanket: an unseeded slot exempts that slot's read-through, not every empty one; a failed aperture exempts the sections that aperture fed, not the whole issue.

**New degradations must be declared in this table to earn an exemption, and must pass all three tests. An undeclared empty section blocks.**

### Ruled out — deliberately not degradations

- **Dead source tier** — fails test 2. The system cannot distinguish "FDA published nothing" from "FDA was unreachable"; both are an empty result set. The only witness is the researcher's own `errors[]`, a model self-report. So it surfaces as an **advisory** (`source_unreachable`) that publishes visibly and grants **no exemption**. A required section that is empty with only a self-report to explain it **blocks** — "we don't know why this is empty" is exactly when blocking is right.
- **Retry-exhausted validator** — not a degradation but a **stub**.
- **First-ever run** — nothing is empty, so test 1 fails. The digest renders in full, the queue honestly shows no slip history because none exists, and `stats.previous_issue: null` is true. Run #1 is simply the empty case of the backwards search below — no declaration, no assertion, no bootstrap flag.

## Continuity across stubs

Three checks compare against a previous issue: `thread_dropped` and `continuity_break` (both advisory), and the catalyst queue's `first_expected_window` check (**blocking**).

> **They bind to the most recent issue that actually carries the thing being compared — never to the positionally-previous issue.**

A stub is **transparent** to continuity: it published no snapshot and covered no window, so it cannot be a join point. `first_expected_window` compares against the latest snapshot found searching backwards; `cycles_quiet` and the coverage window join across stubs.

This is not a nicety. If "previous issue" meant positionally previous, **a single failed run would launder the invariant**: the stub carries no snapshot, so the run after it has no baseline, and a `first_expected_window` contradicting the issue two back would sail through unchecked. Every failed run would be an amnesty for the one check that makes the prediction record tamper-evident.

The backwards search closes that hole and makes run #1 fall out for free — the search returns nothing, the check skips, no special case. The only implementation requirement is that **an empty search result is tolerated rather than an error**.

Run #1 needs no protection from this check anyway: it *creates* every value the check guards. What protects those values is the separate rule that each requires a `window_source` citation. The check only ever protected the chain **between** issues.

### The lookback floor

The backwards search is unbounded in principle — if runs stub repeatedly, it keeps walking. So it has a floor:

> **⚑ The search scans at most 12 issues back.** If nothing in 12 carries the compared field, the check files `continuity_baseline_expired` (advisory, rendered) rather than scanning further.

Provisional, and calibrated to roughly six weeks at baseline cadence. The reasoning is the part to keep: twelve consecutive issues without the compared field means the system has a **louder problem than tampering**, and an unbounded scan would hide it behind a slow check rather than surfacing it. Recalibrate when real cadence data exists.

## Stage 2 — the critic

Codex (`codex exec --json`), on a ChatGPT subscription — deliberately a different model family from the workers.

### What the critic sees

**This is the load-bearing decision of the rubric.** A critic holding only the finished digest cannot audit an *absence*, because the absence was removed from the artifact it's reading. Widening the input set is what turns "you missed a story" from unanswerable into a diff.

| Input | Why the critic needs it |
|---|---|
| `issues/<this>.json` | The artifact under judgment. |
| `runs/<run_id>/findings/*.json` | Each aperture's raw output. Lets the critic catch what the **manager dropped** — the receipt rule's only source of receipts. |
| `issues/<program_id>/<previous>.json` | Continuity: open threads carried forward, `cycles_quiet` honest, coverage window joins up. |
| `state/entities/` + `state/programs/<id>/edges.json` | Every typed competitor accounted for; whether a relation is honestly typed. |
| `state/thesis.json` | Whether `thesis_bearing` is honest, and which slots are dormant (exemptions). |

**The critic has no web access.** It cannot catch what all the apertures missed — only what the pipeline **found and then lost**. That boundary is deliberate: web access would double the run, burn subscription quota on searching rather than judging, and open a prompt-injection surface on an unattended run. It is a named gap, not an oversight.

### The sorting principle

> **A finding blocks when a reader would be misled about a fact. Everything else is advisory.**

Blocking is reserved for harm: the digest asserts something its own sources don't support. Advisory covers *true but weaker than it should be* — thin sourcing, a weak read-through, an uncovered aperture. Advisories publish visibly and never halt the line; the retry loop is expensive and is spent on falsehood, not polish.

This deliberately lets the critic block on **judgment**. The objection — that an unwinnable argument deadlocks the loop — is real, and is answered by the [rebuttal channel](#the-rebuttal-channel) rather than by disarming the critic.

### Blocking findings

| `kind` | Test | Manager's repair |
|---|---|---|
| `provenance_stale` | A claim presented as new rests on a source whose `published_at` predates the coverage window — recycled old news wearing a fresh date. **During a surge window, compares against the conference window, not the run's coverage window** ([02](02-cadence-and-surge.md#the-critics-bar-does-not-move--with-one-fix)). | Re-date the claim, reframe as background, or drop it. |
| `overclaim` | The `summary` or `headline` asserts more than its cited sources support — a hedged "reportedly exploring" rendered as "will acquire". | Soften to what the source says, or source the stronger claim. |
| `aggregator_only` | A material claim's only support is `tier: aggregator`, with no primary or trade corroboration. | Find primary/trade support, or cut. |
| `unconfirmed_as_fact` | A finding flagged `unconfirmed: true` by its researcher is rendered as established fact, with its unconfirmed status not visible to the reader. | Render the flag, or cut the claim. |
| `dropped_story` | A researcher found a story the manager cut, and it changes the picture. **Receipt required** — see below. | Cover it, or move the entity to `quiet_this_cycle` with the omission stated. |
| `thesis_impact_false` | A `read_through` declares `thesis_bearing: confirms` while its own text argues the belief is wrong (or vice versa) — the self-evolution engine fed a false signal. | Correct the enum, or the text. |
| `relation_miscast` | A competitor's `relation` contradicts its own facts — e.g. a target twin (ADC) typed as a `mechanism_twin`, so an ADC's win would be read as validating the signalling thesis. | Retype to the relation the facts support. |

Two of these earn special mention:

**`thesis_impact_false`** exists because that enum is not decoration: accumulated `challenges` mechanically trigger a logged thesis revision. A miscoded impact silently corrupts the worldview the whole product is built on, and no reader could detect it.

**`unconfirmed_as_fact`** is the misled-reader bar applied to the one flag researchers are required to set ([04](04-researchers.md#sourcing-rules--non-negotiable)). It needs no content-farm-specific rule: chase-to-origin is the upstream defence, and this catches what the manager publishes anyway. An `unconfirmed` finding rendered *with* its flag visible publishes clean.

### The receipt rule

Completeness is the critic's reason to exist, but "you missed something" is judgment. So it blocks **only when the critic shows the receipt**.

A `dropped_story` finding is **well-formed** — and therefore blocking — only if it carries a `source` object with:

- a resolvable `url` that appears in `runs/<run_id>/findings/*.json` (a researcher *actually found it*),
- `tier` ∈ `primary｜trade` (an aggregator alone is not enough),
- `published_at` inside `issue.coverage_window`,
- and that URL cited **nowhere** in the issue.

The orchestrator validates this shape. A `dropped_story` **without** a well-formed receipt is **automatically downgraded to advisory** — it does not block and does not consume a retry.

The orchestrator never judges whether the story *matters*, only whether the finding is well-formed enough to act on. **Materiality stays the critic's call; actionability stays mechanical.**

### Advisory findings

Published on the issue, never gate it, never enter the retry payload.

| `kind` | Meaning |
|---|---|
| `thin_sourcing` | Single-source claim, no independent confirmation. |
| `coverage_gap` | In-scope area unaddressed, no receipt to prove a specific story was dropped. |
| `weak_read_through` | A read-through restates facts without arguing what the competitor means for the program. The admission rule's quality half — the field's *presence* is a validator block, its *quality* is this advisory. |
| `thesis_unseeded` | Angle absent because the belief slot is dormant. |
| `paywalled_primary` | Fact rests on secondary coverage of a paywalled primary. |
| `unverifiable_claim` | Critic doubts a cited claim but cannot show it exceeds the source — doubt, not a demonstrated falsehood. |
| `stale_open_thread` | A `developing` item unchanged for several cycles. |
| `source_unreachable` | A researcher reported a source or tier unreachable in `errors[]`. Publishes visibly; grants **no exemption**. |
| `calendar_stale` | No conference window verified in N cycles — surge disabled. |
| `thread_dropped` | An `open_thread` in the previous issue silently absent from this one. |
| `continuity_break` | `cycles_quiet` doesn't increment honestly, or the coverage window doesn't join the previous issue's. |
| `continuity_baseline_expired` | The backwards search hit the 12-issue floor without finding the compared field. |

Continuity findings are advisory by design: they describe incoherence *over time*, not a reader being misled *today*.

## Verdict contract

The critic emits `critic_report.verdict`. It describes the **critic's judgment only** — never the orchestrator's outcome, which is `run.status`.

| `verdict` | Meaning | Orchestrator action |
|---|---|---|
| `pass` | No findings. | Publish. |
| `pass_with_advisories` | Advisories only. | Publish. |
| `blocked` | At least one well-formed blocking finding, retries remain. | Retry. |
| `not_run` | Critic unavailable. | Publish, `run.status: published_uncritiqued` + banner. |

**Malformed or unparseable critic output is treated as `not_run`** with `reason: "unparseable critic output"`. A broken critic must not silently become a passing one.

## The retry loop

Two retries against the critic, separate from the validator's. On `blocked`, the manager receives exactly its own prior `issue.json` and `blocking_findings[]` — it edits rather than regenerates, and researchers are not re-run. Full manager-side behaviour: [05](05-manager.md#in-the-retry-loop).

### The rebuttal channel

**Rebut once, critic adjudicates, then comply.**

- **Retry 1** — the manager either fixes the finding or files a `rebuttal`: a sourced argument for why the finding is wrong. It may not silently ignore it.
- **The critic re-judges** each rebuttal on its next pass and marks it `withdrawn` or `reaffirmed`. **The critic has final say; `adjudication` is set by the critic, never the manager.**
- **Retry 2** — the manager must comply with every `reaffirmed` finding.
- **On exhaustion** — publish with `run.status: published_with_unresolved_findings`, a reader-visible banner, and **both the finding and the rebuttal printed** in `critic_report`.

A genuine dispute between two model families is information the reader should have, not something either side gets to settle silently.

## Run status

| `run.status` | Cause |
|---|---|
| `published` | `pass` or `pass_with_advisories`. |
| `published_uncritiqued` | Critic unavailable — the digest is good, unvetted, and says so. |
| `published_with_unresolved_findings` | Blocking findings survived retry 2. |
| `failed` | An earlier stage died, or validation exhausted — stub issue, same schema, empty sections, `failure.stage` names where. |

**A missing critic is not a failed run.** The digest exists and is worth reading; the gap is banner-visible rather than silent — the same rule as the thesis: a missing piece bends the output, it never kills the run.

## Deferred by decision

Neither blocks a build:

- Whether `unverifiable_claim` advisories should accumulate per entity and eventually force a review.
- Wiring advisory kinds back into the researcher prompts so recurring advisories tighten them over time.

---

*Provenance: tickets [#7](https://github.com/cmengu/Research-Swarm/issues/7) (rubric v0.2.0) and [#23](https://github.com/cmengu/Research-Swarm/issues/23) (register, v0.3.0); the lookback floor and `unconfirmed_as_fact` from [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381). Supersedes `docs/critic-rubric.md`.*
