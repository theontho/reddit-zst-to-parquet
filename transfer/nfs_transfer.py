import logging
import os
import shutil
import time
from pathlib import Path

import humanize

from core import config  # Import configuration
from core.utils import format_size  # Import utility for size formatting

from .base_transfer import TransferHandler

# Use logger configured in main.py
logger = logging.getLogger(__name__)


class NfsTransferHandler(TransferHandler):
    """Handles file transfers using a local NFS mount point."""

    def __init__(self):
        self.nfs_base_path = Path(config.NFS_MOUNT_PATH).expanduser().resolve()
        self.remote_base_dir = self.nfs_base_path / config.REMOTE_DIR
        logger.info(
            f"NFS Transfer Handler initialized. Base path: {self.nfs_base_path}, Remote dir: {self.remote_base_dir}"
        )

    def _resolve_remote_path(self, relative_path):
        """Resolves the absolute path within the configured remote NFS directory."""
        # Clean the relative path
        clean_relative_path = Path(os.path.normpath(relative_path).lstrip("/"))
        absolute_path = (self.remote_base_dir / clean_relative_path).resolve()

        # Security check: ensure the resolved path is within the intended remote directory
        try:
            absolute_path.relative_to(self.remote_base_dir.resolve())
        except ValueError:
            raise ValueError(
                f"Invalid path: '{relative_path}' attempts to access outside the "
                f"configured remote directory '{self.remote_base_dir}'."
            ) from None
        return absolute_path

    def _display_speed(self, start_time, end_time, file_size_bytes):
        """Calculates and logs transfer speed."""
        duration = end_time - start_time
        if duration == 0:
            speed_str = "instant"
        else:
            speed_bps = file_size_bytes / duration
            speed_str = f"{humanize.naturalsize(speed_bps, binary=True)}/s"
        logger.info(f"Transfer speed: {speed_str}")

    def check_prerequisites(self):
        """Checks if the NFS base mount path exists."""
        if not self.nfs_base_path.exists():
            logger.error(f"NFS base mount path '{self.nfs_base_path}' does not exist.")
            return False
        logger.info(f"NFS base mount path '{self.nfs_base_path}' found.")
        return True

    def check_connection(self):
        """Checks if the NFS path is mounted and the remote dir exists."""
        mount_point = self.nfs_base_path
        remote_dir_within_mount = self.remote_base_dir

        # 1. Check if the base path is actually mounted
        is_mounted = False
        try:
            # is_mount() can be unreliable (e.g., symlinks), double check
            if mount_point.is_mount():
                is_mounted = True
            else:
                # Fallback check: compare device ID of the path and its parent
                # If different, it's likely a mount point
                if (
                    mount_point.exists()
                    and mount_point.parent.exists()
                    and mount_point.stat().st_dev != mount_point.parent.stat().st_dev
                ):
                    is_mounted = True
                    logger.info(f"Path '{mount_point}' detected as mount point via device ID check.")

        except OSError as e:
            logger.warning(f"Error checking mount status for {mount_point}: {e}. Assuming not mounted.")
            is_mounted = False

        if not is_mounted:
            logger.error(f"NFS mount point '{mount_point}' is not mounted or not detected.")
            logger.error("Please mount the NFS share manually before running the script.")
            # Construct the suggested command based on config
            # (might need adjustment based on specific NFS server setup)
            nfs_server_path = f"{config.REMOTE_HOST}:/Public/download"
            suggested_command = f"sudo mount -t nfs -o resvport,rsize=65536,wsize=65536 {nfs_server_path} {mount_point}"
            logger.error(f"Suggested command: {suggested_command}")
            return False

        logger.info(f"NFS mount point '{mount_point}' appears to be mounted.")

        # 2. Check if the configured remote directory exists within the mount
        if not remote_dir_within_mount.exists():
            logger.error(
                f"Configured remote directory '{remote_dir_within_mount}' does not exist "
                f"within the NFS mount '{mount_point}'. Please check config.REMOTE_DIR "
                "and the NFS export."
            )
            return False
        if not remote_dir_within_mount.is_dir():
            logger.error(
                f"Configured remote path '{remote_dir_within_mount}' is not a directory. "
                "Please check config.REMOTE_DIR."
            )
            return False

        logger.info(
            f"NFS connection check passed: Mount point '{mount_point}' and "
            f"remote directory '{remote_dir_within_mount}' are accessible."
        )
        return True

    def list_remote_files(self) -> tuple[list[tuple[str, int]], set[str], set[str]]:
        """Lists .zst, .parquet, and other files in the remote NFS directory."""
        zst_files_with_sizes = []
        parquet_files = set()
        other_files = set()
        try:
            logger.info(f"Scanning remote NFS directory for files: {self.remote_base_dir}")
            for item in self.remote_base_dir.iterdir():
                if item.is_file():
                    if item.name.endswith(".zst"):
                        try:
                            size = item.stat().st_size
                            zst_files_with_sizes.append((item.name, size))
                        except OSError as e:
                            logger.warning(f"Could not get size for {item.name}: {e}")
                            zst_files_with_sizes.append((item.name, -1))  # Indicate size unknown
                    elif item.name.endswith(".parquet"):
                        parquet_files.add(item.name)
                    else:
                        other_files.add(item.name)
            logger.info(
                f"Found {len(zst_files_with_sizes)} .zst files, "
                f"{len(parquet_files)} .parquet files, and "
                f"{len(other_files)} others."
            )
            return zst_files_with_sizes, parquet_files, other_files
        except OSError as e:
            logger.error(f"Error listing files in {self.remote_base_dir}: {e}")
            raise  # Re-raise the exception to be handled by the main script

    def download_file(self, remote_filename: str, local_path: str, expected_size: int = 0) -> tuple[bool, float]:
        """Downloads a file from the NFS mount to the local system."""
        nfs_source_path = self._resolve_remote_path(remote_filename)
        local_dest = Path(local_path)

        if not nfs_source_path.exists():
            logger.error(f"NFS source file not found: {nfs_source_path}")
            return False, 0.0
        if not nfs_source_path.is_file():
            logger.error(f"NFS source is not a file: {nfs_source_path}")
            return False, 0.0

        try:
            # Ensure the local destination directory exists
            local_dest.parent.mkdir(parents=True, exist_ok=True)

            file_size = nfs_source_path.stat().st_size
            size_str = format_size(file_size)
            logger.info(f"Downloading from NFS '{remote_filename}' ({size_str}) to '{local_path}'...")

            start_time = time.monotonic()
            shutil.copy2(nfs_source_path, local_dest)  # copy2 preserves metadata
            end_time = time.monotonic()

            logger.info(f"Download complete for '{remote_filename}'.")
            self._display_speed(start_time, end_time, file_size)
            return True, end_time - start_time

        except Exception as e:
            logger.error(f"Error during NFS download of '{remote_filename}': {e}", exc_info=True)
            return False, 0.0

    def upload_file(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        """Uploads a file from the local system to the NFS mount."""
        local_source = Path(local_path)
        nfs_dest_path = self._resolve_remote_path(remote_filename)

        if not local_source.exists():
            logger.error(f"Local source file not found: {local_path}")
            return False, 0.0
        if not local_source.is_file():
            logger.error(f"Local source is not a file: {local_path}")
            return False, 0.0

        try:
            # Ensure the destination directory exists on NFS
            nfs_dest_path.parent.mkdir(parents=True, exist_ok=True)

            file_size = local_source.stat().st_size
            size_str = format_size(file_size)
            logger.info(f"Uploading '{local_path}' ({size_str}) to NFS '{remote_filename}'...")

            start_time = time.monotonic()
            shutil.copy2(local_source, nfs_dest_path)  # copy2 preserves metadata
            end_time = time.monotonic()

            logger.info(f"Upload complete for '{local_path}'.")
            self._display_speed(start_time, end_time, file_size)
            return True, end_time - start_time

        except Exception as e:
            logger.error(f"Error during NFS upload of '{local_path}': {e}", exc_info=True)
            return False, 0.0

    def try_create_claim(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        """Creates a claim on NFS using exclusive creation."""
        local_source = Path(local_path)
        nfs_dest_path = self._resolve_remote_path(remote_filename)
        start_time = time.monotonic()
        try:
            content = local_source.read_bytes()
            nfs_dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(nfs_dest_path, "xb") as f:
                f.write(content)
            return True, time.monotonic() - start_time
        except FileExistsError:
            return False, time.monotonic() - start_time
        except Exception as e:
            logger.error(f"Error creating NFS claim '{remote_filename}': {e}", exc_info=True)
            return False, time.monotonic() - start_time

    def delete_file(self, remote_filename: str) -> bool:
        """Deletes a file on the NFS mount."""
        nfs_file_path = self._resolve_remote_path(remote_filename)

        try:
            if not nfs_file_path.exists():
                logger.warning(f"Attempted to delete non-existent file on NFS: {nfs_file_path}")
                return True  # Treat as success if already gone
            if not nfs_file_path.is_file():
                logger.error(f"Attempted to delete non-file path on NFS: {nfs_file_path}")
                return False

            logger.info(f"Deleting remote file on NFS: '{remote_filename}' ({nfs_file_path})")
            nfs_file_path.unlink()
            logger.info(f"Successfully deleted '{remote_filename}'.")
            return True
        except OSError as e:
            logger.error(f"Error deleting file '{remote_filename}' on NFS: {e}")
            return False

    def download_to_string(self, remote_filename: str) -> str:
        """Downloads a remote file and returns its content as a string."""
        nfs_file_path = self._resolve_remote_path(remote_filename)
        try:
            if not nfs_file_path.exists():
                return ""
            with open(nfs_file_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading NFS file {remote_filename}: {e}")
            return ""

    def file_exists(self, remote_filename: str) -> bool:
        """Checks if a file exists on the remote NFS mount."""
        try:
            nfs_file_path = self._resolve_remote_path(remote_filename)
            return bool(nfs_file_path.exists())
        except Exception:
            return False
