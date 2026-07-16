// PROTOTYPE SAMPLE — all content FABRICATED for design purposes. Not real intelligence.
const ISSUE = {
  schema_version: "0.2.0",
  issue: {
    id: "2026-07-16",
    published_at: "2026-07-16T07:42:11+08:00",
    coverage_window: { from: "2026-07-13", to: "2026-07-16" },
    run: { run_id: "run_20260716_0700", status: "published", critic_verdict: "pass_with_advisories", critic_retries: 1,
      models: { researchers: "claude-sonnet-5", manager: "claude-opus-4-8", critic: "gpt-5.6-codex" } }
  },
  headline: {
    title: "Merck's $9B Verastem buy resets ADC pricing — and every mid-cap oncology board knows it",
    summary: "Merck paid a 71% premium for a company whose lead asset is still in Phase 2, signalling that post-Keytruda-LOE desperation now outweighs clinical de-risking. Two other cash-rich acquirers are reportedly circling the same ADC cohort, and bankers are already re-pricing comparable assets upward.",
    so_what: "The buyer's-market assumption in our thesis is now falsifiable: premiums are re-inflating before readouts, not after.",
    confidence: "high"
  },
  stats: { tracked_updates: 7, tracked_quiet: 4, new_on_radar: 3, frontier_items: 3, sources_cited: 34, critic_catches: 2, previous_issue: "2026-07-13" },
  tldr_bullets: [
    { text: "Merck acquires Verastem for $9B at a 71% premium — the biggest ADC deal since 2025, struck pre-Phase-3.", priority: "high" },
    { text: "Hengrui out-licenses its PD-1/VEGF bispecific to Bristol Myers for $1.4B upfront — the China wave is no longer discount-priced.", priority: "high" },
    { text: "FDA's confirmatory-trial rule for accelerated approval takes effect; three tracked sponsors must now enrol before approval.", priority: "high" },
    { text: "Summit's ivonescimab OS data slips to Q4, pushing the PD-1/VEGF class readout past the JPM window.", priority: "medium" },
    { text: "AstraZeneca widens its Daiichi alliance to two additional ADC targets, locking up the partner most rivals wanted.", priority: "medium" },
    { text: "Arcus posts a clean but unremarkable Phase 2 — no inflection, and the cash runway question returns.", priority: "low" }
  ],
  watchlist: [
    { entity_id: "merck", name: "Merck & Co.", type: "big_pharma", status: "developing", priority: "high", confidence: "high",
      categories: ["deal_ma"], thesis_impact: "challenges",
      summary: "Announced acquisition of Verastem Oncology for $9B ($64/share, 71% premium), targeting its Phase 2 KRAS-G12D ADC. Management framed it as 'pipeline urgency ahead of 2028'. Deal expected to close Q1 2027 pending antitrust review.",
      research_angle: "This is the clearest evidence yet that the Keytruda LOE cliff is forcing Merck to buy clinical risk it would have refused in 2024. Our thesis said acquirers would wait for Phase 3 readouts to compress premiums; Merck just paid pre-readout money, which means scarcity of late-stage ADC assets is now the binding constraint, not capital discipline. If the next two deals price the same way, the 'patient acquirer' model is dead and target selection should be re-modelled around scarcity.",
      sources: [ { url: "https://example.com/merck-pr", publisher: "Merck investor relations", tier: "primary", published_at: "2026-07-15" },
                 { url: "https://example.com/endpoints-merck", publisher: "Endpoints News", tier: "trade", published_at: "2026-07-15" },
                 { url: "https://example.com/reuters-merck", publisher: "Reuters", tier: "trade", published_at: "2026-07-15" } ] },
    { entity_id: "hengrui", name: "Jiangsu Hengrui Pharma", type: "china_pharma", status: "concluded", priority: "high", confidence: "medium",
      categories: ["deal_ma", "platform_tech"], thesis_impact: "confirms",
      summary: "Out-licensed its PD-1/VEGF bispecific to Bristol Myers Squibb: $1.4B upfront, $6.1B in milestones, ex-China rights retained by Hengrui.",
      research_angle: "The China licensing wave is no longer discount-priced. A $1.4B upfront from a Western acquirer for a Chinese-originated bispecific is a re-rating of the entire cohort — and it validates the wedge argument: Chinese biotechs are converting speed-to-clinic into Western capital without surrendering their home market. The interesting question is no longer whether Western pharma will buy Chinese assets, but whether it can still buy them cheaply. It cannot.",
      sources: [ { url: "https://example.com/bms-pr", publisher: "BMS press release", tier: "primary", published_at: "2026-07-14" },
                 { url: "https://example.com/fierce-hengrui", publisher: "Fierce Biotech", tier: "trade", published_at: "2026-07-14" } ] },
    { entity_id: "astrazeneca", name: "AstraZeneca", type: "big_pharma", status: "developing", priority: "high", confidence: "high",
      categories: ["platform_tech", "deal_ma"], thesis_impact: "confirms",
      summary: "Expanded its Daiichi Sankyo alliance to cover two additional ADC targets, with $1.2B upfront and co-commercialization rights in the US and EU.",
      research_angle: "AstraZeneca is not buying assets, it is buying the partner — and that is the smarter version of the same scarcity trade Merck just made loudly. Locking up Daiichi's linker chemistry across more targets denies rivals the one platform with a proven clinical track record, without paying a control premium. Our thesis treats platform access as the real moat; this is the cleanest expression of it this quarter.",
      sources: [ { url: "https://example.com/az-pr", publisher: "AstraZeneca press release", tier: "primary", published_at: "2026-07-14" },
                 { url: "https://example.com/stat-az", publisher: "STAT", tier: "trade", published_at: "2026-07-15" } ] },
    { entity_id: "summit", name: "Summit Therapeutics", type: "biotech", status: "developing", priority: "high", confidence: "medium",
      categories: ["trial_readout"], thesis_impact: "neutral",
      summary: "Guided that ivonescimab overall-survival data in NSCLC will now read out in Q4 2026 rather than Q3, citing event accrual. Shares fell 14% on the update.",
      research_angle: "A slip is not a signal, but the timing is: the readout now lands after the JPM window, which removes the single most likely catalyst for a 2026 take-out. For a company whose entire equity story is one PD-1/VEGF asset, delay is dilution risk by another name. We treat this as neutral to thesis — the class thesis is intact, the company-specific timing is worse.",
      sources: [ { url: "https://example.com/summit-8k", publisher: "SEC 8-K", tier: "primary", published_at: "2026-07-15" },
                 { url: "https://example.com/biopharmadive-summit", publisher: "BioPharma Dive", tier: "trade", published_at: "2026-07-15" } ] },
    { entity_id: "akeso", name: "Akeso Biopharma", type: "china_pharma", status: "concluded", priority: "medium", confidence: "medium",
      categories: ["trial_readout"], thesis_impact: "confirms",
      summary: "Published updated HARMONi-2 subgroup data in a peer-reviewed journal, reinforcing the PD-1/VEGF class hypothesis in PD-L1-high patients.",
      research_angle: "Akeso keeps doing the thing that makes the China wedge argument work: generating credible data faster and cheaper than the Western comparator, then letting a partner monetize it. The data itself is incremental; the fact that it publishes on schedule while Summit slips is the actual competitive information.",
      sources: [ { url: "https://example.com/akeso-journal", publisher: "Journal of Clinical Oncology", tier: "primary", published_at: "2026-07-13" } ] },
    { entity_id: "pfizer", name: "Pfizer", type: "big_pharma", status: "concluded", priority: "medium", confidence: "medium",
      categories: ["pipeline"], thesis_impact: "neutral",
      summary: "Deprioritized two early oncology programs in its quarterly pipeline update, redirecting spend toward obesity and its Seagen-derived ADC portfolio.",
      research_angle: "Pfizer is doing the opposite of Merck: narrowing rather than buying. That is defensible given Seagen already bought it an ADC franchise, but it also means Pfizer is unlikely to be the second bidder that would validate the premium re-rating. One fewer acquirer at the table is a real datapoint for anyone modelling target competition.",
      sources: [ { url: "https://example.com/pfizer-pipeline", publisher: "Pfizer pipeline update", tier: "primary", published_at: "2026-07-14" } ] },
    { entity_id: "arcus", name: "Arcus Biosciences", type: "biotech", status: "concluded", priority: "low", confidence: "medium",
      categories: ["trial_readout"], thesis_impact: "neutral",
      summary: "Reported Phase 2 data for its adenosine-axis combination: clean safety, response rates in line with prior guidance, no inflection.",
      research_angle: "'In line' is the worst possible outcome for a company that needs a reason to exist independently. Nothing here changes the thesis, but it moves Arcus from a possible target to a probable financing story, and we should expect the cash-runway conversation to dominate its next two cycles.",
      sources: [ { url: "https://example.com/arcus-pr", publisher: "Arcus press release", tier: "primary", published_at: "2026-07-13" } ] }
  ],
  quiet_this_cycle: {
    no_news: [ { entity_id: "roche", name: "Roche", cycles_quiet: 2 }, { entity_id: "gilead", name: "Gilead", cycles_quiet: 3 },
               { entity_id: "daiichi", name: "Daiichi Sankyo", cycles_quiet: 1 }, { entity_id: "legend", name: "Legend Biotech", cycles_quiet: 2 } ],
    critic_catches: [
      { claim: "Zentalis raising $400M at a $2.1B valuation", rejected_because: "provenance_stale",
        detail: "Every July 14–15 aggregator repeat traces to a single 12 Mar 2026 Bloomberg piece. No new primary source; the round may have closed months ago or died quietly. Publishing it as this cycle's news would have been wrong by four months.",
        sources: [ { url: "https://example.com/bloomberg-mar", publisher: "Bloomberg", tier: "trade", published_at: "2026-03-12" } ] },
      { claim: "Novartis in advanced talks to acquire Arcus Biosciences", rejected_because: "single_unverified_source",
        detail: "Traced to one paywalled subscription newsletter with no named sourcing, no confirmation from either company, and no unusual options activity. Below the bar for an investor-grade digest.",
        sources: [ { url: "https://example.com/newsletter", publisher: "Unnamed newsletter", tier: "aggregator", published_at: "2026-07-15" } ] }
    ],
    open_threads: [
      { thread: "Antitrust review timeline for the Merck/Verastem deal", since: "2026-07-15", next_expected: "FTC second-request window closes ~Aug 2026" },
      { thread: "Whether a second bidder emerges for the ADC cohort", since: "2026-07-15", next_expected: "Watch for 13D/G filings through August" }
    ]
  },
  new_on_radar: [
    { entity_id: "callio_tx", name: "Callio Therapeutics", type: "startup", priority: "medium", categories: ["funding"],
      what_they_do: "Dual-payload ADC platform out of Basel; lead program in HER2-low breast cancer.",
      development: "Raised $187M Series B at roughly $900M post-money, led by Forbion.",
      why_we_care: "Dual-payload is the direct answer to ADC resistance, which our thesis names as the field's next bottleneck. A $900M post-money before the clinic is exactly the pre-readout premium inflation the Merck deal just rationalized — the private market is pricing the same scarcity the public one is.",
      promotion_proposal: { promote_to_watchlist: true, reason: "Second dual-payload financing above $150M this quarter — the sub-sector now clears our tracking bar." },
      sources: [ { url: "https://example.com/callio", publisher: "Fierce Biotech", tier: "trade", published_at: "2026-07-15" } ] },
    { entity_id: "aktis", name: "Aktis Oncology", type: "startup", priority: "medium", categories: ["funding", "platform_tech"],
      what_they_do: "Alpha-emitter radiopharmaceuticals using miniprotein targeting; lead asset in solid tumors.",
      development: "Extended its Series C by $60M with participation from two strategic pharma investors.",
      why_we_care: "Strategic money in a radiopharma extension round is a soft option on an acquisition. Radiopharma is the modality our thesis flags as most likely to be over-bought relative to evidence — this is the first datapoint of the cycle that tests it.",
      promotion_proposal: { promote_to_watchlist: false, reason: "Single financing event; watch one more cycle before promoting." },
      sources: [ { url: "https://example.com/aktis", publisher: "Endpoints News", tier: "trade", published_at: "2026-07-14" } ] },
    { entity_id: "candid", name: "Candid Therapeutics", type: "startup", priority: "high", categories: ["deal_ma"],
      what_they_do: "T-cell engager platform assembled by in-licensing Chinese-originated assets.",
      development: "In-licensed two additional bispecifics from a Chengdu-based biotech for undisclosed upfronts.",
      why_we_care: "This is the China wedge industrialized: a US company whose entire strategy is arbitraging the price gap our Hengrui angle says is closing. If Candid is still able to in-license cheaply while BMS pays $1.4B, then the re-rating is uneven — and that gap is the most actionable thing in this issue.",
      promotion_proposal: { promote_to_watchlist: true, reason: "Directly tests the China-licensing thesis from the arbitrage side; high analytic value." },
      sources: [ { url: "https://example.com/candid", publisher: "Endpoints News", tier: "trade", published_at: "2026-07-16" } ] }
  ],
  themes_and_signals: [
    { theme: "Pre-readout premiums are back", thesis_impact: "challenges", evidence_refs: ["merck", "callio_tx"],
      argument: "Two datapoints this cycle price clinical risk higher than 2025 comparables — one public, one private. If this holds for one more cycle, the 'patient acquirer' assumption in our thesis is dead and we should re-model target selection around asset scarcity rather than de-risking." },
    { theme: "China is licensing up the value chain", thesis_impact: "confirms", evidence_refs: ["hengrui", "akeso", "candid"],
      argument: "Upfronts have moved from roughly $100–300M to over $1B, and the assets are bispecifics rather than me-too small molecules. The wedge is holding — but Candid's continued cheap in-licensing suggests the re-rating is uneven, and the arbitrage window has not closed everywhere at once." },
    { theme: "Platform access is beating asset ownership", thesis_impact: "confirms", evidence_refs: ["astrazeneca", "pfizer"],
      argument: "AstraZeneca widened a partnership while Pfizer narrowed a pipeline; neither bought a company. The quiet story under Merck's loud one is that the disciplined players are buying access to chemistry, not control of it." }
  ],
  elsewhere_on_frontier: [
    { actor: "FDA", move: "Final rule on accelerated-approval confirmatory trials takes effect; sponsors must have trials underway at approval.",
      detail: "Three tracked oncology sponsors have pending AA applications that now require enrolled confirmatory trials, compressing timelines by an estimated 6–9 months and raising the capital needed to reach the same milestone.",
      why_it_matters: "Raises the capital intensity of the AA shortcut — historically the main reason small oncology biotechs could stay independent. Expect more early sales, which feeds directly into the scarcity story driving premiums.",
      sources: [ { url: "https://example.com/fda-rule", publisher: "FDA", tier: "primary", published_at: "2026-07-14" } ] },
    { actor: "CMS", move: "Second-cycle IRA negotiation list adds two oncology small molecules.",
      detail: "Both are past their peak-sales years, so the direct revenue impact is modest, but the signal is that oncology is no longer carved out of pricing pressure by default.",
      why_it_matters: "The small-molecule penalty relative to biologics is now a portfolio-design input, not a policy talking point. It quietly favours ADCs and bispecifics — the same assets whose premiums are inflating.",
      sources: [ { url: "https://example.com/cms", publisher: "CMS", tier: "primary", published_at: "2026-07-13" } ] },
    { actor: "Isomorphic Labs", move: "Posted first oncology target-validation data from its internal pipeline.",
      detail: "Structural predictions for two undisclosed oncology targets, with wet-lab confirmation reported by a partner. No clinical claims made.",
      why_it_matters: "AI-native discovery entering oncology target selection is the slowest-moving but highest-variance item on this list. Nothing to trade on this cycle; everything to watch over eight.",
      sources: [ { url: "https://example.com/isomorphic", publisher: "Isomorphic Labs", tier: "primary", published_at: "2026-07-16" } ] }
  ],
  thesis_updates: [
    { change: "amended", field: "acquirer_behaviour", triggered_by: ["merck", "callio_tx"],
      before: "Cash-rich acquirers will wait for Phase 3 readouts, compressing target premiums through 2026.",
      after: "Acquirers are paying pre-readout premiums for scarce ADC and bispecific assets; capital discipline is subordinate to pipeline urgency ahead of 2028 LOEs." },
    { change: "sharpened", field: "china_licensing_wave", triggered_by: ["hengrui", "candid"],
      before: "Chinese-originated assets are systematically underpriced relative to Western equivalents.",
      after: "The discount is closing at the top of the market (>$1B upfronts) but persists for smaller in-licensing deals — the re-rating is uneven, and the gap is the tradeable part." }
  ],
  critic_report: { verdict: "pass_with_advisories", retries_used: 1, blocking_findings: [],
    advisory_findings: [
      { kind: "thin_sourcing", where: "watchlist.hengrui", note: "Single primary source; no independent trade confirmation of the milestone structure." },
      { kind: "coverage_gap", where: "elsewhere_on_frontier", note: "EU HTA regulation developments not covered this cycle despite being in scope." },
      { kind: "unhedged_claim", where: "themes.pre_readout_premiums", note: "Two datapoints is thin for a class-wide claim; the argument acknowledges this but the headline does not." }
    ] },
  sources_and_method: {
    beats_run: ["pharma_ma", "oncology_startups", "clinical_science", "policy_regulation", "incumbents_entrants", "backstop"],
    beats_failed: [],
    source_tier_counts: { primary: 14, trade: 17, aggregator: 3 },
    paywalled_flagged: [ { claim: "Verastem board rejected an earlier $7.2B approach", publisher: "STAT+", url: "https://example.com/stat-plus", note: "Primary paywalled — assess manually." } ]
  }
};
