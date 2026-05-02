import io
import json
import os
import sys

import pyarrow.parquet as pq

from core import config
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler
from transfer.nfs_transfer import NfsTransferHandler
from transfer.rsync_ssh_transfer import RsyncSshTransferHandler


def run_generate_manifests(force=False):
    """
    Generates manifest.json files for remote Parquet files using the selected transfer handler.
    """
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
        print(f"Error: Unsupported transfer method for manifests: {method}")
        return

    print(f"Connecting via {method.upper()}...")
    try:
        zst_files, all_parquet, other_files = handler.list_remote_files()
        
        # Filter for Parquet files
        parquet_files = [f for f in all_parquet if f.endswith(".parquet")]
        print(f"Found {len(parquet_files)} parquet files.")

        for filename in sorted(parquet_files):
            manifest_name = f"{filename}.manifest.json"
            if manifest_name in other_files and not force:
                continue

            print(f"Generating manifest for {filename}...")
            try:
                # We need a way to read the parquet metadata. 
                # For Local, it's easy. For others, we might need to download or use fsspec.
                # To keep it simple and robust, we'll download to a temp file if not local.
                
                # Download to a temporary file for analysis (symlinked if local)
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                    # We don't know the size, but download_file verification is optional if expected_size is 0
                    success, _ = handler.download_file(filename, tmp.name, expected_size=0)
                    if not success:
                        raise Exception(f"Failed to download {filename} for manifest generation.")
                    parquet_file = pq.ParquetFile(tmp.name)
                    file_size = os.path.getsize(tmp.name)

                # Basic stats
                metadata = parquet_file.metadata
                schema = parquet_file.schema_arrow

                manifest = {
                    "filename": filename,
                    "file_size": file_size,
                    "row_count": metadata.num_rows,
                    "columns": schema.names,
                    "conversion_method": "manual_regeneration",
                }

                # Upload manifest
                manifest_json = json.dumps(manifest, indent=2)
                
                # Write to temp file first for upload
                import tempfile
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                    tmp.write(manifest_json)
                    tmp_name = tmp.name
                
                try:
                    success, _ = handler.upload_file(tmp_name, manifest_name)
                    if success:
                        print(f"  ✓ Uploaded {manifest_name}")
                    else:
                        print(f"  ✗ Failed to upload {manifest_name}")
                finally:
                    if os.path.exists(tmp_name):
                        os.remove(tmp_name)

            except Exception as e:
                print(f"  ✗ Error processing {filename}: {e}")

    except Exception as e:
        print(f"✗ Error: {e}")
