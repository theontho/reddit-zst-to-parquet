from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, TextIO, cast

import duckdb
import psutil
import zstandard

from engines.chunked_engine import (
    BIGINT_COLUMNS,
    BOOLEAN_COLUMNS,
    DUCKDB_MAXIMUM_OBJECT_SIZE,
    STREAM_BLOCK_SIZE,
    BinaryLineChunker,
    ThreadedZstdReader,
    load_master_schema,
)

SORT_KEYS = ("author", "subreddit", "created_utc")


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def parse_metadata(values: Sequence[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError(f"Metadata must use KEY=VALUE syntax: {value!r}")
        metadata[key] = item
    return metadata


def command_output(command: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def cpu_model() -> str:
    if sys.platform == "darwin":
        model = command_output(["sysctl", "-n", "machdep.cpu.brand_string"])
        if model:
            return model
    if sys.platform == "win32":
        return os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "unknown"
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name"):
                    return line.partition(":")[2].strip()
        except OSError:
            pass
    return platform.processor() or "unknown"


def git_metadata() -> dict[str, object]:
    commit = command_output(["git", "rev-parse", "HEAD"])
    status = command_output(["git", "status", "--porcelain"])
    return {
        "commit": commit,
        "dirty": status is not None,
    }


def system_metadata(zstd_path: str) -> dict[str, object]:
    virtual_memory = psutil.virtual_memory()
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu": cpu_model(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "memory_bytes": virtual_memory.total,
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "zstd": command_output([zstd_path, "--version"]),
        "python_zstandard": zstandard.__version__,
        "python_zstandard_libzstd": ".".join(str(part) for part in zstandard.ZSTD_VERSION),
        "git": git_metadata(),
    }


def configure_connection(
    connection: duckdb.DuckDBPyConnection,
    *,
    threads: int,
    memory_limit_gb: int,
    spill_directory: Path,
    max_temp_bytes: int,
) -> None:
    spill_directory.mkdir(parents=True, exist_ok=True)
    connection.execute(f"SET threads={threads}")
    connection.execute(f"SET memory_limit='{memory_limit_gb}GB'")
    connection.execute("SET preserve_insertion_order=true")
    connection.execute(f"SET temp_directory={sql_string(spill_directory)}")
    connection.execute(f"SET max_temp_directory_size='{max_temp_bytes}B'")


def infer_raw_schema(
    source: Path,
    *,
    compressed: bool,
    threads: int,
    memory_limit_gb: int,
    spill_directory: Path,
    max_temp_bytes: int,
) -> list[tuple[str, str]]:
    compression = ", compression='zstd'" if compressed else ""
    with duckdb.connect(":memory:") as connection:
        configure_connection(
            connection,
            threads=threads,
            memory_limit_gb=memory_limit_gb,
            spill_directory=spill_directory,
            max_temp_bytes=max_temp_bytes,
        )
        rows = connection.execute(
            f"""
            DESCRIBE SELECT * FROM read_json(
                {sql_string(source)},
                union_by_name=true,
                format='newline_delimited',
                ignore_errors=true,
                maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE}
                {compression}
            )
            LIMIT 0
            """
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def normalized_select(raw_schema: Sequence[tuple[str, str]], master_columns: Sequence[str]) -> str:
    raw_names = {name for name, _ in raw_schema}
    selections: list[str] = []
    for column in master_columns:
        if column not in raw_names:
            continue
        identifier = sql_identifier(column)
        if column == "edited":
            selections.append(
                """
                CASE
                    WHEN try_cast(edited AS BOOLEAN) IS TRUE THEN 1
                    WHEN try_cast(edited AS BOOLEAN) IS FALSE THEN 0
                    ELSE try_cast(edited AS BIGINT)
                END AS edited
                """.strip()
            )
        elif column in BIGINT_COLUMNS:
            selections.append(f"TRY_CAST({identifier} AS BIGINT) AS {identifier}")
        elif column in BOOLEAN_COLUMNS:
            selections.append(f"TRY_CAST({identifier} AS BOOLEAN) AS {identifier}")
        else:
            selections.append(f"TRY_CAST({identifier} AS VARCHAR) AS {identifier}")

    master_names = set(master_columns)
    extras = [name for name, _ in raw_schema if name not in master_names]
    if extras:
        fields = ", ".join(f"{sql_identifier(name)} := {sql_identifier(name)}" for name in extras)
        selections.append(f"to_json(struct_pack({fields})) AS extra_json")
    else:
        selections.append("CAST(NULL AS VARCHAR) AS extra_json")
    return ", ".join(selections)


def fixed_columns(raw_schema: Sequence[tuple[str, str]]) -> str:
    entries = []
    for name, data_type in raw_schema:
        entries.append(f"{sql_string(name)}: {sql_string(data_type)}")
    return "{" + ", ".join(entries) + "}"


def copy_query(source_sql: str, selection: str, output: Path) -> str:
    order = ", ".join(f"{sql_identifier(key)} ASC" for key in SORT_KEYS)
    return f"""
        COPY (
            SELECT {selection}
            FROM ({source_sql}) AS raw
            ORDER BY {order}
        ) TO {sql_string(output)} (FORMAT PARQUET, CODEC ZSTD)
    """


def convert_staged(
    jsonl: Path,
    output: Path,
    *,
    master_columns: Sequence[str],
    threads: int,
    memory_limit_gb: int,
    spill_directory: Path,
    max_temp_bytes: int,
) -> tuple[float, list[tuple[str, str]]]:
    started = time.monotonic()
    raw_schema = infer_raw_schema(
        jsonl,
        compressed=False,
        threads=threads,
        memory_limit_gb=memory_limit_gb,
        spill_directory=spill_directory,
        max_temp_bytes=max_temp_bytes,
    )
    source_sql = f"""
        SELECT * FROM read_json(
            {sql_string(jsonl)},
            union_by_name=true,
            format='newline_delimited',
            ignore_errors=true,
            maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE}
        )
    """
    with duckdb.connect(":memory:") as connection:
        configure_connection(
            connection,
            threads=threads,
            memory_limit_gb=memory_limit_gb,
            spill_directory=spill_directory,
            max_temp_bytes=max_temp_bytes,
        )
        connection.execute(copy_query(source_sql, normalized_select(raw_schema, master_columns), output))
    return time.monotonic() - started, raw_schema


def convert_direct(
    source: Path,
    output: Path,
    *,
    rows: int,
    raw_schema: Sequence[tuple[str, str]],
    master_columns: Sequence[str],
    threads: int,
    memory_limit_gb: int,
    spill_directory: Path,
    max_temp_bytes: int,
) -> float:
    source_sql = f"""
        SELECT * FROM read_json(
            {sql_string(source)},
            columns={fixed_columns(raw_schema)},
            format='newline_delimited',
            compression='zstd',
            ignore_errors=true,
            maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE}
        )
        LIMIT {rows}
    """
    started = time.monotonic()
    with duckdb.connect(":memory:") as connection:
        configure_connection(
            connection,
            threads=threads,
            memory_limit_gb=memory_limit_gb,
            spill_directory=spill_directory,
            max_temp_bytes=max_temp_bytes,
        )
        connection.execute(copy_query(source_sql, normalized_select(raw_schema, master_columns), output))
    return time.monotonic() - started


def finish_zstd_process(process: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        process.stdout.close()
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def stage_binary_system(source: Path, output: Path, *, rows: int, zstd_path: str) -> tuple[float, int]:
    started = time.monotonic()
    process = subprocess.Popen(
        [zstd_path, "-dcf", "--long=31", str(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=STREAM_BLOCK_SIZE,
    )
    if process.stdout is None:
        raise RuntimeError("zstd stdout was not created")
    try:
        count = BinaryLineChunker(cast(BinaryIO, process.stdout)).write_chunk(str(output), rows)
    finally:
        finish_zstd_process(process)
    return time.monotonic() - started, count


def stage_binary_python(source: Path, output: Path, *, rows: int) -> tuple[float, int]:
    started = time.monotonic()
    with ThreadedZstdReader(str(source)) as reader:
        count = BinaryLineChunker(reader).write_chunk(str(output), rows)
    return time.monotonic() - started, count


def stage_text(source: Path, output: Path, *, rows: int, zstd_path: str) -> tuple[float, int]:
    started = time.monotonic()
    process = subprocess.Popen(
        [zstd_path, "-dcf", "--long=31", str(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout is None:
        raise RuntimeError("zstd stdout was not created")
    count = 0
    try:
        stream = cast(TextIO, process.stdout)
        with output.open("w", encoding="utf-8") as destination:
            for _ in range(rows):
                line = stream.readline()
                if not line:
                    break
                destination.write(line)
                count += 1
    finally:
        finish_zstd_process(process)
    return time.monotonic() - started, count


def stage_source(
    source: Path,
    output: Path,
    *,
    rows: int,
    zstd_path: str,
    copy_mode: str,
    decoder: str,
) -> tuple[float, int]:
    if copy_mode == "block":
        if decoder == "python-threaded":
            return stage_binary_python(source, output, rows=rows)
        return stage_binary_system(source, output, rows=rows, zstd_path=zstd_path)
    return stage_text(source, output, rows=rows, zstd_path=zstd_path)


def fetch_scalar(connection: duckdb.DuckDBPyConnection, query: str) -> object:
    row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError("DuckDB returned no result")
    return row[0]


def as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Expected a numeric value, got {value!r}")
    return int(value)


def run_metric(run: dict[str, object], section: str, metric: str) -> float:
    section_value = run.get(section)
    if not isinstance(section_value, dict):
        raise TypeError(f"Missing benchmark section: {section}")
    value = section_value.get(metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Missing numeric benchmark metric: {section}.{metric}")
    return float(value)


def inspect_output(path: Path) -> dict[str, object]:
    quoted_path = sql_string(path)
    with duckdb.connect(":memory:") as connection:
        row_count = as_int(fetch_scalar(connection, f"SELECT count(*) FROM read_parquet({quoted_path})"))
        schema = connection.execute(f"DESCRIBE SELECT * FROM read_parquet({quoted_path})").fetchall()
        fingerprint_columns = ", ".join(sql_identifier(str(row[0])) for row in schema)
        fingerprint = as_int(
            fetch_scalar(
                connection,
                f"SELECT bit_xor(hash({fingerprint_columns})) FROM read_parquet({quoted_path})",
            )
        )
        sort_violations = as_int(
            fetch_scalar(
                connection,
                f"""
                WITH keyed AS (
                    SELECT struct_pack(
                        author_is_null := author IS NULL,
                        author_value := coalesce(author, ''),
                        subreddit_is_null := subreddit IS NULL,
                        subreddit_value := coalesce(subreddit, ''),
                        created_utc_is_null := created_utc IS NULL,
                        created_utc_value := coalesce(created_utc, 0)
                    ) AS sort_key
                    FROM read_parquet({quoted_path})
                ),
                compared AS (
                    SELECT sort_key, lag(sort_key) OVER () AS previous_sort_key
                    FROM keyed
                )
                SELECT count(*)
                FROM compared
                WHERE previous_sort_key > sort_key
                """,
            )
        )
    return {
        "bytes": path.stat().st_size,
        "rows": row_count,
        "columns": [{"name": str(row[0]), "type": str(row[1])} for row in schema],
        "content_fingerprint": fingerprint,
        "physical_sort_violations": sort_violations,
    }


def validate_inspection(inspection: dict[str, object], expected_rows: int) -> None:
    if inspection["rows"] != expected_rows:
        raise RuntimeError(f"Expected {expected_rows:,} rows, got {inspection['rows']!r}")
    if inspection["physical_sort_violations"] != 0:
        raise RuntimeError(f"Output has {inspection['physical_sort_violations']!r} physical sort violations")


def summarize(runs: Sequence[dict[str, object]], mode: str, rows: int) -> dict[str, object]:
    if mode == "staged":
        stage_times = [run_metric(run, "staged", "stage_seconds") for run in runs]
        parquet_times = [run_metric(run, "staged", "parquet_seconds") for run in runs]
        total_times = [run_metric(run, "staged", "total_seconds") for run in runs]
        return {
            "median_stage_seconds": statistics.median(stage_times),
            "median_parquet_seconds": statistics.median(parquet_times),
            "median_total_seconds": statistics.median(total_times),
            "median_rows_per_second": rows / statistics.median(total_times),
        }
    total_times = [run_metric(run, "direct", "total_seconds") for run in runs]
    return {
        "median_total_seconds": statistics.median(total_times),
        "median_rows_per_second": rows / statistics.median(total_times),
    }


def resolve_zstd(value: str) -> str:
    resolved = shutil.which(value)
    if resolved:
        return resolved
    path = Path(value).expanduser()
    if path.is_file():
        return str(path.resolve())
    raise FileNotFoundError(f"zstd executable not found: {value}")


def default_threads() -> int:
    return psutil.cpu_count(logical=False) or os.cpu_count() or 1


def default_memory_limit_gb() -> int:
    return max(1, int(psutil.virtual_memory().total / (1024**3) * 0.8))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reproducible Reddit ZST conversion benchmark")
    parser.add_argument("--source", type=Path, required=True, help="RC_*.zst or RS_*.zst source archive")
    parser.add_argument("--output-dir", type=Path, required=True, help="New or empty benchmark output directory")
    parser.add_argument("--expected-source-sha256", help="Fail unless the source has this SHA-256")
    parser.add_argument("--rows", type=int, default=4_000_000)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=0)
    parser.add_argument("--threads", type=int, default=default_threads())
    parser.add_argument("--memory-limit-gb", type=int, default=default_memory_limit_gb())
    parser.add_argument("--zstd", default="zstd", help="zstd executable name or path")
    parser.add_argument(
        "--decoder",
        choices=("python-threaded", "system"),
        default="python-threaded",
        help="Zstandard decoder for block staging (default: python-threaded)",
    )
    parser.add_argument("--mode", choices=("staged", "direct", "both"), default="staged")
    parser.add_argument("--copy-mode", choices=("block", "text-lines"), default="block")
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Record environmental state such as protection=enabled",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.rows < 1:
        raise ValueError("--rows must be positive")
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive")
    if args.warmups < 0:
        raise ValueError("--warmups cannot be negative")
    if args.threads < 1:
        raise ValueError("--threads must be positive")
    if args.memory_limit_gb < 1:
        raise ValueError("--memory-limit-gb must be positive")
    if args.mode in {"staged", "both"} and args.copy_mode == "text-lines" and args.decoder != "system":
        raise ValueError("--copy-mode text-lines requires --decoder system")


def run_benchmark(args: argparse.Namespace) -> dict[str, object]:
    validate_args(args)
    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source archive does not exist: {source}")
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    schema_path = output_dir / "raw-schema.json"
    if result_path.exists() or schema_path.exists():
        raise FileExistsError(f"Benchmark metadata already exists in {output_dir}")

    requires_system_zstd = args.mode in {"staged", "both"} and (
        args.decoder == "system" or args.copy_mode == "text-lines"
    )
    zstd_path = resolve_zstd(args.zstd) if requires_system_zstd else shutil.which(args.zstd) or args.zstd
    master_columns = load_master_schema(str(source))
    if not master_columns:
        raise ValueError("Source filename must contain RC_ or RS_ so the matching master schema can be selected")

    started_utc = datetime.now(timezone.utc).isoformat()
    source_sha256 = sha256_file(source)
    if args.expected_source_sha256 and source_sha256.lower() != args.expected_source_sha256.lower():
        raise ValueError(
            f"Source SHA-256 mismatch: expected {args.expected_source_sha256.lower()}, got {source_sha256}"
        )

    max_temp_bytes = max(1, int(shutil.disk_usage(output_dir).free * 0.75))
    setup_spill = output_dir / "setup-spill"
    schema_started = time.monotonic()
    raw_schema = infer_raw_schema(
        source,
        compressed=True,
        threads=args.threads,
        memory_limit_gb=args.memory_limit_gb,
        spill_directory=setup_spill,
        max_temp_bytes=max_temp_bytes,
    )
    schema_seconds = time.monotonic() - schema_started
    schema_path.write_text(
        json.dumps([{"name": name, "type": data_type} for name, data_type in raw_schema], indent=2),
        encoding="utf-8",
    )

    warmup_runs: list[dict[str, object]] = []
    measured_runs: list[dict[str, object]] = []
    expected_fingerprint: int | None = None
    expected_columns: object | None = None
    total_runs = args.warmups + args.repetitions

    for sequence in range(total_runs):
        is_warmup = sequence < args.warmups
        measured_index = sequence - args.warmups + 1
        run_name = f"warmup-{sequence + 1}" if is_warmup else f"run-{measured_index}"
        run_dir = output_dir / run_name
        run_dir.mkdir()
        spill_directory = run_dir / "duckdb-spill"
        jsonl = run_dir / "chunk.jsonl"
        staged_parquet = run_dir / "staged.parquet"
        direct_parquet = run_dir / "direct.parquet"
        run: dict[str, object] = {
            "name": run_name,
            "warmup": is_warmup,
        }

        if args.mode in {"staged", "both"}:
            stage_seconds, staged_rows = stage_source(
                source,
                jsonl,
                rows=args.rows,
                zstd_path=zstd_path,
                copy_mode=args.copy_mode,
                decoder=args.decoder,
            )
            if staged_rows != args.rows:
                raise RuntimeError(f"Expected {args.rows:,} staged rows, got {staged_rows:,}")
            run["jsonl_bytes"] = jsonl.stat().st_size

        execution_order = ["staged", "direct"]
        if args.mode == "both" and sequence % 2 == 1:
            execution_order.reverse()
        execution_order = [mode for mode in execution_order if args.mode in {mode, "both"}]
        run["execution_order"] = execution_order

        for mode in execution_order:
            if mode == "staged":
                parquet_seconds, staged_schema = convert_staged(
                    jsonl,
                    staged_parquet,
                    master_columns=master_columns,
                    threads=args.threads,
                    memory_limit_gb=args.memory_limit_gb,
                    spill_directory=spill_directory,
                    max_temp_bytes=max_temp_bytes,
                )
                if staged_schema != raw_schema:
                    raise RuntimeError("Raw schema changed between direct source setup and staged input")
                inspection = inspect_output(staged_parquet)
                validate_inspection(inspection, args.rows)
                run["staged"] = {
                    "stage_seconds": stage_seconds,
                    "parquet_seconds": parquet_seconds,
                    "total_seconds": stage_seconds + parquet_seconds,
                    "rows_per_second": args.rows / (stage_seconds + parquet_seconds),
                    "output": inspection,
                }
            else:
                direct_seconds = convert_direct(
                    source,
                    direct_parquet,
                    rows=args.rows,
                    raw_schema=raw_schema,
                    master_columns=master_columns,
                    threads=args.threads,
                    memory_limit_gb=args.memory_limit_gb,
                    spill_directory=spill_directory,
                    max_temp_bytes=max_temp_bytes,
                )
                inspection = inspect_output(direct_parquet)
                validate_inspection(inspection, args.rows)
                run["direct"] = {
                    "total_seconds": direct_seconds,
                    "rows_per_second": args.rows / direct_seconds,
                    "output": inspection,
                }

            fingerprint = as_int(inspection["content_fingerprint"])
            columns = inspection["columns"]
            if expected_fingerprint is None:
                expected_fingerprint = fingerprint
                expected_columns = columns
            elif fingerprint != expected_fingerprint:
                raise RuntimeError("Output content fingerprints do not match")
            if columns != expected_columns:
                raise RuntimeError("Output schemas do not match")

        if is_warmup:
            warmup_runs.append(run)
        else:
            measured_runs.append(run)
            print(json.dumps(run, indent=2))

        if not args.keep_artifacts:
            shutil.rmtree(run_dir)

    summary: dict[str, object] = {}
    for mode in ("staged", "direct"):
        if args.mode in {mode, "both"}:
            summary[mode] = summarize(measured_runs, mode, args.rows)

    result: dict[str, object] = {
        "benchmark": "Reddit ZST to sorted Parquet conversion",
        "benchmark_version": 2,
        "started_utc": started_utc,
        "ended_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": source_sha256,
        },
        "system": system_metadata(zstd_path),
        "configuration": {
            "rows": args.rows,
            "repetitions": args.repetitions,
            "warmups": args.warmups,
            "threads": args.threads,
            "memory_limit_gb": args.memory_limit_gb,
            "max_temp_bytes": max_temp_bytes,
            "mode": args.mode,
            "copy_mode": args.copy_mode,
            "decoder": args.decoder,
            "binary_block_bytes": STREAM_BLOCK_SIZE,
            "compression": "ZSTD",
            "sort": list(SORT_KEYS),
            "schema_inference_seconds_excluded": schema_seconds,
            "metadata": parse_metadata(args.metadata),
        },
        "warmup_runs": warmup_runs,
        "runs": measured_runs,
        "summary": summary,
        "validation": {
            "rows": args.rows,
            "content_fingerprint": expected_fingerprint,
            "schemas_equal": True,
            "physical_sort_order": True,
            "expected_source_sha256": args.expected_source_sha256,
            "source_sha256_matched": None
            if args.expected_source_sha256 is None
            else source_sha256.lower() == args.expected_source_sha256.lower(),
        },
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"result": str(result_path), "summary": summary}, indent=2))
    return result


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_benchmark(args)


if __name__ == "__main__":
    main()
