# 9. Orchestrator and repo layout

`run.py` — the thin Python process that follows the recipe. Covers the stage machine, repo layout, config surface, retention, failure handling, and what to build first.

**This is the only component that writes anything.**

## What `run.py` is, and is not

**Is:** a thin, OS-agnostic recipe-follower. Reads config, gates on cadence, shells out to `claude -p` and `codex exec --json`, validates JSON at each seam, runs the deterministic checks, writes files, commits.

**Is not:** where judgment lives. Every judgment call in this system belongs to a model (the manager interprets, the critic judges) or to a human (the owner seeds stances). `run.py` decides only things a script can decide with certainty — is today a run day, is this JSON valid, does this URL appear in these files, does this immutable field still match.

If you find yourself writing an `if` that weighs significance, it belongs in a prompt. If you find yourself putting a field count in a prompt, it belongs here ([01](01-overview.md#3-determinism-before-judgment)).

## The stage machine

```
0. gate       — read cadence.toml; is today a run day? no → exit(0), no trace
1. prepare    — resolve run_id, coverage window; read 3 state files;
                verify conference calendar against primary sources; compute surge state
2. research   — render prompt × 6 beats; call 6 researchers in PARALLEL (read-only);
                validate each on stdout, 1 retry; persist runs/<run_id>/findings/<beat>.json
3. synthesize — call manager with 6 findings + 3 state files → issue.json draft on stdout
4. validate   — deterministic checks; fail → back to manager, 2 max → stub
5. critique   — codex critic; validate receipts; blocked → back to manager, 2 max → banner
6. publish    — compute derived stats; write issues/<date>.json; regenerate index.json;
                apply state edits (promotions, thesis evolution, queue transitions);
                git commit everything
```

Each stage logs separately. A stage that dies sets `failure.stage` and produces a stub ([failure handling](#failure-handling)).

### Stage 1 details

- **`run_id`** — `run_YYYYMMDD_HHMM`, stable for the whole run; names the findings directory.
- **Coverage window** — `from` = the most recent issue that actually covered a window (walk back past stubs, floor 12 ⚑), `to` = today. A run after a stub therefore widens to cover the missed days automatically.
- **Calendar verification** — for each window in `calendar.toml`, fetch its `source`, resolve `starts`/`ends`, stamp `verified_at`. **Never write a date not read from `source`.** Commit as a diff.
- **Surge state** — if today falls inside a verified window, set `run.surge = {window, day, of}` and switch to daily cadence. `require_verified_dates` means an unverified window surges nothing.

### Stage 2 details

Six calls in parallel, `--model sonnet`, read-only permission flags, `max_turns` from `beats.toml`. Each returns one JSON object on stdout.

Validate immediately; one retry with the error appended; on exhaustion the beat lands in `beats_failed` and **the run continues** ([04](04-researchers.md#when-a-beat-dies)). All six failing is a stub.

`run.py` writes `runs/<run_id>/findings/<beat_id>.json` — the researcher cannot ([04](04-researchers.md#transport)).

### Stage 6 details

Order matters:

1. Compute `stats` from the arrays — derived, never trusted from the manager.
2. Write `issues/<date>.json`.
3. Regenerate `issues/index.json` from the issues on disk.
4. Apply state edits: accepted promotions → `watchlist.json` + `drift_log`; thesis revisions → `thesis.json` + `drift_log` + version bump; queue transitions → `catalyst-queue.json` + `slip_log`.
5. One git commit for the whole run, citing `run_id`.

## Repo layout

```
SPEC.md                     ← the index
CAPTURE.md                  ← historical: the original voice capture
run.py                      ← the orchestrator
schedule-install            ← registers the daily heartbeat (Task Scheduler / cron / launchd)

config/
  cadence.toml              ← run days, surge knob, guards
  calendar.toml             ← six conference windows, self-verified
  beats.toml                ← the six beats; model + max_turns defaults
  models.toml               ← model ids per role (or a [models] block in cadence.toml)

prompts/
  researcher.md             ← ONE template, six beats
  manager.md                ← synthesis + authorship rules
  critic.md                 ← the rubric, rendered for Codex

state/                      ← the system's memory; every write is a diff
  watchlist.json
  thesis.json
  catalyst-queue.json

issues/
  index.json                ← the manifest (derived)
  2026-07-16.json           ← one per cycle, immutable

runs/
  run_20260716_0700/
    findings/<beat>.json    ← retained evidence, 24 runs ⚑
    logs/<stage>.log

dashboard/
  index.html                ← single self-contained file

docs/
  spec/                     ← THIS SPEC — the authority
  schema/                   ← historical drafts, superseded by spec/07
  research/                 ← the evidence base
  critic-rubric.md          ← historical, superseded by spec/06
  surge-mode.md             ← historical, superseded by spec/02
```

**On the historical docs:** `docs/critic-rubric.md`, `docs/surge-mode.md` and `docs/schema/` were the decision assets that produced this spec. They are kept for their reasoning, which is often fuller than the spec's summary of it. Where they disagree with `docs/spec/`, **the spec wins** — they predate the consolidation and several of their "carried to the spec" notes are now resolved here.

## Config surface

Everything a human should be able to change without touching code:

| File | Holds |
|---|---|
| `cadence.toml` | Run days, hour, surge enable + cadence, `require_verified_dates`, `max_surge_days`, `N` for stale-calendar ⚑ |
| `calendar.toml` | The six conference windows: `name`, `typical_window`, `source`, `starts`, `ends`, `verified_at`, `valid_through` |
| `beats.toml` | The beat roster: `id`, `name`, `charter`, `seed_angles`, `notes`; `[defaults] model`, `max_turns` |
| `models.toml` | Model id per role — researchers, manager, critic |

Adding a seventh beat is a `[[beat]]` block. Adding a surge knob is a line in `cadence.toml`. Neither is a code change — that's the test these files are meant to pass.

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
| Not a run day | `exit(0)`. No issue, no stub, no dashboard entry, no trace. |
| One beat fails validation twice | Beat → `beats_failed`; run continues; inline markers render. |
| All six beats fail | Stub, `failure.stage: "research"`. |
| Manager dies | Stub, `failure.stage: "synthesis"`. |
| Validator exhausts 2 retries | Stub, `failure.stage: "validation"`. An unrenderable file, flagged, is still unrenderable. |
| Critic unreachable / unparseable | **Not a failure.** Publish with `published_uncritiqued` + banner. |
| Blocking findings survive 2 retries | **Not a failure.** Publish with `published_with_unresolved_findings` + banner + both sides. |

A **stub** is the same schema with empty sections, `status: "failed"`, and `failure.stage` naming where it died. It appears in the dropdown. The next successful run widens its coverage window to include the missed days — automatic, because the window binds to the last issue that actually covered one.

**No alerting infrastructure in v1.** A failed run is visible in the dashboard, not pushed. Push notifications are phase 2.

## Build order

Nothing here is built. A sensible first vertical slice, each step independently checkable:

1. **Skeleton + gate** — `run.py` reads `cadence.toml`, decides run/no-run, exits. Test by faking the date. This is the whole scheduler story; get it right first.
2. **State readers + the `entity_id` rename** — load the three files, execute the `id` → `entity_id` rename on the seeded watchlist, add the cross-file join check. Cheap, and everything downstream depends on the spine.
3. **One researcher, end to end** — render the template for one beat, call it, validate on stdout, persist. Proves the transport, the read-only wall, and the seam validator in one go.
4. **Fan out to six** — parallelism, `beats_failed`, the retry.
5. **The manager** — synthesis to a v1.0.0 draft. Now you have an issue.
6. **The validator** — deterministic checks + the degradation register + the stub path. Free, and it catches the manager's mistakes before you spend critic budget on them.
7. **The critic** — Codex, the receipt validation, the retry loop, the rebuttal channel.
8. **Publish** — derived stats, `issues/`, `index.json`, state edits, the git commit.
9. **The dashboard** — render the approved v3 against a real issue. Verify the *published* artifact.
10. **Calendar verification + surge** — last, because it's the only stage whose failure is invisible, and you want the marker machinery (step 6) already working before you rely on it.

Steps 1–5 give a digest. Steps 6–8 make it trustworthy. Steps 9–10 make it readable and timely.

---

*Provenance: capture decisions #10–#17 (runtime, storage, orchestration, publishing, read-only, state, failure, defaults); ticket [#2](https://github.com/cmengu/Research-Swarm/issues/2) (Codex on Windows); retention and layout rulings from [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381).*
