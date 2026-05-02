#!/usr/bin/env python3

import argparse
import contextlib
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time

# Add parent directory to path to allow imports from the main package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from collections import defaultdict

import duckdb

from core.config import (
    CHUNK_SIZE,
    COMPRESSION_RATIO_ESTIMATE,
    DUCKDB_MAXIMUM_OBJECT_SIZE,
    DUCKDB_MEMORY_LIMIT_GB,
    DUCKDB_PARQUET_COMPRESSION_CODEC,
    DUCKDB_PATH,
    DUCKDB_PRESERVE_INSERTION_ORDER,
    DUCKDB_ROW_GROUP_SIZE,
    DUCKDB_THREADS,
    TEST_RUN_CHUNK_SIZE,
    ZSTD_LONG_RANGE_BITS,
    ZSTD_PATH,
    ZSTD_TERMINATION_TIMEOUT_SECONDS,
)

# --- Constants ---
DEFAULT_CHUNK_SIZE: int = CHUNK_SIZE
DEFAULT_COMPRESSION_RATIO_ESTIMATE: int = COMPRESSION_RATIO_ESTIMATE

# DuckDB type indicators for complex types (used for filtering)
COMPLEX_TYPE_INDICATORS: list[str] = ["STRUCT", "MAP", "LIST", "[]"]

# --- Column Type Normalization Lists ---
# These columns will be explicitly cast during chunk processing to avoid type-flipping conflicts.
BIGINT_COLUMNS = {
    "score",
    "created_utc",
    "num_comments",
    "ups",
    "downs",
    "controversiality",
    "gilded",
    "total_awards_received",
    "retrieved_on",
    "retrieved_utc",
    "author_created_utc",
    "created",
    "num_crossposts",
    "thumbnail_height",
    "thumbnail_width",
}

BOOLEAN_COLUMNS = {
    "over_18",
    "is_self",
    "is_video",
    "archived",
    "locked",
    "stickied",
    "spoiler",
    "quarantine",
    "pinned",
    "hidden",
    "saved",
    "can_gild",
    "can_mod_post",
    "is_submitter",
    "send_replies",
    "no_follow",
    "is_crosspostable",
    "is_gallery",
    "is_meta",
    "is_original_content",
    "is_reddit_media_domain",
    "is_robot_indexable",
    "is_survey_ad",
    "allow_live_comments",
    "promoted",
    "score_hidden",
}
# DuckDB options
DUCKDB_JSON_READ_OPTIONS: str = f"""
    union_by_name=true,
    format='newline_delimited',
    ignore_errors=true,
    maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE}
"""

DUCKDB_ZSTD_CODEC: str = DUCKDB_PARQUET_COMPRESSION_CODEC  # Use configured codec for merged output


# --- Logging Setup ---
def setup_logging(log_dir: str | None, log_filename_base: str, verbose: bool) -> None:
    """Configures logging to file and console."""
    handlers: list[logging.Handler] = []
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Console Handler (always add)
    console_handler = logging.StreamHandler(sys.stdout)
    # For console, only show INFO level unless verbose is true
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(log_format, datefmt=date_format)
    console_handler.setFormatter(console_formatter)
    handlers.append(console_handler)

    # File Handler (only if log_dir is provided)
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            log_filename = f"{log_filename_base}_{timestamp}.log"
            log_filepath = os.path.join(log_dir, log_filename)

            file_handler = logging.FileHandler(log_filepath, encoding="utf-8")
            # Log everything (INFO and above, or DEBUG if verbose) to the file
            file_handler.setLevel(log_level)
            file_formatter = logging.Formatter(log_format, datefmt=date_format)
            file_handler.setFormatter(file_formatter)
            handlers.append(file_handler)
            print(f"Logging to file: {log_filepath}")  # Still print this info to console
        except OSError as e:
            print(
                f"Warning: Could not create log directory or file handler at '{log_dir}': {e}. File logging disabled."
            )

    # Configure root logger
    # Set level to the lowest level needed by any handler (DEBUG if verbose, otherwise INFO)
    logging.basicConfig(level=log_level, format=log_format, datefmt=date_format, handlers=handlers)


# --- SQL Quoting Helper ---
def quote_sql_string(value: str) -> str:
    """Quotes a string for safe inclusion in a SQL query."""
    if value is None:
        return "NULL"  # Or handle appropriately
    # Replace single quotes with two single quotes and wrap in single quotes
    return "'" + value.replace("'", "''") + "'"


def format_size(size_bytes: int | None) -> str:
    """Converts bytes to a human-readable string (KB, MB, GB)."""
    if size_bytes is None:
        return "N/A"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.1f} GB"


def find_schema_for_file(zst_path: str):
    """Heuristic to find the matching JSON schema in the schemas/ directory."""
    filename = os.path.basename(zst_path)
    # Expected format: RC_YYYY-MM.zst or RS_YYYY-MM.zst
    parts = filename.split("_")
    if len(parts) < 2:
        return None

    prefix = parts[0]  # RC or RS
    date_part = parts[1].split(".")[0]  # YYYY-MM
    year = date_part.split("-")[0]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    # The schemas directory is up one level from parquet_converter
    schemas_dir = os.path.abspath(os.path.join(script_dir, "../schemas"))

    schema_path = os.path.join(schemas_dir, prefix, year, f"{prefix}_{date_part}.json")
    if os.path.exists(schema_path):
        return schema_path

    return None


def load_standard_columns(schema_path: str) -> list[str]:
    """Loads the list of standard columns from the JSON schema file."""
    try:
        with open(schema_path) as f:
            data = json.load(f)
            json_fields = data[0]["schema"]

        # We want to keep fields that have some usage or are core fields
        core_fields = ["id", "author", "subreddit", "link_id", "parent_id", "created_utc"]
        standard_columns = []

        for field in sorted(json_fields.keys()):
            info_list = json_fields[field]
            usage = info_list[0]["usage"]
            usage_val = 1.0 if usage == "always" else float(usage)

            # Use 0.1 threshold like in schema_parser.py
            if usage_val >= 0.1 or field in core_fields:
                standard_columns.append(field)

        return standard_columns
    except Exception as e:
        logging.error(f"Error loading standard columns from {schema_path}: {e}")
        return []


