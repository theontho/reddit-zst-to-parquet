import copy
import io
import subprocess
import sys
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from commands.run import run_conversion_loop
from core import config
from core.converter import convert_to_parquet
from core.processor import get_files_to_process, process_file
from engines.chunked_engine import (
    _initialize_resume_state,
    _parquet_metadata_row_count,
    _write_jsonl_chunk,
    adaptive_chunk_size,
    adaptive_chunk_threads,
    effective_duckdb_memory_limit_gb,
    load_master_schema,
    parse_arguments,
)
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler


def test_chunked_engine_loads_packaged_master_schema():
    assert load_master_schema("RC_2024-01.zst")
    assert load_master_schema("RS_2024-01.zst")


def test_write_jsonl_chunk_streams_bounded_lines(tmp_path):
    output_path = tmp_path / "chunk.jsonl"
    source = io.StringIO('{"id": 1}\n{"id": 2}\n{"id": 3}\n')

    assert _write_jsonl_chunk(source, str(output_path), 2) == 2
    assert output_path.read_text(encoding="utf-8") == '{"id": 1}\n{"id": 2}\n'
    assert source.readline() == '{"id": 3}\n'


def test_parquet_metadata_row_count_sums_files(tmp_path):
    first_path = tmp_path / "first.parquet"
    second_path = tmp_path / "second.parquet"
    pq.write_table(pa.table({"id": [1, 2]}), first_path)
    pq.write_table(pa.table({"id": [3, 4, 5]}), second_path)

    assert _parquet_metadata_row_count([str(first_path), str(second_path)]) == 5


def test_chunked_engine_accepts_merge_resource_overrides(tmp_path, monkeypatch):
    input_path = tmp_path / "RC_2026-05.zst"
    input_path.write_bytes(b"zstd fixture")
    monkeypatch.setattr("engines.chunked_engine.shutil.which", lambda _path: "/usr/bin/tool")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chunked_engine.py",
            str(input_path),
            "--merge-threads",
            "9",
            "--merge-memory-limit-gb",
            "25",
        ],
    )

    args = parse_arguments()

    assert args.merge_threads == 9
    assert args.merge_memory_limit_gb == 25


def test_effective_duckdb_memory_limit_respects_host_headroom():
    assert effective_duckdb_memory_limit_gb(25, total_ram_gb=32, ram_usage_factor=0.8) == 25
    assert effective_duckdb_memory_limit_gb(25, total_ram_gb=16, ram_usage_factor=0.8) == 12
    assert effective_duckdb_memory_limit_gb(8, total_ram_gb=32, ram_usage_factor=0.8) == 8


def test_adaptive_chunk_size_uses_spill_free_memory_ratio():
    assert adaptive_chunk_size("RC_2026-05.zst", 8) == 2_000_000
    assert adaptive_chunk_size("RC_2026-05.zst", 12) == 3_000_000
    assert adaptive_chunk_size("RC_2026-05.zst", 25) == 6_000_000
    assert adaptive_chunk_size("RC_2026-05.zst", 64) == 6_000_000


def test_adaptive_chunk_size_uses_measured_submission_memory_curve():
    assert adaptive_chunk_size("RS_2026-05.zst", 8) == 250_000
    assert adaptive_chunk_size("RS_2026-05.zst", 12) == 500_000
    assert adaptive_chunk_size("RS_2026-05.zst", 25) == 1_500_000
    assert adaptive_chunk_size("RS_2026-05.zst", 64) == 1_500_000


def test_adaptive_chunk_threads_retains_memory_headroom():
    assert adaptive_chunk_threads(15, 8) == 4
    assert adaptive_chunk_threads(15, 12) == 6
    assert adaptive_chunk_threads(9, 25) == 9


def test_chunked_engine_preserves_explicit_chunk_size(tmp_path, monkeypatch):
    input_path = tmp_path / "RC_2026-05.zst"
    input_path.write_bytes(b"zstd fixture")
    monkeypatch.setattr("engines.chunked_engine.shutil.which", lambda _path: "/usr/bin/tool")
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunked_engine.py", str(input_path), "--chunk-size", "2750000"],
    )

    args = parse_arguments()

    assert args.chunk_size == 2_750_000
    assert args.chunk_size_source == "command line"


