"""Every Compose build names a Dockerfile that exists (ADR-0064).

`docker compose config` does not check that, so moving the root Dockerfile to
App/ left four builds pointing at a file that was gone, and nothing failed.
"""

from __future__ import annotations

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
