#!/usr/bin/env python3

"""
Arctic Shift: Parquet Converter CLI
Main entry point for the parquet conversion toolset.
"""

import argparse
import logging
import sys

from core import config
from commands.run import run_conversion_loop
from commands.report import run_fleet_report


def main():
    parser = argparse.ArgumentParser(
        description="Arctic Shift: High-performance Zstandard to Parquet conversion suite."
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # 'run' command
    run_parser = subparsers.add_parser("run", help="Start the conversion processing loop.")
    run_parser.add_argument(
        "--method", 
        choices=["local", "ftp", "rsync", "nfs"], 
        help="Override the transfer method (default from config)"
    )

    # 'report' command
    report_parser = subparsers.add_parser("report", help="Generate a fleet progress and performance report.")

    # 'bench' command (To be implemented)
    bench_parser = subparsers.add_parser("bench", help="Run storage benchmarks.")

    # 'verify' command (To be implemented)
    verify_parser = subparsers.add_parser("verify", help="Verify remote Parquet files.")

    # 'manifests' command
    manifests_parser = subparsers.add_parser("manifests", help="Generate missing remote manifests.")
    manifests_parser.add_argument("--force", action="store_true", help="Force regeneration of all manifests.")

    args = parser.parse_args()

    if args.command == "run":
        if args.method:
            config.TRANSFER_METHOD = args.method
        
        try:
            run_conversion_loop()
        except KeyboardInterrupt:
            print("\n\n" + "=" * 50)
            print("  PROCESSING INTERRUPTED BY USER (Ctrl+C)")
            print("=" * 50 + "\n")
            sys.exit(0)
        except Exception as e:
            logging.critical(f"An unhandled critical error occurred: {e}")
            logging.exception("Traceback:")
            sys.exit(1)

    elif args.command == "report":
        run_fleet_report()

    elif args.command == "bench":
        # Import here to avoid circular dependencies or heavy imports if not needed
        from commands.bench import run_storage_benchmark
        run_storage_benchmark()

    elif args.command == "verify":
        from commands.verify import run_verification
        run_verification()

    elif args.command == "manifests":
        from commands.manifests import run_generate_manifests
        run_generate_manifests(force=args.force)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
