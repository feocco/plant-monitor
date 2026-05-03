from __future__ import annotations

import argparse
import asyncio

from plant_monitor.discovery import discover
from plant_monitor.main import run as run_monitor
from plant_monitor.status import run as run_status


def main() -> None:
    parser = argparse.ArgumentParser(prog="plant", description="Home Assistant plant monitor CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover", help="Discover Home Assistant plant entities.")
    discover_parser.add_argument("--output", help="Output YAML path. Defaults to plants.discovered.yaml.")
    discover_parser.add_argument("--write", action="store_true", help="Write directly to CONFIG_PATH/plants.yaml.")

    status_parser = subparsers.add_parser("status", help="Print current plant status.")
    status_parser.add_argument(
        "--notify",
        action="store_true",
        help="Send the current status as a homelab-functions notification digest.",
    )

    subparsers.add_parser("monitor", help="Run the long-lived monitor.")

    args = parser.parse_args()
    if args.command == "discover":
        asyncio.run(discover(output_path=args.output, write=args.write))
    elif args.command == "status":
        asyncio.run(run_status(send_notification=args.notify))
    elif args.command == "monitor":
        asyncio.run(run_monitor())


if __name__ == "__main__":
    main()
