"""CI/CD supply-chain policy (ADR-0011), enforced offline in the root suite.

Replaces the string checks that lived in the journey-copilot tests, which
looked for six retired SHAs and two spellings of ``NODE_VERSION: 20``. The
invariants here are structural, so they hold for pins nobody has seen yet:

* every action is pinned to a full commit SHA carrying a version comment,
  and one action resolves to one SHA across the whole repo;
* every checkout makes an explicit ``persist-credentials`` decision, and
  only the jobs that push a commit keep the token;
* no workflow input or event text is spliced into a shell or JS body;
* container images are version + digest pinned, never ``:latest`` — in
  workflows and in every Dockerfile, whose directories Dependabot watches;
* Node and Bun come from one version file each, and the Dockerfiles that
  ship the frontend use the same majors, digest-pinned;
* every Dependabot ecosystem waits out a cooldown, the frontend is updated
  through its real lockfile (``bun``), and auto-merge is enabled only where
  the base branch requires status checks;
* the OpenTelemetry family moves in lockstep, and the backend lockfile
  satisfies the requirements it was compiled from;
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
#: The refs build-push signs from; verification accepts no other.
_SIGNER_REFS = "@refs/(heads/(dev|main)|tags/v[^/]+)$"
_SIGN_IF = "github.ref == 'refs/heads/dev' || github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')"
_DIGEST_IMAGE = re.compile(r"^[^\s:@]+(:[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]{64}$")
#: Our own image, rolled to an immutable ``sha-`` tag by ura-chatbot-roll-hf-space.yml.
SELF_ROLLED = {"App/deploy/hf-space/Dockerfile": "landwind/ura-chatbot:sha-"}
_SKIP_DIRS = {"node_modules"}


def _dockerfiles() -> list[Path]:
    return sorted(
        p
        for p in ROOT.rglob("Dockerfile*")
        if p.is_file()
        and p.suffix != ".dockerignore"
        and not any(part.startswith(".") or part in _SKIP_DIRS for part in p.relative_to(ROOT).parts[:-1])
    )


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



@pytest.mark.parametrize("path", _dockerfiles(), ids=lambda p: str(p.relative_to(ROOT)))
def test_dockerfile_images_are_digest_pinned(path: Path):
    """A tag alone is mutable: ``python:3.11-slim-bookworm`` and ``uv:0.7``
    were rebuilt underneath this repo between two identical builds."""
    name = str(path.relative_to(ROOT))
    text = path.read_text(encoding="utf-8")
    stages = {s.lower() for s in re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.M | re.I)}
    images = re.findall(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M | re.I)
    images += re.findall(r"^COPY\s+(?:--\S+\s+)*--from=(\S+)", text, re.M | re.I)
    assert images, f"{name}: no base image found"
    for image in images:
        if image.lower() in stages or image.isdigit():
            continue
        if name in SELF_ROLLED and image.startswith(SELF_ROLLED[name]):
            continue
        assert "$" not in image, f"{name}: {image} — Dependabot cannot see an ARG-built image reference"
        assert _DIGEST_IMAGE.match(image), f"{name}: {image} must be pinned by tag and digest"

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
    # A digest pin nobody bumps goes stale, so every Dockerfile's directory is watched.
    for path in _dockerfiles():
        name = str(path.relative_to(ROOT))
        if name in SELF_ROLLED:
            continue
        rel = path.parent.relative_to(ROOT).as_posix()
        directory = "/" if rel == "." else f"/{rel}"
        assert directory in ecosystems.get("docker", []), f"Dependabot docker does not watch {directory} ({name})"


def test_signatures_are_verified_against_this_repository_only():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"certificate-identity-regexp=[\"']?\.\*[\"']?(\s|\\|$)", text), (
            f"{path.name}: cosign verify must pin the signer identity, not accept any workflow"
        )
        # Any ref of this repo is not enough: a branch anyone can push to would
        # sign as this workflow. Only the protected refs build-push signs from.
        for pattern in re.findall(r"certificate-identity-regexp=\"([^\"]+)\"", text):
            assert pattern.endswith(_SIGNER_REFS), f"{path.name}: {pattern} must end in {_SIGNER_REFS}"
    build = _load(ROOT / ".github" / "workflows" / "ura-chatbot-build-push.yml")
    sign = build["jobs"]["sign"]
    assert sign["uses"] == "./.github/workflows/container-sign-provenance.yml"
    assert "build" in ([sign["needs"]] if isinstance(sign["needs"], str) else sign["needs"])
    assert sign["if"] == _SIGN_IF, "sign only what can be deployed: dev, main and v* tags"
    # The image name contains the Docker Hub username, a secret, so a job
    # output carrying it is dropped ("Skip output ... may contain secret") and
    # the first real run signed nothing. It must be a literal.
    env = build["env"]
    assert sign["with"]["image"] == f"{env['REGISTRY']}/{env['IMAGE_NAME']}"
    for name, value in (build["jobs"]["build"].get("outputs") or {}).items():
        assert "IMAGE_NAME" not in str(value) and "image_ref" not in str(value), (
            f"build output {name} would carry the secret-masked image name"
        )


def _requirement_lines(path: Path) -> list[str]:
    return [
        line.split("#", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip() and not line.lstrip().startswith(("-", "--"))
    ]


_OTEL_REQUIREMENTS = (
    ROOT / "requirements.txt",
    ROOT / "App" / "backend" / "requirements.txt",
    ROOT / "App" / "backend" / "requirements-cranecloud.txt",
)
_OTEL_CORE = ("opentelemetry-api", "opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-grpc")
_OTEL_CONTRIB = ("opentelemetry-instrumentation-fastapi", "opentelemetry-instrumentation-httpx")


def _otel_contrib_for(core: str) -> str:
    """Core ``1.Y.Z`` ships with contrib ``0.(Y+21)bZ``, which pins the core exactly."""
    _, minor, patch = (int(part) for part in core.split("."))
    return f"0.{minor + 21}b{patch}"


@pytest.mark.parametrize("path", _OTEL_REQUIREMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_opentelemetry_family_moves_in_lockstep(path: Path):
    """#633 bumped the core to 1.45.1 and left contrib on 0.66b0, which pins
    ``opentelemetry-api==1.45.0``: every backend install became unresolvable
    while the root suite, which installs 1.45.0, stayed green."""
    lines = {re.split(r"[<>=!~ ;\[]", line, maxsplit=1)[0].lower(): line for line in _requirement_lines(path)}
    floors = set()
    for name in _OTEL_CORE:
        match = re.fullmatch(rf"{name}>=(\d+\.\d+\.\d+),<\d+\.\d+", lines.get(name, ""))
        assert match, f"{path.name}: {name} must be pinned as >=1.Y.Z,<1.(Y+1)"
        floors.add(match.group(1))
    assert len(floors) == 1, f"{path.name}: the core packages disagree: {sorted(floors)}"
    contrib = _otel_contrib_for(floors.pop())
    for name in _OTEL_CONTRIB:
        assert lines.get(name) == f"{name}=={contrib}", f"{path.name}: {name} must be =={contrib}"


def test_backend_lockfile_satisfies_its_requirements():
    """The lock is compiled from App/backend/requirements.txt (see its header).
    Dependabot edits it in place, and it had drifted below its own floors
    (Pillow 12.3.0 under ``Pillow>=13.0.0``, contrib 0.66b0 under api 1.45.1)."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    lock_text = (ROOT / "App" / "backend" / "requirements.lock.txt").read_text(encoding="utf-8")
    pinned = {
        canonicalize_name(name): version
        for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", lock_text, re.M)
    }
    env = {"python_version": "3.14", "python_full_version": "3.14.0", "sys_platform": "linux",
           "platform_system": "Linux", "platform_machine": "x86_64", "os_name": "posix",
           "implementation_name": "cpython", "platform_python_implementation": "CPython", "extra": ""}
    semconv = pinned.get("opentelemetry-semantic-conventions")
    assert semconv == _otel_contrib_for(pinned["opentelemetry-api"]), "lock: OpenTelemetry contrib/core mismatch"
    for line in _requirement_lines(ROOT / "App" / "backend" / "requirements.txt"):
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        version = pinned.get(canonicalize_name(req.name))
        assert version is not None, f"lock is missing {req.name}"
        assert req.specifier.contains(version, prereleases=True), f"lock pins {req.name}=={version}, outside {req}"


