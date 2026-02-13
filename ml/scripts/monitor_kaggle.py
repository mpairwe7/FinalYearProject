"""
Monitor Kaggle Training Script
Polls Kaggle API to check kernel execution status.

Features:
  - Exponential backoff on API errors
  - GitHub Actions annotations (::notice::, ::warning::, ::error::)
  - Optional JSON summary output for step summary integration
  - Structured status output via GITHUB_OUTPUT
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from kaggle.api.kaggle_api_extended import KaggleApi
except ImportError:
    print("Error: kaggle package not installed. Run: pip install kaggle")
    sys.exit(1)


def _is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _annotate(level: str, message: str) -> None:
    """Emit GitHub Actions annotation if running in CI."""
    if _is_github_actions():
        print(f"::{level}::{message}")


def monitor_kernel(
    kernel_id: str,
    timeout: int = 7200,
    poll_interval: int = 60,
    output_summary: str | None = None,
) -> bool:
    """
    Monitor a Kaggle kernel until completion or timeout.

    Args:
        kernel_id: The kernel ID (format: username/kernel-slug)
        timeout: Maximum wait time in seconds
        poll_interval: Time between status checks in seconds
        output_summary: Optional path to write a JSON summary file

    Returns:
        True if kernel completed successfully, False otherwise
    """
    api = KaggleApi()
    api.authenticate()

    start_time = time.time()
    backoff_factor = 1.0
    max_backoff = 300  # 5 minutes max backoff
    consecutive_errors = 0

    print(f"Monitoring kernel: {kernel_id}")
    print(f"Timeout: {timeout}s, Poll interval: {poll_interval}s")
    print("-" * 50)

    final_state = "unknown"
    total_polls = 0

    while True:
        elapsed = time.time() - start_time

        if elapsed > timeout:
            _annotate("error", f"Timeout reached after {elapsed:.0f}s for kernel {kernel_id}")
            print(f"\nTimeout reached after {elapsed:.0f}s")
            final_state = "timeout"
            break

        try:
            status = api.kernel_status(kernel_id)
            state = status.get("status", "unknown")
            total_polls += 1
            consecutive_errors = 0
            backoff_factor = 1.0  # Reset backoff on success

            status_map = {
                "queued": "Queued",
                "running": "Running",
                "complete": "Complete",
                "error": "Error",
                "cancelAcknowledged": "Cancelled",
            }

            status_display = status_map.get(state, f"Unknown ({state})")
            print(f"[{elapsed:6.0f}s] Status: {status_display}")

            if state == "complete":
                _annotate("notice", f"Kernel {kernel_id} completed successfully in {elapsed:.0f}s")
                print(f"\nKernel completed successfully!")
                final_state = "complete"
                break
            elif state in ["error", "cancelAcknowledged"]:
                _annotate("error", f"Kernel {kernel_id} failed with status: {state}")
                print(f"\nKernel failed with status: {state}")
                final_state = state
                # Try to get error log
                try:
                    log = api.kernel_output(kernel_id)
                    if log:
                        print("\nKernel log (last 500 chars):")
                        print(str(log)[-500:])
                except Exception as e:
                    print(f"Could not retrieve log: {e}")
                break

        except Exception as e:
            consecutive_errors += 1
            backoff_factor = min(backoff_factor * 2, max_backoff / poll_interval)
            wait_time = poll_interval * backoff_factor

            if consecutive_errors >= 5:
                _annotate("error", f"5 consecutive API errors for {kernel_id}: {e}")
                print(f"\n5 consecutive API errors, giving up")
                final_state = "api_error"
                break

            _annotate("warning", f"API error (attempt {consecutive_errors}): {e}")
            print(f"[{elapsed:6.0f}s] API error (attempt {consecutive_errors}): {e}")
            print(f"  Backing off for {wait_time:.0f}s")
            time.sleep(wait_time)
            continue

        time.sleep(poll_interval)

    elapsed = time.time() - start_time
    success = final_state == "complete"

    # Write summary file if requested
    if output_summary:
        summary = {
            "kernel_id": kernel_id,
            "final_state": final_state,
            "success": success,
            "elapsed_seconds": round(elapsed, 1),
            "total_polls": total_polls,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        summary_path = Path(output_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Summary written to {summary_path}")

    # Write to GITHUB_OUTPUT if available
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"kernel-state={final_state}\n")
            f.write(f"kernel-success={str(success).lower()}\n")
            f.write(f"elapsed-seconds={round(elapsed)}\n")

    return success


def main():
    parser = argparse.ArgumentParser(description="Monitor Kaggle kernel execution")
    parser.add_argument(
        "--kernel-id",
        type=str,
        required=True,
        help="Kaggle kernel ID (username/kernel-slug)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Maximum wait time in seconds (default: 7200)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Time between status checks in seconds (default: 60)",
    )
    parser.add_argument(
        "--output-summary",
        type=str,
        default=None,
        help="Path to write a JSON summary file",
    )
    args = parser.parse_args()

    success = monitor_kernel(
        args.kernel_id,
        args.timeout,
        args.poll_interval,
        args.output_summary,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
