# 5. The manager

The only component that interprets. Covers what it reads, what it alone may author — including every **read-through** — how it types competitors, how it ranks, and how it behaves in the retry loop. The authorship rule survived the pivot; the fields it authors changed.

**Model:** `claude-opus-4-8` by default (configurable).
**Inputs:** all aperture `findings.json` files, plus the state layers ([03](03-state-and-governance.md)).
**Output:** one `issue.json` draft on stdout, conforming to [07](07-issue-schema.md).

## The manager's job

The apertures hand back facts with sources, deliberately unshaped ([04](04-researchers.md#why-it-isnt-issuejson-shaped)). The manager turns that pile into one dated **program issue** the program's decision-owner can read top to bottom: it merges duplicates, **types each competitor** by its relation to the program, writes each **read-through**, ranks by significance, assembles the subordinate house view, and decides the cycle's biggest story for this program.

**It is the only writer.** Researchers cannot write at the permission level; the critic only judges. Every word a reader sees was authored here.

## The authorship rule

The spine of the design, stated in [01](01-overview.md#2-facts-are-machine-authored-interpretation-is-human-seeded-and-thesis-gated) and enforced here:

> **Researchers report facts. The manager authors interpretation. A human seeds the worldview and the interest list.**

Fields that exist **only** at this stage — no researcher may emit them, and no researcher's contract has a field for them:

| Field | What it is |
|---|---|
| `headline.so_what` | The editorial judgment on the cycle's biggest story: why the program's decision-owner should care today. |
| `read_through.relation` | The typed relation — the answer to "why is this a competitor". |
| `read_through.text` | What the competitor means for the program, argued against a thesis stance. |
| `read_through.thesis_bearing` | `confirms｜challenges｜neutral` — the enum that drives thesis self-evolution. |
| `house_view.*` | The two lenses (partnership/BD, threat/financing), the surviving themes, the ranked blind spots. |
| `newly_discovered[].promotion_proposal` | Whether a discovered entity should be promoted/typed, and whether it proposes an interest. |
| `priority` (published) | Final ranking. A researcher's `priority_hint` is within-aperture only. |
| Section placement | Which section a fact lands in. |

### The read-through is the load-bearing authored object

Every published competitor, arena item, house item and discovery carries a `read_through` ([07](07-issue-schema.md#the-read-through)). It is the manager's central act of interpretation and the answer to the stakeholder's first question — *why is this a competitor?* — rendered on the page ([08](08-publishing-and-dashboard.md)).

- The **relation** types the competitor: `mechanism_twin` (same target and MOA), `target_twin` (same target, different MOA), `setting_rival` (shares patients), `benchmark_soc` (the bar), or, at house altitude, `platform_threat` (company-unit). Typing a target twin as a mechanism twin is a category error the critic can catch — an ADC's win validates the target, not the mechanism.
- The **text** is the prose of what it means for the program.
- The **presence** of a read-through is checked deterministically by the validator (the admission rule); its **quality** is the critic's call (`weak_read_through`, advisory). See [06](06-validator-and-critic.md#the-admission-rule).

### `so_what` and `read_through.text` are different fields

They look like one field wearing two hats. They are not:

- **`so_what`** is the manager's editorial judgment on the **headline** — always present, thesis-independent, written to the decision-owner's one decision.
- **`read_through.text`** is **per-competitor interpretation**, carries `thesis_bearing`, and is thesis-gated.

If they were one field, a dormant thesis would silence the headline's reason to care. Two fields, distinct duties, both kept.

## The admission rule — the manager's side

Every item the apertures surface lands in exactly one of three places, and **nothing is silently omitted** ([06](06-validator-and-critic.md#the-admission-rule)):

1. **Admitted with a read-through** — into `competitors[]`, an indication arena, `newly_discovered[]`, or a `house_view` lens.
2. **Capped blind spot** — into `house_view.blind_spots` (N=5 ⚑, ranked by signal magnitude; overflow emits a receipt, never silent) when the system sees it but cannot yet place it.
3. **Dropped with a receipt** — into `quiet_this_cycle.dropped_with_receipt` with the source that proves it was seen, so a later run does not rediscover it as novel and the critic's `dropped_story` rule has its receipt.

The manager may not respond to "I can't write a good read-through for this" by dropping the item silently — that is exactly the misled-reader failure the admission rule exists to prevent.

## Thesis gating under a program roof

The manager reads stances **fresh from `state/thesis.json` at run time** ([03](03-state-and-governance.md#the-propagation-contract)). For each thesis-dependent field:

- **Slot has a stance** → argue against it; `read_through.thesis_bearing` declares confirms/challenges/neutral.
- **Slot is dormant (`stance: null`)** → render `No thesis seeded — facts only` in place of the read-through's argument. The item **still ships** with facts and relation intact. Do **not** improvise a stance.

**⚑ Whether a program carries its own angle is deferred by decision** ([#49](https://github.com/cmengu/Research-Swarm/issues/49)). v1 does the minimum that forecloses neither answer: read-throughs argue the existing house-level thesis via `thesis_bearing`, feeding the same drift engine v1's `thesis_impact` fed. When the program-thesis question resolves, it slots in here without a schema break. Four of six stances are `agent_draft_delegated` — provisional ([03](03-state-and-governance.md#stance-provenance)); the manager treats them as live while the provenance label travels with the lens.

### `thesis_bearing` is not decoration

Accumulated `challenges` on one belief mechanically trigger a logged thesis revision, published as a `thesis_updates` entry. A miscoded bearing **silently corrupts the worldview**, which is why the critic blocks on `thesis_impact_false` ([06](06-validator-and-critic.md#blocking-findings)).

## Ranking and confidence

- **`priority` is a three-value tag** — `high｜medium｜low`. No numeric score. **Ranking within a tier is document order** — the manager's judgment made visible.
- **`confidence` lives on the headline and on each competitor entry only** — the two places a reader acts on. Not on house items, themes, or blind spots.

## Merging, typing, and coverage duties

- **Duplicates.** Merge on `entity_ids`. The same readout from the biology scan and an arena scan is signal, not noise.
- **Typing.** The manager assigns each competitor its relation and writes the edge's read-through (executed against `state/programs/<id>/edges.json` by `run.py`). `platform_threat` is company-unit and routes to the house view, never `competitors[]`.
- **Every typed competitor is accounted for, every cycle** — covered in `competitors`/an arena, or explicitly in `quiet_this_cycle`. The validator blocks on the unaccounted case.
- **`cycles_quiet` increments honestly** across issues, joining across failed-run stubs ([06](06-validator-and-critic.md#continuity-across-stubs)).
- **Discovery.** A `proposed_entity` / `proposed_relation` is a candidate; the manager decides whether it becomes a `newly_discovered` entry with a `promotion_proposal`, and whether to attach a `proposes_interest` (a finding the human confirms in the editor — the system never writes the interest list). Accepted promotions are executed by `run.py` with a `drift_log` entry.

## Degradation duties

The manager is told which apertures failed or are dormant. For each, it marks **inline, at the point of the absence**, every section that aperture would have fed. Writing the aperture into `apertures_degraded` is the audit trail, **not** the render ([04](04-researchers.md#when-an-aperture-dies)). The reader's risk is reading a thin section and concluding it is a fact about the world.

## Publishing an unconfirmed finding

A researcher may hand up `unconfirmed: true`. The manager may publish it, but may not **launder** it:

> Render an `unconfirmed` finding **with its unconfirmed status visible**, or don't render it. Presenting it as established fact is the misled-reader bar, and the critic blocks on it.

## In the retry loop

When the critic blocks, the manager receives exactly its own prior `issue.json` and `blocking_findings[]`.

- **It edits that draft; it does not regenerate it.** Sections that passed must not silently mutate.
- **Researchers are not re-run.** No new web calls; the manager works with what it has, plus whatever a receipt hands it.
- **Advisory findings are withheld** from the retry payload — they are the record, not a to-do list.

### The rebuttal channel

**Rebut once, critic adjudicates, then comply.**

- **Retry 1** — fix the finding, or file a `rebuttal`: a sourced argument for why it is wrong. Silently ignoring it is not an option.
- **The critic re-judges** each rebuttal and marks it `withdrawn` or `reaffirmed`. The critic has final say; the manager never sets `adjudication`.
- **Retry 2** — comply with every `reaffirmed` finding.
- **On exhaustion** — the issue publishes with both the finding and the rebuttal printed, under a banner.

Full loop mechanics: [06 — Validator and critic](06-validator-and-critic.md#the-retry-loop).

---

*Provenance: authorship split inherited from v1 ([#6](https://github.com/cmengu/Research-Swarm/issues/6), capture decision #3); the read-through and typed relations from pivot children [#50](https://github.com/cmengu/Research-Swarm/issues/50) and [#54](https://github.com/cmengu/Research-Swarm/issues/54); the house view from [#58](https://github.com/cmengu/Research-Swarm/issues/58); the admission rule from [#56](https://github.com/cmengu/Research-Swarm/issues/56); interest proposals from [#55](https://github.com/cmengu/Research-Swarm/issues/55). Retry/rebuttal and the `so_what` distinction inherited from v1 ([#7](https://github.com/cmengu/Research-Swarm/issues/7), [#24](https://github.com/cmengu/Research-Swarm/issues/24)).*
