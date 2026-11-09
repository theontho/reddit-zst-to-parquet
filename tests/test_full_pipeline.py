import hashlib
import json
import sys

import duckdb
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zstandard

from benchmarks.full_pipeline import Metrics, main, verify_order
from benchmarks.merge_tuning import main as tuning_main


@pytest.mark.parametrize("prefix", ["RC", "RS"])
def test_full_pipeline_and_resume(tmp_path, monkeypatch, prefix):
    rows = [
        {"author": "z", "subreddit": "x", "created_utc": 1, "id": "a", "edited": False},
        {"author": "a", "subreddit": "x", "created_utc": 2, "id": "b", "edited": 0},
        {"author": "a", "subreddit": "x", "created_utc": 1, "id": "c", "edited": False},
        {"author": None, "subreddit": "x", "created_utc": 0, "id": "d", "edited": False},
        {"author": "a", "subreddit": None, "created_utc": None, "id": "e", "edited": False},
    ]
    source = tmp_path / f"{prefix}_fixture.zst"
    source.write_bytes(zstandard.ZstdCompressor().compress("\n".join(json.dumps(row) for row in rows).encode()))
    output = tmp_path / "out"
    arguments = [
        "full_pipeline",
        "--source",
        str(source),
        "--expected-source-sha256",
        hashlib.sha256(source.read_bytes()).hexdigest(),
        "--output-dir",
        str(output),
        "--chunk-rows",
        "2",
        "--threads",
        "2",
        "--memory-gb",
        "1",
        "--merge-memory-gb",
        "1",
    ]
    monkeypatch.setattr(sys, "argv", [*arguments, "--stage", "chunks"])
    main()
    state = json.loads((output / "state.json").read_text())
    assert [c["rows"] for c in state["chunks"]] == [2, 2, 1]
    assert state["chunks_complete"]
    assert not state.get("merge_complete")
    monkeypatch.setattr(sys, "argv", [*arguments, "--stage", "merge"])
    main()
    result = json.loads((output / "result.json").read_text())
    assert result["merge_complete"]
    assert result["output"]["rows"] == 5
    assert result["output"]["physical_sort_order"]
    assert pq.read_table(result["output"]["path"], columns=["id"]).column("id").to_pylist() == ["c", "b", "e", "a", "d"]
    monkeypatch.setattr(sys, "argv", arguments)
    main()
    assert json.loads((output / "result.json").read_text()) == result
    monkeypatch.setattr(sys, "argv", ["merge_tuning", "--dataset-dir", str(output)])
    tuning_main()
    tuning = json.loads((output / "merge-tuning" / "result.json").read_text())
    assert tuning["rows"] == 5
    assert [run["threads"] for run in tuning["runs"]] == [1, 4, 4, 1]
    assert tuning["four_thread_speedup"] > 0


def test_order_checker_rejects_bad_sort(tmp_path):
    path = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"author": ["z", "a"], "subreddit": ["x", "x"], "created_utc": [1, 2]}), path)
    with pytest.raises(RuntimeError, match="sort violation"):
        verify_order(path)


def test_metrics_without_platform_process_io_counters(tmp_path, monkeypatch):
    monkeypatch.delattr(psutil.Process, "io_counters", raising=False)
    with Metrics(tmp_path, "portable") as metrics:
        pass
    assert metrics.result["read_bytes"] is None
    assert metrics.result["write_bytes"] is None
    sample = json.loads((tmp_path / "telemetry.jsonl").read_text().splitlines()[0])
    assert sample["process_io"] is None


def test_record_hash_preserves_edit_timestamps_and_nested_extras(tmp_path, monkeypatch):
    rows = [
        {
            "author": "z",
            "subreddit": "s",
            "created_utc": 1,
            "id": "a",
            "edited": 1720000000,
            "novel_field": {"array": [1, False]},
        },
        {"author": "a", "subreddit": "s", "created_utc": 2, "id": "b", "edited": False, "title": "plain text"},
    ]
    source = tmp_path / "RS_fixture.zst"
    source.write_bytes(
        zstandard.ZstdCompressor().compress(("\n".join(json.dumps(row) for row in rows) + "\n").encode())
    )
    output = tmp_path / "out"
    arguments = [
        "full_pipeline",
        "--source",
        str(source),
        "--record-source-sha256",
        "--output-dir",
        str(output),
        "--chunk-rows",
        "1",
        "--threads",
        "1",
        "--memory-gb",
        "1",
        "--merge-memory-gb",
        "1",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    main()
    result = json.loads((output / "result.json").read_text())
    converted = pq.read_table(result["output"]["path"]).to_pylist()
    assert [row["edited"] for row in converted] == [0, 1720000000]
    assert converted[0]["title"] == "plain text"
    assert json.loads(converted[1]["extra_json"])["novel_field"] == {"array": [1, False]}
    source.write_bytes(zstandard.ZstdCompressor().compress(b'{"id":"different"}\n'))
    with pytest.raises(ValueError, match="Resume configuration/source mismatch"):
        main()


@pytest.mark.parametrize(
    "data",
    [
        b'{"author":"a","id":"a"}\nnot valid JSON\n',
        b'{"author":"a","id":"a","score":"not an integer"}\n',
    ],
)
def test_invalid_data_is_not_silently_dropped_or_nulled(tmp_path, monkeypatch, data):
    source = tmp_path / "RC_fixture.zst"
    source.write_bytes(zstandard.ZstdCompressor().compress(data))
    output = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "full_pipeline",
            "--source",
            str(source),
            "--record-source-sha256",
            "--output-dir",
            str(output),
            "--chunk-rows",
            "10",
            "--threads",
            "1",
            "--memory-gb",
            "1",
            "--merge-memory-gb",
            "1",
        ],
    )
    with pytest.raises(duckdb.Error):
        main()
    assert source.is_file()
    assert not (output / "result.json").exists()
