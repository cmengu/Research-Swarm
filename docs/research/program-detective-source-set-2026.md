# The credible source set for a program detective (17 Jul 2026)

Research asset for [wayfinder ticket #51](https://github.com/cmengu/Research-Swarm/issues/51), child of [the per-program detective map (#49)](https://github.com/cmengu/Research-Swarm/issues/49). AFK — evidence, not decisions. Written so the scan-model ticket can wire these feeds in directly.

## What this does and does not re-open

v1 already tiered sources by **trust**: `primary` (FDA/EMA, ClinicalTrials.gov, SEC, company PRs, PubMed/bioRxiv/medRxiv) > `trade` (Endpoints, Fierce, STAT-free, BioPharma Dive, Reuters) > `aggregator`, with the chase-to-origin rule, the `unconfirmed` flag when an aggregator is the only origin, and the `paywalled` flag (see [`docs/spec/04-researchers.md`](../spec/04-researchers.md)). **That tiering is sound and is not re-opened here.** It answers *how much do I trust a source*.

A **program** detective needs a second axis the market digest never did: *which specific feeds emit a competitor program's moves, how fast, and in what format*. A trust tier tells you Endpoints beats an SEO farm; it does not tell you that a Phase 3 OS miss shows up in a ClinicalTrials.gov `lastUpdatePostDate` field six weeks before the press release. This doc extends the tiering along that second axis — **emission**, not trust.

The one-line reframe that drives everything below: **for a program detective, most "competitor program updates" are registry facts, not news.** The system currently waits for someone to write about them. It should read them at source.

---

## 1. The single most important finding: registry-diff is a first-class input

The stakeholder's "competitor program update" list — a phase transition, a new combination arm, a new indication, a quietly-changed primary endpoint, a status change to *terminated* or *active, not recruiting* — is **structured registry data**, exposed machine-readably and for free, before it is news.

**ClinicalTrials.gov API v2** ([official docs](https://clinicaltrials.gov/data-api/api), [NLM technical bulletin, Mar 2024](https://www.nlm.nih.gov/pubs/techbull/ma24/ma24_clinicaltrials_api.html)) is the load-bearing feed:

| Property | Value |
|---|---|
| Format | JSON (also CSV), OpenAPI 3.0, clean query syntax, token pagination |
| Auth | **None.** Public domain, `efts`-style open CORS |
| The diff mechanism | `lastUpdatePostDate` is a **filterable and sortable** status field. `sort=LastUpdatePostDate:desc` + a date filter = a cron-friendly "what changed since my last scan" query |
| Change granularity | The site's version-compare shows **which modules changed** between two record versions — endpoint, arms, status, enrollment — not just that *something* did |

**Why this is a decision, not a nicety.** The failure mode is explicit and has already happened in this exact competitive set: HER3-DXd's HERTHENA-Lung02 OS miss led Daiichi/Merck to **voluntarily withdraw the BLA in May 2025** ([Merck](https://www.merck.com/news/patritumab-deruxtecan-biologics-license-application-for-patients-with-previously-treated-locally-advanced-or-metastatic-egfr-mutated-non-small-cell-lung-cancer-voluntarily-withdrawn/), [Daiichi Sankyo](https://daiichisankyo.us/press-releases/-/article/patritumab-deruxtecan-biologics-license-application-for-patients-with-previously-treated-locally-advanced-or-metastatic-egfr-mutated-non-small-cell-lung-cancer-voluntarily-withdrawn)). A registry-native detective sees the trial's status and results modules move on their posted date; a news-native detective learns it whenever a journalist writes it up. The cost of being wrong about this is measured in weeks of blindness on your own program's closest rival.

**Recommendation for the scan model:** treat a set of tracked NCT IDs (the program's mechanism twins, setting rivals, and SOC comparators) as a **standing registry watch**, polled by `lastUpdatePostDate`, feeding a diff the researcher summarizes. This is a genuinely new *input class*, not a new source URL — flag it to the scan-model ticket as such.

**One blind spot to state honestly:** ClinicalTrials.gov is the US registry. China-sponsored programs (see §2) register on **chictr.org.cn** and move through China's **CDE**; some also cross-register on ClinicalTrials.gov but not reliably or promptly. Registry-diff is near-complete for US/global trials and **partial for China-first assets** — which is precisely where two of this program's competitors live.

---

## 2. The competitive-set feeds for HER3 and squamous

Named feeds for the actual competitors in the pilot program's (HMBD-001) space, in emission order — where each surfaces *first*.

### The mechanism twins and setting rivals

- **HER3-DXd / patritumab deruxtecan (Daiichi Sankyo + Merck).** Program spans ~15 tumour types after the NSCLC BLA withdrawal ([Merck](https://www.merck.com/news/patritumab-deruxtecan-biologics-license-application-for-patients-with-previously-treated-locally-advanced-or-metastatic-egfr-mutated-non-small-cell-lung-cancer-voluntarily-withdrawn/)). Surfaces first at: **ClinicalTrials.gov registry deltas** → **Daiichi Sankyo IR / Merck IR** (press releases, day-of) → conference readouts → JCO/NEJM. Both sponsors are large-cap and US-reporting, so SEC 8-K/10-Q catch material events.
- **SDP0505 (Shengdi / Hengrui lineage — SHR9265 payload).** First-in-class **HER3 × c-Met bispecific ADC**, global Phase 2 in EGFR-TKI-resistant NSCLC, **China BLA targeted 2026** ([*Antibody Therapeutics*, Oxford Academic](https://doi.org/10.1093/abt/tbag015)). Surfaces first at: **China CDE / chictr.org.cn** and **AACR/ESMO/AACR abstracts** → HKEX/Chinese-exchange disclosure → English trade press *late*. **Not in SEC.** This is the archetype of the China-first blind spot.
- **Ivonescimab (Akeso, PD-1×VEGF).** The setting rival in squamous NSCLC: HARMONi-6 showed an OS benefit (HR 0.66, 27.9 vs 23.7 mo) in first-line squamous NSCLC at **ASCO 2026 Plenary** ([PRNewswire](https://www.prnewswire.com/news-releases/harmoni-6-demonstrates-significant-overall-survival-benefit-hr0-66-ivonescimab-plus-chemotherapy-superior-to-pd-1-plus-chemotherapy-in-first-line-sq-nsclc-landmark-results-to-be-presented-at-asco-2026-plenary-session-302786433.html), [ASCO Post](https://ascopost.com/news/june-2026/overall-survival-benefit-with-ivonescimab-plus-chemotherapy-in-advanced-squamous-nsclc/)). ⚠ China-only trial; regional generalizability is the live analyst dispute (see [bellwethers doc](oncology-bellwethers-2026.md)). Akeso is **HKEX-listed (9926.HK), not US-listed** → SEC-invisible; financing surfaces via HKEX filings and PRs.

### The competitive-set feed table

| Feed | What it emits first | Cadence | Machine-readable? | Auth / cost |
|---|---|---|---|---|
| **ClinicalTrials.gov v2 API** | Registry facts: phase, arms, status, endpoint, enrollment changes | Continuous; poll daily on `lastUpdatePostDate` | **Yes** — JSON/CSV, OpenAPI | None |
| **Company IR (Daiichi, Merck, Akeso, etc.)** | Topline readouts, regulatory actions, deals | Event-driven; day-of | Partial (RSS/HTML; PRs are prose) | None |
| **ASCO abstracts** | Trial results text | Titles **21 Apr 2026**; regular text embargo lifts **21 May 2026 5pm ET**; LBA text day-of presentation ([ASCO embargo policy](https://www.asco.org/annual-meeting/abstracts-presentations/abstract-policies-embargoes-exceptions/embargo-abstract-release)) | Partial (abstract DB, HTML) | None (abstracts free) |
| **AACR abstracts** | Trial/preclinical results text | Regular text **17 Mar 2026 4:30pm ET**; LBA/clinical-trial **titles 17 Mar**, **text 17 Apr** ([AACR embargo policy](https://www.aacr.org/about-the-aacr/newsroom/annual-meeting/embargo-policy/)) | Partial | None |
| **ESMO abstracts** | Trial results text | Regular **titles only ~17 Jul 2026** (text at congress); LBA **titles ~25 Sep 2026** ([ESMO 2026 abstracts](https://www.esmo.org/meeting-calendar/esmo-congress-2026/abstracts)) | Partial | None |
| **Peer journals (JCO, Lancet Oncol, NEJM)** | Full datasets, definitive | Weeks–months after conference | PubMed metadata yes; full text often paywalled | Abstract free; full text mixed |
| **EU registries (CTIS + legacy EU CTR)** | EU trial protocol facts | Continuous | **Partial** — see §3 | None |
| **China: chictr.org.cn + CDE** | China-first trial facts (SDP0505, ivonescimab) | Continuous; low English coverage | Poor / scrape | None but language-gated |

**Embargo rhythm matters to the surge trigger.** The map already has an "automatic conference surge" cadence trigger. The dates above are the schedule that trigger should key off: AACR mid-March, ASCO title-drop 21 Apr / text 21 May / LBAs late-May-into-June, ESMO title-drop mid-July / congress in autumn. A program detective should pre-arm around these, because a competitor's pivotal readout is far more likely to *first* appear as an embargoed abstract than as a press release.

---

## 3. EU registry as a source — usable but degraded

The EU's **Clinical Trials Information System (CTIS)** ([EMA overview](https://www.ema.europa.eu/en/human-regulatory-overview/research-development/clinical-trials-human-medicines/clinical-trials-information-system)) went live with a public search site in 2022 and is now the mandatory EU trial registry. Honest assessment of machine access:

- **No official public API.** EMA exposes an API to member states, not the public.
- There is an **undocumented public search endpoint** (`POST euclinicaltrials.eu/ctis-public-api/search`) that returns trial overviews, used by community tooling; and mature scrapers/packages exist (the R **`ctrdata`** package aggregates CTIS + legacy EU CTR + ClinicalTrials.gov into one queryable store — [ctrdata docs](https://rfhb.github.io/ctrdata/)).
- **Verdict:** EU registry data is *reachable* but is a **scrape/undocumented-endpoint** source, not a clean API like ClinicalTrials.gov. Treat it as a lower-tier registry feed — worth it for EU-only trials, but ClinicalTrials.gov remains the machine-readable spine. `ctrdata` is the pragmatic aggregation path if EU coverage becomes load-bearing; note it's a Phase-2-ish build, not a v1 must.

---

## 4. Patents and publications — a record, not a fast signal (recommend: not a v1 feed)

The stakeholder explicitly asked about "published patents." Honest finding, as the ticket invited: **patents are worth watching manually at low cadence, but not worth building a scan feed for in v1.**

**Free feeds that exist:**

| Feed | Coverage | API? | Cost | Note |
|---|---|---|---|---|
| **PatentsView** (USPTO) | US only | **Yes** — official REST, 45 q/min, no auth ([PatentsView]( https://patentsview.org/)) | Free | Cleanest free API; US-only is a real limit for a global competitive set |
| **Espacenet / EPO OPS** | Global, 130M+ docs | Yes — EPO OPS API, free | Free (registration/key) | Global; heavier to use |
| **Google Patents** | Global, 120M+ | **No official free API** — scraping only | Free UI | Best UI, worst automation story |
| **Lens.org** | Global, 158M+ | UI free; **API paid after 14-day trial** | Freemium | Good but the API is the paid part |

**Why it's not a v1 scan feed:**

1. **Structural latency kills it as a signal.** Patent applications publish **~18 months after filing**. By the time a competitor's patent is a machine-readable record, its program is usually already visible in the registry, in a conference abstract, or in IR. Patents *confirm* what you already know; they rarely *break* it.
2. **It's a record, not an event.** Unlike a `lastUpdatePostDate` bump, a patent grant doesn't map to a stakeholder-relevant program moment. Patent activity is a *background signal* about a platform's direction, best read as a periodic human review, not a scan input.
3. **The free automation path is US-only (PatentsView) or scrape-only (Google Patents)** — a poor cost/benefit for a global oncology set where the interesting competitors (Akeso, Shengdi/Hengrui) file heavily outside the US.

**Recommendation:** log patents in the map's *Not yet specified* as a **manual, low-cadence enrichment** — set a periodic Espacenet/Google Patents family-alert on the program's mechanism (anti-HER3 ADC / bispecific), reviewed by a human at the monthly knob, *not* wired into the automated scan. If it ever graduates, PatentsView is the free-API entry point for the US slice. Publications are already covered by v1's `primary` tier (PubMed/bioRxiv/medRxiv) plus the conference feeds above — no new work needed.

---

## 5. Financing as a signal — free path is real but has a hard hole

The feedback: biotech financing "reflects investor enthusiasm." Where it lives for free:

- **SEC EDGAR full-text search** (`efts.sec.gov`) — JSON, **no auth, open CORS**, filterable by form type (8-K for material events, S-1/424B for raises), by CIK/name, and by **SIC code** (biotech: 2834/2835/2836) ([SEC EDGAR full-text search]( https://efts.sec.gov/LATEST/search-index?q=)). This is a clean, free, machine-readable financing feed — for **US-listed issuers**.
- **Company PRs + trade press** (Endpoints, Fierce, BioPharma Dive) for deal terms, upfronts, milestones — already in v1's tiers.

**The hard hole, stated plainly:** SEC covers US-listed companies only. **The most active competitors in this exact set are not US-listed** — Akeso is HKEX (9926.HK); the SDP0505 sponsor is Chinese. Their financing signal lives in **HKEX / Shanghai-Shenzhen exchange filings and Chinese-language PRs**, which have no equivalent free JSON feed and are language-gated. So the honest map: **US-listed competitor financing is free and machine-readable; Asian-listed competitor financing is systematically under-covered** and will reach the detective late, via English trade press, if at all. This is a named blind spot for the house-level "known blind-spot" section the admission rule allows.

---

## 6. The paywall map — what the free path misses, and whether any gate is load-bearing

| Product | ~Annual cost | What it sells |
|---|---|---|
| Endpoints premium / STAT+ | ~$200–400/seat | Faster, deeper trade journalism |
| Evaluate | Five–six figures | Consensus forecasts, valuation models |
| **Cortellis** (Clarivate) | **Six figures** | Structured drug records: MoA, patent expiry, trial phase, deals |
| **Citeline** (Norstella) | **Six figures** | Trialtrove/Pharmaprojects structured pipeline data |
| GlobalData | **$30K–$80K+** | Cross-market breadth (pharma + medtech + digital health) |

Cost anchors from [Salesmotion's Clarivate-alternatives comparison](https://salesmotion.io/clarivate-alternatives) and [IntuitionLabs' market-intelligence overview](https://intuitionlabs.ai/articles/pharmaceutical-market-intelligence-providers).

**What the free path genuinely misses** (per the same sources): (a) **broker/analyst reports** — how the market is *interpreting* a readout; (b) **earnings-call and expert-call transcripts** — strategic priorities and capital allocation from management's mouth; (c) **pre-structured competitor records** — Cortellis/Citeline sell exactly the "typed competitor with MoA, phase, patent expiry" object as a lookup.

**Verdict for a program detective — no gate is load-bearing enough to buy in v1:**

- The **facts** a program detective needs — trial status, endpoints, arms, regulatory actions, deal terms — are all in the **free primary path** (registry + IR + conference + SEC + journals). Cortellis/Citeline *pre-structure* those facts; they don't hold facts you can't otherwise get. And **structuring the competitor record is what this map's own typed-competitor ticket is building** — buying Cortellis would be paying six figures to outsource the thing the product is.
- What the paywalls uniquely hold is **interpretation** (broker views, transcripts) — which sits on the *far* side of the map's authorship rule anyway: the manager authors interpretation, it does not buy it.
- v1's "accept ~1-week latency via free secondary coverage" assumption **holds for narrative** but is **moot for registry facts** — those are real-time and free, so the latency concern the paywall was meant to address partly evaporates once registry-diff (§1) is a first-class input.

**One honest caveat:** if the detective ever needs *analyst consensus* as an explicit evidence stream (it currently does not — the reader is the program's decision-owner, not an investor), Evaluate/broker reports become the one thing with no free substitute. Park that against the steering-wheel/reader-model decision, not here.

---

## Ranked source set for the scan model

Handoff summary — named feeds, ranked by value to a program detective, with the second-axis (emission) properties the scan-model ticket needs:

1. **ClinicalTrials.gov v2 API** — registry-diff via `lastUpdatePostDate`. Free, JSON, no auth. **New input class**, not just a source. Near-complete for US/global, partial for China-first.
2. **Company IR pages** (Daiichi, Merck, Akeso, +program-specific) — day-of topline/regulatory/deal news. Free, semi-structured.
3. **Conference abstract feeds** (AACR / ASCO / ESMO) — the true *first* surface for pivotal readouts; embargo calendar drives the surge trigger. Free.
4. **SEC EDGAR full-text (efts) API** — US-listed financing/material events. Free, JSON, no auth. **US-only hole** for Asian competitors.
5. **Peer journals via PubMed metadata** — definitive but late; already in v1 `primary`.
6. **EU CTIS / legacy EU CTR (via `ctrdata` or the undocumented endpoint)** — EU trial facts; scrape-grade, not clean API. Phase-2 build.
7. **Patents (Espacenet/PatentsView) — manual low-cadence enrichment only, not a v1 scan feed.** Record, not signal; 18-month latency; US-only free API.

**Named blind spots to carry into the house-level "known blind-spots" section:**
- China-first assets (SDP0505, ivonescimab): registry (chictr/CDE) and financing (HKEX/Chinese exchanges) are language-gated with no clean free feed. This is the single largest coverage gap and it lands squarely on this program's closest competitors.
- Analyst/broker interpretation and earnings-call transcripts: paywalled, no free substitute — but out of scope while the reader is the program's decision-owner, not an investor.

*Do not re-tier what v1 tiered — this extends it along the emission axis. The trust tiers still apply to every source above.*
