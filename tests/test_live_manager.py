"""The one manager test that calls a real model. Opt-in, and doubly gated.

    pytest tests/test_live_manager.py --live

It needs two things the rest of the suite fakes: --live (it costs real money and
takes minutes), AND a real findings corpus on disk — a runs/<run_id>/findings/
directory left by an actual research fan-out. It reads the NEWEST such run,
renders the real manager prompt against it, calls the manager once, and asserts
the acceptance criteria that only a real model can exercise: the ones about what
the manager actually authored, not what the template told it to.

If no findings exist on disk it SKIPS with a clear reason rather than failing —
you cannot synthesise from a corpus that was never gathered.
"""

import json

import pytest

from researchswarm.beats import load_beats
from researchswarm.manager import load_models, run_manager
from researchswarm.prompts import RunContext, load_template, render_manager_prompt
from researchswarm.runs import resolve_prior_quiet
from researchswarm.state import load_state

PRIORITIES = {"high", "medium", "low"}
DORMANT_MARKER = "No thesis seeded — facts only"


@pytest.fixture(autouse=True)
def allow_live(request, monkeypatch):
    if not request.config.getoption("--live", default=False):
        pytest.skip("live model test; pass --live to run (costs real money, takes minutes)")
    monkeypatch.delenv("RESEARCHSWARM_OFFLINE", raising=False)


@pytest.fixture
def newest_findings(repo_root):
    """The newest runs/<run_id>/findings/ with at least one findings file, or a
    skip. The manager cannot synthesise from a corpus that was never gathered."""
    runs = repo_root / "runs"
    candidates = sorted(
        (d for d in runs.glob("*/findings") if any(d.glob("*.json"))),
        reverse=True,
    )
    if not candidates:
        pytest.skip("no runs/<run_id>/findings/ on disk — run a research fan-out first")
    findings_dir = candidates[0]
    run_id = findings_dir.parent.name
    by_beat = {p.stem: json.loads(p.read_text()) for p in sorted(findings_dir.glob("*.json"))}
    return run_id, by_beat


@pytest.mark.live
def test_a_real_manager_returns_a_valid_v1_0_0_issue(repo_root, newest_findings):
    run_id, findings_by_beat = newest_findings
    state = load_state(repo_root / "state")
    beats = load_beats(repo_root / "config" / "beats.toml")
    template = load_template(repo_root / "prompts" / "manager.md")
    models_config = load_models(repo_root / "config" / "models.toml")

    # Any roster beat with no findings file this run counts as failed.
    beats_failed = [b.id for b in beats if b.id not in findings_by_beat]
    # Recover the window from a finding if present; fall back to the seed dates.
    window = next(
        (f.get("coverage_window") for f in findings_by_beat.values() if f.get("coverage_window")),
        {"from": "2026-07-13", "to": "2026-07-16"},
    )
    ctx = RunContext(run_id=run_id, coverage_window_from=window["from"],
                     coverage_window_to=window["to"])
    thesis_version = state.thesis.get("version")

    prompt = render_manager_prompt(
        template, ctx, state,
        findings_by_beat=findings_by_beat,
        beats_failed=beats_failed,
        prior_quiet=resolve_prior_quiet(repo_root / "issues"),
        models={"researchers": beats[0].model, "manager": models_config["manager"], "critic": None},
        issue_id="2026-07-16",
        published_at="2026-07-16T07:00:00+08:00",
    )

    result = run_manager(prompt, model=models_config["manager"],
                         thesis_version=thesis_version, run_id=run_id, timeout=1200)
    issue = result.draft

    # --- schema shape (also checked at the seam; re-asserted so failures name this test) ---
    for key in (
        "schema_version", "issue", "headline", "stats", "tldr_bullets", "catalyst_queue",
        "watchlist", "quiet_this_cycle", "new_on_radar", "themes_and_signals",
        "elsewhere_on_frontier", "thesis_updates", "critic_report", "sources_and_method",
    ):
        assert key in issue, f"missing top-level key {key}"
    assert issue["schema_version"] == "1.0.0"
    assert issue["stats"] == {}, "stats must be derived, never authored"
    assert issue["headline"]["so_what"], "so_what is always present and thesis-independent"

    # --- entity accounting: every tracked entity in EXACTLY ONE of watchlist / no_news ---
    tracked = state.entity_ids
    in_watchlist = {e["entity_id"] for e in issue["watchlist"]}
    in_quiet = {e["entity_id"] for e in issue["quiet_this_cycle"]["no_news"]}
    assert not (in_watchlist & in_quiet), "an entity appears in both watchlist and quiet"
    assert tracked <= (in_watchlist | in_quiet), f"unaccounted: {tracked - in_watchlist - in_quiet}"

    # --- priority is the 3-value tag, everywhere it appears ---
    for entry in issue["watchlist"] + issue["new_on_radar"] + issue["tldr_bullets"]:
        assert entry.get("priority") in PRIORITIES, entry

    # --- confidence ONLY on headline and watchlist entries ---
    assert issue["headline"].get("confidence") in PRIORITIES
    for entry in issue["watchlist"]:
        assert entry.get("confidence") in PRIORITIES
    for entry in issue["new_on_radar"]:
        assert "confidence" not in entry, "confidence does not belong on radar items"
    for entry in issue["themes_and_signals"]:
        assert "confidence" not in entry, "confidence does not belong on themes"
    for entry in issue["elsewhere_on_frontier"]:
        assert "confidence" not in entry, "confidence does not belong on frontier moves"

    # --- dormant slots: watchlist items argued against a dormant slot carry the
    #     marker and OMIT thesis_impact (there is no stance to bear on) ---
    dormant_slots = {b["id"] for b in state.thesis["beliefs"] if not b["stance"]}
    if dormant_slots:
        for entry in issue["watchlist"]:
            if entry.get("research_angle") == DORMANT_MARKER:
                assert "thesis_impact" not in entry, "dormant angle must omit thesis_impact"

    # --- failed beats leave an inline degradation marker somewhere ---
    if beats_failed:
        blob = json.dumps(issue)
        assert "beat_failed" in blob or "beat failed" in blob, "no inline degradation for a dead beat"

    # --- no unconfirmed finding published as established fact ---
    unconfirmed_entities = {
        eid
        for payload in findings_by_beat.values()
        for f in payload.get("findings", [])
        if f.get("unconfirmed")
        for eid in f.get("entity_ids", [])
    }
    for entry in issue["watchlist"]:
        if entry["entity_id"] in unconfirmed_entities:
            summary = entry.get("summary", "").lower()
            assert "unconfirmed" in summary or "rumour" in summary or entry.get("degradation"), (
                f"{entry['entity_id']}: an unconfirmed finding rendered without a visible flag"
            )
