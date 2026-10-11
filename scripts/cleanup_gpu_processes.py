#!/usr/bin/env python3
"""Complete GPU Cleanup Utility for ~/Mpairwe7 Workspaces.

Guarantees:
  - Scans active GPU compute applications across GPUs 0-7.
  - Frees EVERY byte of VRAM this project holds, from both sources:
      * host processes whose working directory or command line is under
        ~/Mpairwe7 (or an agent scratchpad worktree named after it);
      * Docker containers composed from ~/Mpairwe7 (matched by the compose
        working-dir label, so profile-gated services such as ``orpheus-tts``
        and orphans from other compose chains are included). A container's
        GPU processes show a container-side cwd such as ``/app``, so a path
        match on the PID alone never finds them.
  - Re-queries nvidia-smi afterwards and exits non-zero if anything of this
    project is still on a GPU.
  - Leaves other users' and other projects' processes and containers untouched.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TARGET_PREFIX = "/home/developer/Mpairwe7"
# Agent scratchpad worktrees live outside TARGET_PREFIX but encode it in the name.
SCRATCHPAD_MARKER = "Mpairwe7-FinalYearProject"
COMPOSE_WORKDIR_LABEL = "com.docker.compose.project.working_dir"

TERM_GRACE_S = 10.0
RELEASE_WAIT_S = 15.0
CONTAINER_STOP_TIMEOUT_S = 30

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


class ContainerLookupError(RuntimeError):
    """Docker could not be queried, so container GPU processes cannot be attributed."""


def _protected_pids() -> set[int]:
    """This process plus every ancestor, which must never be terminated."""
    protected = {os.getpid(), os.getppid()}
    pid = os.getppid()
    # Walk up to init. Bounded so a malformed /proc cannot spin here.
    for _ in range(32):
        if pid <= 1:
            break
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            # comm can contain spaces and parentheses; ppid is the field after
            # the closing paren and the state character.
            pid = int(stat[stat.rindex(")") + 2:].split()[1])
        except Exception:
            break
        protected.add(pid)
    return protected


def _owned_by_project(text: str) -> bool:
    return text.startswith(TARGET_PREFIX) or SCRATCHPAD_MARKER in text


def is_process_in_target_dir(pid: int) -> tuple[bool, str]:
    """Check if the PID originates from /home/developer/Mpairwe7."""
    cwd_path = ""
    cmdline = ""
    try:
        cwd_link = Path(f"/proc/{pid}/cwd")
        if cwd_link.exists():
            cwd_path = str(cwd_link.resolve())
            if _owned_by_project(cwd_path):
                return True, f"cwd: {cwd_path}"
    except Exception:
        pass

    try:
        cmdline_file = Path(f"/proc/{pid}/cmdline")
        if cmdline_file.exists():
            cmdline = cmdline_file.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            if TARGET_PREFIX in cmdline or SCRATCHPAD_MARKER in cmdline:
                return True, f"cmdline: {cmdline[:80]}..."
    except Exception:
        pass

    return False, f"external ({cwd_path or 'unknown'})"


def _compute_apps() -> list[dict[str, Any]]:
    """Every process nvidia-smi reports on any GPU: pid, gpu name, VRAM MiB."""
    out = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,gpu_name,used_memory",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip().splitlines()
    apps: list[dict[str, Any]] = []
    for line in out:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            apps.append({"pid": int(parts[0]), "gpu": parts[1], "vram_mb": parts[2]})
        except ValueError:
            continue
    return apps


def _container_id_for_pid(pid: int) -> str | None:
    """Docker container id owning a host PID, from its cgroup path."""
    try:
        match = _CONTAINER_ID.search(Path(f"/proc/{pid}/cgroup").read_text())
    except OSError:
        return None
    return match.group(0) if match else None


def _project_containers() -> dict[str, dict[str, Any]]:
    """Container id -> {name, wants_gpu, running} for containers composed from ~/Mpairwe7.

    Raises ContainerLookupError rather than returning {}: an empty answer would
    make every container GPU process look external and the cleanup would
    silently free nothing.
    """
    try:
        ids = subprocess.run(
            ["docker", "ps", "-aq", "--no-trunc"], capture_output=True, text=True, check=True
        ).stdout.split()
        if not ids:
            return {}
        fmt = (
            "{{.Id}}\t{{.Name}}\t"
            f'{{{{index .Config.Labels "{COMPOSE_WORKDIR_LABEL}"}}}}\t'
            "{{if .HostConfig.DeviceRequests}}1{{else}}0{{end}}\t{{.State.Running}}"
        )
        out = subprocess.run(
            ["docker", "inspect", "--format", fmt, *ids], capture_output=True, text=True, check=True
        ).stdout
    except OSError as ex:
        raise ContainerLookupError(str(ex)) from ex
    except subprocess.CalledProcessError as ex:
        detail = (ex.stderr or "").strip().splitlines()[:1]
        raise ContainerLookupError(detail[0] if detail else f"docker exited {ex.returncode}") from ex
    found: dict[str, dict[str, Any]] = {}
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 5 or not _owned_by_project(parts[2]):
            continue
        found[parts[0]] = {
            "name": parts[1].lstrip("/"),
            "wants_gpu": parts[3] == "1",
            "running": parts[4] == "true",
        }
    return found


def _stop_containers(containers: dict[str, dict[str, Any]]) -> None:
    """Stop then remove, so a restart policy cannot bring the VRAM back."""
    ids = list(containers)
    subprocess.run(["docker", "stop", "-t", str(CONTAINER_STOP_TIMEOUT_S), *ids], check=False)
    subprocess.run(["docker", "rm", "-f", *ids], check=False)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate(pids: list[int]) -> dict[int, str]:
    """SIGTERM, wait out the grace period, SIGKILL survivors."""
    status: dict[int, str] = {}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            status[pid] = "TERMINATED"
        except ProcessLookupError:
            status[pid] = "ALREADY_DEAD"
        except Exception as ex:
            status[pid] = f"FAILED: {ex}"
    deadline = time.monotonic() + TERM_GRACE_S
    live = [p for p, s in status.items() if s == "TERMINATED"]
    while live and time.monotonic() < deadline:
        time.sleep(0.5)
        live = [p for p in live if _pid_alive(p)]
    for pid in live:
        try:
            os.kill(pid, signal.SIGKILL)
            status[pid] = "KILLED"
        except ProcessLookupError:
            pass
        except Exception as ex:
            status[pid] = f"FAILED: {ex}"
    return status


def _project_holders(
    apps: list[dict[str, Any]], containers: dict[str, dict[str, Any]], protected: set[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split GPU apps into (host processes, project-container processes) owned by this project."""
    host: list[dict[str, Any]] = []
    boxed: list[dict[str, Any]] = []
    for app in apps:
        pid = app["pid"]
        if pid in protected:
            print(f"[SKIP] PID {pid} is this process or an ancestor")
            continue
        container_id = _container_id_for_pid(pid)
        if container_id is not None:
            if container_id in containers:
                boxed.append({**app, "container": containers[container_id]["name"], "container_id": container_id})
            else:
                print(f"[SKIP] External container PID {pid} ({app['gpu']}, {app['vram_mb']} MiB)")
            continue
        is_target, reason = is_process_in_target_dir(pid)
        if is_target:
            host.append({**app, "reason": reason})
        else:
            print(f"[SKIP] External PID {pid} ({app['gpu']}, {app['vram_mb']} MiB) -> {reason}")
    return host, boxed


