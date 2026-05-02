#!/usr/bin/env python3
import os
import sys

# Add parent directory to path to allow imports from the main package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import orjson as json
except ImportError:
    import json as json  # type: ignore
import contextlib

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.types as types

from core.schema_parser import build_arrow_schema

# Ensure both the current directory and the original scripts directory are in path
from scripts.fileStreams import getFileJsonStream
from scripts.utils import FileProgressLog

# Configuration
BATCH_SIZE = 100_000  # Number of rows per batch/row-group
COMPRESSION = "zstd"


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
    schemas_dir = os.path.join(script_dir, "../schemas")

    schema_path = os.path.join(schemas_dir, prefix, year, f"{prefix}_{date_part}.json")
    if os.path.exists(schema_path):
        return schema_path

    return None


def clean_value(value, arrow_type):
    """Recursively cleans a value to match the expected Arrow type."""
    if value is None:
        return None

    if types.is_string(arrow_type):
        if isinstance(value, (dict, list)):
            dumped = json.dumps(value)
            return dumped.decode("utf-8") if isinstance(dumped, bytes) else dumped
        return str(value)

    if types.is_struct(arrow_type):
        if not isinstance(value, dict):
            # If we expected a struct but got something else, try to JSON serialize it
            # But structs expect dicts, so if it's not a dict, we can't fulfill the schema.
            # Returning None is safest to avoid Arrow conversion errors.
            return None

        cleaned_dict = {}
        # Fill in known fields from the struct
        for i in range(arrow_type.num_fields):
            field = arrow_type.field(i)
            cleaned_dict[field.name] = clean_value(value.get(field.name), field.type)
        return cleaned_dict

    if types.is_list(arrow_type):
        if not isinstance(value, list):
            # If data has a single item instead of a list, wrap it
            return [clean_value(value, arrow_type.value_type)]
        return [clean_value(v, arrow_type.value_type) for v in value]

    if types.is_integer(arrow_type):
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    if types.is_floating(arrow_type):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    if types.is_boolean(arrow_type):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.lower() in ("true", "1", "t", "yes")
        return None

    return value


def normalize_row(row, schema_fields, arrow_schema):
    """Ensures row matches schema, moving unknown fields to extra_json."""
    from typing import Any

    normalized: dict[str, Any] = {}
    extra: dict[str, Any] = {}

    for key, value in row.items():
        if key in schema_fields:
            # Special case for 'edited' (can be bool or int)
            if key == "edited":
                if isinstance(value, bool):
                    normalized[key] = 1 if value else 0
                else:
                    try:
                        normalized[key] = int(value)
                    except (ValueError, TypeError):
                        normalized[key] = None
            else:
                normalized[key] = clean_value(value, arrow_schema.field(key).type)
        else:
            extra[key] = value

    # Add nulls for missing schema fields
    for field in arrow_schema.names:
        if field not in normalized and field != "extra_json":
            normalized[field] = None

    dumped_extra = json.dumps(extra) if extra else None
    normalized["extra_json"] = dumped_extra.decode() if isinstance(dumped_extra, bytes) else dumped_extra
    return normalized


def convert_file(input_path: str, output_path: str | None = None) -> None:
    if not output_path:
        output_path = input_path.replace(".zst", ".parquet").replace(".zst_blocks", ".parquet")

    schema_json = find_schema_for_file(input_path)
    if not schema_json:
        print(f"Error: Could not find schema for {input_path}. Skipping.")
        return

    print(f"Using schema: {schema_json}")
    arrow_schema = build_arrow_schema(schema_json)
    schema_fields = set(arrow_schema.names)

    with open(input_path, "rb") as f:
        json_stream = getFileJsonStream(input_path, f)
        progress = FileProgressLog(input_path, f)

        writer = None
        batch_data = []

        if json_stream is None:
            print(f"Error: Could not open stream for {input_path}")
            return

        for row in json_stream:
            progress.onRow()
            batch_data.append(normalize_row(row, schema_fields, arrow_schema))

            if len(batch_data) >= BATCH_SIZE:
                # 1. Create Arrow Table
                table = pa.Table.from_pylist(batch_data, schema=arrow_schema)

                # 2. Sort the table by author and subreddit for fast searching
                table = table.sort_by(
                    [
                        ("author", "ascending"),
                        ("subreddit", "ascending"),
                        ("created_utc", "ascending"),
                    ]
                )

                # 3. Initialize writer or write row group
                if writer is None:
                    writer = pq.ParquetWriter(output_path, arrow_schema, compression=COMPRESSION)

                writer.write_table(table)
                batch_data = []

        # Write final batch
        if batch_data:
            table = pa.Table.from_pylist(batch_data, schema=arrow_schema)
            # Try sorting, but fall back if it fails (unlikely with cleaning)
            with contextlib.suppress(BaseException):
                table = table.sort_by(
                    [
                        ("author", "ascending"),
                        ("subreddit", "ascending"),
                        ("created_utc", "ascending"),
                    ]
                )

            if writer is None:
                writer = pq.ParquetWriter(output_path, arrow_schema, compression=COMPRESSION)
            writer.write_table(table)

        if writer:
            writer.close()
            print(f"\nSuccessfully created {output_path}")

            # Generate Manifest
            manifest_path = output_path + ".manifest.json"
            _generate_parquet_manifest(output_path, manifest_path)


def _generate_parquet_manifest(parquet_path: str, output_manifest_path: str) -> bool:
    """Generates a detailed manifest JSON for a single Parquet file."""
    import time

    import duckdb

    print(f"Generating manifest: {os.path.basename(output_manifest_path)}")
    try:
        parquet_path_sql = f"'{parquet_path}'"

        with duckdb.connect(":memory:") as con:
            schema_info = con.execute(f"DESCRIBE SELECT * FROM read_parquet({parquet_path_sql})").fetchall()
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
                "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(parquet_path))),
                "row_count": total_rows,
                "conversion_method": "pyarrow",
                "schema": types,
                "column_stats": {},
            }

            for i, col in enumerate(columns):
                col_count = stats_result[i + 1]
                usage_ratio = round(col_count / total_rows, 4) if total_rows > 0 else 0
                manifest["column_stats"][col] = {"count": col_count, "usage_ratio": usage_ratio}

            import json as std_json

            with open(output_manifest_path, "w") as f:
                std_json.dump(manifest, f, indent=2)

            return True

    except Exception as e:
        print(f"Error generating manifest: {e}")
        return False


import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="PyArrow ZST to Parquet converter.")
    parser.add_argument("input_path", help="Path to the input .zst file.")
    parser.add_argument("-o", "--output", dest="output_path", help="Path to the output .parquet file.")

    args = parser.parse_args()

    convert_file(args.input_path, args.output_path)


if __name__ == "__main__":
    main()
