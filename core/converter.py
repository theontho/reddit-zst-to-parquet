"""Handles the execution of the Zstandard to Parquet conversion script."""

import contextlib
import logging
import math  # Added for size formatting
import os
import subprocess

from core.config import (
    CHUNKED_CONVERTER_PATH,
    CONVERSION_METHOD,
    DUCKDB_LARGE_FILE_THRESHOLD_GB,
    FALLBACK_TO_CHUNKED,
    PYARROW_CONVERTER_PATH,
    STREAMED_CONVERTER_PATH,
)


# Helper function to format file size
def _format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = math.floor(math.log(size_bytes, 1024))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


def _execute_converter_script(
    script_path: str,
    local_zst_path: str,
    local_parquet_path: str,
    working_dir: str,
    on_claim_stage_change=None,
) -> dict:
    """Executes a specific converter script and handles its output."""
    input_filename = os.path.basename(local_zst_path)
    output_filename = os.path.basename(local_parquet_path)

    logging.info(f"Executing {os.path.basename(script_path)}... (cwd: {working_dir})")

    # Always run the script via the current Python interpreter for cross-platform stability
    import sys

    cmd = [sys.executable, script_path, input_filename, "-o", output_filename]

    env = os.environ.copy()
    env["SKIP_TERMINAL_TITLE"] = "1"

    env = os.environ.copy()
    env["SKIP_TERMINAL_TITLE"] = "1"

    oom_detected = False
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=working_dir,
            bufsize=1,
            env=env,
        )

        if process.stdout:
            for line in process.stdout:
                line_str = line.strip()
                if not line_str:
                    continue

                # Detect OOM
                if "OutOfMemoryException" in line_str or "Out of Memory Error" in line_str:
                    oom_detected = True

                if line_str.startswith("CLAIM_STAGE:"):
                    stage = line_str.replace("CLAIM_STAGE:", "").strip()
                    if on_claim_stage_change:
                        on_claim_stage_change(stage)
                else:
                    # Print script output to stdout for real-time visibility
                    print(f"  {line_str}", flush=True)
                    # Also log to debug for file logs
                    logging.debug(f"[{os.path.basename(script_path)}] {line_str}")

        process.wait()
        return {"success": process.returncode == 0, "oom": oom_detected}

    except Exception as e:
        logging.error(f"Error executing conversion script {script_path}: {e}")
        return {"success": False, "oom": False}


def convert_to_parquet(
    local_zst_path: str, local_parquet_path: str, working_dir: str, on_claim_stage_change=None
) -> bool:
    """Runs the conversion process with potential fallback logic.

    Args:
        local_zst_path: Full path to the local .zst file.
        local_parquet_path: Full path where the output .parquet file should be.
        working_dir: The directory where the conversion script should be run.
        on_claim_stage_change: Optional callback for stage updates.

    Returns:
        True if conversion was successful and the output file exists, False otherwise.
    """
    # 1. Map conversion method to script path
    method_map = {
        "streamed": STREAMED_CONVERTER_PATH,
        "chunked": CHUNKED_CONVERTER_PATH,
        "pyarrow": PYARROW_CONVERTER_PATH,
    }

    primary_method = CONVERSION_METHOD.lower()

    # Stability Rule: Force chunked mode for large files if streamed is requested
    try:
        compressed_size = os.path.getsize(local_zst_path)
        compressed_size_gb = compressed_size / (1024**3)
        if compressed_size_gb >= DUCKDB_LARGE_FILE_THRESHOLD_GB and primary_method == "streamed":
            logging.info(
                f"File size {compressed_size_gb:.2f}GB exceeds stability threshold ({DUCKDB_LARGE_FILE_THRESHOLD_GB}GB). "
                "Forcing 'chunked' method for reliability."
            )
            primary_method = "chunked"
    except Exception:
        compressed_size = 0

    script_path = method_map.get(primary_method, CHUNKED_CONVERTER_PATH)

    # Log estimated size
    try:
        compressed_size = os.path.getsize(local_zst_path)
        estimated_uncompressed_size = compressed_size * 16
        logging.info(
            f"Input: {os.path.basename(local_zst_path)} ({_format_size(compressed_size)}). "
            f"Est. Uncompressed: {_format_size(estimated_uncompressed_size)}"
        )
    except Exception:
        pass

    # 2. Try primary conversion method
    logging.info(f"Attempting conversion using method: {primary_method}")
    result = _execute_converter_script(
        script_path,
        local_zst_path,
        local_parquet_path,
        working_dir,
        on_claim_stage_change=on_claim_stage_change,
    )

    if result["success"]:
        logging.info(f"Conversion complete: {local_parquet_path}")
        return os.path.exists(local_parquet_path)

    # 3. Handle Fallback (if enabled and applicable)
    if FALLBACK_TO_CHUNKED and primary_method != "chunked" and os.path.basename(script_path) != "chunked_engine.py":
        fallback_reason = "OOM" if result["oom"] else "failure"
        logging.warning(
            f"Primary method ({primary_method}) failed ({fallback_reason}). Falling back to chunked converter..."
        )

        if on_claim_stage_change:
            on_claim_stage_change(f"fallback_to_chunked_after_{primary_method}_{fallback_reason}")

        # Clean up any partial output from the failed primary attempt
        if os.path.exists(local_parquet_path):
            with contextlib.suppress(Exception):
                os.remove(local_parquet_path)

        fallback_result = _execute_converter_script(
            CHUNKED_CONVERTER_PATH,
            local_zst_path,
            local_parquet_path,
            working_dir,
            on_claim_stage_change=on_claim_stage_change,
        )
        if fallback_result["success"] and os.path.exists(local_parquet_path):
            logging.info(f"Conversion complete: {local_parquet_path}")
            return True

    logging.error("Conversion failed after all attempts.")
    return False
