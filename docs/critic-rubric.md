# Critic rubric (v0.1.0 draft)

Decision asset for ticket [#7](https://github.com/cmengu/Research-Swarm/issues/7). The concrete checklist the cross-family critic (Codex) judges an `issue.json` against, and what the orchestrator does with the verdict.

Binds to the `critic_report` block defined in [issue.json schema v0.1.0](schema/README.md) (#3) and honours the degradation contract in [`state/thesis.json`](../state/thesis.json) (#5).

## The sorting principle

> **Blocking findings are mechanically checkable against the artifact. Everything else is advisory.**

A finding blocks only if it can be verified by inspecting `issue.json` itself — a missing field, a date comparison, a dangling reference. Judgment calls — thin sourcing, weak angle, "this take is wrong" — are **always advisory**, no matter how strongly the critic feels.

Two reasons this is the rule:

1. **The manager can always act on a blocking finding.** Every blocking kind below has a concrete edit that resolves it. The retry loop never deadlocks on an argument neither side can win.
2. **The critic is a different model family** (Codex judging Claude). Cross-family disagreement on *taste* is expected and healthy; letting it gate publication would burn both retries on aesthetics and ship a banner over a fine issue.

The cost is honest and worth naming: a **wrong-but-well-cited claim publishes**. The critic flags it as an advisory, the reader sees the flag, and the issue ships. We chose visible imperfection over an unwinnable gate — same trade as "flagged issue > missing issue" (CAPTURE #8).

## Blocking findings

Every kind here is verifiable without judgment, and every one has an obvious repair.

| `kind` | Mechanical test | Manager's repair |
|---|---|---|
| `uncited_claim` | A factual assertion in `summary`, `headline`, or `elsewhere_on_frontier` with no entry in its `sources[]`. | Add the source object, or delete the claim. |
| `stale_provenance` | A `source.published_at` falls outside `issue.coverage_window`, but the claim is presented as new. | Re-date the claim, reframe as background, or drop it. |
| `malformed_source` | A `sources[]` entry missing `url`, `publisher`, `tier`, or `published_at`, or with `tier` outside `primary｜trade｜aggregator`. | Complete the source object. |
| `dangling_entity` | An `entity_id` referenced in `headline`, `themes_and_signals`, or `quiet_this_cycle` that appears in neither `watchlist` nor `new_on_radar`. | Add the entity or fix the reference. |
| `unaccounted_watchlist_entity` | A watchlist entity appears in neither the `watchlist` section nor `quiet_this_cycle`. Every tracked entity is accounted for each cycle — covered or explicitly quiet. | Move it to `quiet_this_cycle`. |
| `empty_section` | A required section is empty **and** no declared degradation explains it (see below). | Populate it. |
| `missed_must_cover` | See "the receipt rule" below. | Cover the story or justify the omission. |
| `derived_stats_mismatch` | `stats` counts disagree with the arrays they summarize. | Orchestrator recomputes — `stats` is derived, so this should be unreachable; if it fires, it's a bug, not an edit. |

### The receipt rule (`missed_must_cover`)

Completeness is the critic's whole reason to exist, but "you missed something" is judgment, not a check. So it blocks **only when the critic shows the receipt**:

A `missed_must_cover` finding is **well-formed** — and therefore blocking — only if it carries a `source` object with:
- a resolvable `url`,
- `tier` ∈ `primary｜trade` (an aggregator alone is not enough),
- `published_at` inside `issue.coverage_window`,
- and that URL is cited **nowhere** in the issue.

The orchestrator validates this shape. A `missed_must_cover` finding **without** a well-formed receipt is **downgraded to advisory automatically** — it does not block, and it does not consume a retry. This keeps the completeness bar real while staying inside the sorting principle: the orchestrator isn't judging whether the story matters, only whether the finding is well-formed enough to act on.

**Schema delta this requires.** Schema v0.1.0 gives findings the shape `{kind, where, note}`. The receipt rule needs one more optional field:

```jsonc
blocking_findings[]: {
  kind, where, note,
  source?: { url, publisher, tier, published_at }  // REQUIRED when kind == "missed_must_cover"
}
```

A `missed_must_cover` finding without a well-formed `source` is downgraded to advisory — so the field is what makes the receipt rule enforceable rather than aspirational. Filed as a v0.1.1 addition against the schema (#3 is closed; this rides with the spec compilation).

### `empty_section` vs declared degradation

An empty section blocks **unless** a declared degradation explains it. This reconciles CAPTURE #8 ("empty section = blocking") with the thesis contract from #5, and needs no special-casing — "is this slot's stance null?" is itself a mechanical check.

Currently declared:

- **Unseeded thesis.** `research_angle` / `why_we_care` rendering the marker `No thesis seeded — facts only` is **advisory** (`kind: thesis_unseeded`), never blocking, while the corresponding belief slot's `stance` is `null`. Once a human seeds that slot, the exemption lapses for it and an empty angle blocks again.
- **Genuinely quiet cycle.** An empty `watchlist` section is not blocking if every tracked entity is present in `quiet_this_cycle`. Nothing happened is a valid outcome; failing to say so is not.

New degradations must be declared here to earn an exemption. An undeclared empty section blocks.

## Advisory findings

Published on the issue, never gate it, never enter the retry payload.

| `kind` | Meaning |
|---|---|
| `thin_sourcing` | Single-source claim, no independent confirmation. |
| `coverage_gap` | In-scope area unaddressed, no receipt to prove a specific story was missed. |
| `weak_angle` | Research Angle restates facts without arguing against the thesis. |
| `thesis_unseeded` | Angle absent because the belief slot is dormant (#5). |
| `paywalled_primary` | Fact rests on secondary coverage of a paywalled primary (CAPTURE #9). |
| `unverifiable_claim` | Critic disputes a cited claim's accuracy — judgment, so advisory by construction. |
| `stale_open_thread` | A `developing` item unchanged for several cycles. |

## Verdict contract

The orchestrator parses `critic_report.verdict`:

| `verdict` | Meaning | Orchestrator action |
|---|---|---|
| `pass` | No findings. | Publish. `run.status: published` |
| `pass_with_advisories` | Advisories only. | Publish. `run.status: published` |
| `blocked` | ≥1 well-formed blocking finding, retries remain. | Retry (see loop). |
| `not_run` | Critic unavailable. | Publish. `run.status: published_uncritiqued` + banner. |

Malformed verdict, or the critic returning unparseable output, is treated as `not_run` with `reason: "unparseable critic output"` — a broken critic must not silently become a passing one.

## The retry loop

Max **2 retries** (CAPTURE #8). On `blocked`, the manager receives exactly:

1. **its own prior `issue.json`**, and
2. **`blocking_findings[]`**.

It **edits** that draft rather than regenerating it, so sections that already passed cannot silently mutate between rounds. Researchers are **not** re-run — no new web calls, no new facts; the manager works with what it has. A blocking finding it genuinely cannot fix from existing material (e.g. a `missed_must_cover` receipt it has no sourcing for) is resolved by covering the story from the receipt's own source, or by moving the entity to `quiet_this_cycle` with the omission stated.

Advisory findings are **withheld from the retry payload** — they are the record, not a to-do list. This keeps the published `critic_report` an accurate description of the issue that actually shipped.

**On exhaustion** (blocking findings survive retry 2): publish anyway with `run.status: published_with_unresolved_findings`, the surviving `blocking_findings[]` retained in `critic_report`, and a reader-visible banner. Flagged issue > missing issue.

## Run status summary

| `run.status` | Cause |
|---|---|
| `published` | `pass` or `pass_with_advisories`. |
| `published_uncritiqued` | Critic unavailable — digest is good, unvetted, and says so. |
| `published_with_unresolved_findings` | Blocking findings survived 2 retries. |
| `failed` | An earlier stage died — stub issue, same schema, empty sections (CAPTURE #16). |

A missing critic is **not** a failed run. The digest exists and is worth reading; the gap is banner-visible rather than silent — the same rule as the thesis: a missing piece bends the output, it never kills the run.

## Carried to the spec

- Exact prose of the two reader-facing banners (uncritiqued / unresolved findings) — a dashboard concern (#8).
- Whether `unverifiable_claim` advisories should accumulate per entity and eventually force a review.
- Wiring `critic_report.advisory_findings[].kind` to the researcher prompts (#6) so recurring advisories tighten the prompts over time.
