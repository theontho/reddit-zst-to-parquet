import copy
import subprocess
import sys
from typing import cast

from commands.run import run_conversion_loop
from core import config
from core.converter import convert_to_parquet
from core.processor import get_files_to_process, process_file
from engines.chunked_engine import load_master_schema
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler


def test_chunked_engine_loads_packaged_master_schema():
    assert load_master_schema("RC_2024-01.zst")
    assert load_master_schema("RS_2024-01.zst")


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
