"""Stage 2 of the two gates — the Codex critic, and the mechanical receipt rule.

The cross-family adversarial gate: Codex judges the issue that Claude wrote. A
same-family critic shares the workers' blind spots; a different family does not,
which is the whole reason this gate is Codex and not one more Claude call.

The critic has **no web access, deliberately**. It cannot catch what all six
researchers missed — only what the pipeline **found and then lost**. Web access
would double the run, burn subscription quota on searching rather than judging,
and open a prompt-injection surface on an unattended run. Enforced by the
`--sandbox read-only` flag (denies writes AND network egress for model-run
commands), not by asking nicely — a named gap, not an oversight. Auth is the
operator's ChatGPT subscription; no API key is read or handled here.

Two responsibilities live here, and they are deliberately different in kind:

  - `run_critic` talks to `codex exec --json` over a PORTABLE argv (no shell, no
    bash-isms — it runs natively on Windows) with the prompt on stdin, and turns
    whatever comes back into a `CriticResult`. Every way the critic can be broken
    — missing binary, nonzero exit, timeout, empty/unreadable output, non-JSON,
    an invalid verdict, a malformed findings structure — resolves to
    `verdict="not_run"` with a specific reason. A broken critic must NEVER
    silently become a passing one. `not_run` is a legitimate outcome the
    orchestrator publishes under (`published_uncritiqued` + banner), not a
    failure. The ONLY thing that raises is misusing the offline guard.

  - `enforce_receipt_rule` is the orchestrator's mechanical half of the
    `dropped_story` block. Materiality — does the missed story matter — stays the
    critic's judgment; actionability — is the finding well-formed enough to act
    on — stays mechanical here. A `dropped_story` without a well-formed receipt is
    auto-downgraded to advisory, consuming no retry.

  - `extract_rebuttals` and the adjudication join (`match_survivor_key`,
    `attach_adjudication`, `rebuttal_record`) are the mechanical half of the
    rebuttal channel (spec/06). The MANAGER's judgment is whether to rebut; the
    CRITIC's judgment is whether to re-file the rebutted finding. The orchestrator
    only joins the two: a rebuttal whose finding the critic re-files is
    `reaffirmed`, one it drops is `withdrawn`. The join is (kind, where) first, and
    — because a critic may re-file the same fault with a reworded `where` — the
    sole surviving finding of the same kind second. It never judges whether a
    rebuttal is right, only which finding it belongs to, and it NEVER silently
    deletes one: an unmatched rebuttal publishes as its own record.

The RETRY LOOP itself — the manager calls, the budget, the exhaustion outcome —
lives in critique.py (the stage), exactly as the validator's loop lives in
validation.py. This module stays the pure, mechanical, IO-free half.

Spec: docs/spec/06-validator-and-critic.md (stage 2), docs/spec/07-issue-schema.md
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from researchswarm.researcher import OFFLINE_ENV

log = logging.getLogger("researchswarm.critic")

# The verdict and run-status vocabulary lives here, ONE home, so the stage and
# the publisher compare against named constants rather than re-typing literals
# (an "unpublished_uncritiqued" typo in one copy would silently mis-route a run).
# The critic emits exactly one of PASS / PASS_WITH_ADVISORIES / BLOCKED; NOT_RUN
# is the orchestrator's, never the critic's — a critic emitting it would be
# claiming its own unavailability, a contradiction. An emitted verdict outside the
# first three is malformed and resolves to NOT_RUN (below).
PASS = "pass"
PASS_WITH_ADVISORIES = "pass_with_advisories"
BLOCKED = "blocked"
NOT_RUN = "not_run"
CRITIC_VERDICTS = (PASS, PASS_WITH_ADVISORIES, BLOCKED)

# The run.status each critic outcome publishes under (spec/06 run-status table).
# They live beside the verdict vocabulary because the map from a verdict to the
# status it publishes under is the one thing the stage and the publisher must
# agree on exactly.
PUBLISHED = "published"
PUBLISHED_UNCRITIQUED = "published_uncritiqued"
PUBLISHED_WITH_UNRESOLVED = "published_with_unresolved_findings"

# The two adjudications the critic can pass on a manager's rebuttal (spec/06 the
# rebuttal channel). One home, because the manager reads them (retry 2 complies
# with every reaffirmed finding) and the publisher prints them. Set by the CRITIC,
# never the manager: the orchestrator reads the critic's re-judgment — a rebutted
# finding the critic RE-FILES is reaffirmed, one it drops is withdrawn — so the
# manager cannot author its own acquittal.
WITHDRAWN = "withdrawn"
REAFFIRMED = "reaffirmed"

# The one blocking kind the orchestrator checks mechanically (the receipt rule).
DROPPED_STORY = "dropped_story"

# The six blocking kinds (spec/06). A blocking finding whose kind is not one of
# these is an INVENTED kind — it is demoted to advisory rather than allowed to
# gate the run. "Don't let an invented kind block" is the rule; the reader still
# sees it, it just cannot halt the line.
BLOCKING_KINDS = frozenset(
    {
        "provenance_stale",
        "overclaim",
        "aggregator_only",
        "unconfirmed_as_fact",
        DROPPED_STORY,
        "thesis_impact_false",
    }
)

# The advisory kinds (spec/06). Recorded here for completeness and so a reviewer
# reading this file sees the whole rubric in one place; unknown advisory kinds
# are tolerated rather than rejected, because an advisory never gates and a
# mislabelled one costs the reader nothing.
ADVISORY_KINDS = frozenset(
    {
        "thin_sourcing",
        "coverage_gap",
        "weak_angle",
        "thesis_unseeded",
        "paywalled_primary",
        "unverifiable_claim",
        "stale_open_thread",
        "source_unreachable",
        "calendar_stale",
        "thread_dropped",
        "continuity_break",
        "continuity_baseline_expired",
    }
)

# A receipt's tier must be one of these — an aggregator alone is not enough to
# prove a story was really found and really dropped (spec/06 receipt rule).
RECEIPT_TIERS = frozenset({"primary", "trade"})

# The four fields every source object carries (spec/07). A receipt missing any of
# them is not well-formed and cannot block.
RECEIPT_FIELDS = ("url", "publisher", "tier", "published_at")


class CriticOfflineViolation(RuntimeError):
    """The offline guard was misused: RESEARCHSWARM_OFFLINE is set, yet the real
    subprocess runner reached `run_critic`. This is a TEST/wiring bug, not a
    critic outcome — it raises loudly (like the researcher and manager guards) so
    a test that would spend real Codex quota fails in milliseconds instead. It is
    the ONLY thing this module raises; every genuine critic failure is not_run."""


@dataclass(frozen=True)
class CriticResult:
    """One critic pass, already parsed and kind-sorted — but BEFORE the receipt
    rule (which needs the findings corpus and the issue, held by the stage).

    `verdict` is the critic's own judgment (pass | pass_with_advisories |
    blocked) or `not_run` when the critic was unreachable/unparseable. `reason` is
    set only on not_run, naming exactly what broke. `blocking_findings` holds only
    findings with a real blocking kind; an invented kind has already been demoted
    into `advisory_findings`."""

    verdict: str
    blocking_findings: tuple[dict, ...] = ()
    advisory_findings: tuple[dict, ...] = ()
    reason: str | None = None
    usage: dict | None = None
    thread_id: str | None = None


class _CriticOutputInvalid(ValueError):
    """Internal: the critic's JSON parsed but did not meet the verdict contract.
    Never escapes the module — `run_critic` folds it into a not_run result with
    the message as the reason."""


def build_codex_command(
    model: str, *, last_message_file: Path, schema_file: Path | None = None
) -> list[str]:
    """The portable argv for one read-only Codex pass — no shell, no bash-isms.

    The prompt is NOT in the argv: it rides on stdin (the command ends in `-`),
    because the critic prompt inlines five documents and can exceed the OS argv
    limit. `--sandbox read-only` is the no-web wall (it denies writes and network
    egress for model-run commands); `--ephemeral` avoids leaving session files;
    `--skip-git-repo-check` lets the critic run outside a repo; `-o` names the
    file Codex writes its bare final message to; `--output-schema` pins the
    verdict shape at the model boundary. A plain argv list + stdin prompt is what
    "runs natively on Windows" means — no `bash -c`, no here-strings.
    """
    command = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "-m",
        model,
        "-o",
        str(last_message_file),
    ]
    if schema_file is not None:
        command += ["--output-schema", str(schema_file)]
    command.append("-")  # prompt arrives on stdin
    return command


def run_critic(
    prompt: str,
    *,
    model: str,
    timeout: int = 900,
    schema_file: Path | None = None,
    runner=subprocess.run,
) -> CriticResult:
    """Run one critic pass. Never raises on a critic failure — resolves to not_run.

    The prompt goes in on stdin; the final verdict comes back from the `-o`
    tempfile (the bare final message, no envelope); stdout JSONL is parsed only
    for usage/thread metadata, tolerating unknown event types. Every failure mode
    — missing binary, nonzero exit, timeout, empty/unreadable file, non-JSON, bad
    verdict, malformed findings — becomes `CriticResult(verdict="not_run", ...)`
    with a specific reason. The one exception is offline-guard misuse, which
    raises: a test must never reach the real binary.
    """
    if os.environ.get(OFFLINE_ENV) and runner is subprocess.run:
        raise CriticOfflineViolation(
            f"{OFFLINE_ENV} is set but the critic tried to call the real codex "
            "binary. Inject a fake runner, or run with the critic disabled."
        )

    with tempfile.TemporaryDirectory(prefix="researchswarm-critic-") as tmp:
        last_message_file = Path(tmp) / "last-message.txt"
        command = build_codex_command(
            model, last_message_file=last_message_file, schema_file=schema_file
        )

        try:
            completed = runner(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return _not_run("codex binary not found on PATH")
        except subprocess.TimeoutExpired:
            return _not_run(f"critic timed out after {timeout}s")

        if completed.returncode != 0:
            return _not_run(
                f"codex exited {completed.returncode}: "
                f"stderr={(completed.stderr or '')[:400]!r}"
            )

        try:
            final_message = last_message_file.read_text()
        except OSError:
            return _not_run("critic wrote no final-message file")

        if not final_message.strip():
            return _not_run("critic final-message file was empty")

        try:
            payload = json.loads(final_message)
        except json.JSONDecodeError as exc:
            return _not_run(f"unparseable critic output: {exc}")

        usage, thread_id = _parse_stdout_meta(completed.stdout)

        try:
            verdict, blocking, advisory = _sort_output(payload)
        except _CriticOutputInvalid as exc:
            return _not_run(str(exc))

        return CriticResult(
            verdict=verdict,
            blocking_findings=blocking,
            advisory_findings=advisory,
            usage=usage,
            thread_id=thread_id,
        )


def _not_run(reason: str) -> CriticResult:
    """A not_run result with its reason recorded and logged.

    Recorded loudly on purpose: a missing critic is banner-visible, and the
    operator log is where the reason a run went uncritiqued has to be legible."""
    log.warning("critic did not run: %s", reason)
    return CriticResult(verdict=NOT_RUN, reason=reason)


def _sort_output(payload) -> tuple[str, tuple[dict, ...], tuple[dict, ...]]:
    """Validate the verdict contract and sort findings by kind, or raise.

    Issue-level malformed structure (not an object, bad verdict, findings that
    are not arrays of objects) raises `_CriticOutputInvalid` — the whole output
    is untrustworthy, so it becomes not_run. A single blocking finding with an
    INVENTED kind is NOT fatal: it is demoted into the advisory list with a note,
    so an invented kind can never gate the run.
    """
    if not isinstance(payload, dict):
        raise _CriticOutputInvalid("critic output was not a JSON object")

    verdict = payload.get("verdict")
    if verdict not in CRITIC_VERDICTS:
        raise _CriticOutputInvalid(f"invalid verdict {verdict!r}")

    raw_blocking = payload.get("blocking_findings", [])
    raw_advisory = payload.get("advisory_findings", [])
    if not isinstance(raw_blocking, list) or not isinstance(raw_advisory, list):
        raise _CriticOutputInvalid(
            "malformed critic output: blocking_findings and advisory_findings must be arrays"
        )
    for finding in [*raw_blocking, *raw_advisory]:
        if not isinstance(finding, dict):
            raise _CriticOutputInvalid("malformed critic output: a finding was not an object")

    blocking: list[dict] = []
    advisory: list[dict] = list(raw_advisory)
    for finding in raw_blocking:
        if finding.get("kind") in BLOCKING_KINDS:
            blocking.append(finding)
        else:
            # An invented (or advisory-only) kind reported as blocking: demote it
            # rather than let it gate, and say so in the note so the downgrade is
            # visible in the published report.
            advisory.append(_annotate(finding, f"reported as blocking under unknown kind {finding.get('kind')!r}"))

    return verdict, tuple(blocking), tuple(advisory)


def _annotate(finding: dict, note: str) -> dict:
    """A copy of `finding` with `note` appended — the audit crumb that explains a
    demotion or downgrade in the published report, never mutating the original."""
    existing = finding.get("note", "")
    joined = f"{existing} [{note}]" if existing else f"[{note}]"
    return {**finding, "note": joined}


def _parse_stdout_meta(stdout: str) -> tuple[dict | None, str | None]:
    """Pull usage and thread_id out of the JSONL event stream, tolerating anything.

    stdout is a stream of `{"type": ...}` events. We want only two of them —
    `thread.started` (its thread_id) and `turn.completed` (its usage) — and every
    other event type, and any non-JSON line, is ignored. The critic's VERDICT does
    not come from here; this is telemetry, so a parsing hiccup must never turn a
    good verdict into not_run.
    """
    usage: dict | None = None
    thread_id: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "turn.completed":
            usage = event.get("usage")
    return usage, thread_id


def enforce_receipt_rule(
    blocking_findings, *, findings_corpus: str, issue: dict
) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """The orchestrator's mechanical half of the `dropped_story` block.

    Every `dropped_story` blocking finding must carry a WELL-FORMED receipt:

      - a `source` object with all four fields (url, publisher, tier, published_at);
      - `url` appearing in the raw findings corpus — a researcher actually found
        it (string containment across the corpus JSON is accepted and honest: a
        url is a rare, distinctive token, and a substring match is exactly the
        "this url was in some finding" question the receipt rule asks);
      - `tier` ∈ {primary, trade} — an aggregator alone is not enough;
      - `published_at` inside `issue.coverage_window` — not recycled old news;
      - and `url` cited NOWHERE in the issue — the manager really did drop it.

    The corpus side uses substring containment (a url in *some* finding is the
    question). The issue side does NOT: it collects the url value of every source
    object in the issue and matches EXACTLY, because a receipt url that is a strict
    prefix of a genuinely-cited url ("…/merck" under "…/merck-verastem") would read
    as "already cited" under substring and wrongly downgrade a real drop.

    A finding that fails ANY of these is auto-downgraded into the advisory list
    with a note naming what failed, consuming no retry. Non-`dropped_story`
    blocking kinds pass through untouched — the orchestrator never judges THEIR
    materiality, only a dropped_story's well-formedness. Returns (kept_blocking,
    downgraded_to_advisory).
    """
    window = issue.get("issue", {}).get("coverage_window", {}) or {}
    cited_urls = _collect_source_urls(issue)

    kept: list[dict] = []
    downgraded: list[dict] = []
    for finding in blocking_findings:
        if finding.get("kind") != DROPPED_STORY:
            kept.append(finding)  # a different blocking kind — not ours to judge
            continue
        failure = _receipt_failure(finding, findings_corpus, cited_urls, window)
        if failure is None:
            kept.append(finding)
        else:
            downgraded.append(_annotate(finding, f"receipt downgrade: {failure}"))

    return tuple(kept), tuple(downgraded)


def _collect_source_urls(node) -> set[str]:
    """Every url cited anywhere in the issue, as an exact-match set.

    Walks the whole issue tree collecting the `url` of any object that carries one
    — every source object, and the paywalled_flagged entries, which are citations
    too. Exact membership — not substring — is what keeps a receipt url that is a
    prefix of a genuinely-cited url from falsely counting as already cited.
    """
    urls: set[str] = set()
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str):
            urls.add(url)
        for value in node.values():
            urls |= _collect_source_urls(value)
    elif isinstance(node, list):
        for item in node:
            urls |= _collect_source_urls(item)
    return urls


def _receipt_failure(
    finding: dict, findings_corpus: str, cited_urls: set[str], window: dict
) -> str | None:
    """Return the first reason a dropped_story receipt is not well-formed, or None.

    Ordered so the message names the most fundamental problem first: no receipt at
    all, then each field, then the two cross-references. `None` means the receipt
    is well-formed and the finding may block."""
    source = finding.get("source")
    if not isinstance(source, dict):
        return "no source object on the finding"
    missing = [f for f in RECEIPT_FIELDS if not source.get(f)]
    if missing:
        return f"source missing {', '.join(missing)}"

    if source["tier"] not in RECEIPT_TIERS:
        return f"tier {source['tier']!r} is not primary or trade"

    if source["url"] not in findings_corpus:
        return "url does not appear in the raw findings corpus"

    within = _within_window(source["published_at"], window)
    if within is None:
        return "coverage window or published_at is unparseable"
    if not within:
        return "published_at is outside the coverage window"

    if source["url"] in cited_urls:
        return "url is already cited in the issue"

    return None


def finding_key(finding: dict) -> tuple:
    """The (kind, where) pair that joins a finding across critic passes.

    The rebuttal channel needs to know whether THIS pass's blocking finding is the
    same one the manager rebutted last round. kind+where is that identity: kind is
    the rubric category, where is the path into the issue it faults. Mechanical on
    purpose — the orchestrator never judges whether two findings 'mean the same
    thing', only whether they name the same fault at the same place."""
    return (finding.get("kind"), finding.get("where"))


