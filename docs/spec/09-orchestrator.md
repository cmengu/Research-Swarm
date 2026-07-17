# 9. Orchestrator and repo layout

`run.py` — the thin Python process that follows the recipe. Covers the stage machine, repo layout, config surface, retention, failure handling, and what to build first.

**This is the only component that writes anything.**

## What `run.py` is, and is not

**Is:** a thin, OS-agnostic recipe-follower. Reads config, gates on cadence, shells out to `claude -p` and `codex exec --json`, validates JSON at each seam, runs the deterministic checks, writes files, commits.

**Is not:** where judgment lives. Every judgment call in this system belongs to a model (the manager interprets, the critic judges) or to a human (the owner seeds stances). `run.py` decides only things a script can decide with certainty — is today a run day, is this JSON valid, does this URL appear in these files, does this immutable field still match.

If you find yourself writing an `if` that weighs significance, it belongs in a prompt. If you find yourself putting a field count in a prompt, it belongs here ([01](01-overview.md#3-determinism-before-judgment)).

## The stage machine

```
0. gate       — read each program's cadence; is any program due today (or --push)? no → exit(0), no trace
1. prepare    — for the due program P: resolve run_id, coverage window; read state layers;
                verify conference calendar; poll the registry watch; compute surge state
2. research   — render prompt × apertures (1 biology + N arena + 1 house); call 1+N+1
                researchers in PARALLEL (read-only); validate each on stdout, 1 retry;
                persist runs/<run_id>/findings/<aperture>.json
3. synthesize — call manager with aperture findings + state → issue.json draft on stdout
4. validate   — deterministic checks incl. the admission rule; fail → back to manager, 2 max → stub
5. critique   — codex critic; validate receipts; blocked → back to manager, 2 max → banner
6. publish    — compute derived stats; write issues/P/<date>.json; regenerate index;
                apply state edits (entity facts, relation edges, thesis evolution, queue
                transitions, accepted promotions); git commit everything
```

The stage machine — the engine — did not change in the pivot; the counts and paths did. This confirms the [blast-radius ruling](01-overview.md#the-pivot-that-produced-this): program identity lives in `state/` data and prompts, never in the orchestrator's control flow ([#52](https://github.com/cmengu/Research-Swarm/issues/52) merged the re-root with **zero code changes** to the pipeline).

Each stage logs separately. A stage that dies sets `failure.stage` and produces a stub ([failure handling](#failure-handling)).

### Stage 1 details

- **`run_id`** — `run_YYYYMMDD_HHMM`, stable for the whole run; names the findings directory.
- **Coverage window** — `from` = the most recent issue *of this program* that actually covered a window (walk back past stubs, floor 12 ⚑), `to` = today. A run after a stub widens to cover the missed days automatically.
- **Calendar verification** — for each window in `calendar.toml`, fetch its `source`, resolve `starts`/`ends`, stamp `verified_at`. **Never write a date not read from `source`.** Commit as a diff.
- **Registry poll** — for the program's tracked NCT set, query ClinicalTrials.gov v2 by `lastUpdatePostDate` since the last run; hand the diff to the biology/arena apertures ([04](04-researchers.md#the-registry-watch-and-the-feed-set)).
- **Surge state** — if today falls inside a verified window and the program has a competitor there, set `run.surge = {window, day, of}` and switch to daily cadence.

### Stage 2 details

`1 + N + 1` calls in parallel, `--model sonnet`, read-only permission flags, `max_turns` from the program config. Each returns one JSON object on stdout.

Validate immediately; one retry with the error appended; on exhaustion the aperture lands in `apertures_degraded` and **the run continues** ([04](04-researchers.md#when-an-aperture-dies)). All apertures failing is a stub.

`run.py` writes `runs/<run_id>/findings/<aperture_id>.json` — the researcher cannot ([04](04-researchers.md#transport)).

### Stage 6 details

Order matters:

1. Compute `stats` from the arrays — derived, never trusted from the manager.
2. Write `issues/<program_id>/<date>.json`.
3. Regenerate `issues/<program_id>/index.json` from the issues on disk.
4. Apply state edits: new/corrected entity facts → `state/entities/` (append, cite the run); accepted promotions + retypes → `state/programs/<id>/edges.json` + `drift_log`; thesis revisions → `thesis.json` + `drift_log` + version bump; queue transitions → `catalyst-queue.json` + `slip_log`. **Interest proposals are recorded as findings, never written to `interests.toml`** — the human confirms them in the editor ([03](03-state-and-governance.md#the-interest-list)).
5. One git commit for the whole run, citing `run_id`.

## Repo layout

```
SPEC.md                     ← the index
CAPTURE.md                  ← historical: the original voice capture
run.py                      ← the orchestrator
schedule-install            ← registers the daily heartbeat (Task Scheduler / cron / launchd)

config/
  calendar.toml             ← six conference windows, self-verified (surge knob + guards live here)
  interests.toml            ← the steering wheel: strong/watching tiers + notes (human-owned)
  programs/
    hmbd-001.toml           ← one detective per drug: identity, indications, aperture, cadence
  models.toml               ← model ids per role

prompts/
  researcher.md             ← ONE template, all apertures
  manager.md                ← synthesis + authorship + read-through rules
  critic.md                 ← the rubric, rendered for Codex

state/                      ← the system's memory; every write is a diff
  entities/<entity_id>.json ← shared competitor facts, program-agnostic
  programs/
    hmbd-001/
      edges.json            ← per-program relation + read-through edges
      catalyst-queue.json   ← per-program predictions
  thesis.json               ← the shared worldview (six belief slots)

issues/
  hmbd-001/
    index.json              ← the per-program manifest (derived)
    2026-07-18.json         ← one per cycle, immutable

runs/
  run_20260718_0700/
    findings/<aperture>.json ← retained evidence, 24 runs ⚑
    logs/<stage>.log

dashboard/
  index.html                ← single self-contained file (v4 detective IA)

docs/
  spec/                     ← THIS SPEC — the authority
  schema/                   ← reference samples; the HMBD-001 sample backs spec/07
  research/                 ← the evidence base + the program-detective source set
  critic-rubric.md          ← historical, superseded by spec/06
  surge-mode.md             ← historical, superseded by spec/02
```

**On the historical docs:** `docs/critic-rubric.md` and `docs/surge-mode.md` were decision assets that produced the v1 spec; they are kept for their fuller reasoning, and where they disagree with `docs/spec/`, **the spec wins**. `docs/schema/` is *not* historical — it holds the reference samples that back the schema, including `sample-issue-hmbd-001-2026-07-18.json`, the hand-built v2.0.0 example ([07](07-issue-schema.md)). The two capture docs — `CAPTURE.md` and the pivot grilling ([PR #48](https://github.com/cmengu/Research-Swarm/pull/48)) — are historical; the spec wins wherever they disagree.

## Config surface

Everything a human should be able to change without touching code:

| File | Holds |
|---|---|
| `programs/<id>.toml` | One detective: identity, `moa`, indications, aperture, per-program `baseline` cadence ⚑, `cold_start_lookback_days` ⚑, `seed_competitors`. **Adding a program is one new file.** |
| `interests.toml` | The steering wheel: `[[interest]]` `tier` + `note`, `version`, `last_edited`. Human-owned; edited via the interest editor, not by the loop ([03](03-state-and-governance.md#the-interest-list)). |
| `calendar.toml` | The six windows: `name`, `typical_window`, `source`, `starts`, `ends`, `verified_at`, `valid_through`; the surge knob (`enabled`, `cadence`), guards (`require_verified_dates`, `max_surge_days`), `N` for stale-calendar ⚑ |
| `models.toml` | Model id per role — researchers, manager, critic |

Adding a program is one `programs/<id>.toml`. Adding an indication is a `[[indication]]` block. Adding a surge knob is a line in `calendar.toml`. None is a code change — that's the test these files are meant to pass, and [#52](https://github.com/cmengu/Research-Swarm/issues/52) confirmed the pipeline passes it.

## Retention

| Artifact | Policy | Why |
|---|---|---|
| `issues/*.json` | **Forever** | Small, and the archive is the track record. |
| `state/*.json` | **Forever**, in git | The memory; history is the drift log. |
| `runs/<id>/findings/` | **24 runs** ⚑ | See below. |
| `runs/<id>/logs/` | 24 runs ⚑ | Same window as the findings they explain. |

**⚑ Findings retention = 24 runs (~3 months at Mon+Thu, roughly 8–9 runs/month).** Provisional; the reasoning is what matters.

Raw findings are **evidence**, not scratch ([04](04-researchers.md#this-corpus-is-evidence)): they are the critic's only source of receipts for a blocking `dropped_story`, and the record of any published dispute. Three months covers the critic's live duty (which only ever needs the current run) with a wide margin for auditing a dispute after the fact, and it bounds disk growth. **Beyond the window, findings prune but the published issue stays** — the issue is the product; the findings are the working papers.

If a dispute audit ever needs findings older than the window, that's a signal to lengthen it, not to keep everything forever by default.

## Failure handling

| Failure | Behaviour |
|---|---|
| No program due | `exit(0)`. No issue, no stub, no dashboard entry, no trace. |
| One aperture fails validation twice | Aperture → `apertures_degraded`; run continues; inline markers render. |
| All apertures fail | Stub, `failure.stage: "research"`. |
| Manager dies | Stub, `failure.stage: "synthesis"`. |
| Validator exhausts 2 retries | Stub, `failure.stage: "validation"`. An unrenderable file, flagged, is still unrenderable. |
| Critic unreachable / unparseable | **Not a failure.** Publish with `published_uncritiqued` + banner. |
| Blocking findings survive 2 retries | **Not a failure.** Publish with `published_with_unresolved_findings` + banner + both sides. |

A **stub** is the same schema with empty sections, `status: "failed"`, and `failure.stage` naming where it died. It appears in the dropdown. The next successful run widens its coverage window to include the missed days — automatic, because the window binds to the last issue that actually covered one.

**No alerting infrastructure in v1.** A failed run is visible in the dashboard, not pushed. Push notifications are phase 2.

## Build order

The v1 pipeline (builds #28–#33 / #41–#47) is built and merged; [#52](https://github.com/cmengu/Research-Swarm/issues/52) confirmed it re-roots onto the program model with **zero code changes** — program identity lives only in `state/` data and prompts. So the remaining build work is a **state-shape + prompt-framing change**, not an engine rewrite, resumed as `build` tickets. A sensible order, each step independently checkable:

1. **Program config + the state split** — add `config/programs/hmbd-001.toml`, `config/interests.toml`; split `state/` into `entities/` + `programs/<id>/`; extend the cross-file join check across the split. Everything downstream depends on the spine.
2. **Re-frame the researcher prompt** — apertures replace beats (`1 + N + 1`), the registry watch feeds the biology/arena scans, the competitor set + interests render as the coverage duty. No new transport.
3. **Re-frame the manager prompt** — author read-throughs and typed relations, assemble the house view, obey the admission rule. Emits a v2.0.0 draft.
4. **Extend the validator** — the four new blocking checks (`missing_read_through`, `untyped_competitor`, `blind_spot_overflow`, `landscape_number_unsourced`) + the new degradation rows. Free, and it catches the manager before critic budget is spent.
5. **Extend the critic** — `weak_read_through`, `relation_miscast`; the receipt rule and rebuttal channel are unchanged.
6. **Publish per-program** — `issues/<program_id>/`, the per-program manifest, edge/entity state edits, the git commit.
7. **The dashboard v4** — render the detective IA against a real HMBD-001 issue. Verify the *published* artifact.
8. **Cadence + registry + surge** — the per-program dial, the registry poll, calendar verification and surge. Last, because the calendar's failure is the only invisible one, and you want the marker machinery already working.
9. **Add program #2** — the modularity test: drop one `config/programs/*.toml`, edit nothing else. If it isn't one file, step 1 was wrong.

## Scaling to many programs

"Evolving" is structural, not aspirational ([#59](https://github.com/cmengu/Research-Swarm/issues/59)):

- **Cost scales with distinct apertures, not program count.** A second squamous-NSCLC program reuses the shared arena scan and treatment landscape ([07](07-issue-schema.md#indications)) — it adds ~zero arena cost, only its own biology scan and its own read-throughs. The house sweep is fixed O(1).
- **The competitor pool splits** into a shared global fact layer (`state/entities/`, one record per `entity_id`) and per-program relation edges (`(program_id × entity_id) → relation + read_through`). Facts lift to global, which kills silo-drift; publish dedup renders one shared fact with a per-program read-through in each program's issue.
- **State is one repo, many programs**; the git-diff audit holds. **SQLite un-parks only when cross-entity query over the shared pool becomes the hot access pattern** — a named trigger, not a speculative migration.
- **Lifecycle mirrors demote-and-archive** ([03](03-state-and-governance.md#the-failed-competitor-afterlife)): a discontinued *own* program authors a lessons retrospective — our own failure is the case the failed-competitor design never had to consider; an out-licensed program drops to a `watching` cadence.
- **Smallest change to add program #2 = drop one config file, zero edits to anything existing.** That is the modularity pass/fail.

---

*Provenance: the engine, retention and failure handling inherited unchanged from v1 (capture decisions #10–#17, [#24](https://github.com/cmengu/Research-Swarm/issues/24)); the state split, per-program paths and scaling from pivot children [#52](https://github.com/cmengu/Research-Swarm/issues/52) (zero-code re-root) and [#59](https://github.com/cmengu/Research-Swarm/issues/59); Codex on Windows [#2](https://github.com/cmengu/Research-Swarm/issues/2).*
