import hashlib
import json
import shutil

import pytest
import zstandard

from benchmarks.conversion import main, parse_metadata


def test_parse_metadata_requires_key_value_pairs():
    assert parse_metadata(["protection=enabled", "power_plan=Balanced"]) == {
        "protection": "enabled",
        "power_plan": "Balanced",
    }

    with pytest.raises(ValueError, match="KEY=VALUE"):
        parse_metadata(["missing-separator"])


@pytest.mark.skipif(shutil.which("zstd") is None, reason="system zstd is required")
def test_conversion_benchmark_smoke(tmp_path):
    records = [
        {
            "id": f"id-{index}",
            "author": author,
            "subreddit": subreddit,
            "created_utc": created,
            "score": index,
            "over_18": False,
            "edited": index % 2 == 0,
            "new_field": f"extra-{index}",
        }
        for index, (author, subreddit, created) in enumerate(
            [
                ("zeta", "python", 4),
                ("alpha", "duckdb", 3),
                ("alpha", "duckdb", 1),
                ("alpha", "parquet", 2),
            ]
        )
    ]
    source_bytes = b"".join(json.dumps(record).encode() + b"\n" for record in records)
    source = tmp_path / "RC_smoke.zst"
    source.write_bytes(zstandard.ZstdCompressor().compress(source_bytes))
    output = tmp_path / "benchmark-output"

    main(
        [
            "--source",
            str(source),
            "--expected-source-sha256",
            hashlib.sha256(source.read_bytes()).hexdigest(),
            "--output-dir",
            str(output),
            "--mode",
            "both",
            "--rows",
            "4",
            "--repetitions",
            "1",
            "--warmups",
            "0",
            "--threads",
            "1",
            "--memory-limit-gb",
            "1",
            "--metadata",
            "protection=test",
        ]
    )

    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["validation"]["rows"] == 4
    assert result["validation"]["schemas_equal"] is True
    assert result["validation"]["physical_sort_order"] is True
    assert result["configuration"]["metadata"] == {"protection": "test"}
    assert (
        result["runs"][0]["staged"]["output"]["content_fingerprint"]
        == result["runs"][0]["direct"]["output"]["content_fingerprint"]
    )
    assert not (output / "run-1").exists()
