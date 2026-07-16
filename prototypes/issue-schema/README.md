# PROTOTYPE — issue.json schema (throwaway)

**Question this answers:** What is the exact field-level contract of one `issue.json`?
(Wayfinder ticket [#3](https://github.com/cmengu/Research-Swarm/issues/3))

**All content in the samples is FICTIONAL** — companies, deals, and trial results are
invented to look realistic. Do not cite anything here.

Files:

- `2026-07-16.issue.json` — a complete sample issue exercising **every** digest section
  from CAPTURE.md (TLDR headline, stats bar, TLDR bullets, tracked watchlist,
  quiet-this-cycle incl. critic catches + open threads, new-on-radar, themes & signals,
  elsewhere-on-frontier, sources & method, critic report, thesis + watchlist changelogs).
- `2026-07-13.issue.failed.json` — the stub variant a failed run publishes
  (locked decision #16: fail visible, next run widens its coverage window).
- `SCHEMA-NOTES.md` — the field-level contract: every field, its type, enums,
  the SQLite mapping, and the open questions to react to.

React by reading `2026-07-16.issue.json` top-to-bottom as if it were this week's digest,
then hit the open questions at the bottom of `SCHEMA-NOTES.md`.

This directory is throwaway: once the schema is locked, the contract graduates into the
build spec and this branch is retired.
