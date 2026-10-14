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
    project is still on a GPU, if a container would not stop, or if either
    nvidia-smi or Docker could not be queried (never a silent "clean").
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
COMPOSE_WORKDIR_LABEL = "com.docker.compose.project.working_dir"

# Boundary-anchored: /home/developer/Mpairwe7-other or /srv/home/developer/Mpairwe7
# are not this project, and this utility kills what it matches.
_PROJECT_PATH = re.compile(r"(?<![\w.\-])" + re.escape(TARGET_PREFIX) + r"(?![\w.\-])")
# Agent scratchpad worktrees live outside TARGET_PREFIX; the directory name is the
# project path with "/" flattened to "-", e.g. /-home-developer-Mpairwe7-FinalYearProject-App/.
_SCRATCHPAD_PATH = re.compile(r"/-home-developer-Mpairwe7-FinalYearProject(?:-[^/\s]*)?(?:/|$)")

QUERY_TIMEOUT_S = 30
TERM_GRACE_S = 10.0
RELEASE_WAIT_S = 15.0
CONTAINER_STOP_TIMEOUT_S = 30

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


class VerificationError(RuntimeError):
    """Something needed to attribute or verify GPU memory could not be queried."""


class ContainerLookupError(VerificationError):
    """Docker could not be queried, so container GPU processes cannot be attributed."""


