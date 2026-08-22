#!/usr/bin/env python3
"""Safe GPU Process Cleanup Utility for ~/Mpairwe7 Workspaces.

Guarantees:
  - Scans active GPU compute applications across GPUs 0-7.
  - ONLY terminates processes whose working directory or command line originates from ~/Mpairwe7.
  - Leaves external system services, Triton servers, VLLM engines, and other users untouched.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

TARGET_PREFIX = "/home/developer/Mpairwe7"


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


def is_process_in_target_dir(pid: int) -> tuple[bool, str]:
    """Check if the PID originates from /home/developer/Mpairwe7."""
    cwd_path = ""
    cmdline = ""
    try:
        cwd_link = Path(f"/proc/{pid}/cwd")
        if cwd_link.exists():
            cwd_path = str(cwd_link.resolve())
            if cwd_path.startswith(TARGET_PREFIX):
                return True, f"cwd: {cwd_path}"
    except Exception:
        pass

    try:
        cmdline_file = Path(f"/proc/{pid}/cmdline")
        if cmdline_file.exists():
            cmdline = cmdline_file.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            if TARGET_PREFIX in cmdline:
                return True, f"cmdline: {cmdline[:80]}..."
    except Exception:
        pass

    return False, f"external ({cwd_path or 'unknown'})"


def cleanup_mpairwe7_gpu_processes(dry_run: bool = False) -> list[dict[str, Any]]:
    """Find and clean up GPU processes strictly originating from ~/Mpairwe7."""
    cleaned = []
    try:
        cmd = [
            "nvidia-smi",
            "--query-compute-apps=pid,gpu_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True).strip().splitlines()
        
        for line in out:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                
                gpu_name = parts[1]
                vram_mb = parts[2]
                
                # Never terminate the caller or its ancestors. The benchmark
                # scripts call this from inside their own process, and a
                # benchmark that has just loaded models onto the GPU is itself
                # a ~/Mpairwe7 GPU process — so this used to SIGTERM the very
                # run that invoked it, killing it between the cleanup step and
                # the "save JSON report" step that follows. The reports still
                # appeared historically only because those runs had silently
                # fallen back to CPU (a CUDA-13 torch wheel on a 12.x driver),
                # which kept them out of nvidia-smi's compute-app list
                # entirely. Fixing the GPU fallback exposed the self-kill.
                if pid in _protected_pids():
                    print(f"[SKIP] PID {pid} is this process or an ancestor")
                    continue

                is_target, reason = is_process_in_target_dir(pid)

                if is_target:
                    print(f"[KILL] Target PID {pid} ({gpu_name}, {vram_mb} MiB) -> {reason}")
                    if not dry_run:
                        try:
                            os.kill(pid, signal.SIGTERM)
                            cleaned.append({"pid": pid, "gpu": gpu_name, "vram_mb": vram_mb, "reason": reason, "status": "TERMINATED"})
                        except ProcessLookupError:
                            cleaned.append({"pid": pid, "gpu": gpu_name, "vram_mb": vram_mb, "reason": reason, "status": "ALREADY_DEAD"})
                        except Exception as ex:
                            cleaned.append({"pid": pid, "gpu": gpu_name, "vram_mb": vram_mb, "reason": reason, "status": f"FAILED: {ex}"})
                else:
                    print(f"[SKIP] External PID {pid} ({gpu_name}, {vram_mb} MiB) -> {reason}")
    except Exception as ex:
        print(f"Error checking GPU compute applications: {ex}")

    return cleaned


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(f"Scanning GPU processes strictly inside {TARGET_PREFIX} (dry_run={dry})...")
    res = cleanup_mpairwe7_gpu_processes(dry_run=dry)
    print(f"Cleanup finished: {len(res)} matching process(es) processed.")
