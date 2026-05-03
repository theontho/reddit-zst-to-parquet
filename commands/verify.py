import io
import json
import os
import time
from typing import Any

import pyarrow.parquet as pq

from commands.precheck import create_transfer_handler
from core import config
from core.parquet_footer import FOOTER_PROBE_BYTES, MAX_FOOTER_BYTES, footer_length_from_tail, parquet_file_from_tail
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler

FTP_VERIFY_DELAY_SECONDS = 2.0
FTP_FOOTER_RETRIES = 1


def _load_master_schemas() -> tuple[set[str], set[str]]:
    core_dir = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(core_dir, "core", "master_schema_rc.json")) as f:
        master_rc = set(json.load(f))
    with open(os.path.join(core_dir, "core", "master_schema_rs.json")) as f:
        master_rs = set(json.load(f))
    return master_rc, master_rs


def _schema_from_manifest(data: dict[str, Any]) -> dict[str, str]:
    schema = data.get("schema")
    if isinstance(schema, dict):
        return {str(column): str(data_type) for column, data_type in schema.items()}

    columns = data.get("columns")
    if isinstance(columns, list):
        return {str(column): "" for column in columns}

    column_stats = data.get("column_stats")
    if isinstance(column_stats, dict):
        return {str(column): "" for column in column_stats}

    return {}


def _columns_from_manifest(data: dict[str, Any]) -> set[str]:
    return set(_schema_from_manifest(data))


def _download_manifest_schema(handler: TransferHandler, manifest_name: str) -> dict[str, str]:
    content = handler.download_to_string(manifest_name)
    if not content:
        raise ValueError("manifest is empty or could not be downloaded")

    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("manifest root is not a JSON object")

    schema = _schema_from_manifest(data)
    if not schema:
        raise ValueError("manifest does not contain schema, columns, or column_stats")
    return schema


def _download_manifest_columns(handler: TransferHandler, manifest_name: str) -> set[str]:
    return set(_download_manifest_schema(handler, manifest_name))


def _schema_from_parquet_file(parquet_file: pq.ParquetFile) -> dict[str, str]:
    return {field.name: str(field.type) for field in parquet_file.schema_arrow}


def _download_ftp_tail(handler: FtpTransferHandler, filename: str, file_size: int, byte_count: int) -> bytes:
    if file_size <= 0:
        raise ValueError("remote file size is unknown")

    offset = max(file_size - byte_count, 0)
    last_error: Exception | None = None
    for _attempt in range(FTP_FOOTER_RETRIES + 1):
        buffer = io.BytesIO()
        try:
            ftp = handler._get_ftp()
            ftp.retrbinary(f"RETR {filename}", buffer.write, rest=offset)
            return buffer.getvalue()
        except Exception as exc:
            last_error = exc
            # A failed RETR can leave the control connection with a pending reply.
            # Reconnect before retrying or continuing to the next file.
            handler.close()

    raise last_error or RuntimeError(f"Failed to download FTP tail for {filename}")


def _columns_from_parquet_tail(tail_bytes: bytes) -> set[str] | None:
    parquet_file = parquet_file_from_tail(tail_bytes)
    if parquet_file is None:
        return None
    return set(parquet_file.schema_arrow.names)


def _schema_from_parquet_tail(tail_bytes: bytes) -> dict[str, str] | None:
    parquet_file = parquet_file_from_tail(tail_bytes)
    if parquet_file is None:
        return None
    return _schema_from_parquet_file(parquet_file)


def _download_ftp_parquet_schema(
    handler: FtpTransferHandler,
    filename: str,
    file_size: int,
    probe_bytes: int = FOOTER_PROBE_BYTES,
    max_footer_bytes: int = MAX_FOOTER_BYTES,
) -> dict[str, str]:
    tail = _download_ftp_tail(handler, filename, file_size, min(file_size, probe_bytes))
    schema = _schema_from_parquet_tail(tail)
    if schema is not None:
        return schema

    footer_length = footer_length_from_tail(tail)
    required_bytes = footer_length + 8
    if required_bytes > max_footer_bytes:
        raise ValueError(
            f"Parquet footer is too large for safe FTP verification: {required_bytes} bytes (max {max_footer_bytes})"
        )
    if required_bytes > file_size:
        raise ValueError(f"Parquet footer length {footer_length} exceeds file size {file_size}")

    full_footer = _download_ftp_tail(handler, filename, file_size, required_bytes)
    schema = _schema_from_parquet_tail(full_footer)
    if schema is None:
        raise ValueError("Downloaded Parquet footer is incomplete")
    return schema


def _load_real_parquet_schema(
    handler: TransferHandler,
    method: str,
    filename: str,
    parquet_sizes: dict[str, int],
) -> dict[str, str] | None:
    if method == "ftp":
        if not isinstance(handler, FtpTransferHandler):
            raise TypeError("FTP verification requires FtpTransferHandler")
        return _download_ftp_parquet_schema(handler, filename, parquet_sizes.get(filename, 0))

    if method == "local":
        if not isinstance(handler, LocalTransferHandler):
            raise TypeError("Local verification requires LocalTransferHandler")
        parquet_file = pq.ParquetFile(handler._resolve_path(filename))
        return _schema_from_parquet_file(parquet_file)

    return None


def _verify_columns(columns: set[str], master: set[str]) -> list[str]:
    errors = []
    missing = master - columns
    extra = columns - master - {"extra_json"}

    if missing:
        errors.append(f"Missing columns: {sorted(missing)}")
    if extra:
        errors.append(f"Unexpected extra columns: {sorted(extra)}")
    if "extra_json" not in columns:
        errors.append("extra_json column MISSING")
    return errors


