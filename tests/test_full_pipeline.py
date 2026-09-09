import hashlib
import json
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zstandard

from benchmarks.full_pipeline import main, verify_order
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
