import json
import os

import fsspec
import pyarrow.parquet as pq

from core import config
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler
from transfer.nfs_transfer import NfsTransferHandler
from transfer.rsync_ssh_transfer import RsyncSshTransferHandler

_FTP_FS = None


def run_verification():
    """Verifies that the remote Parquet files match the master schemas."""
    method = config.TRANSFER_METHOD.lower()
    handler: TransferHandler
    if method == "ftp":
        handler = FtpTransferHandler()
    elif method == "rsync":
        handler = RsyncSshTransferHandler()
    elif method == "nfs":
        handler = NfsTransferHandler()
    elif method == "local":
        handler = LocalTransferHandler()
    else:
        print(f"Error: Unsupported transfer method for verification: {method}")
        return

    print(f"Connecting via {method.upper()}...")

    try:
        _, all_parquet, _ = handler.list_remote_files()
    except Exception as e:
        print(f"Error listing remote directory: {e}")
        return

    parquet_files = [f for f in all_parquet if f.startswith("new-")]

    if not parquet_files:
        print("No new-*.parquet files found.")
        return

    print(f"Found {len(parquet_files)} files to verify.")

    # Load master schemas
    core_dir = os.path.dirname(os.path.dirname(__file__))
    try:
        with open(os.path.join(core_dir, "core", "master_schema_rc.json")) as f:
            master_rc = set(json.load(f))
        with open(os.path.join(core_dir, "core", "master_schema_rs.json")) as f:
            master_rs = set(json.load(f))
    except Exception as e:
        print(f"Error loading master schemas: {e}")
        return

    results = {"total": 0, "ok": 0, "failed": 0}

    for filename in sorted(parquet_files):
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
                continue

            # This is a bit complex for non-FTP methods, but let's assume FTP for now as per original script
            # or implement a generic 'open_remote' in handlers if we want it truly modular.
            # For now, we'll keep the FTP-centric verification or use local if method is local.

            if method == "ftp":
                global _FTP_FS
                if _FTP_FS is None:
                    _FTP_FS = fsspec.filesystem(
                        "ftp",
                        host=config.FTP_HOST,
                        user=config.FTP_USER,
                        password=config.FTP_PASSWORD,
                        port=config.FTP_PORT,
                        timeout=config.FTP_TIMEOUT_SECONDS,
                    )

                f_path = os.path.join(config.REMOTE_DIR, filename)
                if not f_path.startswith("/"):
                    f_path = "/" + f_path

                # Use a block cache to prevent multiple small FTP RETR commands
                # for metadata reads, which overloads the server.
                with _FTP_FS.open(f_path, "rb", cache_type="readahead") as f:
                    parquet_file = pq.ParquetFile(f)
                    columns = set(parquet_file.schema_arrow.names)
            elif method == "local":
                f_path = os.path.join(config.REMOTE_DIR, filename)
                parquet_file = pq.ParquetFile(f_path)
                columns = set(parquet_file.schema_arrow.names)
            else:
                print("SKIPPED (Verification only implemented for FTP and Local currently)")
                continue

            # Check columns
            missing = master - columns
            extra = columns - master - {"extra_json"}

            if not missing and not extra and "extra_json" in columns:
                print("✓ OK")
                results["ok"] += 1
            else:
                print("✗ FAILED")
                if missing:
                    print(f"    Missing columns: {sorted(missing)}")
                if extra:
                    print(f"    Unexpected extra columns: {sorted(extra)}")
                if "extra_json" not in columns:
                    print("    extra_json column MISSING")
                results["failed"] += 1

        except Exception as e:
            print(f"✗ ERROR: {e}")
            results["failed"] += 1

    print("\n--- Verification Summary ---")
    print(f"Total Files Processed: {results['total']}")
    print(f"Passed: {results['ok']}")
    print(f"Failed: {results['failed']}")
