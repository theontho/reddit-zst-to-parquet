#!/usr/bin/env python3
import contextlib
import os
import subprocess
import sys
import tempfile

# Add parent directory to path to allow imports from the main package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import duckdb

from core.config import (
    DUCKDB_MAXIMUM_OBJECT_SIZE,
    DUCKDB_MEMORY_LIMIT_GB,
    DUCKDB_PARQUET_COMPRESSION_CODEC,
    DUCKDB_PRESERVE_INSERTION_ORDER,
    DUCKDB_ROW_GROUP_SIZE,
    DUCKDB_THREADS,
    TOTAL_RAM_GB,
    ZSTD_LONG_RANGE_BITS,
    ZSTD_PATH,
)
from core.schema_parser import build_arrow_schema

# Configuration
COMPRESSION = "zstd"


def find_schema_for_file(zst_path: str) -> str | None:
    """Heuristic to find the matching JSON schema in the schemas/ directory."""
    filename = os.path.basename(zst_path)
    parts = filename.split("_")
    if len(parts) < 2:
        return None

    prefix = parts[0]  # RC or RS
    date_part = parts[1].split(".")[0]  # YYYY-MM
    year = date_part.split("-")[0]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    schemas_dir = os.path.join(script_dir, "../schemas")

    schema_path = os.path.join(schemas_dir, prefix, year, f"{prefix}_{date_part}.json")
    if os.path.exists(schema_path):
        return schema_path
    return None


def arrow_to_duckdb_type(arrow_field) -> str:
    """Maps Arrow types to DuckDB SQL types."""
    import pyarrow.types as types

    t = arrow_field.type
    if types.is_string(t):
        return "VARCHAR"
    if types.is_int64(t):
        return "BIGINT"
    if types.is_float64(t):
        return "DOUBLE"
    if types.is_boolean(t):
        return "BOOLEAN"
    if types.is_struct(t) or types.is_list(t) or types.is_map(t):
        return "JSON"
    return "VARCHAR"


