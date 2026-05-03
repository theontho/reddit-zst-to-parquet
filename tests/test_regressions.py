from core import config
from core.converter import convert_to_parquet
from engines.chunked_engine import load_master_schema
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
