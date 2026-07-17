# ResearchSwarm — Pivot Capture (17 Jul 2026)

## The per-program detective

Stakeholder feedback on the v1 product, and the decisions locked in the grilling that
chartered the second Wayfinder map.

**Status of this document.** It stands to map #2 as [`CAPTURE.md`](../../CAPTURE.md) stands to map #1:
a record of what was decided *before* the map existed, so the map can stay an index. Where this
document and `CAPTURE.md` disagree, **this one wins** — it is later and it is a deliberate
re-rooting. Where this document and the map's tickets disagree, **the tickets win**.

---

## The feedback, as received

The first reaction to the app was the one that matters: **"what is the objective of building
this?"** Nobody could answer it from looking at the thing. The specific complaints:

- **It is too broad for internal use.** It needs filtering down to what is actually relevant to us.
- **Pharma news and biotech news are mixed together**, but the takeaways are different in kind.
  From pharma you learn *BD appetite and future priorities*. Biotech companies are our
  *competitors*, and their financing activity reflects *investor enthusiasm*. Blending them
  destroys both signals.
- **From broad news you cannot zoom into a pipeline or a modality.** The aperture is wrong.
- **Source quality is unaddressed.** Work is needed to identify the most valuable and credible
  sources.
- **The thing we actually want is a detective for each program**, with configuration tweaked per
  program — not a general biotech newsletter, but a consolidation of the competitive news that
  bears on one drug.

The structure proposed alongside the critique:

- A **competitive-intelligence instance per program**, scanning monthly, automatically, plus a
  manual push after a big conference.
- Each scan covers: **competitor program updates** (data readouts, phase transitions, failures,
  new indications, new combinations), **BD activity**, and a **catch-all** for other pharma
  updates — stated interest in a modality or indication, regulatory environment shifts.
- **Competitors are not hand-defined.** They are other programs at the same MOA or target, or in
  the same clinical setting.
- A **competitor overview**: each competitor listed with a brief summary and *why it is considered
  a competitor*. Double-click for available data, development and BD history, publications and
  patents, next catalyst.
- **Newly discovered competitors are added to the list** and searched in all future scans.
- **Failed programs leave the competitor list** but are not deleted — a separate tab, because
  their data and development strategy carry lessons.
- A **per-indication treatment landscape**: standard of care at 1L and 2L, plus emerging therapies
  that may push the benchmarks — synthesized from competitor data.

The worked example given: **HMBD-001**, an antibody in squamous cell carcinoma. What we need is
information about the drugs being developed in the same indication.

---

## The pilot program (verified, 17 Jul 2026)

Checked against primary and trade sources rather than assumed, because the whole map hangs on it:

- **HMBD-001** is Hummingbird Bioscience's **anti-HER3 IgG1 antibody**, from its Rational Antibody
  Discovery (RAD) platform. It inhibits HER3 oncogenic signalling — a naked signalling antibody,
  not an ADC.
- Near-term priority indications: **NRG1-fusion-driven cancers, mCRPC, mCRC, and squamous cell
  carcinoma of the head and neck (SCCHN)**.
- A **Phase 1b in squamous NSCLC** evaluates HMBD-001 with standard-of-care chemotherapy, **with
  or without Merck's cetuximab**. Phase 1b trials also run in Australia, with Omico as a partner
  for clinical acceleration.
- So "squamous cell carcinoma" spans **SCCHN and sqNSCLC**, and a **partner relationship (Merck)**
  is already inside the competitive picture.

### The competitive set, and why it broke the first design

- **HER3-DXd (patritumab deruxtecan)** — Daiichi Sankyo and Merck, in global collaboration since
  October 2023. The most advanced HER3 agent. Its Phase 3 **HERTHENA-Lung02** beat chemotherapy on
  PFS in **EGFR-mutated** NSCLC; its BLA in that setting was **voluntarily withdrawn**.
- **No HER3-directed agent is FDA-approved.**
- **SDP0505** — a HER3×c-Met ADC, Phase 1 in China.
- **Cetuximab, pembrolizumab, platinum chemotherapy** — the squamous standard of care.