def convert_file(input_path: str, output_path: str | None = None) -> None:
    if not output_path:
        output_path = input_path.replace(".zst", ".parquet").replace(".zst_blocks", ".parquet")

    schema_json = find_schema_for_file(input_path)
    if not schema_json:
        print(f"Error: Could not find schema for {input_path}. Skipping.")
        return

    # Check file size to determine strategy
    file_size_gb = os.path.getsize(input_path) / (1024**3)
    # Dynamic Resource Scaling based on RAM-to-File-Size ratio
    limit_gb = DUCKDB_MEMORY_LIMIT_GB
    active_threads = DUCKDB_THREADS

    # Calculate risk: how much of our RAM budget does this one file represent?
    # Compressed files expand significantly, so we use a conservative ratio.
    risk_ratio = file_size_gb / limit_gb

    if file_size_gb > 10:
        # Behemoth file (>10GB compressed, ~160GB+ uncompressed).
        # Force ultra-stable mode: 1 thread and aggressive disk spilling.
        active_threads = 1
        # Use 48GB on massive machines, 24GB otherwise.
        behemoth_cap = 48 if TOTAL_RAM_GB > 100 else 24
        limit_gb = min(limit_gb, behemoth_cap)

    elif risk_ratio > 0.6:
        # High risk: File is > 60% of RAM.
        active_threads = 1
        limit_gb = int(TOTAL_RAM_GB * 0.5)
    elif risk_ratio > 0.4:
        # Medium-High risk: File is > 40% of RAM.
        active_threads = max(1, active_threads // 2)
        limit_gb = int(TOTAL_RAM_GB * 0.6)
    elif risk_ratio > 0.2:
        # Medium risk: File is > 20% of RAM.
        active_threads = max(1, active_threads // 2)

    # Ensure working_dir is absolute for DuckDB temp_directory
    working_dir = os.path.dirname(os.path.abspath(output_path))
    abs_working_dir = os.path.abspath(working_dir)


    print(f"Using Streamed-DuckDB-Converter for: {input_path} ({file_size_gb:.2f} GB)")

    print(f"Resource Profile: {limit_gb}GB RAM limit, {active_threads} threads")

    arrow_schema = build_arrow_schema(schema_json)

    columns_config = {}
    select_clauses = []
    for field in arrow_schema:
        if field.name == "extra_json":
            continue
        dtype = arrow_to_duckdb_type(field)
        columns_config[field.name] = dtype
        if field.name == "edited":
            select_clauses.append("""
                CASE
                    WHEN try_cast(edited AS BOOLEAN) IS TRUE THEN 1
                    WHEN try_cast(edited AS BOOLEAN) IS FALSE THEN 0
                    ELSE try_cast(edited AS BIGINT)
                END AS edited
            """)
        else:
            select_clauses.append(f'try_cast("{field.name}" AS {dtype}) AS "{field.name}"')

    col_list = ", ".join(select_clauses)
    cols_param = "{" + ", ".join([f"'{k}': '{v}'" for k, v in columns_config.items()]) + "}"

    working_dir = os.path.dirname(os.path.abspath(output_path))
    temp_db_path = os.path.join(working_dir, f"{os.path.basename(output_path)}.duckdb_temp")
    fifo_path = os.path.join(tempfile.gettempdir(), f"duckdb_fifo_{os.getpid()}")

    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)
    if os.path.exists(fifo_path):
        os.remove(fifo_path)
    os.mkfifo(fifo_path)

    try:
        # Start zstd with high-memory support
        zstd_cmd = f'"{ZSTD_PATH}" -dcf --long={ZSTD_LONG_RANGE_BITS} "{input_path}" > "{fifo_path}"'

        zstd_proc = subprocess.Popen(zstd_cmd, shell=True)

        # Use an in-memory connection for the conversion
        con = duckdb.connect(":memory:")
        con.execute("INSTALL json; LOAD json;")

        # DYNAMIC RESOURCE TUNING
        pio = "true" if DUCKDB_PRESERVE_INSERTION_ORDER else "false"
        con.execute(f"SET preserve_insertion_order={pio};")
        con.execute(f"SET temp_directory='{abs_working_dir}';")
        con.execute(f"SET memory_limit='{limit_gb}GB';")
        con.execute(f"SET threads={active_threads};")
        con.execute("SET max_temp_directory_size='500GB';")

        from core.utils import Heartbeat

        # Intermediate scratch file for unsorted data
        scratch_parquet = os.path.join(abs_working_dir, "unsorted_scratch.parquet")
        if os.path.exists(scratch_parquet):
            os.remove(scratch_parquet)

        # Phase 1: Stream JSON directly to an unsorted Parquet file
        # This is extremely memory-efficient as it bypasses the internal table manager.
        print(f"Phase 1: Streaming JSON to intermediate scratch Parquet ({limit_gb}GB RAM limit)...")
        with Heartbeat("streaming to disk"):
            con.execute(f"""
                COPY (
                    SELECT {col_list}
                    FROM read_json(
                        '{fifo_path}',
                        columns={cols_param},
                        format='newline_delimited',
                        ignore_errors=true,
                        maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE}
                    )
                ) TO '{scratch_parquet}' (FORMAT 'parquet');
            """)

        zstd_proc.wait()

        # Phase 2: Sort from Parquet to Parquet
        # DuckDB's Parquet-to-Parquet sorting is the gold standard for Out-of-Core performance.
        print("Phase 2: Performing external sort from scratch file to final Parquet...")
        with Heartbeat("sorting and exporting"):
            con.execute(f"""
                COPY (
                    SELECT * FROM '{scratch_parquet}'
                    ORDER BY author, subreddit, created_utc
                ) TO '{output_path}' (
                    FORMAT 'parquet',
                    COMPRESSION '{DUCKDB_PARQUET_COMPRESSION_CODEC.lower()}',
                    ROW_GROUP_SIZE {DUCKDB_ROW_GROUP_SIZE}
                );
            """)

        # Phase 3: Cleanup
        if os.path.exists(scratch_parquet):
            os.remove(scratch_parquet)



        zstd_proc.wait()

        con.close()


        # Generate Manifest
        manifest_path = output_path + ".manifest.json"
        _generate_parquet_manifest(output_path, manifest_path)

    finally:
        if os.path.exists(fifo_path):
            os.remove(fifo_path)
        if os.path.exists(temp_db_path):
            with contextlib.suppress(BaseException):
                os.remove(temp_db_path)

    print(f"Successfully created {output_path}")


def _generate_parquet_manifest(parquet_path: str, output_manifest_path: str) -> bool:
    """Generates a detailed manifest JSON for a single Parquet file."""
    import json
    import time

    print(f"Generating manifest: {os.path.basename(output_manifest_path)}")
    try:
        parquet_path_sql = f"'{parquet_path}'"

        with duckdb.connect(":memory:") as con:
            schema_info = con.execute(
                f"DESCRIBE SELECT * FROM read_parquet({parquet_path_sql})"
            ).fetchall()
            columns = [r[0] for r in schema_info]
            types = {r[0]: r[1] for r in schema_info}

            select_parts = ["COUNT(*) as total_rows"]
            for col in columns:
                select_parts.append(f'COUNT("{col}")')

            stats_query = f"SELECT {', '.join(select_parts)} FROM read_parquet({parquet_path_sql})"
            stats_result = con.execute(stats_query).fetchone()

            if stats_result is None:
                return False

            total_rows = stats_result[0]
            manifest = {
                "filename": os.path.basename(parquet_path),
                "file_size": os.path.getsize(parquet_path),
                "last_modified": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(parquet_path))
                ),
                "row_count": total_rows,
                "conversion_method": "streamed",
                "schema": types,
                "column_stats": {},
            }

            for i, col in enumerate(columns):
                col_count = stats_result[i + 1]
                usage_ratio = round(col_count / total_rows, 4) if total_rows > 0 else 0
                manifest["column_stats"][col] = {"count": col_count, "usage_ratio": usage_ratio}

            with open(output_manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

            return True

    except Exception as e:
        print(f"Error generating manifest: {e}")
        return False



import argparse

def main() -> None:
    parser = argparse.ArgumentParser(description="Streamed ZST to Parquet converter.")
    parser.add_argument("input_path", help="Path to the input .zst file.")
    parser.add_argument("-o", "--output", dest="output_path", help="Path to the output .parquet file.")
    
    args = parser.parse_args()
    
    convert_file(args.input_path, args.output_path)


if __name__ == "__main__":
    main()