def project_vram_remaining() -> list[dict[str, Any]]:
    """GPU apps that still belong to this project (empty means fully clean)."""
    apps = _compute_apps()
    containers = _project_containers()  # raises ContainerLookupError; never reports a false "clean"
    protected = _protected_pids()
    host, boxed = [], []
    for app in apps:
        pid = app["pid"]
        if pid in protected:
            continue
        container_id = _container_id_for_pid(pid)
        if container_id is not None:
            if container_id in containers:
                boxed.append(app)
        elif is_process_in_target_dir(pid)[0]:
            host.append(app)
    return host + boxed


def cleanup_mpairwe7_gpu_processes(dry_run: bool = False) -> list[dict[str, Any]]:
    """Free all VRAM held by this project and verify it; returns one entry per holder.

    Any entry whose status is ``STILL_ON_GPU`` means the cleanup was incomplete.
    """
    cleaned: list[dict[str, Any]] = []
    try:
        apps = _compute_apps()
    except Exception as ex:
        print(f"Error checking GPU compute applications: {ex}")
        return cleaned

    lookup_error: ContainerLookupError | None = None
    try:
        containers = _project_containers()
    except ContainerLookupError as ex:
        containers, lookup_error = {}, ex
        print(f"[ERROR] Docker lookup failed, container GPU processes cannot be attributed: {ex}")
    host, boxed = _project_holders(apps, containers, _protected_pids())
    if lookup_error is not None:
        for app in apps:
            if _container_id_for_pid(app["pid"]) is not None:
                cleaned.append({"pid": app["pid"], "gpu": app["gpu"], "vram_mb": app["vram_mb"],
                                "reason": f"container ownership unknown ({lookup_error})",
                                "status": "STILL_ON_GPU"})

    # Running GPU-requesting containers may not hold VRAM yet (still loading
    # weights) but will the moment they finish, so they go too.
    doomed: dict[str, dict[str, Any]] = {
        cid: meta
        for cid, meta in containers.items()
        if (meta["wants_gpu"] and meta["running"]) or any(b["container_id"] == cid for b in boxed)
    }

    for app in boxed:
        print(f"[STOP] Container {app['container']} PID {app['pid']} ({app['gpu']}, {app['vram_mb']} MiB)")
    for cid, meta in doomed.items():
        if not any(b["container_id"] == cid for b in boxed):
            print(f"[STOP] Container {meta['name']} (GPU requested, no VRAM held yet)")
    for app in host:
        print(f"[KILL] Target PID {app['pid']} ({app['gpu']}, {app['vram_mb']} MiB) -> {app['reason']}")

    if dry_run:
        return cleaned

    if doomed:
        _stop_containers(doomed)
    for app in boxed:
        cleaned.append({"pid": app["pid"], "gpu": app["gpu"], "vram_mb": app["vram_mb"],
                        "reason": f"container: {app['container']}", "status": "CONTAINER_REMOVED"})
    statuses = _terminate([a["pid"] for a in host])
    for app in host:
        cleaned.append({"pid": app["pid"], "gpu": app["gpu"], "vram_mb": app["vram_mb"],
                        "reason": app["reason"], "status": statuses[app["pid"]]})

    if lookup_error is not None:
        return cleaned  # container holders already flagged above; nothing can be verified

    # Verify: the driver can take a moment to release a dead process's memory.
    deadline = time.monotonic() + RELEASE_WAIT_S
    try:
        leftover = project_vram_remaining()
        while leftover and time.monotonic() < deadline:
            time.sleep(1.0)
            leftover = project_vram_remaining()
    except ContainerLookupError as ex:
        print(f"[ERROR] Could not verify that VRAM was released: {ex}")
        cleaned.append({"pid": 0, "gpu": "", "vram_mb": "", "reason": f"verification failed ({ex})",
                        "status": "STILL_ON_GPU"})
        return cleaned
    for app in leftover:
        print(f"[LEFTOVER] PID {app['pid']} ({app['gpu']}, {app['vram_mb']} MiB) still on a GPU")
        cleaned.append({"pid": app["pid"], "gpu": app["gpu"], "vram_mb": app["vram_mb"],
                        "reason": "still reported by nvidia-smi", "status": "STILL_ON_GPU"})
    return cleaned


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(f"Scanning GPU processes and containers strictly inside {TARGET_PREFIX} (dry_run={dry})...")
    res = cleanup_mpairwe7_gpu_processes(dry_run=dry)
    stuck = [r for r in res if r["status"] == "STILL_ON_GPU"]
    print(f"Cleanup finished: {len(res) - len(stuck)} holder(s) released, {len(stuck)} still on a GPU.")
    sys.exit(1 if stuck else 0)