def _compare_manifest_to_parquet(manifest_columns: set[str], parquet_columns: set[str]) -> list[str]:
    errors = []
    missing_from_manifest = parquet_columns - manifest_columns
    extra_in_manifest = manifest_columns - parquet_columns
    if missing_from_manifest:
        errors.append(f"Manifest missing Parquet columns: {sorted(missing_from_manifest)}")
    if extra_in_manifest:
        errors.append(f"Manifest has columns not in Parquet: {sorted(extra_in_manifest)}")
    return errors


def _compare_manifest_schema_to_parquet(manifest_schema: dict[str, str], parquet_schema: dict[str, str]) -> list[str]:
    errors = _compare_manifest_to_parquet(set(manifest_schema), set(parquet_schema))
    for column in sorted(set(manifest_schema).intersection(parquet_schema)):
        manifest_type = manifest_schema[column]
        parquet_type = parquet_schema[column]
        if manifest_type and parquet_type and manifest_type != parquet_type:
            errors.append(f"Manifest type mismatch for {column}: manifest={manifest_type}, parquet={parquet_type}")
    return errors


def _list_remote_files_for_verify(handler: TransferHandler, method: str) -> tuple[dict[str, int], set[str], set[str]]:
    if method == "ftp" and isinstance(handler, FtpTransferHandler):
        _zst_files, parquet_files, other_files = handler.list_remote_files_with_all_sizes()
        return dict(parquet_files), {name for name, _size in parquet_files}, {name for name, _size in other_files}

    _zst_files, parquet_names, other_names = handler.list_remote_files()
    return {}, parquet_names, other_names


def run_verification(limit: int | None = None, delay_seconds: float | None = None, offset: int = 0) -> int:
    """Verifies that the remote Parquet files match the master schemas."""
    if limit is not None and limit < 0:
        print("Error: --limit must be non-negative")
        return 1
    if offset < 0:
        print("Error: --offset must be non-negative")
        return 1

    method = config.TRANSFER_METHOD.lower()
    try:
        handler = create_transfer_handler(method)
    except ValueError:
        print(f"Error: Unsupported transfer method for verification: {method}")
        return 1

    if delay_seconds is None:
        delay_seconds = FTP_VERIFY_DELAY_SECONDS if method == "ftp" else 0.0

    print(f"Connecting via {method.upper()}...")
    if method == "ftp":
        print(
            "FTP safety: verifying actual Parquet footer schemas plus manifest JSON; "
            f"delay between files: {delay_seconds:.1f}s."
        )

    try:
        parquet_sizes, all_parquet, other_files = _list_remote_files_for_verify(handler, method)
    except Exception as e:
        print(f"Error listing remote directory: {e}")
        handler.close()
        return 1

    parquet_files = sorted(f for f in all_parquet if f.startswith("new-"))
    if offset > 0:
        parquet_files = parquet_files[offset:]
    if limit is not None:
        parquet_files = parquet_files[:limit]

    if not parquet_files:
        print("No new-*.parquet files found.")
        handler.close()
        return 0

    print(f"Found {len(parquet_files)} files to verify.")

    try:
        master_rc, master_rs = _load_master_schemas()
    except Exception as e:
        print(f"Error loading master schemas: {e}")
        handler.close()
        return 1

    results = {"total": 0, "ok": 0, "failed": 0, "skipped": 0}

    try:
        for filename in parquet_files:
            results["total"] += 1
            print(
                f"[{results['total']}/{len(parquet_files)}] Verifying {filename}...",
                end=" ",
                flush=True,
            )

            try:
                if "RC_" in filename:
                    master = master_rc
                elif "RS_" in filename:
                    master = master_rs
                else:
                    print("SKIPPED (Unknown type)")
                    results["skipped"] += 1
                    continue

                parquet_schema = _load_real_parquet_schema(handler, method, filename, parquet_sizes)
                manifest_name = f"{filename}.manifest.json"

                if parquet_schema is None:
                    if manifest_name not in other_files:
                        print("FAILED (manifest missing; direct Parquet verification unavailable for this method)")
                        results["failed"] += 1
                        continue
                    columns = _download_manifest_columns(handler, manifest_name)
                    errors = _verify_columns(columns, master)
                else:
                    errors = [f"Parquet schema: {error}" for error in _verify_columns(set(parquet_schema), master)]
                    if manifest_name not in other_files:
                        errors.append("Manifest missing")
                    else:
                        manifest_schema = _download_manifest_schema(handler, manifest_name)
                        errors.extend(_compare_manifest_schema_to_parquet(manifest_schema, parquet_schema))

                    edited_type = parquet_schema.get("edited")
                    if edited_type and edited_type != "int64":
                        errors.append(f"Parquet schema: edited must be int64, got {edited_type}")

                if not errors:
                    print("OK")
                    results["ok"] += 1
                else:
                    print("FAILED")
                    for error in errors:
                        print(f"    {error}")
                    results["failed"] += 1

            except Exception as e:
                print(f"ERROR: {e}")
                results["failed"] += 1

            if method == "ftp" and delay_seconds > 0 and results["total"] < len(parquet_files):
                time.sleep(delay_seconds)
    finally:
        handler.close()

    print("\n--- Verification Summary ---")
    print(f"Total Files Processed: {results['total']}")
    print(f"Passed: {results['ok']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")
    return 0 if results["failed"] == 0 else 1
