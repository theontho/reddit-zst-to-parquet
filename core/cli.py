#!/usr/bin/env python3

"""
Reddit ZST to Parquet Converter CLI
Main entry point for the parquet conversion toolset.
"""

import argparse
import logging
import sys

from commands.config_cmd import run_config_command
from commands.precheck import TRANSFER_METHODS, run_precheck
from commands.report import run_fleet_report
from commands.run import run_conversion_loop
from core import config


def main():
    parser = argparse.ArgumentParser(
        description="Reddit ZST to Parquet: High-performance Zstandard to Parquet conversion suite."
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # 'run' command
    run_parser = subparsers.add_parser("run", help="Start the conversion processing loop.")
    run_parser.add_argument(
        "--method", choices=TRANSFER_METHODS, help="Override the transfer method (default from config)"
    )
    run_parser.add_argument("--only", help="Process only one remote .zst filename.")
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess the --only file even if output manifest or claim already exists.",
    )

    # 'precheck' command
    precheck_parser = subparsers.add_parser("precheck", help="Validate host readiness without processing files.")
    precheck_parser.add_argument(
        "--method", choices=TRANSFER_METHODS, help="Override the transfer method for this check."
    )
    precheck_parser.add_argument(
        "--skip-connection",
        action="store_true",
        help="Skip remote connectivity checks; useful for CI and offline checks.",
    )

    # 'config' command
    config_parser = subparsers.add_parser("config", help="Inspect and validate configuration safely.")
    config_parser.add_argument("--paths", action="store_true", help="Show config file locations.")
    config_parser.add_argument("--show", action="store_true", help="Show loaded config with secrets redacted.")
    config_parser.add_argument("--validate", action="store_true", help="Validate the loaded config.")

    # 'report' command
    subparsers.add_parser("report", help="Generate a fleet progress and performance report.")

    # 'bench' command
    subparsers.add_parser("bench", help="Run storage benchmarks.")

    # 'verify' command
    verify_parser = subparsers.add_parser("verify", help="Verify remote Parquet files.")
    verify_parser.add_argument("--limit", type=int, help="Verify only the first N files; useful for safe smoke tests.")
    verify_parser.add_argument("--offset", type=int, default=0, help="Skip the first N sorted files before verifying.")
    verify_parser.add_argument(
        "--delay",
        type=float,
        help="Seconds to wait between files for FTP safety (default: 2 for FTP, 0 otherwise).",
    )

    # 'manifests' command
    manifests_parser = subparsers.add_parser("manifests", help="Generate missing remote manifests.")
    manifests_parser.add_argument("--force", action="store_true", help="Force regeneration of all manifests.")
    manifests_parser.add_argument(
        "--full",
        action="store_true",
        help="Download and scan full Parquet files instead of footer-only FTP regeneration.",
    )
    manifests_parser.add_argument(
        "--limit", type=int, help="Generate only the first N manifests; useful for safe smoke tests."
    )
    manifests_parser.add_argument(
        "--delay",
        type=float,
        help="Seconds to wait between files for FTP safety (default: 5 for FTP, 0 otherwise).",
    )

    args = parser.parse_args()

    if args.command == "run":
        if args.force and not args.only:
            run_parser.error("--force requires --only")
        if args.method:
            config.TRANSFER_METHOD = args.method
            config.config_data["transfer"]["method"] = args.method

        try:
            run_conversion_loop(only=args.only, force=args.force)
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

    elif args.command == "precheck":
        sys.exit(run_precheck(method=args.method, skip_connection=args.skip_connection))

    elif args.command == "config":
        sys.exit(run_config_command(show_paths=args.paths, validate=args.validate, show=args.show))

    elif args.command == "bench":
        # Import here to avoid circular dependencies or heavy imports if not needed
        from commands.bench import run_storage_benchmark

        run_storage_benchmark()

    elif args.command == "verify":
        from commands.verify import run_verification

        sys.exit(run_verification(limit=args.limit, delay_seconds=args.delay, offset=args.offset))

    elif args.command == "manifests":
        from commands.manifests import run_generate_manifests

        sys.exit(run_generate_manifests(force=args.force, limit=args.limit, delay_seconds=args.delay, full=args.full))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
