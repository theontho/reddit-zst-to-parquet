import json
import os
import tempfile
import time
from typing import Any

import pyarrow.parquet as pq

from commands.precheck import create_transfer_handler
from commands.verify import _download_ftp_tail
from core import config
from core.parquet_footer import FOOTER_PROBE_BYTES, MAX_FOOTER_BYTES, footer_length_from_tail, parquet_file_from_tail
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler

FTP_MANIFEST_DELAY_SECONDS = 5.0


def _list_remote_files_for_manifests(
    handler: TransferHandler, method: str
) -> tuple[dict[str, int], set[str], set[str]]:
    if method == "ftp" and isinstance(handler, FtpTransferHandler):
        _zst_files, parquet_files, other_files = handler.list_remote_files_with_all_sizes()
        return dict(parquet_files), {name for name, _size in parquet_files}, {name for name, _size in other_files}

    _zst_files, parquet_names, other_names = handler.list_remote_files()
    return {}, parquet_names, other_names


def _build_manifest(filename: str, local_path: str) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(local_path)
    return _build_manifest_from_parquet_file(filename, os.path.getsize(local_path), parquet_file)


def _build_manifest_from_parquet_file(filename: str, file_size: int, parquet_file: pq.ParquetFile) -> dict[str, Any]:
    metadata = parquet_file.metadata
    if metadata is None:
        raise ValueError("Parquet metadata is missing")
    schema = parquet_file.schema_arrow
    types = {field.name: str(field.type) for field in schema}
    return {
        "filename": filename,
        "file_size": file_size,
        "row_count": metadata.num_rows,
        "conversion_method": "manual_regeneration",
        "schema": types,
        "columns": schema.names,
    }


def _download_ftp_parquet_file_from_footer(
    handler: FtpTransferHandler,
    filename: str,
    file_size: int,
    probe_bytes: int = FOOTER_PROBE_BYTES,
    max_footer_bytes: int = MAX_FOOTER_BYTES,
) -> pq.ParquetFile:
    tail = _download_ftp_tail(handler, filename, file_size, min(file_size, probe_bytes))
    parquet_file = parquet_file_from_tail(tail)
    if parquet_file is not None:
        return parquet_file

    footer_length = footer_length_from_tail(tail)
    required_bytes = footer_length + 8
    if required_bytes > max_footer_bytes:
        raise ValueError(
            f"Parquet footer is too large for safe FTP manifest generation: {required_bytes} bytes "
            f"(max {max_footer_bytes})"
        )
    if required_bytes > file_size:
        raise ValueError(f"Parquet footer length {footer_length} exceeds file size {file_size}")

    full_footer = _download_ftp_tail(handler, filename, file_size, required_bytes)
    parquet_file = parquet_file_from_tail(full_footer)
    if parquet_file is None:
        raise ValueError("Downloaded Parquet footer is incomplete")
    return parquet_file


def _build_ftp_manifest(handler: FtpTransferHandler, filename: str, file_size: int) -> dict[str, Any]:
    if file_size <= 0:
        raise ValueError(f"Cannot build footer-only manifest for {filename}: remote size is unknown")
    parquet_file = _download_ftp_parquet_file_from_footer(handler, filename, file_size)
    return _build_manifest_from_parquet_file(filename, file_size, parquet_file)


def _upload_manifest(handler: TransferHandler, manifest_name: str, manifest: dict[str, Any]) -> bool:
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as tmp:
            json.dump(manifest, tmp, indent=2)
            tmp.write("\n")
            tmp_name = tmp.name

        success, _ = handler.upload_file(tmp_name, manifest_name)
        return success
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.remove(tmp_name)


def run_generate_manifests(
    force=False,
    limit: int | None = None,
    delay_seconds: float | None = None,
    full: bool = False,
) -> int:
    """
    Generates manifest.json files for remote Parquet files using the selected transfer handler.
    """
    method = config.TRANSFER_METHOD.lower()
    try:
        handler = create_transfer_handler(method)
    except ValueError:
        print(f"Error: Unsupported transfer method for manifests: {method}")
        return 1

    if delay_seconds is None:
        delay_seconds = FTP_MANIFEST_DELAY_SECONDS if method == "ftp" else 0.0

    print(f"Connecting via {method.upper()}...")
    if method == "ftp":
        if full:
            print(
                "FTP full manifest mode: downloading each full Parquet file and processing one file at a time, "
                f"with {delay_seconds:.1f}s between files."
            )
        else:
            print(
                "FTP safety: reading Parquet footers only and processing one file at a time, "
                f"with {delay_seconds:.1f}s between files."
            )

    try:
        parquet_sizes, all_parquet, other_files = _list_remote_files_for_manifests(handler, method)

        # Filter for Parquet files
        parquet_files = sorted(f for f in all_parquet if f.endswith(".parquet"))
        if limit is not None:
            parquet_files = parquet_files[:limit]
        print(f"Found {len(parquet_files)} parquet files.")

        generated = 0
        skipped = 0
        failed = 0

        for index, filename in enumerate(parquet_files, start=1):
            manifest_name = f"{filename}.manifest.json"
            if manifest_name in other_files and not force:
                skipped += 1
                continue

            print(f"[{index}/{len(parquet_files)}] Generating manifest for {filename}...")
            try:
                if method == "ftp" and isinstance(handler, FtpTransferHandler) and not full:
                    manifest = _build_ftp_manifest(handler, filename, parquet_sizes.get(filename, 0))
                else:
                    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                        expected_size = parquet_sizes.get(filename, 0)
                        success, _ = handler.download_file(filename, tmp.name, expected_size=expected_size)
                        if not success:
                            raise RuntimeError(f"Failed to download {filename} for manifest generation.")
                        manifest = _build_manifest(filename, tmp.name)

                if _upload_manifest(handler, manifest_name, manifest):
                    print(f"  Uploaded {manifest_name}")
                    generated += 1
                else:
                    print(f"  Failed to upload {manifest_name}")
                    failed += 1

            except Exception as e:
                print(f"  Error processing {filename}: {e}")
                failed += 1

            if method == "ftp" and delay_seconds > 0 and index < len(parquet_files):
                time.sleep(delay_seconds)

        print("\n--- Manifest Summary ---")
        print(f"Generated: {generated}")
        print(f"Skipped existing: {skipped}")
        print(f"Failed: {failed}")
        return 0 if failed == 0 else 1

    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        handler.close()