This set is the reason the competitor model is **typed** (see decision 6). HER3-DXd is an ADC in
EGFR-mutated NSCLC and breast; HMBD-001 is a signalling antibody in squamous NSCLC and head-and-neck.
EGFR mutations are rare in squamous, so **those populations barely overlap**. A "same target =
competitor" rule files HER3-DXd as the arch-rival. A "same indication = competitor" rule **drops it
entirely**. Both are wrong: HER3-DXd is the most important program in HMBD-001's world because it is
the **read-through on whether HER3 is druggable at all** — its Phase 3 win validates the biology, its
BLA withdrawal is a fact about HER3's regulatory path, and neither has anything to do with squamous.

And the fact a general newsletter can never surface: **Merck is simultaneously HMBD-001's combination
partner and a co-owner of the leading rival HER3 program.** That is not a news item. It is a standing
strategic fact about our BD position, and it is exactly what a detective is for.

---

## Decisions locked in the charting grilling (17 Jul 2026)

**1. Two layers, neither of them the old product.**
The **program scan** is the focus and the first thing built. A **house view** survives, but curated
to us — oncology-tight, not biotech-at-large. Both are needed to form a whole picture; the main
focus always sits with the company's interest, and that interest shifts with team strategy. Nothing
here is rigid: programs get added as drugs are acquired, and the system is expected to evolve.
*Qualifies CAPTURE #3 (domain scope) and #4 (agent roster).*

**2. Expressed interest is a separate, weighted, human-set knob.**
The house layer's aperture is **not** derived from the program roster. It is its own declared list,
because interest must be able to point where no program exists yet — you cannot decide whether to
enter ovarian if the system can only see spaces you have already entered. Interest carries
**weight**: strong interest versus just watching. This is **the only place a human deliberately
steers**; everything else self-evolves. A derived aperture was rejected for a second reason: it
makes the system agree with us by construction, so it can never say we are in the wrong space.
*Unparks the "human steering" phase-2 item from map #1.*