def test_chunked_engine_automatically_sizes_chunks_from_memory(tmp_path, monkeypatch):
    input_path = tmp_path / "RC_2026-05.zst"
    input_path.write_bytes(b"zstd fixture")
    monkeypatch.setattr("engines.chunked_engine.shutil.which", lambda _path: "/usr/bin/tool")
    monkeypatch.setattr("engines.chunked_engine.ADAPTIVE_CHUNK_SIZE", True)
    monkeypatch.setattr("engines.chunked_engine.DUCKDB_MEMORY_LIMIT_GB", 25)
    monkeypatch.setattr("engines.chunked_engine.DUCKDB_THREADS", 15)
    monkeypatch.setattr("engines.chunked_engine.TOTAL_RAM_GB", 16)
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunked_engine.py", str(input_path)],
    )

    args = parse_arguments()

    assert args.chunk_memory_limit_gb == 12
    assert args.chunk_threads == 6
    assert args.chunk_size == 3_000_000
    assert args.spill_free_chunk_size == 3_000_000
    assert args.chunk_size_source.startswith("adaptive")


def test_resume_rejects_changed_adaptive_chunk_size(tmp_path):
    (tmp_path / "chunk_00001.parquet").write_bytes(b"existing")
    (tmp_path / "chunking.json").write_text('{"chunk_size": 2000000}', encoding="utf-8")

    try:
        _initialize_resume_state(str(tmp_path), 3_000_000)
    except RuntimeError as exc:
        assert "different chunk size" in str(exc)
    else:
        raise AssertionError("Expected changed chunk size to make resume fail")


def test_chunked_engine_rejects_unsorted_chunks_with_final_merge(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "RC_2026-05.zst"
    input_path.write_bytes(b"zstd fixture")
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunked_engine.py", str(input_path), "--skip-chunk-sort"],
    )

    try:
        parse_arguments()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected unsafe unsorted global merge to be rejected")

    assert "--skip-chunk-sort requires --no-merge" in capsys.readouterr().err


def test_local_transfer_rejects_sibling_prefix_path(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(config, "REMOTE_DIR", str(base))

    handler = LocalTransferHandler()

    try:
        handler._resolve_path("../base2/escape.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected path traversal to be rejected")


def test_local_claim_create_is_exclusive(tmp_path, monkeypatch):
    base = tmp_path / "remote"
    base.mkdir()
    local_claim = tmp_path / "claim.json"
    local_claim.write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.setattr(config, "REMOTE_DIR", str(base))

    handler = LocalTransferHandler()

    assert handler.try_create_claim(str(local_claim), "file.claim.json")[0] is True
    assert handler.try_create_claim(str(local_claim), "file.claim.json")[0] is False


def test_converter_falls_back_when_primary_output_missing(tmp_path, monkeypatch):
    input_path = tmp_path / "input.zst"
    output_path = tmp_path / "output.parquet"
    primary = tmp_path / "primary.py"
    fallback = tmp_path / "fallback.py"
    input_path.write_bytes(b"not really zstd")

    primary.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    fallback.write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'parquet')\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("core.converter.CONVERSION_METHOD", "streamed")
    monkeypatch.setattr("core.converter.STREAMED_CONVERTER_PATH", str(primary))
    monkeypatch.setattr("core.converter.CHUNKED_CONVERTER_PATH", str(fallback))
    monkeypatch.setattr("core.converter.FALLBACK_TO_CHUNKED", True)

    assert convert_to_parquet(str(input_path), str(output_path), str(tmp_path)) is True
    assert output_path.exists()


class _ClaimedTransfer:
    def try_create_claim(self, local_path, remote_filename):
        return False, 0.0

    def file_exists(self, remote_filename):
        return False