def test_dependabot_automerge_waits_for_required_checks():
    """``gh pr merge --auto`` waits only for required checks; with none it merges at once."""
    doc = _load(ROOT / ".github" / "workflows" / "dependabot-automerge.yml")
    steps = doc["jobs"]["dependabot-automerge"]["steps"]
    gate = next(step for step in steps if step.get("id") == "gate")
    assert "required_status_checks" in gate["run"]
    for step in steps:
        if "gh pr merge" in str(step.get("run", "")):
            assert "steps.gate.outputs.required == 'true'" in str(step.get("if", "")), (
                f"{step.get('name')}: auto-merge must be gated on the base branch requiring checks"
            )


def test_crane_cloud_requirements_install_on_the_python_they_ship_with():
    """#633 raised numpy to >=2.5.4, which needs Python 3.12, in a file the
    production image and CI install on 3.11 — so neither could install it."""
    from packaging.requirements import Requirement

    dockerfile = (ROOT / "App" / "Dockerfile.cranecloud").read_text(encoding="utf-8")
    image_python = set(re.findall(r"^FROM\s+python:(\d+\.\d+)", dockerfile, re.M))
    ci_python = _load(ROOT / ".github" / "workflows" / "ci-ml-pipeline.yml")["env"]["PYTHON_VERSION"]
    assert image_python == {str(ci_python)}, "the Crane Cloud image and CI must test the same Python"
    if image_python == {"3.11"}:
        lines = _requirement_lines(ROOT / "App" / "backend" / "requirements-cranecloud.txt")
        numpy = Requirement(next(line for line in lines if line.lower().startswith("numpy")))
        assert not numpy.specifier.contains("2.5.0"), f"{numpy}: numpy 2.5 requires Python 3.12"
        assert numpy.specifier.contains("1.26.4"), f"{numpy}: CI shares an env with the root numpy==1.26.4"


def test_each_requirements_file_is_audited_on_the_python_it_ships_on():
    """On the wrong Python pip-audit cannot resolve the file and scans nothing."""
    audit = _load(ROOT / ".github" / "workflows" / "devsecops-sast-dast.yml")["jobs"]["pip-audit"]
    pairs = {entry["requirements"]: str(entry["python"]) for entry in audit["strategy"]["matrix"]["include"]}
    ci_python = str(_load(ROOT / ".github" / "workflows" / "ci-ml-pipeline.yml")["env"]["PYTHON_VERSION"])
    api_python = set(re.findall(r"^FROM\s+python:(\d+\.\d+)", (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.M))
    assert pairs == {"requirements.txt": ci_python, "App/backend/requirements.txt": api_python.pop()}
