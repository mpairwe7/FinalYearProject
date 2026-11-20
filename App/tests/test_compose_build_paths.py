"""Every Compose build names a Dockerfile that exists, and the full stack is one overlay (ADR-0064).

`docker compose config` does not check that a Dockerfile exists, so moving the
root Dockerfile to App/ left four builds pointing at a file that was gone, and
nothing failed.

`docker-compose.full.yml` used to define the base's four services a second
time. It is now an overlay on `docker-compose.yml`, and the tests at the end
keep it one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[1]
COMPOSE_FILES = sorted(APP.glob("docker-compose*.yml"))


class _Loader(yaml.SafeLoader):
    """Compose's own tags (`!override`, `!reset`) are read as the plain value."""


def _plain(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_Loader.add_multi_constructor("!", _plain)


def _services(path: Path) -> dict:
    return (yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}).get("services") or {}  # noqa: S506


def _build(service: dict | None) -> dict:
    build = (service or {}).get("build") or {}
    return {"context": build} if isinstance(build, str) else build


def _builds() -> list[tuple[str, str, str, str]]:
    """(file, service, context, dockerfile); an overlay inherits the context it leaves out."""
    builds = [(path.name, name, _build(service)) for path in COMPOSE_FILES for name, service in _services(path).items()]
    # The file that gives a service its context is the one an overlay of that service layers on.
    contexts = {name: build["context"] for _file, name, build in reversed(builds) if build.get("context")}
    return [
        (file, name, build.get("context") or contexts.get(name, "."), build.get("dockerfile") or "Dockerfile")
        for file, name, build in builds
        if build
    ]


def test_there_are_builds_to_check() -> None:
    assert len(_builds()) >= 8


@pytest.mark.parametrize(("compose", "service", "context", "dockerfile"), _builds())
def test_the_dockerfile_of_every_build_exists(compose: str, service: str, context: str, dockerfile: str) -> None:
    # Compose resolves a context against the first file's folder, and every stack here starts in App/.
    target = (APP / context / dockerfile).resolve()
    assert target.is_file(), f"{compose}: service {service} builds from {target}, which does not exist"


BASE, FULL = APP / "docker-compose.yml", APP / "docker-compose.full.yml"
# `docker-compose.local-sunflower.yml` defines its own `vllm` on the base, and Compose would merge the two.
ONLY_IN_THE_OVERLAY = {"vllm"}


def test_the_full_file_only_changes_services_the_base_defines() -> None:
    assert set(_services(FULL)) - set(_services(BASE)) == ONLY_IN_THE_OVERLAY


def test_the_full_file_repeats_nothing_the_base_says() -> None:
    base = _services(BASE)
    repeated = [
        f"{name}.{key}"
        for name, service in _services(FULL).items()
        for key, value in service.items()
        if key in base.get(name, {}) and base[name][key] == value
    ]
    assert not repeated, f"already set by docker-compose.yml: {repeated}"


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], capture_output=True, text=True, timeout=120, check=False)  # noqa: S603, S607


def _has_compose() -> bool:
    return shutil.which("docker") is not None and _compose("version").returncode == 0


def _resolved_full_stack(tmp_path: Path) -> dict:
    """The model Compose builds from the pair, read beside an empty `.env` so no real setting is loaded."""
    tree = tmp_path / "App"
    tree.mkdir()
    (tree / ".env").write_text("")
    for path in (BASE, FULL):
        shutil.copy(path, tree / path.name)
    done = _compose("-f", str(tree / BASE.name), "-f", str(tree / FULL.name), "config", "--no-interpolate", "--format", "json")
    assert done.returncode == 0, done.stderr[-400:]
    return json.loads(done.stdout)["services"]


# CI always has Compose, so there the test runs or fails. It never skips unseen.
@pytest.mark.skipif(not os.environ.get("CI") and not _has_compose(), reason="needs Docker Compose to merge the two files")
def test_the_full_stack_resolves_to_the_hardened_variant(tmp_path: Path) -> None:
    services = _resolved_full_stack(tmp_path)
    overlay = _services(FULL)

    for name in ("redis", "qdrant", "api", "frontend"):
        assert services[name]["cap_drop"] == ["ALL"], name
    for name in ("redis", "api", "frontend"):
        assert "no-new-privileges:true" in services[name]["security_opt"], name
    for name in ("api", "frontend"):
        assert services[name]["read_only"] is True, name
    assert services["redis"]["user"] == "999:1000"
    assert [volume["target"] for volume in services["qdrant"]["volumes"]] == ["/qdrant/storage"]

    # `!override` replaces the base's lists. Without it Compose appends, and the base's settings leak in.
    api = services["api"]
    assert api["environment"] == overlay["api"]["environment"]
    assert api["ports"] == ["${API_PORT:-8000}:8000"]
    assert services["frontend"]["ports"] == ["${FRONTEND_PORT:-3000}:3000"]
    # `!reset` drops the base's GPU reservation and host alias, which this variant never had.
    assert "deploy" not in api
    assert "extra_hosts" not in api


def test_every_compose_file_reads_the_settings_file_inside_app() -> None:
    """The working settings file is `App/.env`. An `env_file` that climbs out of `App/` names the
    repository root, where the file no longer is, and Compose refuses to start without it."""
    climbing = []
    for path in sorted(APP.rglob("docker-compose*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"\s*-\s*\.\./.*\.env\b", line) or re.match(r"\s*env_file:\s*\.\./", line):
                climbing.append(f"{path.relative_to(APP)}: {line.strip()}")
    assert climbing == []


def test_a_build_from_the_repository_root_cannot_take_the_settings_file() -> None:
    """A `.dockerignore` pattern is anchored at the context root. `.env` alone excluded only a
    root-level file, which would have left `App/.env` inside the context of the API image."""
    patterns = (APP.parent / ".dockerignore").read_text(encoding="utf-8").split()
    assert "**/.env" in patterns and "**/.env.*" in patterns


def test_the_backend_reads_app_env_from_any_working_directory() -> None:
    from app.providers.config import ENV_FILE

    assert ENV_FILE == APP / ".env"
