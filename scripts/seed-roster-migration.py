#!/usr/bin/env python3
"""The roster migration — seed the company records the published issues imply.

WHY THIS EXISTS, AND WHY IT IS NOT run.py.

`run.py` is the sole MACHINE writer ([03] clause 1). This is not a machine write:
spec/03 "Migrating the seeded roster" names the migration as *a deferred HITL
curation session, not a compile step*, and `state/programs/<id>/edges.json`
ships with that sentence in its own `_comment`. A human deciding "this asset is
that company's" is exactly the judgement the loop is forbidden to invent, so it
gets a human-invoked, auditable, re-runnable script rather than a hidden branch
inside the orchestrator.

WHY IT WAS NEEDED. The dossier aperture is reachable in code (#96), the layer
publishes (#97) and the view renders (#98) — but no company record had ever been
written, so every cycle logged "no company records exist" and the whole chain sat
dark. Nothing in the loop could bootstrap it: a company enters the roster only
when an asset names a holder, and the assets on disk carried none.

PROVENANCE IS THE WHOLE POINT. These records are built through the SAME builder
the loop uses (`build_company_dossier_record`), so their shape, their drift log
and their `coverage.thin_sections` are computed exactly as a scan's would be.
Only the provenance differs, and it differs LOUDLY:

  - `established_by` is `seed:roster-migration-<date>`, never a run id. It
    renders on the page as "established by seed:roster-migration-…", so a reader
    can always tell a seeded fact from a scanned one.
  - `last_edited_by` is "owner", the field [03] uses to adjudicate human vs loop
    writes.
  - `coverage.degradation` says, in the reader's own words, that no dossier scan
    has run on this company yet.

Every fact below is public and was verified against a primary or trade source
before it was written here; sources are listed per section in SOURCES. Sections
that could NOT be verified are omitted rather than guessed — an omitted section
reports as `not measured`, which is the honest answer and is the behaviour the
thin-sections marker exists for.

Re-running is safe: the builder is a merge, an unchanged section is a no-op, and
a changed one appends to the drift log rather than overwriting.

Usage:  uv run python scripts/seed-roster-migration.py [--root PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from researchswarm.dossiers import (  # noqa: E402
    build_asset_company_link,
    build_company_dossier_record,
    company_dossier_path,
    load_company_dossier,
)
from researchswarm.state_edits import write_json  # noqa: E402

SEED_DATE = "2026-07-20"
SEED_ID = f"seed:roster-migration-{SEED_DATE}"

# The sources each record was verified against, kept beside the data they
# justify. Not a schema field — a receipt for the human who reviews this diff.
SOURCES = {
    "co_remegen": [
        "https://www.remegen.com/",
        "https://stockanalysis.com/quote/hkg/9995/company/",
        "https://news.abbvie.com/2026-01-12-AbbVie-and-RemeGen-Announce-Exclusive-Licensing-Agreement-to-Develop-A-Novel-Bispecific-Antibody-for-Advanced-Solid-Tumors",
        "https://www.businesswire.com/news/home/20210809005208/en/Seagen-and-RemeGen-Announce-Exclusive-Worldwide-License-and-Co-Development-Agreement-for-Disitamab-Vedotin",
    ],
    "co_abbvie": [
        "https://news.abbvie.com/2026-01-12-AbbVie-and-RemeGen-Announce-Exclusive-Licensing-Agreement-to-Develop-A-Novel-Bispecific-Antibody-for-Advanced-Solid-Tumors",
        "https://fortune.com/company/abbvie/",
        "https://www.europeanpharmaceuticalreview.com/news/270207/abbvie-bispecific-antibody-remegen-licensing-deal/",
    ],
}

# ---------------------------------------------------------------------------
# The records. Omission is deliberate everywhere it appears.
# ---------------------------------------------------------------------------

REMEGEN = {
    "identity": {
        "legal_name": "RemeGen Co., Ltd.",
        "aliases": ["RemeGen", "榮昌生物"],
        "founded": "2008",
        "hq": "Yantai, Shandong, China",
        "status": "public",
        "listings": [{"exchange": "HKEX", "ticker": "9995"}],
    },
    "origin": {
        "founding_story": (
            "Founded in 2008 in Yantai by Dr. Jianmin Fang and Weidong Wang, built on "
            "in-house antibody and antibody-drug-conjugate engineering rather than "
            "in-licensed assets. Fang is the named inventor of both marketed products."
        ),
        "founders": ["Jianmin Fang", "Weidong Wang"],
    },
    "pipeline": [
        {"asset_entity_id": "asset_rc148", "indication": "Advanced solid tumours (NSCLC, CRC)",
         "phase": "Phase 2", "status": "active"},
        {"asset_entity_id": "asset_disitamab_vedotin", "indication": "Gastric and urothelial carcinoma",
         "phase": "Marketed (China)", "status": "active"},
        {"asset_entity_id": "asset_telitacicept", "indication": "SLE, rheumatoid arthritis, myasthenia gravis",
         "phase": "Marketed (China)", "status": "active"},
        {"asset_entity_id": "asset_rc88", "indication": "Mesothelin-expressing solid tumours",
         "phase": "Clinical", "status": "active"},
    ],
    "deals": [
        {"date": "2026-01-12", "type": "licence", "counterparty": "AbbVie", "direction": "out",
         "upfront": "$650M", "milestones": "up to $4.95B",
         "royalty": "tiered double-digit on ex-China sales",
         "territory": "ex-Greater China (RemeGen retains mainland China, Hong Kong, Macau, Taiwan)"},
        {"date": "2021-08", "type": "licence", "counterparty": "Seagen", "direction": "out",
         "territory": "exclusive worldwide licence and co-development for disitamab vedotin"},
    ],
    "people": [
        {"name": "Jianmin Fang", "role": "Co-founder and Chief Executive Officer", "since": "2008"},
    ],
    # funding / pivots / setbacks OMITTED — the HKEX prospectus and any setback
    # history were not read, and a guessed round is worse than a marked gap.
}

ABBVIE = {
    "identity": {
        "legal_name": "AbbVie Inc.",
        "aliases": ["AbbVie"],
        "founded": "2013",
        "hq": "North Chicago, Illinois, United States",
        "status": "public",
        "listings": [{"exchange": "NYSE", "ticker": "ABBV"}],
    },
    "origin": {
        "founding_story": (
            "Created on 1 January 2013 when Abbott Laboratories distributed all outstanding "
            "AbbVie shares to Abbott shareholders, separating the research-based "
            "biopharmaceutical business into an independent company."
        ),
        "spun_out_of": "Abbott Laboratories",
    },
    "pipeline": [
        {"asset_entity_id": "asset_rc148", "indication": "Advanced solid tumours (NSCLC, CRC)",
         "phase": "Phase 2", "status": "in-licensed ex-Greater China", "first_disclosed": "2026-01-12"},
    ],
    "deals": [
        {"date": "2026-01-12", "type": "licence", "counterparty": "RemeGen", "direction": "in",
         "upfront": "$650M", "milestones": "up to $4.95B",
         "royalty": "tiered double-digit on ex-China sales",
         "territory": "exclusive rights to develop, manufacture and commercialise RC148 outside Greater China"},
    ],
    # people / funding / pivots / setbacks OMITTED. Current leadership was not
    # verified, and a stale CEO stated confidently is worse than a marked gap.
}

RECORDS = {
    "co_remegen": (REMEGEN, (
        "Seeded by hand during the roster migration from public company and deal sources — "
        "no dossier scan has run on this company yet. Funding, pivots and setbacks were not "
        "researched, so they read as unmeasured rather than empty."
    )),
    "co_abbvie": (ABBVIE, (
        "Seeded by hand during the roster migration from public company and deal sources — "
        "no dossier scan has run on this company yet. Current leadership was not verified and "
        "is deliberately absent rather than stated stale."
    )),
}

# The asset the published issue already names these two companies as holders of.
# Writing the link back onto the asset is what makes the aperture reachable from
# state instead of only from an issue that happens to still be on disk.
ASSET_ID = "asset_rc148"
ASSET_HOLDERS = ["RemeGen Co., Ltd.", "AbbVie"]
ASSET_SPONSOR = "co_remegen"  # originator; AbbVie holds ex-Greater-China rights only


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Report, write nothing.")
    args = parser.parse_args()
    root = args.root

    wrote = 0
    for entity_id, (payload, degradation) in RECORDS.items():
        prior = load_company_dossier(root, entity_id)
        record, changed = build_company_dossier_record(
            prior, payload,
            entity_id=entity_id, run_id=SEED_ID, issue_id=None,
            date=SEED_DATE, degradation=degradation,
        )
        # The loop stamps "loop" here; this write is a human curation act and says so.
        record["last_edited_by"] = "owner"
        thin = ", ".join(record["coverage"]["thin_sections"]) or "none"
        held = ", ".join(k for k in record["facts"])
        print(f"{entity_id}: {'CHANGED' if changed else 'no-op'} | held: {held} | not measured: {thin}")
        if changed and not args.dry_run:
            out = company_dossier_path(root, entity_id)
            # `write_json` writes, it does not create trees — and `state/entities/
            # companies/` has never existed, because nothing has ever written a
            # company. This script is the first writer of that directory.
            out.parent.mkdir(parents=True, exist_ok=True)
            write_json(out, record)
            wrote += 1

    # The asset side of the link.
    asset_path = root / "state" / "entities" / f"{ASSET_ID}.json"
    existing = json.loads(asset_path.read_text()) if asset_path.exists() else {}
    record, changed = build_asset_company_link(
        existing, asset_entity_id=ASSET_ID, company_entity_id=ASSET_SPONSOR,
        run_id=SEED_ID, issue_id=None, date=SEED_DATE,
    )
    # `holders` is the DISCOVERY path the planner reads (apertures.company_ids_from_holders);
    # `held_by` is the resolved link. Both are written, in the provenance shape the
    # entity-fact writer uses, so a later scan merges with them rather than around them.
    facts = record.setdefault("facts", {})
    if (facts.get("holders") or {}).get("value") != ASSET_HOLDERS:
        facts["holders"] = {"value": ASSET_HOLDERS, "established_by": SEED_ID, "issue": None}
        changed = True
    record["last_edited_by"] = "owner"
    print(f"{ASSET_ID}: {'CHANGED' if changed else 'no-op'} | holders={ASSET_HOLDERS} held_by={ASSET_SPONSOR}")
    if changed and not args.dry_run:
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(asset_path, record)
        wrote += 1

    print(f"\n{'[dry-run] would write' if args.dry_run else 'wrote'} {wrote} record(s).")
    print("Sources verified against:")
    for entity_id, urls in SOURCES.items():
        for url in urls:
            print(f"  {entity_id}: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