def rebuttal_of(finding: dict) -> dict | None:
    """A WELL-FORMED rebuttal attached to a finding, or None.

    A rebuttal is the manager's SOURCED argument that a finding is wrong (spec/05),
    so it counts only with non-empty `text` and at least one `sources[]` entry. An
    unsourced or empty rebuttal is not a rebuttal — the finding reads as ignored,
    and the critic re-files it. Mechanical, like the receipt rule: the orchestrator
    checks the rebuttal is actionable, never whether its argument is correct."""
    rebuttal = finding.get("rebuttal")
    if not isinstance(rebuttal, dict):
        return None
    if not rebuttal.get("text"):
        return None
    sources = rebuttal.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    return rebuttal


def extract_rebuttals(draft: dict) -> dict[tuple, dict]:
    """Pull the manager's well-formed rebuttals out of an edited draft.

    The manager files a rebuttal by attaching it to the finding in
    `critic_report.blocking_findings[].rebuttal` — the same field the critic reads
    on its next pass and the publisher prints. Returns {finding_key: rebuttal} for
    every well-formed one, dropping the `adjudication` field if the manager tried
    to set it (that is the critic's, never the manager's). Malformed critic_report
    shapes yield an empty map rather than raising — a manager that mangled the
    field simply filed no rebuttal."""
    report = draft.get("critic_report")
    if not isinstance(report, dict):
        return {}
    findings = report.get("blocking_findings")
    if not isinstance(findings, list):
        return {}
    rebuttals: dict[tuple, dict] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rebuttal = rebuttal_of(finding)
        if rebuttal is not None:
            # Strip a manager-set adjudication: only the critic's re-judgment sets it.
            rebuttals[finding_key(finding)] = {
                k: v for k, v in rebuttal.items() if k != "adjudication"
            }
    return rebuttals