def test_process_file_returns_skipped_on_claim_contention(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONVERSION_TEMP_BASE_DIR", str(tmp_path))

    result = process_file(
        zst_filename="RC_2005-01.zst",
        remote_size=123,
        log_data={"files": {"RC_2005-01.zst": {"status": "pending"}}},
        transfer_handler=cast(TransferHandler, _ClaimedTransfer()),
    )

    assert result == "skipped"


class _FakeFtp:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = set()
        self.sock = None

    def mkd(self, name):
        if name in self.directories:
            raise OSError("exists")
        self.directories.add(name)

    def rmd(self, name):
        self.directories.remove(name)

    def storbinary(self, command, fp, callback=None):
        _, name = command.split(maxsplit=1)
        data = fp.read()
        self.files[name] = data
        if callback:
            callback(data)

    def size(self, name):
        if name not in self.files:
            raise OSError("missing")
        return len(self.files[name])

    def delete(self, name):
        del self.files[name]


def test_ftp_claim_uses_sidecar_lock(tmp_path, monkeypatch):
    ftp = _FakeFtp()
    local_claim = tmp_path / "claim.json"
    local_claim.write_text('{"ok": true}', encoding="utf-8")
    handler = FtpTransferHandler()
    monkeypatch.setattr(handler, "_get_ftp", lambda: ftp)

    assert handler.try_create_claim(str(local_claim), "file.claim.json")[0] is True
    assert "file.claim.json.lock" not in ftp.directories

    assert handler.try_create_claim(str(local_claim), "file.claim.json")[0] is False

    assert handler.delete_file("file.claim.json") is True
    assert "file.claim.json.lock" not in ftp.directories


def test_ftp_upload_deletes_mismatched_remote(tmp_path, monkeypatch):
    ftp = _FakeFtp()
    local_file = tmp_path / "file.txt"
    local_file.write_text("hello", encoding="utf-8")
    handler = FtpTransferHandler()
    monkeypatch.setattr(handler, "_get_ftp", lambda: ftp)

    def wrong_size(name):
        if name in ftp.files:
            return len(ftp.files[name]) + 1
        raise OSError("missing")

    monkeypatch.setattr(ftp, "size", wrong_size)

    assert handler.upload_file(str(local_file), "remote.txt")[0] is False
    assert "remote.txt" not in ftp.files


def test_get_files_to_process_retries_parquet_without_manifest():
    log_data = {"files": {"RC_2005-01.zst": {"status": "upload_failed"}}}

    files = get_files_to_process(
        log_data=log_data,
        remote_zst_files_with_sizes=[("RC_2005-01.zst", 10)],
        remote_parquet_files={"new-RC_2005-01.parquet"},
        remote_other_files=set(),
        transfer_handler=cast(TransferHandler, _ClaimedTransfer()),
        machine_meta={},
    )

    assert files == [("RC_2005-01.zst", 10)]


def test_get_files_to_process_skips_when_manifest_exists():
    log_data = {"files": {"RC_2005-01.zst": {"status": "upload_failed"}}}

    files = get_files_to_process(
        log_data=log_data,
        remote_zst_files_with_sizes=[("RC_2005-01.zst", 10)],
        remote_parquet_files={"new-RC_2005-01.parquet"},
        remote_other_files={"new-RC_2005-01.parquet.manifest.json"},
        transfer_handler=cast(TransferHandler, _ClaimedTransfer()),
        machine_meta={},
    )

    assert files == []


def test_run_only_reprocesses_completed_file(monkeypatch, tmp_path):
    test_config = copy.deepcopy(config.config_data)
    test_config["transfer"]["method"] = "local"
    monkeypatch.setattr(config, "config_data", test_config)
    monkeypatch.setattr(config, "TRANSFER_METHOD", "local")
    monkeypatch.setattr(config, "REMOTE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONVERSION_TEMP_BASE_DIR", str(tmp_path / "temp"))

    calls: list[tuple[str, bool]] = []

    class _RunOnlyTransfer(_ClaimedTransfer):
        def __init__(self):
            self.closed = False

        def list_remote_files(self):
            return [("RC_2005-01.zst", 10), ("RC_2005-02.zst", 20)], set(), {"new-RC_2005-01.parquet.manifest.json"}

        def check_prerequisites(self):
            return True

        def check_connection(self):
            return True

        def close(self):
            self.closed = True

    def fake_process_file(**kwargs):
        calls.append((kwargs["zst_filename"], kwargs["force"]))
        return "success"

    monkeypatch.setattr("commands.run.LocalTransferHandler", _RunOnlyTransfer)
    monkeypatch.setattr("commands.run.get_machine_metadata", lambda: {})
    monkeypatch.setattr("commands.run.select_temp_dir", lambda: str(tmp_path / "temp"))
    monkeypatch.setattr("commands.run.cleanup_orphan_temp_dirs", lambda *_args: None)
    monkeypatch.setattr("commands.run.process_file", fake_process_file)

    run_conversion_loop(only="RC_2005-01.zst", force=True)

    assert calls == [("RC_2005-01.zst", True)]


def test_process_file_force_replaces_existing_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONVERSION_TEMP_BASE_DIR", str(tmp_path))
    deleted_claims = []

    class _ForceClaimTransfer(_ClaimedTransfer):
        def __init__(self):
            self.claim_exists = True

        def file_exists(self, remote_filename):
            return self.claim_exists

        def delete_file(self, remote_filename):
            deleted_claims.append(remote_filename)
            self.claim_exists = False
            return True

        def try_create_claim(self, local_path, remote_filename):
            return (not self.claim_exists), 0.0

        def upload_file(self, local_path, remote_filename):
            return True, 0.0

        def download_file(self, remote_filename, local_path, expected_size):
            return False, 0.0

    result = process_file(
        zst_filename="RC_2005-01.zst",
        remote_size=123,
        log_data={"files": {"RC_2005-01.zst": {"status": "pending"}}},
        transfer_handler=cast(TransferHandler, _ForceClaimTransfer()),
        force=True,
    )

    assert result == "failed"
    assert deleted_claims == ["RC_2005-01.claim.json"]


def test_run_force_without_only_shows_run_usage():
    result = subprocess.run(
        [sys.executable, "-m", "core.cli", "run", "--force"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "usage: " in result.stderr
    assert "run" in result.stderr
    assert "--only" in result.stderr
