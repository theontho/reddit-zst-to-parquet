import logging
import os
import shutil
import time
from collections.abc import Sequence
from typing import Any

from core import config
from core.processor import process_file
from core.utils import get_machine_metadata
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler
from transfer.nfs_transfer import NfsTransferHandler
from transfer.rsync_ssh_transfer import RsyncSshTransferHandler

STAGE_ORDER = ("download", "conversion", "upload")


def _format_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "N/A"

    return f"{seconds:.2f}s"


def _format_speed_mb_s(stage_data: dict[str, Any]) -> str:
    if "speed_mb_s" not in stage_data:
        return "-"

    try:
        speed = float(stage_data["speed_mb_s"])
    except (TypeError, ValueError):
        return "N/A"

    return f"{speed:.2f} MB/s"


def _format_table(headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [
        border,
        "| " + " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)) + " |",
        border,
    ]
    for row in rows:
        lines.append("| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) + " |")
    lines.append(border)
    return "\n".join(lines)


def _format_benchmark_summary(test_file: str, results: list[dict[str, Any]]) -> str:
    lines = ["", "=" * 70, "BENCHMARK RESULTS", "=" * 70, f"Input file: {test_file}"]

    if not results:
        lines.extend(["", "No benchmark completed successfully.", "=" * 70])
        return "\n".join(lines)

    for result in results:
        stages = result.get("stages", {})
        stage_names = [stage for stage in STAGE_ORDER if stage in stages]
        stage_names.extend(sorted(stage for stage in stages if stage not in STAGE_ORDER))

        rows = [
            (
                stage.replace("_", " ").title(),
                _format_seconds(stages[stage].get("duration_sec")),
                _format_speed_mb_s(stages[stage]),
            )
            for stage in stage_names
        ]
        rows.append(("Total", _format_seconds(result.get("total_duration")), "-"))

        lines.extend(
            [
                "",
                f"Target:    {result.get('label', 'Unknown')}",
                f"Temp path: {result.get('temp_path', 'Unknown')}",
                _format_table(("Stage", "Duration", "Throughput"), rows),
            ]
        )

    lines.append("=" * 70)
    return "\n".join(lines)


def run_benchmark(zst_file: str, temp_dir_base: str, label: str) -> dict[str, Any] | None:
    logging.info(f"\n--- STARTING BENCHMARK: {label} ---")
    logging.info(f"Using Path: {temp_dir_base}")

    # Setup
    config.CONVERSION_TEMP_BASE_DIR = temp_dir_base
    if os.path.abspath(temp_dir_base) in {"/", os.path.expanduser("~")}:
        logging.error(f"Refusing to remove unsafe benchmark temp directory: {temp_dir_base}")
        return None
    if os.path.exists(temp_dir_base):
        shutil.rmtree(temp_dir_base)
    os.makedirs(temp_dir_base, exist_ok=True)

    # Initialize transfer handler based on config
    method = config.TRANSFER_METHOD.lower()
    transfer_handler: TransferHandler
    if method == "ftp":
        transfer_handler = FtpTransferHandler()
    elif method == "rsync":
        transfer_handler = RsyncSshTransferHandler()
    elif method == "nfs":
        transfer_handler = NfsTransferHandler()
    elif method == "local":
        transfer_handler = LocalTransferHandler()
    else:
        print(f"Error: Unsupported transfer method for benchmark: {method}")
        return None

    log_data: dict[str, Any] = {"files": {}}
    machine_meta = get_machine_metadata()

    # We need to find the size of the file first
    logging.info(f"Listing remote files to get size for {zst_file}...")
    zst_files_with_sizes, _, _ = transfer_handler.list_remote_files()
    expected_size = next((size for name, size in zst_files_with_sizes if name == zst_file), 0)

    if expected_size == 0:
        logging.error(f"Could not find file {zst_file} on remote storage.")
        return None

    start_time = time.time()
    result = process_file(
        zst_filename=zst_file,
        remote_size=expected_size,
        log_data=log_data,
        transfer_handler=transfer_handler,
        current_index=0,
        total_files=1,
        machine_meta=machine_meta,
        temp_dir_used=temp_dir_base,
        force=True,
    )
    total_duration = time.time() - start_time

    if result == "success":
        perf = log_data["files"][zst_file]["perf"]
        logging.info(f"--- FINISHED BENCHMARK: {label} ---")
        return {
            "label": label,
            "total_duration": round(total_duration, 2),
            "stages": perf["stages"],
            "temp_path": temp_dir_base,
        }
    else:
        logging.error(f"Benchmark failed for {label}")
        return None


def run_storage_benchmark():
    """Runs performance benchmarks against different storage targets to identify bottlenecks."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(module)s] %(message)s")

    # A large sized file for testing
    test_file = "RC_2011-09.zst"
    ssd_path = os.path.expanduser("~/reddit_parquet_benchmark_ssd")

    print("\n" + "=" * 70)
    print(f"STORAGE PERFORMANCE BENCHMARK: {test_file}")
    print("=" * 70)

    results = []
    res_ssd = run_benchmark(test_file, ssd_path, "Local Temp Directory")
    if res_ssd:
        results.append(res_ssd)

    print(_format_benchmark_summary(test_file, results))