**3. The reader is the program's decision-owner; one decision, two evidence streams.**
Strategy team and C-suite. They are not two audiences — they are one class of reader asking one
question: **what should we do about this program?** The clinical-competitive stream ("did someone
reset the benchmark in squamous") and the deal-and-value stream ("is Merck's appetite here rising")
are **two readings of the same event**, not two products. A rival's Phase 3 readout is
simultaneously a clinical threat and a revaluation.
*Supersedes CAPTURE #2 (audience = investor/BD-grade). The citation discipline and the strict critic
bar survive; the "investor" framing does not.*

**4. The admission rule: every item carries a stated read-through.**
An item earns its place in a program issue only by naming what it means **for that program** — not
"Merck acquired X" but "Merck acquired X, and they now own a squamous asset that competes with the
cetuximab combination arm of our Phase 1b." No read-through, no publish. This single rule is what
makes it a detective rather than a newsletter: **the old system published because something
happened; this one publishes because something happened to us.**

Genuinely large items with no read-through to any program or interest do **not** vanish — they
publish in a **capped "outside our field of view" section**, each arguing why it might matter later.
That section is **house-level, not per-program**, by necessity: an item with no read-through to a
program cannot sit inside a program issue without breaking the very rule the issue exists to enforce.

The system **does not widen its own interest list** on the back of what it finds. That was
considered and rejected: it would be the system steering its own steering wheel, and decision 2 puts
that wheel in human hands. Auto-proposal may return later as an evolution.

**5. Cadence: three triggers, not two.**
- **Monthly baseline** per program — a **per-program knob**, not a global constant. A program in an
  active Phase 1b with a partner readout pending runs at a different tempo from one in discovery.
- **Automatic conference surge** — the existing machinery is kept unchanged. It is strictly better
  than the manual push it was proposed to replace, because ASCO and ESMO are known months ahead and
  a human trigger fails precisely on the week everyone is at the conference.
- **Manual push** — a button for "something just happened and I want it now". The *surprise*
  mechanism, not the conference mechanism.

The **house view runs on the same monthly beat** and publishes as one thing; a separate rhythm would
hand back the newsletter we just killed.
*Qualifies CAPTURE #12 and #17 (twice-weekly Mon+Thu). The daily heartbeat and the self-verifying
calendar are untouched.*

**6. The competitor is a program, and the relation is typed.**
The unit is the **program, not the company** — HER3-DXd is the competitor; Daiichi and Merck are its
owners. This is the only unit under which Merck can be partner and rival at once, and under which a
program can die while its company thrives. It also matches the ruling the watchlist already reached:
readouts set the agenda, not companies; tickers vanish on acquisition.

Four typed relations, and one competitor may hold several. **The type is not a label — it is a scan
instruction:**

| Relation | Unit | What it means | Scan behaviour |
|---|---|---|---|
| **Mechanism twin** | program | Same target or MOA (HER3-DXd, SDP0505) | Tracked in **every** indication — their data is evidence about our biology and our regulatory path even in tumours we will never enter |
| **Setting rival** | program | Same indication and line, any mechanism (cetuximab, pembrolizumab, chemo) | Tracked only in **our** settings — they take our patients |
| **Benchmark / SOC** | program | Defines the number we must beat | Feeds the per-indication treatment landscape |
| **Platform threat** | **company** | An engine that out-produces us (e.g. a rival antibody-discovery platform) | Neither mechanism nor setting — a capability that makes drugs we have not seen yet |

**Platform threat is the one relation whose unit is the company**, not the program. That asymmetry is
deliberate and must stay explicit, or someone will later "fix" it by forcing platform threats into a
program slug they do not have.

"Why is this a competitor" — the stakeholder's explicit ask — **is the relation**, stated, not free
text.

**7. Land the build stack; re-root on top.**
Builds 01–03 are merged. Builds 04–10 (PRs #41–#47 — manager, validator, critic, retry/rebuttal,
publish, dashboard, calendar/surge) are **merged, not stranded**. The pivot's blast radius was
checked before deciding:

- **Survives untouched** — the orchestrator and stage machine, the cadence gate, the self-verifying
  calendar and surge, the deterministic validator, the Codex critic and its rubric, the degradation
  register, the retry and rebuttal loop, publish-and-commit, run retention, the dashboard shell.
- **Gets re-rooted** — the domain model: `config/beats.toml` (six global beats → per-program scan
  config), `state/watchlist.json` (one global roster → per-program typed competitor lists plus the
  weighted house interest list), `issue.json` v1.0.0 (one digest → program issue + house view), and
  the manager and researcher prompts that must carry the admission rule.

**The pipeline survives; the product it produces changes.** The known cost is accepted rather than
hidden: build 04 bakes in the old digest shape, so merging it means merging a schema we are about to
revise. That rework is cheap — the beats are TOML, the schema is a versioned contract with a delta
log built for revision, and the manager's synthesis logic does not care what the sections are called
— and it is cheaper than seven stacked PRs rotting through a planning cycle.

---

## What carries over unchanged from map #1

Not re-litigated, and not re-decided by this pivot:

- Subscriptions, not API (headless Claude Code + Codex CLI).
- Read-only researchers; `run.py` the sole writer; every machine write a logged git commit.
- The authorship rule: **researchers report facts, the manager authors interpretation.**
- The two gates: deterministic validator free and first, adversarial critic on judgment only.
- The degradation register and its three-part admission test; fail-visible over fail-silent.
- `entity_id` as the spine.
- Static single-file dashboard, H&E palette, no build step.

## What this pivot puts back in question

Named here so no one mistakes silence for a ruling — each is a ticket or fog on the map, not a
decision made in this document:

- The **six global beats** as a roster (decision 1 re-roots them; what replaces them is open).
- **issue.json v1.0.0's** section set (watchlist, "elsewhere on the frontier").
- The **thesis** — currently one house-level worldview in six slots. Whether a program gets its own
  angle is open.
- The **seeded 22-entity watchlist** and the catalyst queue's relationship to per-program competitors.
- Whether the **admission rule becomes a blocking critic check**.
