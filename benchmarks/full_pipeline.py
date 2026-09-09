"""Resumable full-archive chunk creation and globally sorted merge benchmark."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import psutil
import pyarrow.compute as pc
import pyarrow.parquet as pq

from benchmarks.conversion import (
    SORT_KEYS,
    configure_connection,
    infer_raw_schema,
    normalized_select,
    sha256_file,
    sql_identifier,
    sql_string,
    system_metadata,
)
from engines.chunked_engine import (
    BIGINT_COLUMNS,
    BOOLEAN_COLUMNS,
    DUCKDB_MAXIMUM_OBJECT_SIZE,
    BinaryLineChunker,
    ThreadedZstdReader,
    load_master_schema,
)


def save_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class Metrics:
    """Sample process and host counters, including transient DuckDB spill."""

    def __init__(self, root: Path, name: str):
        self.root = root
        self.name = name
        self.stop = threading.Event()
        self.process = psutil.Process()
        self.peak_rss = 0
        self.peak_spill = 0
        self.minimum_available = psutil.virtual_memory().available
        self.error: Exception | None = None

    def sample(self) -> None:
        memory = psutil.virtual_memory()
        spill = 0
        for path in (self.root / "scratch").rglob("*"):
            try:
                if path.is_file():
                    spill += path.stat().st_size
            except FileNotFoundError:
                continue
        rss = self.process.memory_info().rss
        self.peak_rss = max(self.peak_rss, rss)
        self.peak_spill = max(self.peak_spill, spill)
        self.minimum_available = min(self.minimum_available, memory.available)
        record = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "phase": self.name,
            "elapsed_seconds": time.monotonic() - self.started,
            "rss_bytes": rss,
            "spill_bytes": spill,
            "available_memory_bytes": memory.available,
            "swap": psutil.swap_memory()._asdict(),
            "process_cpu": self.process.cpu_times()._asdict(),
            "process_io": self.process.io_counters()._asdict(),
            "load_average": os.getloadavg(),
            "disk_free_bytes": shutil.disk_usage(self.root).free,
        }
        self.samples.write(json.dumps(record) + "\n")
        self.samples.flush()

    def monitor(self) -> None:
        try:
            while not self.stop.wait(1):
                self.sample()
        except Exception as error:
            self.error = error

    def __enter__(self) -> Metrics:
        self.started = time.monotonic()
        self.cpu_start = self.process.cpu_times()
        self.io_start = self.process.io_counters()
        self.samples = (self.root / "telemetry.jsonl").open("a", encoding="utf-8")
        self.sample()
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()
        print(f"START {self.name}", flush=True)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop.set()
        self.thread.join()
        self.sample()
        self.samples.close()
        cpu = self.process.cpu_times()
        io = self.process.io_counters()
        self.result = {
            "phase": self.name,
            "seconds": time.monotonic() - self.started,
            "peak_rss_bytes": self.peak_rss,
            "peak_spill_bytes": self.peak_spill,
            "minimum_available_memory_bytes": self.minimum_available,
            "cpu_user_seconds": cpu.user - self.cpu_start.user,
            "cpu_system_seconds": cpu.system - self.cpu_start.system,
            "read_bytes": io.read_bytes - self.io_start.read_bytes,
            "write_bytes": io.write_bytes - self.io_start.write_bytes,
            "success": exc_type is None and self.error is None,
        }
        with (self.root / "phases.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(self.result) + "\n")
        print(f"END {self.name}: {self.result['seconds']:.2f}s", flush=True)
        if self.error is not None and exc_type is None:
            raise RuntimeError("Resource sampler failed") from self.error


def connection(root: Path, threads: int, memory: int, profile: str | None = None):
    con = duckdb.connect(":memory:")
    configure_connection(
        con,
        threads=threads,
        memory_limit_gb=memory,
        spill_directory=root / "scratch",
        max_temp_bytes=int(shutil.disk_usage(root).free * 0.75),
    )
    con.execute("SET preserve_insertion_order=false")
    if profile:
        con.execute("SET enable_profiling='json'")
        con.execute(f"SET profiling_output={sql_string(root / (profile + '.profile.json'))}")
    return con


def canonical_projection(raw_schema: list[tuple[str, str]], master: list[str]) -> str:
    present = {name for name, _ in raw_schema}
    projection = normalized_select(raw_schema, master)
    for name in master:
        if name not in present:
            kind = (
                "BIGINT"
                if name in BIGINT_COLUMNS or name == "edited"
                else ("BOOLEAN" if name in BOOLEAN_COLUMNS else "VARCHAR")
            )
            projection += f", CAST(NULL AS {kind}) AS {sql_identifier(name)}"
    columns = ", ".join(sql_identifier(name) for name in [*master, "extra_json"])
    return f"SELECT {columns} FROM (SELECT {projection} FROM source_rows)"


def inspect(path: Path, root: Path, threads: int, memory: int) -> dict:
    with connection(root, threads, memory) as con:
        schema = [
            (r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({sql_string(path)})").fetchall()
        ]
        columns = ", ".join(sql_identifier(name) for name, _ in schema)
        counts = ", ".join(f"count({sql_identifier(name)})" for name, _ in schema)
        row = con.execute(
            f"SELECT count(*), bit_xor(hash({columns})), sum(hash({columns})::HUGEINT), "
            f"min(created_utc), max(created_utc), {counts} FROM read_parquet({sql_string(path)})"
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No inspection result for {path}")
    return {
        "rows": row[0],
        "fingerprint_xor": row[1],
        "fingerprint_sum": row[2],
        "created_utc_min": row[3],
        "created_utc_max": row[4],
        "non_null_counts": dict(zip((name for name, _ in schema), row[5:], strict=True)),
        "schema": schema,
        "bytes": path.stat().st_size,
        "row_groups": pq.ParquetFile(path).metadata.num_row_groups,
    }


def verify_order(path: Path) -> None:
    """Check physical ordering with bounded Arrow batches, never a global window."""
    previous = None
    for batch in pq.ParquetFile(path).iter_batches(batch_size=65536, columns=list(SORT_KEYS), use_threads=False):
        if not batch.num_rows:
            continue
        keys = []
        for name in SORT_KEYS:
            values = batch.column(name)
            keys.extend(
                [pc.call_function("is_null", [values]), pc.fill_null(values, 0 if name == "created_utc" else "")]
            )
        first = tuple(key[0].as_py() for key in keys)
        if previous is not None and previous > first:
            raise RuntimeError(f"Sort violation across batches in {path}")
        previous = tuple(key[-1].as_py() for key in keys)
        equal = None
        violation = None
        for key in keys:
            left, right = key.slice(0, len(key) - 1), key.slice(1)
            greater = pc.call_function("greater", [left, right])
            current = greater if equal is None else pc.call_function("and", [equal, greater])
            violation = current if violation is None else pc.call_function("or", [violation, current])
            same = pc.call_function("equal", [left, right])
            equal = same if equal is None else pc.call_function("and", [equal, same])
        if pc.call_function("any", [violation]).as_py():
            raise RuntimeError(f"Physical sort violation in {path}")


def create_chunks(args: argparse.Namespace, root: Path, state: dict) -> None:
    source = Path(state["source"]["path"])
    master = load_master_schema(str(source))
    if not master:
        raise ValueError("Expected an RC_ or RS_ source filename")
    if state.get("chunks_complete"):
        return
    chunks = state["chunks"]
    with ThreadedZstdReader(str(source)) as reader:
        chunker = BinaryLineChunker(reader)
        skip = sum(chunk["source_rows"] for chunk in chunks)
        if skip:
            with Metrics(root, "resume-decompression"):
                if chunker.skip_lines(skip) != skip:
                    raise RuntimeError("Source ended before resume position")
        while True:
            index = len(chunks) + 1
            jsonl = root / "chunk.jsonl"
            path = root / "chunks" / f"chunk_{index:05d}.parquet"
            partial = path.with_suffix(".partial.parquet")
            with Metrics(root, f"chunk-{index:05d}-stage") as staging:
                rows = chunker.write_chunk(str(jsonl), args.chunk_rows)
            if rows == 0:
                break
            jsonl_bytes = jsonl.stat().st_size
            with Metrics(root, f"chunk-{index:05d}-convert") as conversion:
                raw_schema = infer_raw_schema(
                    jsonl,
                    compressed=False,
                    threads=args.threads,
                    memory_limit_gb=args.memory_gb,
                    spill_directory=root / "scratch",
                    max_temp_bytes=int(shutil.disk_usage(root).free * 0.75),
                )
                projection = canonical_projection(raw_schema, master)
                with connection(root, args.threads, args.memory_gb, f"chunk-{index:05d}") as con:
                    con.execute(
                        f"COPY (WITH source_rows AS (SELECT * FROM read_json({sql_string(jsonl)}, "
                        f"format='newline_delimited', union_by_name=true, ignore_errors=true, "
                        f"maximum_object_size={DUCKDB_MAXIMUM_OBJECT_SIZE})) "
                        f"{projection} ORDER BY author, subreddit, created_utc) TO {sql_string(partial)} "
                        f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {args.row_group_size})"
                    )
            with Metrics(root, f"chunk-{index:05d}-validate"):
                details = inspect(partial, root, args.threads, args.memory_gb)
                if details["rows"] != rows:
                    raise RuntimeError(f"Source/output row mismatch in chunk {index}: {rows} != {details['rows']}")
                verify_order(partial)
                if chunks and details["schema"] != [tuple(item) for item in chunks[0]["schema"]]:
                    raise RuntimeError(f"Schema drift in chunk {index}")
            partial.replace(path)
            chunks.append(
                {
                    **details,
                    "path": str(path),
                    "source_rows": rows,
                    "jsonl_bytes": jsonl_bytes,
                    "staging": staging.result,
                    "conversion": conversion.result,
                }
            )
            save_json(root / "state.json", state)
            jsonl.unlink()
            print(f"CHUNK {index}: {rows:,} rows; total {sum(c['rows'] for c in chunks):,}", flush=True)
    state["chunks_complete"] = True
    save_json(root / "state.json", state)


def merge_chunks(args: argparse.Namespace, root: Path, state: dict) -> None:
    if not state.get("chunks_complete"):
        raise RuntimeError("Chunk creation is not complete")
    if state.get("merge_complete"):
        return
    chunks = state["chunks"]
    if not chunks:
        raise RuntimeError("No chunks to merge")
    output = root / (Path(state["source"]["path"]).stem + ".parquet")
    partial = output.with_suffix(".partial.parquet")
    files = ", ".join(sql_string(chunk["path"]) for chunk in chunks)
    with Metrics(root, "merge") as metrics, connection(root, args.threads, args.merge_memory_gb, "merge") as con:
        con.execute(
            f"COPY (SELECT * FROM read_parquet([{files}], union_by_name=false) "
            f"ORDER BY author, subreddit, created_utc) TO {sql_string(partial)} "
            f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {args.row_group_size})"
        )
    state["merge_metrics"] = metrics.result
    save_json(root / "state.json", state)
    with Metrics(root, "merge-validation") as validation:
        result = inspect(partial, root, args.threads, args.merge_memory_gb)
        expected_xor = 0
        for chunk in chunks:
            expected_xor ^= chunk["fingerprint_xor"]
        if result["rows"] != sum(chunk["rows"] for chunk in chunks):
            raise RuntimeError("Merged row count mismatch")
        if result["fingerprint_xor"] != expected_xor or result["fingerprint_sum"] != sum(
            chunk["fingerprint_sum"] for chunk in chunks
        ):
            raise RuntimeError("Merged full-row fingerprints differ")
        if result["schema"] != [tuple(item) for item in chunks[0]["schema"]]:
            raise RuntimeError("Merged schema mismatch")
        for name, count in result["non_null_counts"].items():
            if count != sum(chunk["non_null_counts"][name] for chunk in chunks):
                raise RuntimeError(f"Merged column count mismatch: {name}")
        verify_order(partial)
    partial.replace(output)
    state["merge_complete"] = True
    state["output"] = {**result, "path": str(output), "physical_sort_order": True}
    state["merge_validation_metrics"] = validation.result
    state["ended_utc"] = datetime.now(timezone.utc).isoformat()
    save_json(root / "state.json", state)
    save_json(root / "result.json", state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-gb", type=int, default=24)
    parser.add_argument("--merge-memory-gb", type=int, default=16)
    parser.add_argument("--row-group-size", type=int, default=100000)
    parser.add_argument("--stage", choices=["all", "chunks", "merge"], default="all")
    args = parser.parse_args()
    for value in (args.chunk_rows, args.threads, args.memory_gb, args.merge_memory_gb, args.row_group_size):
        if value < 1:
            parser.error("Resource limits and row counts must be positive")
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "chunks").mkdir(exist_ok=True)
    (root / "scratch").mkdir(exist_ok=True)
    # Serialize runs sharing an output directory, including across invocations.
    import fcntl

    with (root / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        settings = {
            "chunk_rows": args.chunk_rows,
            "threads": args.threads,
            "memory_gb": args.memory_gb,
            "merge_memory_gb": args.merge_memory_gb,
            "row_group_size": args.row_group_size,
            "decoder": "python-threaded",
            "compression": "ZSTD",
            "sort": list(SORT_KEYS),
        }
        state_path = root / "state.json"
        with Metrics(root, "source-hash"):
            actual_hash = sha256_file(args.source)
        if actual_hash != args.expected_source_sha256.lower():
            raise ValueError("Source SHA-256 mismatch")
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state["settings"] != settings or state["source"]["sha256"] != actual_hash:
                raise ValueError("Resume configuration/source mismatch")
            for chunk in state["chunks"]:
                if Path(chunk["path"]).stat().st_size != chunk["bytes"]:
                    raise ValueError(f"Retained chunk changed: {chunk['path']}")
        else:
            state = {
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "path": str(args.source.resolve()),
                    "sha256": actual_hash,
                    "bytes": args.source.stat().st_size,
                },
                "settings": settings,
                "system": system_metadata("zstd"),
                "chunks": [],
            }
            save_json(state_path, state)
        if args.stage in {"all", "chunks"}:
            create_chunks(args, root, state)
        if args.stage in {"all", "merge"}:
            merge_chunks(args, root, state)


if __name__ == "__main__":
    main()
