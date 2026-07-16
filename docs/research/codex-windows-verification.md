# Codex CLI on Windows for Unattended Runs — Verification (16 Jul 2026)

Research asset for ticket [#2](https://github.com/cmengu/Research-Swarm/issues/2). Question: can the Codex critic run on the target Windows machine, headless, on the ChatGPT subscription, with auth that survives unattended scheduled runs?

## Verdict: YES — viable path, no WSL required

Native Windows + `codex exec` + one-time ChatGPT sign-in works for our twice-weekly cadence. The locked assumption in CAPTURE.md ("likely via WSL") was **wrong in our favor**: WSL is optional, not required.

## Findings

### 1. Native Windows vs WSL

- OpenAI now ships a **first-party native Windows path**: Node.js 22 + `npm install -g @openai/codex` in PowerShell. Their install page says to use the native Windows sandbox by default ([developers.openai.com/codex/windows](https://developers.openai.com/codex/windows)).
- The native binary has an **AppContainer-based sandbox**: filesystem writes restricted, **network blocked by default** — which suits the critic perfectly, since it only reads the draft digest and returns findings; it never needs the web.
- Caveat: OpenAI still labels Windows support **experimental**. WSL2 remains the documented fallback ("when neither native Windows sandbox mode meets your needs").

### 2. Headless invocation

- `codex exec "<task>"` is the official non-interactive mode: runs one session to completion, streams progress to stderr, prints the final answer to stdout ([non-interactive docs](https://developers.openai.com/codex/noninteractive)).
- `--json` switches stdout to a JSONL event stream — the orchestrator can parse the critic's structured verdict mechanically.
- `--ephemeral` avoids persisting session files; `codex exec resume --last` exists if a retry loop wants continuity.

### 3. Subscription auth persistence

- Sign-in caches credentials at `~/.codex/auth.json` (or OS credential store). **Tokens auto-refresh during use** — official: "Codex refreshes tokens automatically during use before they expire" ([auth docs](https://learn.chatgpt.com/docs/auth)).
- **Device-code login** (`codex login --device-auth`, beta) exists for machines where the browser flow is awkward.
- Secondary sources report sessions go stale after **~8 idle days** (not in official docs — treat as approximate). Our Mon+Thu cadence means a max 4-day gap, and every run refreshes the token, so steady-state never goes stale. The risk window is a **machine off/failing for >1 week** (holiday, outage) → next run fails auth → our failed-run stub page surfaces it, and recovery is one interactive re-login.
- OpenAI's docs recommend API keys for CI/CD; we consciously deviate — this is a personal machine, not shared CI, and subscription billing is a locked project constraint. `auth.json` is plaintext: keep it out of the repo (gitignore `~/.codex` never enters the tree anyway).

### 4. Rate limits (ChatGPT plans, Jul 2026)

- Usage is capped by a rolling 5-hour window + a stacked weekly cap; Plus (~$20/mo) allows roughly 15–280 messages per 5-hour window depending on model tier, Pro multiplies this 5–20× ([pricing](https://developers.openai.com/codex/pricing), [help center](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)).
- Our load — ≤3 critic calls per run (initial + 2 retries), twice a week — is negligible even on Plus. Top-up credits exist if ever needed.

## Consequences for the design

1. **Orchestrator contract**: critic stage = `codex exec --json` with the draft issue on disk; parse verdict from stdout JSONL; exit code + parse failure both count as a stage failure.
2. **Setup checklist gains one HITL step**: one-time `codex login` (or `--device-auth`) on the target PC during install.
3. **Failure mode documented**: >1 week of missed runs may require re-login; the failed-run stub is the alert.
4. **No WSL ticket needed**; drop the WSL assumption from CAPTURE.md context when the spec is compiled.

## Sources

- https://developers.openai.com/codex/windows — native Windows install + sandbox
- https://developers.openai.com/codex/noninteractive — `codex exec` non-interactive mode
- https://learn.chatgpt.com/docs/auth — auth methods, auto-refresh, auth.json, device code
- https://developers.openai.com/codex/pricing and https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan — plan limits
- https://github.com/openai/codex — repo/changelog
