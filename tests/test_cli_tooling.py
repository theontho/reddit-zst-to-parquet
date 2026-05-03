from __future__ import annotations

import copy
import subprocess
import sys

from commands.bench import _format_benchmark_summary
from commands.config_cmd import _format_value
from commands.manifests import _build_manifest, _build_manifest_from_parquet_file, _list_remote_files_for_manifests
from commands.precheck import apply_method_override, create_transfer_handler
from commands.verify import (
    _columns_from_manifest,
    _columns_from_parquet_tail,
    _compare_manifest_to_parquet,
    _verify_columns,
)
from core import config
from core.parquet_footer import parquet_file_from_tail
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler


def test_config_summary_redacts_password():
    assert _format_value("password", "secret-value") == "<redacted>"


def test_apply_method_override_updates_loaded_config(monkeypatch):
    original_config = copy.deepcopy(config.config_data)
    original_method = config.TRANSFER_METHOD
    monkeypatch.setattr(config, "config_data", original_config)
    monkeypatch.setattr(config, "TRANSFER_METHOD", original_method)

    apply_method_override("local")

    assert config.TRANSFER_METHOD == "local"
    assert config.config_data["transfer"]["method"] == "local"


def test_create_local_transfer_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REMOTE_DIR", str(tmp_path))

    handler = create_transfer_handler("local")

    assert isinstance(handler, LocalTransferHandler)


def test_validate_config_rejects_example_ftp_password():
    config_data = copy.deepcopy(config.config_data)
    config_data["transfer"]["method"] = "ftp"
    config_data["ftp"]["password"] = "YOUR_FTP_PASSWORD_HERE"

    try:
        config.validate_config(config_data)
    except ValueError as exc:
        assert "example placeholder" in str(exc)
    else:
        raise AssertionError("Expected placeholder FTP password to fail validation")


def test_benchmark_summary_formats_stage_table():
    summary = _format_benchmark_summary(
        "RC_2011-09.zst",
        [
            {
                "label": "Local Temp Directory",
                "temp_path": "/tmp/bench",
                "total_duration": 12.345,
                "stages": {
                    "download": {"duration_sec": 1.2, "speed_mb_s": 45.678},
                    "conversion": {"duration_sec": 10},
                    "upload": {"duration_sec": 1.145, "speed_mb_s": 67},
                },
            }
        ],
    )

    assert "BENCHMARK RESULTS" in summary
    assert "Input file: RC_2011-09.zst" in summary
    assert "Target:    Local Temp Directory" in summary
    assert "| Stage      | Duration | Throughput |" in summary
    assert "| Download   | 1.20s    | 45.68 MB/s |" in summary
    assert "| Conversion | 10.00s   | -          |" in summary
    assert "| Total      | 12.35s   | -          |" in summary


def test_benchmark_summary_handles_no_successful_results():
    summary = _format_benchmark_summary("RC_2011-09.zst", [])

    assert "BENCHMARK RESULTS" in summary
    assert "No benchmark completed successfully." in summary


def test_verify_reads_columns_from_supported_manifest_shapes():
    assert _columns_from_manifest({"schema": {"a": "int64", "extra_json": "string"}}) == {"a", "extra_json"}
    assert _columns_from_manifest({"columns": ["a", "extra_json"]}) == {"a", "extra_json"}
    assert _columns_from_manifest({"column_stats": {"a": {}, "extra_json": {}}}) == {"a", "extra_json"}


def test_verify_columns_reports_schema_mismatch():
    errors = _verify_columns({"a", "unexpected"}, {"a", "b"})

    assert "Missing columns: ['b']" in errors
    assert "Unexpected extra columns: ['unexpected']" in errors
    assert "extra_json column MISSING" in errors


def test_verify_reads_schema_from_parquet_tail(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet_path = tmp_path / "new-RC_2005-01.parquet"
    table = pa.table({"body": ["hello"], "extra_json": [None]})
    pq.write_table(table, parquet_path)
    tail = parquet_path.read_bytes()[-4096:]

    assert _columns_from_parquet_tail(tail) == {"body", "extra_json"}


def test_verify_reports_manifest_parquet_mismatch():
    errors = _compare_manifest_to_parquet({"body", "manifest_only"}, {"body", "parquet_only"})

    assert "Manifest missing Parquet columns: ['parquet_only']" in errors
    assert "Manifest has columns not in Parquet: ['manifest_only']" in errors


def test_manifest_listing_keeps_sizes_for_ftp_handler():
    class _FtpLike(FtpTransferHandler):
        def list_remote_files_with_all_sizes(self):
            return [], [("new-RC_2005-01.parquet", 123)], [("new-RC_2005-01.parquet.manifest.json", 45)]

    sizes, parquet_files, other_files = _list_remote_files_for_manifests(_FtpLike(), "ftp")

    assert sizes == {"new-RC_2005-01.parquet": 123}
    assert parquet_files == {"new-RC_2005-01.parquet"}
    assert other_files == {"new-RC_2005-01.parquet.manifest.json"}


def test_build_manifest_includes_schema_and_columns(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet_path = tmp_path / "new-RC_2005-01.parquet"
    table = pa.table({"body": ["hello"], "extra_json": [None]})
    pq.write_table(table, parquet_path)

    manifest = _build_manifest("new-RC_2005-01.parquet", str(parquet_path))

    assert manifest["filename"] == "new-RC_2005-01.parquet"
    assert manifest["row_count"] == 1
    assert manifest["columns"] == ["body", "extra_json"]
    assert manifest["schema"] == {"body": "string", "extra_json": "null"}


def test_build_manifest_from_footer_only_parquet(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet_path = tmp_path / "new-RC_2005-01.parquet"
    table = pa.table({"body": ["hello", "there"], "extra_json": [None, None]})
    pq.write_table(table, parquet_path)
    parquet_file = parquet_file_from_tail(parquet_path.read_bytes()[-4096:])
    assert parquet_file is not None

    manifest = _build_manifest_from_parquet_file("new-RC_2005-01.parquet", parquet_path.stat().st_size, parquet_file)

    assert manifest["file_size"] == parquet_path.stat().st_size
    assert manifest["row_count"] == 2
    assert manifest["columns"] == ["body", "extra_json"]
    assert manifest["schema"] == {"body": "string", "extra_json": "null"}


def test_manifests_help_includes_full_flag():
    result = subprocess.run(
        [sys.executable, "-m", "core.cli", "manifests", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--full" in result.stdout