def load_master_schema(zst_path: str) -> list[str]:
    """Loads the fixed master schema for RC or RS files."""
    filename = os.path.basename(zst_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if "RC_" in filename:
        master_path = os.path.join(script_dir, "master_schema_rc.json")
    elif "RS_" in filename:
        master_path = os.path.join(script_dir, "master_schema_rs.json")
    else:
        return []

    if os.path.exists(master_path):
        try:
            with open(master_path) as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except Exception as e:
            logging.error(f"Error loading master schema from {master_path}: {e}")

    return []


def run_command(command, shell=False):
    """Executes a command and raises an exception on error."""
    logging.debug(f"Running command: {' '.join(command)}")
    process = subprocess.run(command, capture_output=True, text=True, shell=shell)
    if process.returncode != 0:
        logging.error(f"Error executing command: {' '.join(command)}")
        logging.error(f"stdout:\n{process.stdout}")
        logging.error(f"stderr:\n{process.stderr}")
        raise RuntimeError(f"Command failed with exit code {process.returncode}")
    # logging.debug(f"Command output:\n{process.stdout}") # Changed commented print to logging.debug
    return process


def run_duckdb_command(command_list: list[str], description: str, verbose: bool = False) -> tuple[str | None, bool]:
    """Helper function to run a DuckDB command via subprocess and handle errors."""
    logging.debug(f"Executing DuckDB command ({description})...")
    if verbose:
        logging.debug(f"  Command: {' '.join(command_list)}")
    try:
        # Use Popen for potentially large stderr, communicate handles timeouts/deadlocks
        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        stdout, stderr = process.communicate()

        has_error = False
        if stderr:
            if "error:" in stderr.lower() or "parser error:" in stderr.lower():
                has_error = True
            if verbose or has_error:
                logging.debug(f"DuckDB ({description}) stderr:\n{stderr}")
            elif not verbose and not has_error:
                logging.debug(f"DuckDB ({description}) stderr (non-error):\n{stderr}")

        if process.returncode != 0:
            if not has_error:
                logging.error(
                    f"Error: DuckDB command ({description}) failed with non-zero exit code {process.returncode}"
                )
            has_error = True

        if verbose and stdout:
            logging.debug(f"DuckDB ({description}) stdout:\n{stdout}")

        if has_error:
            if process.returncode != 0 and not ("error:" in stderr.lower() or "parser error:" in stderr.lower()):
                logging.error(
                    f"Error: DuckDB command ({description}) failed with exit code {process.returncode} (stderr did not contain standard error patterns). Check logs."
                )
            elif process.returncode == 0 and has_error:
                logging.error(
                    f"Error: DuckDB command ({description}) indicated failure via stderr, even with exit code 0. Check logs."
                )
            return None, False
        else:
            return stdout, True

    except FileNotFoundError:
        duckdb_executable_path = command_list[0] if command_list else "duckdb"
        logging.error(
            f"Error: '{duckdb_executable_path}' command not found. Make sure DuckDB is installed and in your system's PATH or specified via --duckdb_path."
        )
        return None, False
    except Exception as e:
        logging.error(f"An unexpected error occurred during DuckDB execution ({description}): {e}")
        return None, False


def parse_duckdb_count(output: str | None) -> int | None:
    """Parses the count from DuckDB CLI output."""
    if output is None:
        return None
    # Updated Regex: Find digits on a line following the header/separator lines
    match = re.search(
        r"(?:\n|^)?[\s┌─│]+[\s┐]+[\n│](?:\s*count_star\(\)\s*\n)?(?:\s*int\d*\s*\n)?[├─\s│]+[\s┤]+[\n│]\s*(\d+)\s*[\n└─\s│]+[\s┘]+",
        output,
        re.IGNORECASE | re.MULTILINE,
    )

    if match:
        try:
            return int(match.group(1))
        except ValueError:
            # Log as error if parsing fails
            logging.error(f"Error: Could not parse count from extracted digits: {match.group(1)}")
            return None
    else:
        # Log as error if pattern not found
        logging.error(f"Error: Could not find count pattern in DuckDB output.\nOutput was:\n{output}")
        return None


def merge_and_verify_parquet(
    input_files: list[str], output_path: str, duckdb_path: str, log_dir: str, verbose: bool = False
) -> bool:
    """
    Merges multiple Parquet files into a single output file using DuckDB,
    verifies that the row counts match, and generates a chunk stats JSON file.
    """
    logging.debug(f"\nStarting merge process for {len(input_files)} files into {output_path}")
    print("CLAIM_STAGE: merging chunks", flush=True)
    if not input_files:
        logging.error("Error: No input Parquet files provided for merge.")
        return False

    # --- Generate Stats First ---
    # Construct stats path inside the log directory
    stats_filename = os.path.basename(output_path) + ".chunk_stats.json"
    stats_json_path = os.path.join(log_dir, stats_filename)
    logging.debug(f"Generating chunk stats file at: {stats_json_path}")  # Log the path

    stats_generated_ok = _generate_chunk_stats(input_files, stats_json_path, verbose)
    if not stats_generated_ok:
        logging.warning("Warning: Failed to generate chunk stats JSON file. Continuing with merge...")
        # Decide if merge should proceed if stats fail? For now, yes.

    # --- Merge Step (Standardized Logic) ---
    quoted_output_file = quote_sql_string(output_path)
    parquet_files_list_str = ", ".join([quote_sql_string(f) for f in input_files])

    master_columns = load_master_schema(output_path)

    logging.debug(f"--- Running Standardized Merge (Master Schema: {'Yes' if master_columns else 'No'}) ---")
    try:
        with duckdb.connect(":memory:") as con:
            # --- Ultra-Stable Merge Configuration ---
            # For large merges (many chunks), we force threads=1 to minimize disk spill overhead
            # and ensure stability. We also cap memory to avoid OOM before spilling starts.
            merge_threads = DUCKDB_THREADS
            merge_memory = DUCKDB_MEMORY_LIMIT_GB

            if len(input_files) > 5:
                logging.info(
                    f"Large merge detected ({len(input_files)} chunks). "
                    "Enabling Ultra-Stable mode: threads=1, memory_limit=16GB."
                )
                merge_threads = 1
                merge_memory = min(merge_memory, 16)  # Cap at 16GB for stability

            con.execute(f"SET threads={merge_threads};")
            con.execute(f"SET memory_limit='{merge_memory}GB';")
            # Increase max temp size to avoid conservative defaults on some systems
            con.execute("SET max_temp_directory_size='1TB';")
            con.execute(f"SET preserve_insertion_order={'true' if DUCKDB_PRESERVE_INSERTION_ORDER else 'false'};")

            from core.utils import Heartbeat

            select_clause = "*"
            if master_columns:
                # 1. Inspect available columns from the chunks
                inspect_query = f"SELECT * FROM read_parquet([{parquet_files_list_str}], union_by_name=true) LIMIT 0"
                con.execute(inspect_query)
                available_cols = {con.description[i][0] for i in range(len(con.description))}

                select_parts = []
                for col in master_columns:
                    if col in available_cols:
                        select_parts.append(f'"{col}"')
                    else:
                        if col in BIGINT_COLUMNS:
                            select_parts.append(f'CAST(NULL AS BIGINT) AS "{col}"')
                        elif col in BOOLEAN_COLUMNS:
                            select_parts.append(f'CAST(NULL AS BOOLEAN) AS "{col}"')
                        else:
                            select_parts.append(f'CAST(NULL AS VARCHAR) AS "{col}"')

                # Always include extra_json
                if "extra_json" in available_cols:
                    # Select existing extra_json but ensure it is VARCHAR (in case some chunks had it as NULL)
                    select_parts.append('CAST("extra_json" AS VARCHAR) AS "extra_json"')
                else:
                    select_parts.append('CAST(NULL AS VARCHAR) AS "extra_json"')

                select_clause = ", ".join(select_parts)

            sql_merge_command = f"""
            COPY (
                SELECT {select_clause} FROM read_parquet(
                    [{parquet_files_list_str}],
                    union_by_name=true
                )
                ORDER BY author ASC, subreddit ASC, created_utc ASC
            ) TO {quoted_output_file} (FORMAT PARQUET, CODEC {DUCKDB_ZSTD_CODEC}, ROW_GROUP_SIZE {DUCKDB_ROW_GROUP_SIZE});

            """

            with Heartbeat("merging chunks"):
                con.execute(sql_merge_command)

    except Exception as e:
        logging.error(f"Merge process failed: {e}")
        if os.path.exists(output_path):
            with contextlib.suppress(BaseException):
                os.remove(output_path)
        return False

    logging.info(f"Successfully created merged Parquet file: {output_path}")

    # --- Verification Steps (Existing Logic) ---
    merge_verified = _verify_merge_counts(parquet_files_list_str, quoted_output_file, duckdb_path, output_path, verbose)

    if merge_verified:
        # --- Generate Final Manifest ---
        manifest_path = output_path + ".manifest.json"
        _generate_parquet_manifest(output_path, duckdb_path, manifest_path)

    # Return the result of the verification
    return merge_verified


def _generate_parquet_manifest(parquet_path: str, duckdb_path: str, output_manifest_path: str) -> bool:
    """Generates a detailed manifest JSON for a single Parquet file including schema and null frequency."""
    logging.info(f"--- Generating Final Manifest: {os.path.basename(output_manifest_path)} ---")
    try:
        parquet_path_sql = quote_sql_string(parquet_path)

        # Connect to get schema first
        with duckdb.connect(":memory:") as con:
            con.execute(f"SET threads={DUCKDB_THREADS};")  # Use config threads for manifest scan
            schema_info = con.execute(f"DESCRIBE SELECT * FROM read_parquet({parquet_path_sql})").fetchall()
            columns = [r[0] for r in schema_info]
            types = {r[0]: r[1] for r in schema_info}

            # Construct a query to get counts for all columns in a single pass
            select_parts = ["COUNT(*) as total_rows"]
            for col in columns:
                select_parts.append(f'COUNT("{col}")')

            stats_query = f"SELECT {', '.join(select_parts)} FROM read_parquet({parquet_path_sql})"
            stats_result = con.execute(stats_query).fetchone()

            if stats_result is None:
                logging.error(f"Error: No stats returned for {parquet_path}")
                return False

            total_rows = stats_result[0]
            manifest = {
                "filename": os.path.basename(parquet_path),
                "file_size": os.path.getsize(parquet_path),
                "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(parquet_path))),
                "row_count": total_rows,
                "conversion_method": "chunked",
                "schema": types,
                "column_stats": {},
            }

            for i, col in enumerate(columns):
                col_count = stats_result[i + 1]
                usage_ratio = round(col_count / total_rows, 4) if total_rows > 0 else 0
                manifest["column_stats"][col] = {"count": col_count, "usage_ratio": usage_ratio}

            with open(output_manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

            logging.info(f"Successfully generated manifest: {output_manifest_path}")
            return True

    except Exception as e:
        logging.error(f"Error generating manifest for {parquet_path}: {e}")
        return False


def _verify_merge_counts(
    parquet_files_list_str: str,
    quoted_output_file: str,
    duckdb_path: str,
    output_path: str,
    verbose: bool,
) -> bool:
    """Helper function to verify row counts after merging Parquet files."""
    logging.info("--- Verifying Row Counts ---")

    # 2. Count rows in input files (using the list)
    sql_count_input = f"SELECT COUNT(*) FROM read_parquet([{parquet_files_list_str}], union_by_name=True);"
    duckdb_count_input_command = [duckdb_path, "-c", sql_count_input]
    input_count_output, input_count_success = run_duckdb_command(
        duckdb_count_input_command, "count input chunks", verbose=verbose
    )

    if not input_count_success:
        logging.error("Failed to execute input row count command. Verification aborted.")
        return False  # Indicate failure

    input_row_count = parse_duckdb_count(input_count_output)

    if input_row_count is None:
        logging.error("Could not determine input chunk row count. Verification failed.")
        return False  # Indicate failure

    logging.info(f"Total rows reported by DuckDB for input files: {input_row_count}")

    # 3. Count rows in output file
    sql_count_output = f"SELECT COUNT(*) FROM read_parquet({quoted_output_file});"
    duckdb_count_output_command = [duckdb_path, "-c", sql_count_output]
    output_count_output, output_count_success = run_duckdb_command(
        duckdb_count_output_command, "count output file", verbose=verbose
    )

    if not output_count_success:
        logging.error("Failed to execute output row count command. Verification aborted.")
        return False  # Indicate failure

    output_row_count = parse_duckdb_count(output_count_output)

    if output_row_count is None:
        logging.error("Could not determine output row count. Verification failed.")
        return False  # Indicate failure

    logging.info(f"Total rows in output file ('{output_path}'): {output_row_count}")

    # 4. Compare counts
    if input_row_count == output_row_count:
        logging.info("\nVerification successful: Row counts match!")
        return True  # Indicate success
    else:
        logging.error(
            f"\nVerification FAILED: Row counts do not match! Input={input_row_count}, Output={output_row_count}"
        )
        # Consider removing the failed output file if verification fails strongly
        # try:
        #     os.remove(output_path)
        #     print(f"Removed output file due to verification failure: {output_path}")
        # except OSError as e:
        #     print(f"Error removing failed output file {output_path}: {e}")
        return False  # Indicate failure


def summarize_schema_differences(chunk_schemas: dict[int, dict[str, str]], verbose: bool = False) -> None:
    """Analyzes collected schemas and prints a summary of differences."""
    logging.info("\n--- Schema Analysis Summary ---")
    if not chunk_schemas:
        logging.info("No schema information was collected.")
        return

    processed_chunks = 0
    error_chunks = 0
    all_columns = set()
    column_presence: dict[str, int] = defaultdict(int)
    column_types: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    error_messages: dict[str, int] = defaultdict(int)

    for _chunk_num, schema in chunk_schemas.items():
        processed_chunks += 1
        if "__error__" in schema:
            error_chunks += 1
            error_messages[schema["__error__"]] += 1
            continue  # Skip error schemas for column analysis

        for col_name, col_type in schema.items():
            all_columns.add(col_name)
            column_presence[col_name] += 1
            column_types[col_name][col_type] += 1

    successful_chunks = processed_chunks - error_chunks
    logging.info(f"Processed {processed_chunks} chunks.")
    logging.info(f"  Successfully introspected schema for: {successful_chunks} chunks.")
    if error_chunks > 0:
        logging.info(f"  Failed to get schema (or no simple columns found) for: {error_chunks} chunks.")
        if verbose:
            logging.info("  Error details:")
            for msg, count in error_messages.items():
                logging.info(f"    - '{msg}': {count} times")

    if not all_columns:
        logging.info("No columns found in successfully processed schemas.")
        return

    logging.info(f"Found {len(all_columns)} unique simple columns across {successful_chunks} successful chunks.")

    if verbose:
        logging.info("\n--- Detailed Column Analysis --- (Present in X / Y successful chunks)")
        # Sort columns alphabetically for consistent output
        sorted_columns = sorted(all_columns)
        for col_name in sorted_columns:
            presence_count = column_presence[col_name]
            types_observed = column_types[col_name]
            # Format types: "TYPE1 (N1), TYPE2 (N2)"
            type_str = ", ".join([f"{t} ({c})" for t, c in sorted(types_observed.items())])
            # Use consistent quoting style
            logging.info(f"- '{col_name}': Present in {presence_count}/{successful_chunks} chunks. Types: [{type_str}]")

    logging.info("-----------------------------")


# --- New Function for Schema Stats ---
def _generate_chunk_stats(input_files: list[str], stats_json_path: str, verbose: bool) -> bool:
    """Introspects schemas of input Parquet files and writes a JSON stats file.
    Args:
        input_files: List of paths to input Parquet files.
        stats_json_path: Path where the output JSON stats file should be written.
        verbose: Enable verbose logging.
    Returns:
        True if stats generation was successful, False otherwise.
    """
    logging.debug(f"--- Introspecting Schemas for Stats File: {stats_json_path} ---")
    all_chunk_schemas: dict[int | str, dict[str, str]] = {}
    column_locations: dict[str, list[int | str]] = defaultdict(list)
    column_frequencies: dict[str, int] = defaultdict(int)
    total_chunks_processed = len(input_files)
    introspection_errors = 0

    if not input_files:
        logging.warning("Warning: No input files provided for schema stats generation.")
        total_chunks_processed = 0

    try:
        # Use duckdb python client for introspection
        with duckdb.connect(":memory:") as con:
            for file_path in input_files:
                chunk_key: int | str = os.path.basename(file_path)  # Default key is filename
                match = re.search(r"chunk_(\d+)\.parquet$", str(chunk_key))
                if match:
                    chunk_key = int(match.group(1))  # Use chunk number if parseable

                if verbose:
                    logging.debug(f"  Analyzing schema for: {os.path.basename(file_path)} (Key: {chunk_key})")

                try:
                    file_path_sql = quote_sql_string(file_path)
                    describe_query = f"DESCRIBE SELECT * FROM read_parquet({file_path_sql}) LIMIT 0;"
                    rel = con.execute(describe_query)
                    col_names = [c[0] for c in rel.description]
                    schema_rows = [dict(zip(col_names, row, strict=False)) for row in rel.fetchall()]

                    if not schema_rows:
                        if verbose:
                            logging.debug(
                                f"  Warning: No schema information returned for {os.path.basename(file_path)}. Skipping."
                            )
                        all_chunk_schemas[chunk_key] = {"__error__": "No schema info returned"}
                        introspection_errors += 1
                        continue

                    current_schema = {}
                    for row in schema_rows:
                        col_name = row["column_name"]
                        col_type = str(row["column_type"]).upper()
                        current_schema[col_name] = col_type
                        key = f"{col_name} ({col_type})"
                        column_locations[key].append(chunk_key)
                        column_frequencies[key] += 1

                    if not current_schema:
                        if verbose:
                            logging.debug(
                                f"  Warning: No columns found in schema for {os.path.basename(file_path)}. Skipping."
                            )
                        all_chunk_schemas[chunk_key] = {"__error__": "No columns found in schema"}
                        introspection_errors += 1
                    else:
                        all_chunk_schemas[chunk_key] = current_schema

                except Exception:
                    error_msg = "Failed to describe schema"
                    logging.error(f"  Error analyzing {os.path.basename(file_path)}: {error_msg}")
                    all_chunk_schemas[chunk_key] = {"__error__": error_msg}
                    introspection_errors += 1

    except Exception as e:
        logging.error(f"\nAn error occurred during DuckDB connection for schema introspection: {e}")
        logging.error("Schema statistics generation failed.")
        return False  # Indicate failure

    logging.debug(
        f"Schema introspection complete. Errors encountered in {introspection_errors}/{total_chunks_processed} chunks."
    )

    # --- Sort Stats by Frequency ---
    # Sort column_frequencies by frequency (value), ascending
    sorted_freq_items = sorted(column_frequencies.items(), key=lambda item: item[1])
    sorted_column_frequencies = dict(sorted_freq_items)

    # Sort column_locations based on the frequency of the column (obtained from column_frequencies)
    # The key for sorting is the frequency associated with the column_locations key
    sorted_loc_items = sorted(column_locations.items(), key=lambda item: column_frequencies.get(item[0], 0))
    sorted_column_locations = dict(sorted_loc_items)

    # --- Generate Chunk Stats JSON ---
    stats_data = {
        "total_chunks_processed": total_chunks_processed,
        "introspection_errors": introspection_errors,
        "individual_chunk_schemas": all_chunk_schemas,
        "column_locations": sorted_column_locations,  # Use sorted dict
        "column_frequencies": sorted_column_frequencies,  # Use sorted dict
    }

    try:
        with open(stats_json_path, "w", encoding="utf-8") as f_json:
            # Set sort_keys=False to preserve the frequency-based order
            json.dump(stats_data, f_json, indent=2, sort_keys=False)
        logging.debug(f"Successfully generated chunk stats JSON: {stats_json_path}")
        return True  # Indicate success
    except OSError as e:
        logging.error(f"Error writing chunk stats JSON file '{stats_json_path}': {e}")
        return False  # Indicate failure
    except TypeError as e:
        logging.error(f"Error serializing chunk stats data to JSON: {e}")
        return False  # Indicate failure


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments and performs initial validation."""
    parser = argparse.ArgumentParser(
        description="Convert Zstandard compressed JSONL to Parquet using zstd and duckdb, or merge/analyze existing Parquet files."
    )
    parser.add_argument(
        "input_path",
        nargs="+",
        help="Path(s) to the input .zst file (default mode, one path only), OR path(s)/glob pattern(s) matching .parquet files (for --merge-only or --analyze-schemas modes).",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_path",
        help="Path for the final output Parquet file (.parquet). Required if --merge-only is used. Optional otherwise (defaults to input filename with .parquet extension). Ignored if --analyze-schemas is used.",
    )
    # ...
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging output.")
    parser.add_argument(
        "--chunk_size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Number of lines per processing chunk (default: {DEFAULT_CHUNK_SIZE:,})",
    )
    parser.add_argument(
        "--duckdb_path", default=DUCKDB_PATH, help=f"Path to the duckdb executable (default: '{DUCKDB_PATH}')"
    )
    parser.add_argument("--zstd_path", default=ZSTD_PATH, help=f"Path to the zstd executable (default: '{ZSTD_PATH}')")
    parser.add_argument("--temp_dir", default=None, help="Directory for temporary files (default: system temp)")
    parser.add_argument(
        "--test-run",
        action="store_true",
        help="Process only the first 2 chunks with smaller chunk size for testing.",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Skip final merge step, keep Parquet chunks in temp dir.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Skip decompression and chunking. Merge existing .parquet files from input_path directory into output_path.",
    )
    parser.add_argument(
        "--analyze-schemas",
        action="store_true",
        help="Analyze and report schema differences across Parquet files in the input directory. Does not merge or convert.",
    )
    parser.add_argument(
        "--log-dir",
        default="conversion_logs",
        help="Directory to store conversion log files (default: 'conversion_logs').",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Do not delete temporary directory even on failure or completion (for debugging).",
    )

    args = parser.parse_args()

    # --- Mode-specific Validation ---
    is_zst_conversion_mode = not args.analyze_schemas and not args.merge_only
    input_file_for_logic = args.input_path[0] if args.input_path else None  # Get first input for logic checks

    if args.analyze_schemas:
        # Analyze Schemas Mode Validation
        # Input can now be a glob pattern, validation moved to analyze_parquet_schemas
        if args.merge_only or args.no_merge or args.test_run:
            parser.error("--analyze-schemas cannot be used with --merge-only, --no-merge, or --test-run.")
        # output_path is now effectively ignored for analysis mode, stats file goes to log_dir
        if args.output_path is not None:
            logging.warning(
                f"Warning: output_path '{args.output_path}' is ignored when using --analyze-schemas. Stats JSON will be placed in the log directory."
            )  # Warn if output_path provided

        # Ensure other modes aren't accidentally triggered
        args.merge_only = False
        args.no_merge = True  # Effectively, as no merge happens
        args.test_run = False
    elif args.merge_only:
        # Merge-only Mode Validation
        if args.no_merge:
            parser.error("--merge-only and --no-merge cannot be used together.")
        if args.test_run:
            parser.error("--merge-only and --test-run cannot be used together.")
        if not args.output_path:
            parser.error("--output is required when using --merge-only.")
        # Input can now be a glob pattern, validation moved to handle_merge_only_mode
        # Ensure test_run logic isn't accidentally triggered later
        if args.chunk_size != DEFAULT_CHUNK_SIZE:
            logging.warning("Warning: --chunk_size is ignored in --merge-only mode.")
        args.test_run = False  # Explicitly disable test run in merge-only
    else:
        # ZST conversion mode validation
        if len(args.input_path) != 1:
            parser.error("Default ZST conversion mode requires exactly one input file path.")
        input_file = args.input_path[0]
        if not os.path.isfile(input_file):
            parser.error(f"Input path '{input_file}' is not a valid file for ZST conversion mode.")

    # --- Output Path Determination ---
    if not args.merge_only and args.output_path is None:
        input_file_for_output = args.input_path[0]  # Use first input for default output name
        base, ext = os.path.splitext(input_file_for_output)
        if ext.lower() == ".zst":
            args.output_path = base + ".parquet"
        else:
            logging.warning(
                f"Warning: Input file '{input_file_for_output}' does not end with .zst. Appending '.parquet' to input name for output."
            )
            args.output_path = input_file_for_output + ".parquet"
        logging.info(f"Output path not specified, defaulting to: {args.output_path}")

    # --- Tool Existence Validation ---
    if not shutil.which(args.duckdb_path):
        parser.error(
            f"Cannot find duckdb executable at '{args.duckdb_path}'. Use --duckdb_path or ensure it's in PATH."
        )
    if (
        not args.merge_only and not args.analyze_schemas and not shutil.which(args.zstd_path)
    ):  # Only check zstd if not merge/analyze
        parser.error(f"Cannot find zstd executable at '{args.zstd_path}'. Use --zstd_path or ensure it's in PATH.")

    # --- Adjust Chunk Size Based on Input Filename (RC vs RS) ---
    # Apply only in ZST conversion mode and NOT during a test run
    if is_zst_conversion_mode and not args.test_run and input_file_for_logic:
        input_filename_lower = os.path.basename(input_file_for_logic).lower()
        # Assuming 'RC' indicates comments (larger chunks) and 'RS' indicates submissions (default chunks)
        # Using 'rc' check to be broader, adjust if needed
        if "rc" in input_filename_lower:
            original_chunk_size = args.chunk_size
            args.chunk_size *= 4
            # Use logging AFTER setup_logging is called, so log this info later or print for now
            print(
                f"Info: Input filename suggests 'RC' data. Increasing chunk size 4x from {original_chunk_size:,} to {args.chunk_size:,}."
            )
            # logging.info(f"Input filename suggests 'RC' data. Increasing chunk size 4x from {original_chunk_size:,} to {args.chunk_size:,}.")

    # --- Adjustments for Test Run (AFTER potential RC multiplier) ---
    if args.test_run:
        # Store the potentially multiplied chunk size before overriding for test run
        original_chunk_size = args.chunk_size  # This might be 4x the default if it was an RC file
        args.chunk_size = TEST_RUN_CHUNK_SIZE  # Use constant
        logging.info("--- TEST RUN MODE ---")
        # Log the size it was *before* being set to TEST_RUN_CHUNK_SIZE
        logging.info(f"Overriding chunk size from {original_chunk_size:,} to {args.chunk_size:,}")
        logging.info("Will process at most 2 chunks.")
        logging.info("Temporary files will be kept.")

    return args


def handle_merge_only_mode(args: argparse.Namespace) -> None:
    """Handles the logic for the --merge-only mode."""
    # Setup logging first since we need it for the rest of the function
    log_filename_base = "merge_log"
    if args.output_path:
        log_filename_base = os.path.splitext(os.path.basename(args.output_path))[0]
    setup_logging(args.log_dir, log_filename_base, args.verbose)

    logging.info("--- Running in Merge-Only Mode ---")
    input_paths_or_patterns = args.input_path
    logging.info(f"Searching for Parquet files matching: {input_paths_or_patterns}")

    # Use glob on each item in the input list and collect results
    parquet_files_to_merge = []
    for item in input_paths_or_patterns:
        # Expand any environment variables in the path
        expanded_item = os.path.expandvars(item)
        # Expand any user home directory references
        expanded_item = os.path.expanduser(expanded_item)
        found_files = glob.glob(expanded_item)
        if not found_files and not os.path.exists(expanded_item):
            logging.warning(f"Warning: Input '{item}' did not match any files and is not an existing file.")
        parquet_files_to_merge.extend(found_files)

    # Filter for only .parquet files
    parquet_files_to_merge = [f for f in parquet_files_to_merge if f.lower().endswith(".parquet")]

    # Sort the found files for consistent order
    parquet_files_to_merge = sorted(set(parquet_files_to_merge))

    if not parquet_files_to_merge:
        logging.error(f"Error: No Parquet files found matching any input: {input_paths_or_patterns}")
        sys.exit(1)

    logging.info(f"Found {len(parquet_files_to_merge)} Parquet files to merge.")
    if args.verbose:
        logging.debug("Files to merge:")
        for f in parquet_files_to_merge:
            logging.debug(f"  - {f}")

    success = merge_and_verify_parquet(
        parquet_files_to_merge,
        args.output_path,  # Already validated to exist
        args.duckdb_path,
        args.log_dir,  # Pass log_dir for stats file
        verbose=args.verbose,
    )

    if success:
        logging.info("\nMerge-only operation completed successfully.")
        sys.exit(0)
    else:
        logging.error("\nMerge-only operation failed.")
        sys.exit(1)


def handle_zst_to_parquet_mode(args: argparse.Namespace) -> None:
    """Handles the logic for converting ZST to Parquet."""
    logging.info("--- Running in ZST-to-Parquet Mode ---")
    input_zst_path = args.input_path[0]
    output_parquet_path = args.output_path  # Already determined

    # Estimate total size for progress reporting
    estimated_decompressed_size = _estimate_decompressed_size(input_zst_path)

    # 1. Try to load the fixed Master Schema for consistency
    standard_columns = load_master_schema(input_zst_path)
    if standard_columns:
        logging.info(
            f"Using fixed Master Schema with {len(standard_columns)} columns for consistency across all months."
        )
    else:
        # Fallback to monthly dynamic schema if master is missing
        schema_path = find_schema_for_file(input_zst_path)
        if schema_path:
            logging.info(f"Master schema not found. Falling back to monthly schema: {schema_path}")
            standard_columns = load_standard_columns(schema_path)
            logging.info(f"Loaded {len(standard_columns)} standard columns from monthly schema.")
        else:
            logging.warning("No master or monthly schema found. Falling back to dynamic simple columns.")

    # Determine and create the temporary directory
    temp_dir_path = _setup_temp_directory(args.temp_dir, output_parquet_path)
    if temp_dir_path is None:
        sys.exit(1)  # Error creating temp dir

    # --- Initialize Resume State ---
    start_chunk_num, lines_to_skip, initial_parquet_files = _initialize_resume_state(temp_dir_path, args.chunk_size)
    # Initialize based on resume state
    parquet_chunk_files: list[str] = initial_parquet_files
    chunk_schemas: dict[int, dict[str, str]] = {}
    merge_and_verify_successful = False  # Initialize success flag
    zstd_proc = None  # Define before try block
    line_count = 0  # Start line count from 0 for skipping logic
    chunk_num = start_chunk_num - 1  # Start chunk_num logic correctly
    cumulative_bytes_processed = 0  # Reset progress estimate for simplicity

    try:
        # Start zstd decompression process
        zstd_cmd = [args.zstd_path, "-dcvf", f"--long={ZSTD_LONG_RANGE_BITS}", input_zst_path]

        if args.verbose:
            logging.debug(f"Starting decompression command: {' '.join(zstd_cmd)}")
        try:
            zstd_proc = subprocess.Popen(
                zstd_cmd, stdout=subprocess.PIPE, text=True, errors="replace", encoding="utf-8"
            )
        except FileNotFoundError:
            logging.error(f"Error: Failed to start zstd process. Command not found at '{args.zstd_path}'.")
            sys.exit(1)
        except Exception as e:
            logging.error(f"Error starting zstd process: {e}")
            sys.exit(1)

        if zstd_proc.stdout is None:
            logging.error("Error: Could not get stdout from zstd process.")
            sys.exit(1)

        # --- Skip Lines for Resume ---
        if lines_to_skip > 0:
            logging.info(f"Skipping {lines_to_skip:,} lines...")
            skipped_count = 0
            try:
                for _ in range(lines_to_skip):
                    line = zstd_proc.stdout.readline()
                    if not line:
                        logging.warning(
                            f"Warning: End of stream reached while skipping lines after {skipped_count} lines (expected {lines_to_skip})."
                        )
                        break
                    skipped_count += 1
                line_count = skipped_count  # Update line count to reflect skipped lines
                logging.info(f"Skipped {line_count:,} lines successfully.")
            except Exception as e:
                logging.error(f"\nError skipping lines from zstd stream: {e}")
                sys.exit(1)
            if line_count < lines_to_skip:
                logging.warning(
                    "Warning: Input stream ended before all lines could be skipped. Proceeding, but results may be incomplete."
                )

        # Reset chunk_num to the actual starting chunk number for the loop
        chunk_num = start_chunk_num - 1  # Will be incremented to start_chunk_num at loop start

        # --- Chunk Processing Loop ---
        while True:
            chunk_num += 1
            chunk_start_line = line_count + 1
            chunk_lines: list[str] = []  # Initialize chunk_lines here

            # Read lines for the current chunk
            try:
                for _ in range(args.chunk_size):
                    line = zstd_proc.stdout.readline()
                    if not line:  # End of stream
                        break
                    chunk_lines.append(line)
                    line_count += 1
            except Exception as e:
                logging.error(f"\nError reading from zstd stream during chunk {chunk_num}: {e}")
                # Check zstd process status
                if zstd_proc.poll() is not None:
                    logging.error(f"zstd process exited unexpectedly with code {zstd_proc.returncode}.")
                break  # Exit the loop on read error

            if not chunk_lines:  # No more lines read, previous chunk was the last
                chunk_num -= 1  # Decrement chunk_num as no lines were added
                logging.info("End of input stream reached.")
                break

            chunk_end_line = line_count

            # Process the collected chunk
            chunk_byte_size_or_none = _process_chunk(
                args,
                temp_dir_path,
                chunk_num,
                chunk_lines,
                chunk_start_line,
                chunk_end_line,
                estimated_decompressed_size,
                cumulative_bytes_processed,
                chunk_schemas,
                parquet_chunk_files,
                standard_columns,  # Pass standard columns
            )

            # Check for failure and exit if needed
            if chunk_byte_size_or_none is None:
                logging.error(f"\nError processing chunk {chunk_num}. Exiting.")
                # Cleanup is handled in the finally block
                sys.exit(1)
            else:
                # Update cumulative size on success
                cumulative_bytes_processed += chunk_byte_size_or_none
                # parquet_chunk_files list is updated inside _process_chunk now

            # Check if the zstd process has exited unexpectedly AFTER reading
            if zstd_proc.poll() is not None and not chunk_lines:  # Check poll only if readline returned empty last time
                logging.error(f"zstd process exited with code {zstd_proc.returncode} while reading data.")
                break

            # Check for test run condition
            if args.test_run and chunk_num >= 2:
                logging.info("Test run: Stopping after 2 chunks.")
                break  # Will terminate zstd in finally block

        # --- Post-Processing ---

        # Check for zstd errors after processing all output
        if zstd_proc:
            zstd_returncode = zstd_proc.poll()  # Check if already exited
            if zstd_returncode is None:  # If still running (e.g., test run break)
                logging.warning("Terminating zstd process...")
                zstd_proc.terminate()
                try:
                    zstd_returncode = zstd_proc.wait(timeout=ZSTD_TERMINATION_TIMEOUT_SECONDS)  # Wait briefly

                except subprocess.TimeoutExpired:
                    logging.warning(
                        f"Warning: zstd did not terminate gracefully after {ZSTD_TERMINATION_TIMEOUT_SECONDS}s, killing..."
                    )
                    zstd_proc.kill()
                    zstd_returncode = zstd_proc.wait()  # Get final code after kill
            if zstd_returncode != 0:
                # Non-zero might be okay if terminated (SIGTERM often 143)
                logging.warning(f"Warning: zstd process finished with exit code {zstd_returncode}.")
                # Consider checking stderr if available and return code indicates error

        # --- Schema Summary ---
        if chunk_schemas:
            summarize_schema_differences(chunk_schemas, args.verbose)

        # --- Merge Step ---
        if not parquet_chunk_files:
            logging.error("\nError: No Parquet chunk files were successfully created.")
            logging.error("Input file might be empty, contain only invalid JSON, or all chunks failed processing.")
            sys.exit(1)  # Exit if no chunks produced

        if not args.no_merge:
            logging.info(
                f"\nCombining {len(parquet_chunk_files)} Parquet chunks into final file: {output_parquet_path}"
            )

            # Optional: Inspect schemas during test run
            if args.test_run:
                _inspect_chunk_schemas(parquet_chunk_files, args.duckdb_path)

            merge_and_verify_successful = merge_and_verify_parquet(
                parquet_chunk_files,
                output_parquet_path,
                args.duckdb_path,
                args.log_dir,  # Pass log_dir for stats file
                verbose=args.verbose,
            )
            if not merge_and_verify_successful:
                logging.error("\nMerge and verification step failed.")
                # Keep temp files in this case (handled in finally)
                sys.exit(1)  # Exit if merge/verify fails
            else:
                logging.info(f"\nSuccessfully created merged Parquet file: {output_parquet_path}")

        else:  # --no-merge specified
            logging.info("\nSkipping final merge step (--no-merge specified).")
            logging.info(f"Parquet chunks are located in: {temp_dir_path}")
            merge_and_verify_successful = True  # Consider successful if no merge requested

    except Exception as e:
        logging.error(f"\nAn unexpected error occurred during ZST processing: {e}")
        import traceback

        traceback.print_exc()  # Print traceback for debugging
        sys.exit(1)  # General exit code for unexpected errors

    finally:
        # Gracefully terminate zstd if it's somehow still running
        if zstd_proc and zstd_proc.poll() is None:
            logging.warning("Terminating zstd process in finally block...")
            zstd_proc.terminate()
            try:
                zstd_proc.wait(timeout=ZSTD_TERMINATION_TIMEOUT_SECONDS)

            except subprocess.TimeoutExpired:
                zstd_proc.kill()

        # Clean up temporary directory
        _cleanup_temp_directory(temp_dir_path, args, merge_and_verify_successful)


def _initialize_resume_state(temp_dir_path: str, chunk_size: int) -> tuple[int, int, list[str]]:
    """Checks for existing chunks in the temp directory to determine resume state.

    Returns:
        Tuple[int, int, List[str]]: start_chunk_num, lines_to_skip, initial_parquet_files
    """
    logging.info(f"Checking for existing chunks in {temp_dir_path}...")
    existing_parquet_files = sorted(glob.glob(os.path.join(temp_dir_path, "chunk_*.parquet")))
    chunk_numbers_found = set()
    max_chunk_num = 0

    if not existing_parquet_files:
        logging.info("No existing Parquet chunks found. Starting fresh.")
        return 1, 0, []

    for f in existing_parquet_files:
        match = re.search(r"chunk_(\d+)\.parquet$", os.path.basename(f))
        if match:
            num = int(match.group(1))
            chunk_numbers_found.add(num)
            max_chunk_num = max(max_chunk_num, num)

    if not chunk_numbers_found:
        logging.info("Found Parquet files, but couldn't parse chunk numbers. Starting fresh.")
        # Consider cleaning up unrecognized files here if desired
        return 1, 0, []

    # Check for contiguous chunks
    expected_chunks = set(range(1, max_chunk_num + 1))
    if chunk_numbers_found == expected_chunks:
        start_chunk_num = max_chunk_num + 1
        lines_to_skip = max_chunk_num * chunk_size
        logging.info(f"Found contiguous chunks up to {max_chunk_num}. Resuming from chunk {start_chunk_num}.")
        logging.info(f"Will skip approximately {lines_to_skip:,} lines from the input.")
        return start_chunk_num, lines_to_skip, existing_parquet_files
    else:
        missing = expected_chunks - chunk_numbers_found
        extra = chunk_numbers_found - expected_chunks
        logging.warning("Warning: Found non-contiguous or unexpected chunk numbers.")
        if missing:
            logging.warning(f"  Missing chunk numbers: {sorted(missing)}")
        if extra:
            logging.warning(f"  Unexpected chunk numbers: {sorted(extra)}")
        logging.warning("Inconsistent state detected. Cleaning temporary directory and starting fresh for safety.")
        try:
            for f in glob.glob(os.path.join(temp_dir_path, "chunk_*")):
                os.remove(f)
        except Exception:
            logging.error("Error cleaning temporary directory. Please clean manually or use --temp_dir.")
            # Decide whether to exit or proceed? Proceeding might be okay.
        return 1, 0, []


def _estimate_decompressed_size(input_path: str) -> int | None:
    """Estimates the decompressed size of the input file."""
    try:
        input_file_size = os.path.getsize(input_path)
        estimated_decompressed_size = input_file_size * DEFAULT_COMPRESSION_RATIO_ESTIMATE
        logging.info(f"---> Input file size: {format_size(input_file_size)}")
        logging.info(
            f"---> Estimated decompressed size: ~{format_size(estimated_decompressed_size)} (using {DEFAULT_COMPRESSION_RATIO_ESTIMATE}:1 ratio)"
        )
        return estimated_decompressed_size
    except OSError as e:
        logging.warning(f"Warning: Could not get size of input file {input_path}: {e}")
        logging.warning("---> Could not estimate total size (input size unavailable?) <---")
        return None


def _setup_temp_directory(temp_dir_base: str | None, output_path: str) -> str | None:
    """Creates or finds and returns the path to a deterministic temporary directory."""
    # Default to current directory '.' if no temp_dir_base is provided
    temp_base_dir = temp_dir_base if temp_dir_base else "."
    # Generate a deterministic temp directory name based on the output file
    output_base_name = os.path.splitext(os.path.basename(output_path))[0]
    # Use a simpler, fixed temporary directory name suffix
    temp_dir_name = f"{output_base_name}.zst_parquet_tmp"
    temp_dir_path = os.path.join(temp_base_dir, temp_dir_name)

    try:
        # Create the directory if it doesn't exist
        os.makedirs(temp_dir_path, exist_ok=True)
        logging.info(f"Using temporary directory: {temp_dir_path}")
        return temp_dir_path
    except Exception as e:
        logging.error(f"Error ensuring temporary directory exists at '{temp_dir_path}': {e}")
        return None


def _process_chunk(
    args: argparse.Namespace,
    temp_dir_path: str,
    chunk_num: int,
    chunk_lines: list[str],
    chunk_start_line: int,
    chunk_end_line: int,
    estimated_total_size: int | None,
    current_cumulative_bytes: int,
    chunk_schemas: dict[int, dict[str, str]],
    parquet_chunk_files: list[str],
    standard_columns: list[str] | None = None,  # Added standard_columns
) -> int | None:
    """
    Processes a single chunk of lines: writes JSONL, introspects schema,
    converts to Parquet, and cleans up JSONL. Updates chunk_schemas dict directly
    and appends to parquet_chunk_files list.
    Returns chunk_byte_size on success, None on failure.
    """
    if standard_columns is None:
        standard_columns = []
    progress_str = f"Processing chunk {chunk_num:>4} (lines {chunk_start_line:,}-{chunk_end_line:,})"
    temp_jsonl_filename = os.path.join(temp_dir_path, f"chunk_{chunk_num:05d}.jsonl")
    temp_parquet_filename = os.path.join(temp_dir_path, f"chunk_{chunk_num:05d}.parquet")
    chunk_byte_size = 0

    try:
        # Write chunk to temporary JSONL file
        with open(temp_jsonl_filename, "w", encoding="utf-8") as f_jsonl:
            f_jsonl.writelines(chunk_lines)

        # Update cumulative size and progress string
        try:
            chunk_byte_size = os.path.getsize(temp_jsonl_filename)
            cumulative_bytes_processed = current_cumulative_bytes + chunk_byte_size
            if estimated_total_size is not None and estimated_total_size > 0:
                percentage = min(100.0, (cumulative_bytes_processed / estimated_total_size) * 100)  # Cap at 100%
                progress_str += f" | Size: {format_size(chunk_byte_size):>8} | Total: ~{format_size(cumulative_bytes_processed):>8} / {format_size(estimated_total_size)} ({percentage: >5.1f}%)"
            else:
                progress_str += f" | Size: {format_size(chunk_byte_size):>8} | Total: {format_size(cumulative_bytes_processed)} (total size unknown)"
        except Exception:
            progress_str += " | (Size info unavailable)"

        print(progress_str, end="\r", flush=True)  # Use carriage return for progress update

        # --- Introspect and filter columns ---
        duckdb_query = ""
        try:
            # Connect to an in-memory DuckDB database for introspection
            # Using a file like ':memory:' might not persist state if needed across calls,
            # but for single introspection it's fine.
            with duckdb.connect(":memory:") as con:  # Use context manager
                con.execute("SET threads=1;")  # Keep introspection to 1 thread to avoid overhead
                con.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT_GB}GB';")
                # Get schema from the JSONL chunk
                # Using LIMIT 0 makes describe faster as it only needs schema
                # Use SQL quoting for the filename
                temp_jsonl_filename_sql = quote_sql_string(temp_jsonl_filename)
                describe_query = f"""
                DESCRIBE SELECT * FROM read_json({temp_jsonl_filename_sql},
                                                 union_by_name=true,
                                                 format='newline_delimited',
                                                 ignore_errors=true,
                                                 maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE})
                LIMIT 0;
                """
                # Using a larger maximum_object_size can help with complex nested JSONs during schema detection
                if args.verbose:
                    # Ensure newline if printing verbose schema info after progress bar
                    logging.debug(f"\nRunning DESCRIBE query for {os.path.basename(temp_jsonl_filename)}")

                # Fetch results as a list of dictionaries to avoid Pandas dependency
                rel = con.execute(describe_query)
                col_names = [c[0] for c in rel.description]
                schema_rows = [dict(zip(col_names, row, strict=False)) for row in rel.fetchall()]

                if args.verbose:
                    logging.debug(f"Schema for chunk {chunk_num}: {schema_rows}")

                # --- Filter and Bundle Columns ---
                if standard_columns:
                    # Use provided standard columns
                    chunk_cols = [row["column_name"] for row in schema_rows]
                    chunk_cols_set = set(chunk_cols)
                    standard_set = set(standard_columns)
                    final_selects = []
                    # 2. Add columns that exist in the chunk (with explicit normalization)
                    for col in standard_columns:
                        if col in chunk_cols_set:
                            if col == "edited":
                                # Normalize 'edited' (bool/int flip) to BIGINT
                                final_selects.append("""
                                    CASE
                                        WHEN try_cast(edited AS BOOLEAN) IS TRUE THEN 1
                                        WHEN try_cast(edited AS BOOLEAN) IS FALSE THEN 0
                                        ELSE try_cast(edited AS BIGINT)
                                    END AS edited
                                """)
                            elif col in BIGINT_COLUMNS:
                                final_selects.append(f'TRY_CAST("{col}" AS BIGINT) AS "{col}"')
                            elif col in BOOLEAN_COLUMNS:
                                final_selects.append(f'TRY_CAST("{col}" AS BOOLEAN) AS "{col}"')
                            else:
                                # Default to VARCHAR for all other standard columns.
                                # This is CRITICAL to prevent conflicts between VARCHAR and JSON/STRUCT/LIST
                                # which often occur in messy Reddit data (e.g., top_awarded_type).
                                final_selects.append(f'TRY_CAST("{col}" AS VARCHAR) AS "{col}"')
                        # We DON'T add NULLs here to avoid type conflicts during merge.
                        # DuckDB's union_by_name=true will handle missing columns automatically.

                    # 3. Bundle all extra columns into extra_json
                    extra_cols = [c for c in chunk_cols if c not in standard_set]
                    if extra_cols:
                        # Construct struct_pack for all extra columns
                        struct_fields = ", ".join([f'"{c}" := "{c}"' for c in extra_cols])
                        final_selects.append(f"to_json(struct_pack({struct_fields})) AS extra_json")
                    else:
                        final_selects.append("CAST(NULL AS VARCHAR) AS extra_json")

                    select_clause = ", ".join(final_selects)
                    current_schema = {col: "DYNAMIC" for col in standard_columns if col in chunk_cols_set}
                else:
                    # Original dynamic simple columns logic
                    simple_columns = []
                    current_schema = {}
                    for row in schema_rows:
                        col_name = row["column_name"]
                        col_type = str(row["column_type"]).upper()
                        if not any(ind in col_type for ind in COMPLEX_TYPE_INDICATORS):
                            simple_columns.append(f'"{col_name}"')
                            current_schema[col_name] = col_type

                    if not simple_columns:
                        logging.warning(f"\nWarning: No simple columns found for chunk {chunk_num}. Skipping.")
                        return None
                    select_clause = ", ".join(simple_columns)

                # Build the final COPY command
                temp_parquet_filename_sql = quote_sql_string(temp_parquet_filename)
                duckdb_query = f"""
                COPY (
                    SELECT {select_clause}
                    FROM read_json({temp_jsonl_filename_sql},
                                   union_by_name=true,
                                   format='newline_delimited',
                                   ignore_errors=true,
                                   maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE})
                    ORDER BY author ASC, subreddit ASC, created_utc ASC
                ) TO {temp_parquet_filename_sql} (FORMAT PARQUET, CODEC {DUCKDB_ZSTD_CODEC});

                """
                if args.verbose:
                    logging.debug(f"Generated COPY query for chunk {chunk_num}:")
                    logging.debug(duckdb_query)
                # Store the successful simple schema for this chunk
                chunk_schemas[chunk_num] = current_schema
                schema_success = True  # Mark as succeeded

        except Exception as e:
            logging.error(
                f"\nError during schema introspection or query generation for chunk {chunk_num} ({temp_jsonl_filename}): {e}"
            )
            logging.warning("Skipping Parquet conversion for this chunk.")
            chunk_schemas[chunk_num] = {"__error__": f"Introspection failed: {e}"}  # Record schema error
            schema_success = False  # Mark as failed
            # Optionally close connection if open - Context manager handles this
            # try: con.close()
            # except Exception: pass
            # Continue handled below

        # Skip DuckDB execution if schema introspection failed
        if not schema_success:
            # Clean up the JSONL file now if needed (and not test run)
            if not args.test_run and os.path.exists(temp_jsonl_filename):
                logging.warning(f"\nCleaning up failed chunk's JSONL: {temp_jsonl_filename}")
                try:
                    os.remove(temp_jsonl_filename)
                except OSError as e:
                    logging.warning(f"Warning: Failed to remove JSONL {temp_jsonl_filename}: {e}")
            return None  # Indicate failure for this chunk

        # Execute the generated DuckDB command
        if duckdb_query:  # Only run if query was successfully generated
            try:
                # Use run_duckdb_command now, pass verbose flag
                _, success = run_duckdb_command(
                    [
                        args.duckdb_path,
                        "-c",
                        duckdb_query,
                    ],  # Use command line for COPY TO for simplicity here
                    f"convert chunk {chunk_num} to parquet",
                    verbose=args.verbose,
                )
                if not success:
                    raise RuntimeError(f"DuckDB command failed for chunk {chunk_num}")
                # Append to the list if conversion successful
                parquet_chunk_files.append(temp_parquet_filename)
                if args.verbose:
                    logging.debug(
                        f"\nSuccessfully converted chunk {chunk_num} to {temp_parquet_filename}"
                    )  # Newline if verbose
            except Exception as e:
                logging.error(f"\nError running DuckDB command for chunk {chunk_num}: {e}")
                # Decide if you want to stop or continue
                # For now, let's continue to the next chunk
                # Clean up JSONL if conversion failed
                if not args.test_run and os.path.exists(temp_jsonl_filename):
                    logging.warning(f"\nCleaning up JSONL for failed conversion: {temp_jsonl_filename}")
                    try:
                        os.remove(temp_jsonl_filename)
                    except OSError as e_rem:
                        logging.warning(f"Warning: Failed to remove JSONL {temp_jsonl_filename}: {e_rem}")
                return None  # Indicate failure for this chunk
        else:
            # This case should not be hit due to schema_success check above
            logging.warning(f"\nSkipped DuckDB command execution for chunk {chunk_num} due to previous errors.")
            # Clean up JSONL
            if not args.test_run and os.path.exists(temp_jsonl_filename):
                logging.warning(f"\nCleaning up JSONL for skipped execution: {temp_jsonl_filename}")
                try:
                    os.remove(temp_jsonl_filename)
                except OSError as e_rem:
                    logging.warning(f"Warning: Failed to remove JSONL {temp_jsonl_filename}: {e_rem}")
            return None  # Indicate failure for this chunk

    except Exception as e:
        logging.error(f"\nAn unexpected error occurred processing chunk {chunk_num}: {e}")
        import traceback

        traceback.print_exc()
        return None  # Indicate general failure for this chunk

    finally:
        # Delete the temporary JSONL file for this chunk (unless in test run)
        if not args.test_run and os.path.exists(temp_jsonl_filename):
            logging.debug(f"Deleting temporary JSONL: {temp_jsonl_filename}")
            try:
                os.remove(temp_jsonl_filename)
            except OSError as e:
                logging.warning(f"\nWarning: Failed to delete temp JSONL {temp_jsonl_filename}: {e}")
        else:
            if args.verbose:
                logging.debug(f"Test run: Keeping temporary JSONL: {temp_jsonl_filename}")

    # If we reach here, processing was successful
    # Return chunk byte size, schema/file list were updated directly
    return chunk_byte_size


def _inspect_chunk_schemas(parquet_files: list[str], duckdb_path: str) -> None:
    """Inspects and prints the schema of given Parquet files (for debugging/test run)."""
    logging.info("\n--- Inspecting Parquet Chunk Schemas (Test Run) ---")
    for chunk_file in parquet_files:
        logging.info(f"--- Schema for {os.path.basename(chunk_file)} ---")
        try:
            # Ensure duckdb_path is used here too
            # Use the SQL-quoted variable directly
            chunk_file_sql_quoted = quote_sql_string(chunk_file)
            schema_query = f"DESCRIBE SELECT * FROM read_parquet({chunk_file_sql_quoted});"
            # Use run_duckdb_command for consistency
            schema_output, schema_success = run_duckdb_command(
                [duckdb_path, "-c", schema_query],
                f"inspect schema {os.path.basename(chunk_file)}",
                verbose=False,  # Usually don't need verbose output from the helper here
            )
            if schema_success and schema_output:
                logging.info(schema_output.strip())  # Print the actual schema
            elif schema_success:
                logging.info(" (Schema inspection command produced no output) ")
            else:
                logging.error(f"Error inspecting schema for {os.path.basename(chunk_file)}")
        except Exception as e:
            logging.error(f"Error inspecting schema for {chunk_file}: {e}")
    logging.info("----------------------------------------------------")


def _cleanup_temp_directory(temp_dir_path: str | None, args: argparse.Namespace, merge_successful: bool) -> None:
    """Cleans up the temporary directory based on script arguments and success."""
    if temp_dir_path and os.path.exists(temp_dir_path):
        if args.no_merge:
            logging.info(f"\n--no-merge specified: Keeping temporary directory with Parquet chunks: {temp_dir_path}")
        elif args.test_run:
            logging.info(f"\nTest run: Keeping temporary directory with Parquet/JSONL chunks: {temp_dir_path}")
        elif merge_successful:  # Standard mode success
            logging.info(f"\nCleaning up temporary directory: {temp_dir_path}")
            try:
                shutil.rmtree(temp_dir_path)
                logging.info("Cleanup complete.")
            except OSError as e:
                logging.error(f"Error cleaning up temporary directory {temp_dir_path}: {e}")
        else:  # Merge/Verification failed or other error occurred
            if args.keep_temp:
                logging.warning(
                    f"\nErrors occurred or merge failed: Keeping temporary directory for inspection (due to --keep-temp): {temp_dir_path}"
                )
            else:
                logging.warning(
                    "\nErrors occurred or merge failed: Cleaning up temporary directory for safety (use --keep-temp to inspect)."
                )
                try:
                    shutil.rmtree(temp_dir_path)
                    logging.info("Cleanup complete.")
                except OSError as e:
                    logging.error(f"Error cleaning up temporary directory {temp_dir_path}: {e}")
    elif temp_dir_path:
        logging.warning(f"\nTemporary directory {temp_dir_path} does not exist (creation failed?), skipping cleanup.")
    # else: temp_dir_path was None or not created


def analyze_parquet_schemas(input_paths_or_patterns: list[str], verbose: bool, log_dir: str) -> None:
    """Analyzes schemas of all Parquet files matching pattern(s), prints summary, and writes detailed stats to JSON in the log directory."""
    logging.info(f"--- Analyzing Parquet Schemas Matching: {input_paths_or_patterns} ---")

    # Use glob on each item in the input list and collect results
    parquet_files = []
    for item in input_paths_or_patterns:
        logging.info(f"Searching for Parquet files matching pattern: {item}")
        found_files = glob.glob(item)
        if not found_files and item not in parquet_files:
            logging.warning(f"Warning: Input '{item}' did not match any files and is not an existing file.")
        parquet_files.extend(found_files)

    # --- Debugging: Print files found by glob --- #
    # print(f"DEBUG: Combined files found by glob: {parquet_files}") # Keep commented out unless needed

    # Remove duplicates and sort
    parquet_files = sorted(set(parquet_files))

    if not parquet_files:
        logging.error(f"Error: No Parquet files found matching any input: {input_paths_or_patterns}")
        sys.exit(1)

    logging.info(f"Found {len(parquet_files)} Parquet files to analyze.")

    # --- Generate Stats File in Log Directory ---
    stats_filename = "schema_analysis.chunk_stats.json"  # Fixed filename for analysis mode
    output_stats_path = os.path.join(log_dir, stats_filename)
    logging.info(f"Attempting to generate schema stats file at: {output_stats_path}")

    stats_generated = _generate_chunk_stats(parquet_files, output_stats_path, verbose)
    if not stats_generated:
        logging.warning(f"Warning: Failed to generate schema stats file at {output_stats_path}")
    # else:
    #     logging.info("No output path specified for stats file (--output_path), skipping JSON generation.") # Removed this else block

    # --- Console Summary Printing Removed ---
    # The detailed stats are now in the JSON file generated by _generate_chunk_stats.


def main() -> None:
    """Main function to orchestrate the conversion or merge process."""
    args = parse_arguments()

    # Determine log filename base from output path
    log_filename_base = "conversion_log"
    if args.output_path:
        log_filename_base = os.path.splitext(os.path.basename(args.output_path))[0]

    # Setup logging
    setup_logging(args.log_dir, log_filename_base, args.verbose)

    # Handle analyze-schemas mode first if specified
    if args.analyze_schemas:
        logging.info("--- Running in Analyze Schemas Mode ---")  # Use logging
        # Pass log_dir for stats file location, remove duckdb_path and output_path args
        analyze_parquet_schemas(args.input_path, args.verbose, args.log_dir)
        sys.exit(0)  # Exit after analysis

    if args.merge_only:
        handle_merge_only_mode(args)
    else:
        handle_zst_to_parquet_mode(args)


if __name__ == "__main__":
    main()
