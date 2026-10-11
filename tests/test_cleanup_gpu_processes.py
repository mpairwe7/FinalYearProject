"""scripts/cleanup_gpu_processes.py must leave none of this project's VRAM behind.

The original matched GPU PIDs by cwd/cmdline only. A container's GPU process has
a container-side cwd (``/app``), so every compose-stack process was skipped as
"external" and the whole stack stayed in VRAM.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts import cleanup_gpu_processes as cg

PROJECT_CID = "a" * 64
ORPHEUS_CID = "b" * 64
OTHER_CID = "c" * 64


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake box: GPU apps, containers, and a record of what was stopped/killed."""
    state: dict[str, Any] = {
        "apps": [],
        "containers": {},
        "cgroup": {},      # pid -> container id
        "host_owned": {},  # pid -> bool (is_process_in_target_dir)
        "stopped": [],
        "terminated": [],
    }

    def stop(containers: dict[str, dict[str, Any]]) -> None:
        state["stopped"].extend(containers)
        gone = {pid for pid, cid in state["cgroup"].items() if cid in containers}
        state["apps"] = [a for a in state["apps"] if a["pid"] not in gone]

    def terminate(pids: list[int]) -> dict[int, str]:
        state["terminated"].extend(pids)
        state["apps"] = [a for a in state["apps"] if a["pid"] not in pids]
        return {p: "TERMINATED" for p in pids}

    monkeypatch.setattr(cg, "_compute_apps", lambda: list(state["apps"]))
    monkeypatch.setattr(cg, "_project_containers", lambda: dict(state["containers"]))
    monkeypatch.setattr(cg, "_container_id_for_pid", lambda pid: state["cgroup"].get(pid))
    monkeypatch.setattr(
        cg, "is_process_in_target_dir",
        lambda pid: (state["host_owned"].get(pid, False), "test"),
    )
    monkeypatch.setattr(cg, "_stop_containers", stop)
    monkeypatch.setattr(cg, "_terminate", terminate)
    monkeypatch.setattr(cg, "_protected_pids", lambda: {1})
    monkeypatch.setattr(cg, "RELEASE_WAIT_S", 0.0)
    return state


def _app(pid: int, mb: str = "1000") -> dict[str, Any]:
    return {"pid": pid, "gpu": "NVIDIA RTX A6000", "vram_mb": mb}


def test_project_container_process_is_released_and_external_one_is_not(world: dict[str, Any]) -> None:
    world["apps"] = [_app(100, "25000"), _app(200, "23000")]
    world["cgroup"] = {100: PROJECT_CID, 200: OTHER_CID}
    world["containers"] = {PROJECT_CID: {"name": "ura-app-vllm-sunflower", "wants_gpu": True, "running": True}}

    result = cg.cleanup_mpairwe7_gpu_processes()

    assert world["stopped"] == [PROJECT_CID]
    assert [r["pid"] for r in result] == [100]
    assert result[0]["status"] == "CONTAINER_REMOVED"
    assert [a["pid"] for a in world["apps"]] == [200]  # the other project's vLLM is untouched


def test_running_gpu_container_with_no_vram_yet_is_stopped(world: dict[str, Any]) -> None:
    """Orpheus mid-load: it holds nothing now but will the moment weights finish."""
    world["containers"] = {ORPHEUS_CID: {"name": "ura-app-orpheus-tts", "wants_gpu": True, "running": True}}

    cg.cleanup_mpairwe7_gpu_processes()

    assert world["stopped"] == [ORPHEUS_CID]


def test_cpu_only_project_container_is_left_alone(world: dict[str, Any]) -> None:
    world["containers"] = {PROJECT_CID: {"name": "ura-app-redis", "wants_gpu": False, "running": True}}

    cg.cleanup_mpairwe7_gpu_processes()

    assert world["stopped"] == []


def test_host_process_from_scratchpad_worktree_is_terminated(world: dict[str, Any]) -> None:
    world["apps"] = [_app(300), _app(400)]
    world["host_owned"] = {300: True, 400: False}

    result = cg.cleanup_mpairwe7_gpu_processes()

    assert world["terminated"] == [300]
    assert [a["pid"] for a in world["apps"]] == [400]
    assert result[0]["status"] == "TERMINATED"


def test_dry_run_changes_nothing(world: dict[str, Any]) -> None:
    world["apps"] = [_app(100), _app(300)]
    world["cgroup"] = {100: PROJECT_CID}
    world["host_owned"] = {300: True}
    world["containers"] = {PROJECT_CID: {"name": "ura-app-api", "wants_gpu": True, "running": True}}

    result = cg.cleanup_mpairwe7_gpu_processes(dry_run=True)

    assert result == []
    assert world["stopped"] == [] and world["terminated"] == []
    assert len(world["apps"]) == 2


def test_protected_pids_are_never_terminated(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cg, "_protected_pids", lambda: {300})
    world["apps"] = [_app(300)]
    world["host_owned"] = {300: True}

    cg.cleanup_mpairwe7_gpu_processes()

    assert world["terminated"] == []


def test_leftover_vram_is_reported_not_swallowed(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A holder that survives termination must surface as STILL_ON_GPU (exit 1)."""
    world["apps"] = [_app(300, "8000")]
    world["host_owned"] = {300: True}
    monkeypatch.setattr(cg, "_terminate", lambda pids: {p: "TERMINATED" for p in pids})  # but it lingers

    result = cg.cleanup_mpairwe7_gpu_processes()

    stuck = [r for r in result if r["status"] == "STILL_ON_GPU"]
    assert [r["pid"] for r in stuck] == [300]


def test_docker_lookup_failure_is_never_reported_as_clean(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The first live run swallowed an inspect error and reported '0 still on a GPU'."""

    def broken() -> dict[str, dict[str, Any]]:
        raise cg.ContainerLookupError("len of nil pointer")

    monkeypatch.setattr(cg, "_project_containers", broken)
    world["apps"] = [_app(100, "25000"), _app(300, "8000")]
    world["cgroup"] = {100: PROJECT_CID}
    world["host_owned"] = {300: True}

    result = cg.cleanup_mpairwe7_gpu_processes()

    stuck = [r for r in result if r["status"] == "STILL_ON_GPU"]
    assert [r["pid"] for r in stuck] == [100]          # container holder: ownership unknown
    assert world["stopped"] == []                       # nothing guessed at
    assert 300 in world["terminated"]                   # host-side match still acted on


def test_docker_lookup_failure_with_no_container_holders_still_cleans_host(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> dict[str, dict[str, Any]]:
        raise cg.ContainerLookupError("docker not installed")

    monkeypatch.setattr(cg, "_project_containers", broken)
    world["apps"] = [_app(300)]
    world["host_owned"] = {300: True}

    result = cg.cleanup_mpairwe7_gpu_processes()

    assert [r["status"] for r in result] == ["TERMINATED"]


def test_clean_box_reports_nothing_remaining(world: dict[str, Any]) -> None:
    world["apps"] = [_app(200)]
    world["cgroup"] = {200: OTHER_CID}

    assert cg.project_vram_remaining() == []
