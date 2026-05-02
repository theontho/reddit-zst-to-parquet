import logging
import os
import shutil
import time
from pathlib import Path

import humanize

from core import config

from .base_transfer import TransferHandler

logger = logging.getLogger(__name__)


class LocalTransferHandler(TransferHandler):
    """Handles file transfers using local file system paths (standalone mode)."""

    def __init__(self):
        # In standalone mode, REMOTE_DIR is just a local path.
        # We'll treat it as absolute if it starts with /, otherwise relative to SCRIPT_DIR or CWD.
        self.base_dir = Path(config.REMOTE_DIR).expanduser().resolve()
        logger.info(f"Local Transfer Handler initialized. Source directory: {self.base_dir}")

    def _resolve_path(self, relative_path: str) -> Path:
        """Resolves the absolute path within the configured base directory."""
        clean_relative_path = Path(os.path.normpath(relative_path).lstrip("/"))
        absolute_path = (self.base_dir / clean_relative_path).resolve()

        # Security check: ensure the resolved path is within the intended directory
        if not str(absolute_path).startswith(str(self.base_dir.resolve())):
            raise ValueError(
                f"Invalid path: '{relative_path}' attempts to access outside the "
                f"configured directory '{self.base_dir}'."
            )
        return absolute_path

    def _display_speed(self, start_time: float, end_time: float, file_size_bytes: int) -> None:
        """Calculates and logs transfer speed."""
        duration = end_time - start_time
        if duration == 0:
            speed_str = "instant"
        else:
            speed_bps = file_size_bytes / duration
            speed_str = f"{humanize.naturalsize(speed_bps, binary=True)}/s"
        logger.info(f"Transfer speed: {speed_str}")

    def check_prerequisites(self):
        """Checks if the local tools are available. For local, nothing extra needed."""
        return True

    def check_connection(self):
        """Checks if the configured directory exists."""
        if not self.base_dir.exists():
            logger.error(f"Configured source directory '{self.base_dir}' does not exist.")
            return False
        if not self.base_dir.is_dir():
            logger.error(f"Configured source path '{self.base_dir}' is not a directory.")
            return False
        return True

    def list_remote_files(self) -> tuple[list[tuple[str, int]], set[str], set[str]]:
        """Lists files in the local source directory."""
        zst_files_with_sizes = []
        parquet_files = set()
        other_files = set()
        try:
            logger.info(f"Scanning local directory for files: {self.base_dir}")
            for item in self.base_dir.iterdir():
                if item.is_file():
                    if item.name.endswith(".zst"):
                        try:
                            size = item.stat().st_size
                            zst_files_with_sizes.append((item.name, size))
                        except OSError as e:
                            logger.warning(f"Could not get size for {item.name}: {e}")
                            zst_files_with_sizes.append((item.name, -1))
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
            logger.error(f"Error listing files in {self.base_dir}: {e}")
            raise

    def download_file(self, remote_filename: str, local_path: str, expected_size: int = 0) -> tuple[bool, float]:
        """Creates a symlink from the source directory to the temp processing path."""
        source_path = self._resolve_path(remote_filename)
        dest_path = Path(local_path)

        if not source_path.exists():
            logger.error(f"Source file not found: {source_path}")
            return False, 0.0

        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # If a file/link already exists at the destination, remove it
            if dest_path.exists() or dest_path.is_symlink():
                dest_path.unlink()

            logger.info(f"Symlinking '{remote_filename}' -> '{local_path}'")
            start_time = time.monotonic()
            # Use symlink to avoid IO cost.
            dest_path.symlink_to(source_path)
            end_time = time.monotonic()

            logger.info("Symlink created (instant).")
            return True, end_time - start_time
        except Exception as e:
            logger.error(f"Error creating symlink for '{remote_filename}': {e}")
            # Fallback to copy if symlinking fails (e.g. cross-device or permission issues)
            try:
                logger.info(f"Falling back to copy for '{remote_filename}'...")
                start_time = time.monotonic()
                shutil.copy2(source_path, dest_path)
                end_time = time.monotonic()
                file_size = source_path.stat().st_size
                self._display_speed(start_time, end_time, file_size)
                return True, end_time - start_time
            except Exception as copy_e:
                logger.error(f"Fallback copy failed: {copy_e}")
                return False, 0.0

    def upload_file(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        """Copies the finished parquet file back to the source directory."""
        local_source = Path(local_path)
        dest_path = self._resolve_path(remote_filename)

        if not local_source.exists():
            logger.error(f"Local file not found: {local_path}")
            return False, 0.0

        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            file_size = local_source.stat().st_size

            logger.info(f"Moving '{local_path}' to final path '{dest_path}'...")
            start_time = time.monotonic()
            # Use move since it's the final step
            shutil.move(str(local_source), str(dest_path))
            end_time = time.monotonic()

            self._display_speed(start_time, end_time, file_size)
            return True, end_time - start_time
        except Exception as e:
            logger.error(f"Error moving '{local_path}': {e}")
            return False, 0.0

    def delete_file(self, remote_filename: str) -> bool:
        """Deletes a file in the source directory."""
        file_path = self._resolve_path(remote_filename)
        try:
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            logger.error(f"Error deleting '{remote_filename}': {e}")
            return False

    def download_to_string(self, remote_filename: str) -> str:
        """Reads a local file into a string."""
        file_path = self._resolve_path(remote_filename)
        try:
            if not file_path.exists():
                return ""
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error(f"Error reading '{remote_filename}': {e}")
            return ""

    def file_exists(self, remote_filename: str) -> bool:
        """Checks if a file exists locally."""
        try:
            return self._resolve_path(remote_filename).exists()
        except Exception:
            return False
