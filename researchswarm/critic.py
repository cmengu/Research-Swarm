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

The retry loop and the rebuttal channel are ticket #35, NOT here: this module
wires one critic pass and its verdicts.

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

# The critic emits exactly one of the first three; `not_run` is the
# orchestrator's, never the critic's — a critic that emitted it would be claiming
# its own unavailability, which is a contradiction. So an emitted verdict outside
# these three is malformed and resolves to not_run (below).
CRITIC_VERDICTS = ("pass", "pass_with_advisories", "blocked")
NOT_RUN = "not_run"

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
        "dropped_story",
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

    A finding that fails ANY of these is auto-downgraded into the advisory list
    with a note naming what failed, consuming no retry. Non-`dropped_story`
    blocking kinds pass through untouched — the orchestrator never judges THEIR
    materiality, only a dropped_story's well-formedness. Returns (kept_blocking,
    downgraded_to_advisory).
    """
    window = issue.get("issue", {}).get("coverage_window", {}) or {}
    issue_json = json.dumps(issue, ensure_ascii=False)

    kept: list[dict] = []
    downgraded: list[dict] = []
    for finding in blocking_findings:
        if finding.get("kind") != "dropped_story":
            kept.append(finding)  # a different blocking kind — not ours to judge
            continue
        failure = _receipt_failure(finding, findings_corpus, issue_json, window)
        if failure is None:
            kept.append(finding)
        else:
            downgraded.append(_annotate(finding, f"receipt downgrade: {failure}"))

    return tuple(kept), tuple(downgraded)


def _receipt_failure(
    finding: dict, findings_corpus: str, issue_json: str, window: dict
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

    if source["url"] in issue_json:
        return "url is already cited in the issue"

    return None


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
