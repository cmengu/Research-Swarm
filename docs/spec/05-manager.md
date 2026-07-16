# 5. The manager

The only component that interprets. Covers what it reads, what it alone may author, how it ranks, and how it behaves in the retry loop.

**Model:** `claude-opus-4-8` by default (configurable).
**Inputs:** all six `findings.json` files, plus the three state files ([03](03-state-and-governance.md)).
**Output:** one `issue.json` draft on stdout, conforming to [07](07-issue-schema.md).

## The manager's job

Six researchers hand back facts with sources, deliberately unshaped ([04](04-researchers.md#why-it-isnt-issuejson-shaped)). The manager turns that pile into one dated digest a busy investor can read top to bottom: it merges duplicates, ranks by significance, argues each item against the thesis, and decides what the biggest story of the cycle actually is.

**It is the only writer.** Researchers cannot write at the permission level; the critic only judges. Every word a reader sees was authored here.

## The authorship rule

This is the spine of the whole design, stated in [01](01-overview.md#2-facts-are-machine-authored-interpretation-is-human-seeded-and-thesis-gated) and enforced here:

> **Researchers report facts. The manager authors interpretation. A human seeds the worldview.**

Fields that exist **only** at this stage — no researcher may emit them, and no researcher's contract has a field for them:

| Field | What it is |
|---|---|
| `headline.so_what` | The editorial judgment on the cycle's biggest story: why this reader should care today. |
| `watchlist[].research_angle` | The opinionated take on one entity, argued **against** a thesis stance. |
| `watchlist[].thesis_impact` | `confirms｜challenges｜neutral` — the enum that drives thesis self-evolution. |
| `new_on_radar[].why_we_care` | Why a newly surfaced entity matters, tied to the thesis. |
| `themes_and_signals[].argument` | The cross-cutting pattern and what it would mean if it holds. |
| `elsewhere_on_frontier[].why_it_matters` | Why an incumbent's move reprices something. |
| `priority` (published) | Final ranking. A researcher's `beat_priority` is a within-beat hint only. |
| Section placement | Which section a fact lands in. |

### `so_what` and `research_angle` are different fields

They look like the same field wearing two hats. They are not, and collapsing them is a bug:

- **`so_what`** is the manager's editorial judgment on the **headline** — always present, thesis-independent. It answers "why does this matter today?" for the one biggest story.
- **`research_angle`** is **thesis machinery** — per watchlist entity, carries `thesis_impact`, and is **thesis-gated**: if the relevant belief slot is dormant, it renders the degradation marker instead.

If they were one field, a dormant thesis would silence the headline's reason to care — a thesis-gated field would have swallowed a thesis-independent duty. That violates the rule that a missing piece bends the output rather than blanking it. Two fields, distinct duties, both kept.

## Thesis gating

The manager reads stances **fresh from `state/thesis.json` at run time** — never from a cached copy or an inlined prompt ([03](03-state-and-governance.md#the-propagation-contract)).

For each thesis-dependent field:

- **Slot has a stance** → argue against it. `thesis_impact` declares whether the evidence confirms, challenges, or is neutral to that stance.
- **Slot is dormant (`stance: null`)** → render `No thesis seeded — facts only` in place of the angle. The item **still ships** with its facts intact. Do **not** improvise a stance to fill the gap.

The exemption is **scoped to the dormant slot**, not blanket: one dormant slot exempts that slot's angles, not every empty angle in the issue.

Four of six stances are currently `agent_draft_delegated` — provisional, not owner-endorsed ([03](03-state-and-governance.md#stance-provenance)). The manager treats them as live stances (they are what the machine argues against) while the provenance label travels with the lens.

### `thesis_impact` is not decoration

Accumulated `challenges` on one belief mechanically trigger a logged thesis revision, which the issue publishes as a `thesis_updates` entry. A miscoded impact **silently corrupts the worldview the whole product is built on**, and no reader could detect it — which is why the critic blocks on `thesis_impact_false` ([06](06-validator-and-critic.md#blocking-findings)).

## Ranking and confidence

- **`priority` is a three-value tag** — `high｜medium｜low`. No numeric score: 82-vs-79 is false precision. **Ranking within a tier is document order**, i.e. the manager's judgment made visible.
- **`confidence` lives on the headline and on each watchlist entry only** — the two places a reader acts on. Not on radar items, themes, or frontier moves. Scoring everything makes the manager stamp "high" everywhere and kills the signal.

## Merging and coverage duties

- **Duplicates.** Beats overlap by design; the same story arriving from three beats is expected. Merge on `entity_ids`. Two beats independently finding the same readout is signal, not noise.
- **Every tracked entity is accounted for, every cycle** — either covered in `watchlist` or explicitly listed in `quiet_this_cycle`. There is no third option; the validator blocks on `unaccounted_watchlist_entity`.
- **`cycles_quiet` increments honestly** across issues, joining across failed-run stubs ([06](06-validator-and-critic.md#continuity-across-stubs)).
- **Off-roster finds.** A researcher's `proposed_entity` is a candidate; the manager decides whether it becomes a `new_on_radar` entry, and whether to attach a `promotion_proposal` — the self-maintaining watchlist's mechanism. No human approval, but the reason is written down so drift is auditable.
- **Radar promotion** proposed here is executed by `run.py` against `state/watchlist.json` with a `drift_log` entry ([03](03-state-and-governance.md#self-evolution)).

## Degradation duties

The manager is told which beats failed. For each, it marks **inline, at the point of the absence**, every section that beat would have fed — *"M&A coverage unavailable this cycle — beat failed"*. Writing the beat into `beats_failed` is the audit trail, **not** the render ([04](04-researchers.md#when-a-beat-dies)).

The reader's risk is never "not knowing something failed". It is **reading a thin section and concluding it is a fact about the world** — a quiet week for deals rather than a dead M&A beat. An absence that doesn't look like an absence misleads the reader about a fact, which is precisely the critic's blocking bar.

## Publishing an unconfirmed finding

A researcher may hand up a finding flagged `unconfirmed: true` — an aggregator was the only traceable origin ([04](04-researchers.md#sourcing-rules--non-negotiable)). The manager may publish it. What it may not do is **launder it**:

> Render an `unconfirmed` finding **with its unconfirmed status visible**, or don't render it. Presenting it as established fact is exactly the misled-reader bar, and the critic blocks on it.

No separate rule is needed for content farms: chase-to-origin catches them upstream, and anything that survives to publication is covered by the existing bar.

## In the retry loop

When the critic blocks, the manager receives exactly two things: its own prior `issue.json`, and `blocking_findings[]`.

- **It edits that draft; it does not regenerate it.** Sections that already passed must not silently mutate between rounds.
- **Researchers are not re-run.** No new web calls, no new facts. The manager works with what it has, plus whatever a receipt hands it.
- **Advisory findings are withheld** from the retry payload. They are the record, not a to-do list — which keeps the published critic report an accurate description of the issue that actually shipped.

### The rebuttal channel

A manager forced to comply with a false finding silently deletes a true story, and the deletion leaves no trace. A manager free to overrule its own auditor makes the cross-family design theatre. So: **rebut once, critic adjudicates, then comply.**

- **Retry 1** — either fix the finding, or file a `rebuttal`: a sourced argument for why the finding is wrong. Silently ignoring it is not an option.
- **The critic re-judges** each rebuttal and marks it `withdrawn` or `reaffirmed`. The critic has final say; the manager never sets `adjudication`.
- **Retry 2** — comply with every `reaffirmed` finding.
- **On exhaustion** — the issue publishes with both the finding and the rebuttal printed, under a banner.

Full loop mechanics, budgets and outcomes: [06 — Validator and critic](06-validator-and-critic.md#the-retry-loop).

---

*Provenance: capture decision #3 (only the manager writes); tickets [#3](https://github.com/cmengu/Research-Swarm/issues/3) (schema and ranking), [#5](https://github.com/cmengu/Research-Swarm/issues/5)/[#11](https://github.com/cmengu/Research-Swarm/issues/11) (thesis gating), [#6](https://github.com/cmengu/Research-Swarm/issues/6) (authorship split), [#7](https://github.com/cmengu/Research-Swarm/issues/7) (retry and rebuttal); the `so_what`/`research_angle` ruling from [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381).*
