#!/usr/bin/env python3
"""Self-containment checker for the build spec.

Verifies every intra-repo Markdown link — file paths and #anchors — resolves.
External http(s) links and mailto: are skipped. GitHub-flavoured heading slugs.

Usage:  python3 scripts/check-spec-links.py [root]
Exit 0 if all links resolve, 1 otherwise. Chartered by ticket #62.
"""
import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")

# Files whose links we check. The spec is the authority; capture/rubric/schema
# docs are historical and not link-audited here.
TARGETS = ["SPEC.md", "README.md"] + [
    os.path.join("docs/spec", f)
    for f in sorted(os.listdir(os.path.join(ROOT, "docs/spec")))
    if f.endswith(".md")
]

LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE_RE = re.compile(r"`([^`]*)`")


def slugify(heading: str) -> str:
    """GitHub-flavoured anchor slug."""
    text = INLINE_CODE_RE.sub(r"\1", heading)            # drop code backticks, keep text
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> their text
    text = re.sub(r"[*~]", "", text)                     # emphasis marks (NOT underscore — kept in slugs)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)                 # drop punctuation (keep word chars, -, space)
    text = text.replace(" ", "-")
    return text


def anchors_of(path: str):
    slugs = {}
    out = set()
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return out
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = ATX_RE.match(line)
        if not m:
            continue
        base = slugify(m.group(2))
        n = slugs.get(base, 0)
        slugs[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def main() -> int:
    anchor_cache = {}
    broken = []
    checked = 0
    for rel in TARGETS:
        src = os.path.join(ROOT, rel)
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        src_dir = os.path.dirname(src)
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            path_part, _, anchor = target.partition("#")
            if path_part == "":
                dest = src           # same-file anchor
            else:
                dest = os.path.normpath(os.path.join(src_dir, path_part))
                if not os.path.exists(dest):
                    broken.append((rel, target, "file not found"))
                    continue
            if anchor:
                if dest not in anchor_cache:
                    anchor_cache[dest] = anchors_of(dest)
                if anchor not in anchor_cache[dest]:
                    broken.append((rel, target, f"anchor #{anchor} not in {os.path.relpath(dest, ROOT)}"))
    print(f"checked {checked} intra-repo links across {len(TARGETS)} files")
    if broken:
        print(f"\n{len(broken)} BROKEN:")
        for rel, target, why in broken:
            print(f"  {rel}: [{target}] — {why}")
        return 1
    print("all intra-repo links resolve ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
