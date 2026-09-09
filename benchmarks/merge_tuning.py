"""Compare the legacy single-thread merge with the physical-core Linux profile."""

import argparse
import json
import statistics
from pathlib import Path

from benchmarks.conversion import sql_string
from benchmarks.full_pipeline import Metrics, connection, inspect, save_json, verify_order


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    args = parser.parse_args()
    state = json.loads((args.dataset_dir / "state.json").read_text())
    if not state.get("chunks_complete"):
        raise RuntimeError("Run tuning after chunk creation to avoid CPU/I/O contention")
    chunks = state["chunks"][:4]
    if not chunks:
        raise ValueError("No chunks available")
    root = args.dataset_dir / "merge-tuning"
    root.mkdir(exist_ok=True)
    (root / "scratch").mkdir(exist_ok=True)
    if (root / "result.json").exists():
        raise FileExistsError("Tuning results already exist")
    files = ", ".join(sql_string(chunk["path"]) for chunk in chunks)
    expected_xor = 0
    for chunk in chunks:
        expected_xor ^= chunk["fingerprint_xor"]
    rows = sum(chunk["rows"] for chunk in chunks)
    results: list[dict] = []
    for sequence, threads in enumerate((1, 4, 4, 1), start=1):
        name = f"run-{sequence}-threads-{threads}"
        path = root / f"{name}.parquet"
        with (
            Metrics(root, name) as metrics,
            connection(root, threads, 20, name) as con,
        ):
            con.execute(
                f"COPY (SELECT * FROM read_parquet([{files}], union_by_name=false) "
                f"ORDER BY author, subreddit, created_utc) TO {sql_string(path)} "
                f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {state['settings']['row_group_size']})"
            )
        with Metrics(root, name + "-validate"):
            output = inspect(path, root, 4, 20)
            if (
                output["rows"] != rows
                or output["fingerprint_xor"] != expected_xor
                or output["fingerprint_sum"] != sum(chunk["fingerprint_sum"] for chunk in chunks)
            ):
                raise RuntimeError("Tuning output does not match input chunks")
            verify_order(path)
        results.append({"threads": threads, "metrics": metrics.result, "output": output})
        save_json(root / "runs.json", results)
        path.unlink()
    medians = {
        str(threads): statistics.median(r["metrics"]["seconds"] for r in results if r["threads"] == threads)
        for threads in (1, 4)
    }
    save_json(
        root / "result.json",
        {
            "input_chunks": [chunk["path"] for chunk in chunks],
            "rows": rows,
            "memory_limit_gb": 20,
            "runs": results,
            "median_seconds": medians,
            "four_thread_speedup": medians["1"] / medians["4"],
            "note": "Subset comparison; full-month spill behavior can differ. Run order 1,4,4,1, no cache flushing.",
        },
    )


if __name__ == "__main__":
    main()
