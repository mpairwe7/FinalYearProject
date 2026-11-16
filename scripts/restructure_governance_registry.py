"""Move governance records into the registry layout and repoint every reference.

Safe to rerun: moves are skipped once done, and no new path contains an old one.
Usage: python3 scripts/restructure_governance_registry.py [repo root]  (ADR-0058)
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
SYSTEM = "governance/systems/ura-ai-chatbot"
MOVES = {
    "governance/ai_risk_manifest.yaml": f"{SYSTEM}/manifest.yaml",
    "docs/MODEL_CARD.md": f"{SYSTEM}/model_card.md",
    "docs/compliance/DPIA_306.md": f"{SYSTEM}/dpia.md",
    "docs/compliance/PRIVACY_COMPLIANCE_306.md": f"{SYSTEM}/privacy_controls.md",
    "docs/compliance/PDPO_REGISTRATION_306.md": "governance/evidence/approvals/pdpo_registration.md",
}
NEW_TO_OLD = {new: old for old, new in MOVES.items()}
# These two name the old paths on purpose.
KEEP_OLD_PATHS = {"scripts/restructure_governance_registry.py", "docs/adr/0058-ai-governance-registry-layout.md"}
LINK = re.compile(r"(\]\(\s*<?)([^)\s>]+)(>?(?:\s+\"[^\"]*\")?\s*\))|^(\s*\[[^\]]+\]:\s*<?)(\S+?)(>?\s*)$", re.M)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/|\{|\$)", re.I)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def move_files():
    for old, new in MOVES.items():
        if (ROOT / old).exists():
            (ROOT / new).parent.mkdir(parents=True, exist_ok=True)
            git("mv", old, new)


def rewrite_links(rel, text):
    """Resolve each relative link from where the file used to live; re-relativise from where it lives now."""
    old_dir = Path(NEW_TO_OLD.get(rel, rel)).parent
    new_dir = Path(rel).parent

    def fix(m):
        pre, raw, post = (m.group(1), m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5), m.group(6))
        if EXTERNAL.match(raw):
            return m.group(0)
        target, sep, frag = raw.partition("#")
        if not target:
            return m.group(0)
        resolved = os.path.normpath(old_dir / target)
        if rel not in NEW_TO_OLD and resolved not in MOVES:
            return m.group(0)
        resolved = MOVES.get(resolved, resolved)
        if not (ROOT / resolved).exists():
            return m.group(0)  # already broken before the move; leave it for the before/after diff to show
        new_raw = os.path.relpath(resolved, new_dir) + ("/" if target.endswith("/") else "") + sep + frag
        return pre + new_raw + post if new_raw != raw else m.group(0)

    return LINK.sub(fix, text)


def main():
    move_files()
    for rel in git("ls-files").splitlines():
        path = ROOT / rel
        if not path.is_file() or rel in KEEP_OLD_PATHS or rel.startswith(("Data/", "CosyVoice/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new = rewrite_links(rel, text) if rel.endswith(".md") else text
        for old_path, new_path in MOVES.items():
            new = new.replace(old_path, new_path)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(rel)


main()
