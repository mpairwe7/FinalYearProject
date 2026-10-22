"""CI/CD supply-chain policy (ADR-0011), enforced offline in the root suite.

Replaces the string checks that lived in the journey-copilot tests, which
looked for six retired SHAs and two spellings of ``NODE_VERSION: 20``. The
invariants here are structural, so they hold for pins nobody has seen yet:

* every action is pinned to a full commit SHA carrying a version comment,
  and one action resolves to one SHA across the whole repo;
* every checkout makes an explicit ``persist-credentials`` decision, and
  only the jobs that push a commit keep the token;
* no workflow input or event text is spliced into a shell or JS body;
* container images are version + digest pinned, never ``:latest``;
* Node and Bun come from one version file each, and the Dockerfiles that
  ship the frontend use the same majors, digest-pinned;
* every Dependabot ecosystem waits out a cooldown, and the frontend is
  updated through its real lockfile (``bun``);
* cosign verification is pinned to this repository's signing workflow.

``zizmor`` (actions-security.yml) audits the same tree more deeply in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
COMPOSITES = sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
FRONTEND = ROOT / "App" / "frontend"

#: Workflows whose job commits back to the repo and so must keep the token.
PUSHING_WORKFLOWS = {"scheduled-crawl.yml", "ura-chatbot-roll-hf-space.yml"}
#: Reviewed workflow_run consumers (they never check out the triggering head).
WORKFLOW_RUN_ALLOWED = {"lighthouse-live.yml"}
#: Pins retired by ADR-0011 (Node 20 runtimes) and the #621 refresh.
RETIRED_PINS = (
    "setup-python@a26af69b",
    "download-artifact@d3f86a10",
    "build-push-action@4f58ea79",
    "checkout@34e11487",
    "cache@0057852b",
    "github-script@f28e40c7",
    "codecov-action@b9fd7d16",
    "fetch-metadata@d7267f60",
    "action-baseline@2b5fc7f9",
    "scorecard-action@2d114668",
    "cache@27d5ce7f",
)

_USES_LINE = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>[^\s#]+)(?P<rest>.*)$", re.M)
_PINNED = re.compile(r"^(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^@]+)?@(?P<sha>[0-9a-f]{40})$")
_VERSION_COMMENT = re.compile(r"#\s*v?\d+(\.\d+)*\b")
_UNTRUSTED_EXPR = re.compile(r"\$\{\{\s*(inputs\.|github\.event\.|github\.head_ref|github\.base_ref)")
_DIGEST_IMAGE = re.compile(r"^[^\s:@]+(:[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _steps(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    if "runs" in doc:  # composite action
        return [("composite", s) for s in doc["runs"].get("steps", []) or []]
    out = []
    for job_name, job in (doc.get("jobs") or {}).items():
        out.extend((job_name, s) for s in job.get("steps", []) or [])
    return out


def _triggers(doc: dict[str, Any]) -> set[str]:
    # PyYAML reads the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on or {})


@pytest.mark.parametrize("path", WORKFLOWS + COMPOSITES, ids=lambda p: p.parent.name + "/" + p.name)
def test_every_action_is_sha_pinned_with_a_version_comment(path: Path):
    for m in _USES_LINE.finditer(path.read_text(encoding="utf-8")):
        ref = m.group("ref").strip("'\"")
        if ref.startswith("./"):
            continue
        assert _PINNED.match(ref), f"{path.name}: `{ref}` is not pinned to a 40-char commit SHA"
        assert _VERSION_COMMENT.search(m.group("rest")), (
            f"{path.name}: `{ref}` needs a `# vX.Y.Z` comment so Dependabot can track it"
        )
    text = path.read_text(encoding="utf-8")
    for retired in RETIRED_PINS:
        assert retired not in text, f"{path.name} still uses retired pin {retired}"


def test_one_action_resolves_to_one_sha_across_the_repo():
    seen: dict[str, dict[str, str]] = {}
    for path in WORKFLOWS + COMPOSITES:
        for m in _USES_LINE.finditer(path.read_text(encoding="utf-8")):
            pinned = _PINNED.match(m.group("ref").strip("'\""))
            if pinned:
                seen.setdefault(pinned.group("action"), {})[pinned.group("sha")] = path.name
    drift = {action: shas for action, shas in seen.items() if len(shas) > 1}
    assert not drift, f"same action pinned to different SHAs: {json.dumps(drift, indent=2)}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkouts_decide_persist_credentials_explicitly(path: Path):
    for job, step in _steps(_load(path)):
        if "actions/checkout@" not in str(step.get("uses", "")):
            continue
        with_block = step.get("with") or {}
        assert "persist-credentials" in with_block, (
            f"{path.name}:{job}: checkout must set persist-credentials (false unless the job pushes)"
        )
        if with_block["persist-credentials"] is not False:
            assert path.name in PUSHING_WORKFLOWS, (
                f"{path.name}:{job}: only workflows that push a commit may keep the checkout token"
            )


@pytest.mark.parametrize("path", WORKFLOWS + COMPOSITES, ids=lambda p: p.parent.name + "/" + p.name)
def test_no_untrusted_expression_is_spliced_into_a_script(path: Path):
    for job, step in _steps(_load(path)):
        bodies = [str(step.get("run") or ""), str((step.get("with") or {}).get("script") or "")]
        for body in bodies:
            hit = _UNTRUSTED_EXPR.search(body)
            assert hit is None, (
                f"{path.name}:{job}:{step.get('name', '?')}: pass `{hit.group(0)}…` through env: "
                "and reference the variable instead (template injection)"
            )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_triggers_that_run_with_repo_trust_are_reviewed(path: Path):
    triggers = _triggers(_load(path))
    assert "pull_request_target" not in triggers, f"{path.name}: pull_request_target is not allowed"
    if "workflow_run" in triggers:
        assert path.name in WORKFLOW_RUN_ALLOWED, f"{path.name}: unreviewed workflow_run trigger"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_container_images_are_digest_pinned(path: Path):
    for job_name, job in (_load(path).get("jobs") or {}).items():
        images = []
        container = job.get("container")
        if isinstance(container, dict) and container.get("image"):
            images.append(container["image"])
        elif isinstance(container, str):
            images.append(container)
        for svc in (job.get("services") or {}).values():
            if isinstance(svc, dict) and svc.get("image"):
                images.append(svc["image"])
        for image in images:
            assert ":latest" not in image, f"{path.name}:{job_name}: {image} uses :latest"
            assert _DIGEST_IMAGE.match(image), f"{path.name}:{job_name}: {image} must be pinned by digest"


def test_node_and_bun_come_from_one_version_file_each():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*NODE_VERSION:", text, re.M), f"{path.name}: use App/frontend/.nvmrc"
        assert not re.search(r"^\s*BUN_VERSION:", text, re.M), f"{path.name}: use App/frontend/.bun-version"
        for job, step in _steps(_load(path)):
            uses, with_block = str(step.get("uses", "")), step.get("with") or {}
            if "actions/setup-node@" in uses:
                assert with_block.get("node-version-file") == "App/frontend/.nvmrc", f"{path.name}:{job}"
                assert "node-version" not in with_block, f"{path.name}:{job}: literal node-version"
            if "oven-sh/setup-bun@" in uses:
                assert with_block.get("bun-version-file") == "App/frontend/.bun-version", f"{path.name}:{job}"
                assert "bun-version" not in with_block, f"{path.name}:{job}: literal bun-version"


def test_shipped_frontend_runtimes_match_ci_and_are_digest_pinned():
    node_major = (FRONTEND / ".nvmrc").read_text(encoding="utf-8").strip()
    bun_version = (FRONTEND / ".bun-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+", node_major), ".nvmrc holds a bare Node major"
    assert int(node_major) >= 24, "Node 20 is EOL and removed from GitHub runners"
    engines = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["engines"]["node"]
    assert engines == f">={node_major}.0.0", "package.json engines.node must match .nvmrc"

    dockerfiles = {
        "App/frontend/Dockerfile": FRONTEND / "Dockerfile",
        "App/Dockerfile.cranecloud": ROOT / "App" / "Dockerfile.cranecloud",
    }
    for name, path in dockerfiles.items():
        text = path.read_text(encoding="utf-8")
        froms = re.findall(r"^FROM\s+(\S+)", text, re.M)
        node_froms = [f for f in froms if f.startswith("node:")]
        bun_froms = [f for f in froms if f.startswith("oven/bun:")]
        assert node_froms or bun_froms, f"{name}: no Node/Bun base image found"
        for image in node_froms:
            assert image.startswith(f"node:{node_major}-"), f"{name}: {image} != Node {node_major} (.nvmrc)"
            assert _DIGEST_IMAGE.match(image), f"{name}: {image} must be pinned by digest"
        for image in bun_froms:
            assert image.startswith(f"oven/bun:{bun_version}-"), f"{name}: {image} != Bun {bun_version}"
            assert _DIGEST_IMAGE.match(image), f"{name}: {image} must be pinned by digest"
        for image in re.findall(r"^COPY\s+--from=(\S*[:/]\S*)", text, re.M):
            assert not image.startswith(("node:", "oven/bun:")), (
                f"{name}: copy Node/Bun from a named FROM stage so Dependabot sees the pin"
            )
            assert _DIGEST_IMAGE.match(image), f"{name}: COPY --from={image} must be pinned by digest"
        instructions = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert "setup_2" not in instructions and "nodesource" not in instructions, (
            f"{name}: no curl | bash Node installer"
        )


def test_dependabot_cooldown_and_coverage():
    config = _load(ROOT / ".github" / "dependabot.yml")
    ecosystems: dict[str, list[str]] = {}
    for update in config["updates"]:
        eco = update["package-ecosystem"]
        dirs = update.get("directories") or [update["directory"]]
        ecosystems.setdefault(eco, []).extend(dirs)
        cooldown = update.get("cooldown") or {}
        assert int(cooldown.get("default-days", 0)) >= 7, f"{eco} {dirs}: cooldown under 7 days"
    assert "npm" not in ecosystems, "the frontend lockfile is bun.lock; npm updates skip it"
    assert "/App/frontend" in ecosystems.get("bun", [])
    for needed in ("/", "/App", "/App/frontend"):
        assert needed in ecosystems.get("docker", []), f"Dependabot docker does not cover {needed}"


def test_signatures_are_verified_against_this_repository_only():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"certificate-identity-regexp=[\"']?\.\*[\"']?(\s|\\|$)", text), (
            f"{path.name}: cosign verify must pin the signer identity, not accept any workflow"
        )
    build = _load(ROOT / ".github" / "workflows" / "ura-chatbot-build-push.yml")
    sign = build["jobs"]["sign"]
    assert sign["uses"] == "./.github/workflows/container-sign-provenance.yml"
    assert "build" in ([sign["needs"]] if isinstance(sign["needs"], str) else sign["needs"])