def match_survivor_key(key: tuple, findings) -> tuple | None:
    """The key of the surviving finding a rebuttal for `key` attaches to, or None.

    Exact (kind, where) first. Failing that, the SOLE surviving finding of the
    same kind — the critic weighed the rebuttal and re-filed the same fault with a
    reworded `where`, and one survivor of that kind is an unambiguous re-file. Two
    or more of the same kind is ambiguous, and none means the fault is gone; both
    return None, and the caller treats the rebuttal as unmatched (withdrawn) rather
    than guess. This is the whole of the join's judgment, and it is mechanical."""
    for finding in findings:
        if finding_key(finding) == key:
            return key
    same_kind = [finding_key(f) for f in findings if f.get("kind") == key[0]]
    return same_kind[0] if len(same_kind) == 1 else None


def attach_adjudication(finding: dict, rebuttal: dict, adjudication: str) -> dict:
    """A copy of `finding` carrying the rebuttal stamped with the critic's verdict.

    Both sides now travel together — in front of the manager on the next round, and
    in the published report — which is the point of the channel."""
    return {**finding, "rebuttal": {**rebuttal, "adjudication": adjudication}}


def rebuttal_record(key: tuple, rebuttal: dict, adjudication: str) -> dict:
    """A standalone critic_report entry for a rebuttal with no surviving finding.

    So a filed rebuttal is NEVER silently deleted (spec/06: a genuine dispute is
    information the reader should have). It rides as a finding-shaped record keyed
    by the rebuttal's own (kind, where), non-gating: the fault it answered is gone
    — the critic WITHDREW it, or the manager complied and it was reaffirmed-then-
    fixed — so it publishes among the advisories, both sides still visible."""
    kind, where = key
    return {
        "kind": kind,
        "where": where,
        "note": f"rebuttal {adjudication} by the critic",
        "rebuttal": {**rebuttal, "adjudication": adjudication},
    }


def _within_window(published_at: str, window: dict) -> bool | None:
    """Is `published_at` inside [window.from, window.to] inclusive?

    Dates only — a source's published_at is a date, and the coverage window is
    two dates. The first ten characters are the date part (tolerating a full
    timestamp). Returns None when anything is unparseable, so the caller reports
    that distinctly rather than silently treating a bad date as out-of-window.
    """
    try:
        when = date.fromisoformat(str(published_at)[:10])
        start = date.fromisoformat(str(window.get("from"))[:10])
        end = date.fromisoformat(str(window.get("to"))[:10])
    except (TypeError, ValueError):
        return None
    return start <= when <= end
