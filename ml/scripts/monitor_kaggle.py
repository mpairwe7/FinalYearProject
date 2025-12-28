"""
Monitor Kaggle Training Script
Polls Kaggle API to check kernel execution status
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from kaggle.api.kaggle_api_extended import KaggleApi
except ImportError:
    print("Error: kaggle package not installed. Run: pip install kaggle")
    sys.exit(1)


def monitor_kernel(kernel_id: str, timeout: int = 7200, poll_interval: int = 60) -> bool:
    """
    Monitor a Kaggle kernel until completion or timeout.
    
    Args:
        kernel_id: The kernel ID (format: username/kernel-slug)
        timeout: Maximum wait time in seconds
        poll_interval: Time between status checks in seconds
    
    Returns:
        True if kernel completed successfully, False otherwise
    """
    api = KaggleApi()
    api.authenticate()
    
    start_time = time.time()
    print(f"Monitoring kernel: {kernel_id}")
    print(f"Timeout: {timeout}s, Poll interval: {poll_interval}s")
    print("-" * 50)
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed > timeout:
            print(f"\n❌ Timeout reached after {elapsed:.0f}s")
            return False
        
        try:
            status = api.kernel_status(kernel_id)
            state = status.get('status', 'unknown')
            
            # Map status to readable format
            status_map = {
                'queued': '⏳ Queued',
                'running': '🔄 Running',
                'complete': '✅ Complete',
                'error': '❌ Error',
                'cancelAcknowledged': '🚫 Cancelled',
            }
            
            status_display = status_map.get(state, f'❓ {state}')
            print(f"[{elapsed:6.0f}s] Status: {status_display}")
            
            if state == 'complete':
                print(f"\n✅ Kernel completed successfully!")
                return True
            elif state in ['error', 'cancelAcknowledged']:
                print(f"\n❌ Kernel failed with status: {state}")
                # Try to get error log
                try:
                    log = api.kernel_output(kernel_id)
                    if log:
                        print("\nKernel log (last 500 chars):")
                        print(str(log)[-500:])
                except Exception as e:
                    print(f"Could not retrieve log: {e}")
                return False
            
        except Exception as e:
            print(f"[{elapsed:6.0f}s] API error: {e}")
        
        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description='Monitor Kaggle kernel execution')
    parser.add_argument('--kernel-id', type=str, required=True,
                        help='Kaggle kernel ID (username/kernel-slug)')
    parser.add_argument('--timeout', type=int, default=7200,
                        help='Maximum wait time in seconds (default: 7200)')
    parser.add_argument('--poll-interval', type=int, default=60,
                        help='Time between status checks in seconds (default: 60)')
    args = parser.parse_args()
    
    success = monitor_kernel(args.kernel_id, args.timeout, args.poll_interval)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