class GpuQueryError(VerificationError):
    """nvidia-smi could not be queried; ``missing`` means the binary is absent (no NVIDIA GPU)."""

    def __init__(self, message: str, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


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
    return bool(_PROJECT_PATH.search(text) or _SCRATCHPAD_PATH.search(text))


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
            if _owned_by_project(cmdline):
                return True, f"cmdline: {cmdline[:80]}..."
    except Exception:
        pass

    return False, f"external ({cwd_path or 'unknown'})"


def _compute_apps() -> list[dict[str, Any]]:
    """Every process nvidia-smi reports on any GPU: pid, gpu name, VRAM MiB."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,gpu_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, check=True, timeout=QUERY_TIMEOUT_S,
        ).stdout.strip().splitlines()
    except FileNotFoundError as ex:
        raise GpuQueryError(f"nvidia-smi not found: {ex}", missing=True) from ex
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise GpuQueryError(str(ex)) from ex
    except subprocess.CalledProcessError as ex:
        detail = (ex.stderr or "").strip().splitlines()[:1]
        raise GpuQueryError(detail[0] if detail else f"nvidia-smi exited {ex.returncode}") from ex
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
            ["docker", "ps", "-aq", "--no-trunc"],
            capture_output=True, text=True, check=True, timeout=QUERY_TIMEOUT_S,
        ).stdout.split()
        if not ids:
            return {}
        fmt = (
            "{{.Id}}\t{{.Name}}\t"
            f'{{{{index .Config.Labels "{COMPOSE_WORKDIR_LABEL}"}}}}\t'
            "{{if .HostConfig.DeviceRequests}}1{{else}}0{{end}}\t{{.State.Running}}"
        )
        out = subprocess.run(
            ["docker", "inspect", "--format", fmt, *ids],
            capture_output=True, text=True, check=True, timeout=QUERY_TIMEOUT_S,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as ex:
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


def _stop_containers(containers: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Stop then remove each container, so a restart policy cannot bring the VRAM back.

    Returns {container id: reason} for every container that could not be removed.
    """
    failed: dict[str, str] = {}
    for cid, meta in containers.items():
        name = meta["name"]
        try:
            steps = (
                ("stop", subprocess.run(
                    ["docker", "stop", "-t", str(CONTAINER_STOP_TIMEOUT_S), cid],
                    capture_output=True, text=True, timeout=CONTAINER_STOP_TIMEOUT_S + QUERY_TIMEOUT_S,
                )),
                ("rm", subprocess.run(
                    ["docker", "rm", "-f", cid],
                    capture_output=True, text=True, timeout=QUERY_TIMEOUT_S,
                )),
            )
        except (OSError, subprocess.TimeoutExpired) as ex:
            failed[cid] = f"{name}: {ex}"
            continue
        for step, result in steps:
            if result.returncode != 0 and "No such container" not in result.stderr:
                failed[cid] = f"{name}: docker {step} failed: {result.stderr.strip()[:200]}"
                break
    return failed


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
    """GPU apps that still belong to this project (empty means fully clean).

    Raises VerificationError when nvidia-smi or Docker cannot be queried, so a
    failed check is never mistaken for a clean one.
    """
    apps = _compute_apps()
    containers = _project_containers()
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


def _entry(app: dict[str, Any] | None, reason: str, status: str) -> dict[str, Any]:
    app = app or {}
    return {"pid": app.get("pid", 0), "gpu": app.get("gpu", ""), "vram_mb": app.get("vram_mb", ""),
            "reason": reason, "status": status}


def cleanup_mpairwe7_gpu_processes(dry_run: bool = False) -> list[dict[str, Any]]:
    """Free all VRAM held by this project and verify it; returns one entry per holder.

    Any entry whose status is ``STILL_ON_GPU`` means the cleanup was incomplete or
    could not be verified.
    """
    cleaned: list[dict[str, Any]] = []
    try:
        apps = _compute_apps()
    except GpuQueryError as ex:
        if ex.missing:
            print(f"No NVIDIA GPU to clean: {ex}")
            return cleaned
        print(f"[ERROR] Could not enumerate GPU processes: {ex}")
        cleaned.append(_entry(None, f"GPU enumeration failed ({ex})", "STILL_ON_GPU"))
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
                cleaned.append(_entry(app, f"container ownership unknown ({lookup_error})", "STILL_ON_GPU"))

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

    stop_failed = _stop_containers(doomed) if doomed else {}
    for app in boxed:
        why = stop_failed.get(app["container_id"])
        cleaned.append(_entry(app, why or f"container: {app['container']}",
                              "STILL_ON_GPU" if why else "CONTAINER_REMOVED"))
    for cid, why in stop_failed.items():
        print(f"[ERROR] {why}")
        if not any(b["container_id"] == cid for b in boxed):
            cleaned.append(_entry(None, why, "STILL_ON_GPU"))
    statuses = _terminate([a["pid"] for a in host])
    for app in host:
        cleaned.append(_entry(app, app["reason"], statuses[app["pid"]]))

    if lookup_error is not None:
        return cleaned  # container holders already flagged above; nothing can be verified

    # Verify: the driver can take a moment to release a dead process's memory.
    deadline = time.monotonic() + RELEASE_WAIT_S
    try:
        leftover = project_vram_remaining()
        while leftover and time.monotonic() < deadline:
            time.sleep(1.0)
            leftover = project_vram_remaining()
    except VerificationError as ex:
        print(f"[ERROR] Could not verify that VRAM was released: {ex}")
        cleaned.append(_entry(None, f"verification failed ({ex})", "STILL_ON_GPU"))
        return cleaned
    flagged = {r["pid"] for r in cleaned if r["status"] == "STILL_ON_GPU"}
    for app in leftover:
        if app["pid"] in flagged:
            continue
        print(f"[LEFTOVER] PID {app['pid']} ({app['gpu']}, {app['vram_mb']} MiB) still on a GPU")
        cleaned.append(_entry(app, "still reported by nvidia-smi", "STILL_ON_GPU"))
    return cleaned


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(f"Scanning GPU processes and containers strictly inside {TARGET_PREFIX} (dry_run={dry})...")
    res = cleanup_mpairwe7_gpu_processes(dry_run=dry)
    stuck = [r for r in res if r["status"] == "STILL_ON_GPU"]
    print(f"Cleanup finished: {len(res) - len(stuck)} holder(s) released, {len(stuck)} still on a GPU.")
    sys.exit(1 if stuck else 0)
