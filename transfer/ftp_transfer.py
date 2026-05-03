"""Implements the TransferHandler using FTP."""

import contextlib
import io
import logging
import os
import sys  # Add sys import for stdout manipulation
import time
from ftplib import FTP, FTP_TLS, error_perm

from core.config import (
    # FTP doesn't have built-in retries/timeouts like rsync args, need manual implementation
    FTP_HOST,
    FTP_PASSIVE_MODE,
    FTP_PASSWORD,
    FTP_PORT,
    FTP_TIMEOUT_SECONDS,
    FTP_USER,
    # Need to add FTP specific config: password, port, passive mode?
    REMOTE_DIR,
    USE_FTPS,
)
from core.utils import format_size, format_speed

from .base_transfer import TransferHandler

# TODO: Add FTP specific configuration to config.py (e.g., FTP_PORT, FTP_PASSIVE, FTP_PASSWORD/use_netrc)
# TODO: Implement retry logic for FTP operations.
# TODO: Implement robust error handling for FTP commands.
# TODO: Consider using TLS/SSL for secure FTP (FTPS).
# TODO: Consider using netrc for password handling.


# Helper class for progress reporting
class FtpProgressReporter:
    """Prints transfer progress updates to the console."""

    def __init__(self, total_size=0, description="Transferring"):
        self.start_time = time.time()
        self.last_print_time = 0.0
        self.bytes_transferred = 0
        self.total_size = total_size  # Expected total size (bytes)
        self.description = description
        self.print_interval = 0.5  # Print progress update frequency (seconds)
        self._last_bytes = 0
        self._last_time = time.time()
        self._last_speed = 0.0

    def callback(self, block):
        """Callback function for ftplib transfer methods."""
        self.bytes_transferred += len(block)
        self._print_progress()

    def _print_progress(self):
        """Prints the progress if enough time has passed."""
        current_time = time.time()
        # Avoid printing too frequently
        if current_time - self.last_print_time >= self.print_interval or (
            self.total_size > 0 and self.bytes_transferred == self.total_size
        ):
            elapsed_total = current_time - self.start_time
            interval_time = current_time - self._last_time

            if interval_time > 0:
                # Calculate current speed over the last interval
                interval_bytes = self.bytes_transferred - self._last_bytes
                current_speed = interval_bytes / interval_time
                self._last_speed = current_speed
            else:
                current_speed = self._last_speed

            format_speed(self.bytes_transferred, elapsed_total)  # Total avg for overall ETR
            cur_speed_str = format_size(current_speed) + "/s"

            transferred_str = format_size(self.bytes_transferred)
            etr_str = "--:--:--"

            if self.total_size > 0:
                total_str = format_size(self.total_size)
                percentage = (self.bytes_transferred / self.total_size) * 100
                avg_speed = self.bytes_transferred / elapsed_total if elapsed_total > 0 else 0
                if avg_speed > 0 and self.bytes_transferred < self.total_size:
                    remaining_bytes = self.total_size - self.bytes_transferred
                    remaining_time = remaining_bytes / avg_speed
                    etr_str = self._format_time(remaining_time)
                elif self.bytes_transferred == self.total_size:
                    etr_str = "00:00:00"

                sys.stdout.write(
                    f"\r{self.description}: {transferred_str} / {total_str} ({percentage:.1f}%) at {cur_speed_str}, ETR: {etr_str}... "
                )
                sys.stdout.flush()
            else:
                sys.stdout.write(f"\r{self.description}: {transferred_str} at {cur_speed_str}... ")
                sys.stdout.flush()

            self.last_print_time = current_time
            self._last_bytes = self.bytes_transferred
            self._last_time = current_time

    def _format_time(self, seconds: float) -> str:
        """Formats seconds into HH:MM:SS string."""
        if seconds < 0 or not isinstance(seconds, (int, float)):
            return "--:--:--"
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def finish(self):
        """Prints the final summary message."""
        # Ensure the last update is printed if the loop finished quickly
        self._print_progress()

        elapsed_time = time.time() - self.start_time
        speed = format_speed(self.bytes_transferred, elapsed_time) if elapsed_time > 0 else "?? B/s"
        transferred_str = format_size(self.bytes_transferred)

        # Clear the progress line before printing the final message
        terminal_width = 80  # Assume a reasonable default width
        with contextlib.suppress(OSError):
            terminal_width = os.get_terminal_size().columns
        sys.stdout.write(f"\r{' ' * terminal_width}\r")  # Clear the line
        sys.stdout.flush()

        if self.total_size > 0 and self.bytes_transferred == self.total_size:
            total_str = format_size(self.total_size)
            print(
                f"{self.description} complete: {total_str} in {elapsed_time:.1f}s ({speed})",
                flush=True,
            )
        elif self.total_size > 0:  # Size mismatch or incomplete
            total_str = format_size(self.total_size)
            print(
                f"{self.description} finished: {transferred_str} / {total_str} in {elapsed_time:.1f}s ({speed}) (Size verification may follow)",
                flush=True,
            )
        else:  # Total size unknown
            print(
                f"{self.description} complete: {transferred_str} in {elapsed_time:.1f}s ({speed})",
                flush=True,
            )


# End Helper class


class FtpTransferHandler(TransferHandler):
    """Handles file transfers and listing using standard FTP."""

    def __init__(self):
        self._ftp: FTP | FTP_TLS | None = None

    def _get_ftp(self) -> FTP | FTP_TLS:
        """Returns the current connection or creates a new one if needed."""
        if self._ftp is not None:
            try:
                self._ftp.voidcmd("NOOP")
                return self._ftp
            except Exception:
                logging.debug("Cached FTP connection is dead, reconnecting...")
                self._disconnect(self._ftp)
                self._ftp = None

        self._ftp = self._connect()
        return self._ftp

    def _connect(self) -> FTP | FTP_TLS:
        """Establishes an FTP or FTPS connection with retries."""
        max_retries = 3
        last_exception = None

        for attempt in range(max_retries):
            try:
                conn_type = "FTPS" if USE_FTPS else "FTP"
                logging.debug(
                    f"Connecting to {FTP_HOST}:{FTP_PORT} ({conn_type}) (attempt {attempt + 1}/{max_retries})..."
                )

                ftp = FTP_TLS() if USE_FTPS else FTP()

                ftp.connect(FTP_HOST, FTP_PORT, timeout=FTP_TIMEOUT_SECONDS)
                ftp.login(FTP_USER, FTP_PASSWORD)

                if USE_FTPS and hasattr(ftp, "prot_p"):
                    ftp.prot_p()

                ftp.set_pasv(FTP_PASSIVE_MODE)

                try:
                    ftp.cwd(REMOTE_DIR)
                except error_perm as e_cwd:
                    logging.error(f"FTP error changing directory to {REMOTE_DIR}: {e_cwd}")
                    with contextlib.suppress(Exception):
                        ftp.quit()
                    raise

                return ftp
            except Exception as e:
                last_exception = e
                logging.warning(f"FTP connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)

        raise last_exception or Exception("Failed to connect to FTP after multiple attempts")

    def _disconnect(self, ftp: FTP | FTP_TLS | None):
        """Closes the FTP/FTPS connection."""
        if ftp:
            try:
                ftp.quit()
                logging.debug("FTP connection closed.")
            except Exception as e:
                logging.warning(f"Error closing FTP connection ({type(e).__name__}): {e}")
                logging.debug("Traceback for FTP disconnect error:", exc_info=True)

    def close(self):
        """Public method to close the persistent connection."""
        if self._ftp:
            self._disconnect(self._ftp)
            self._ftp = None

    def _parse_ftp_list_output(
        self, list_output: list[str]
    ) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[tuple[str, int]]]:
        """Parses the output of FTP LIST command (potentially unreliable format)."""
        zst_files_with_sizes = []
        parquet_files_with_sizes = []
        other_files_with_sizes = []

        for line in list_output:
            try:
                parts = line.split()
                if len(parts) < 9:
                    continue

                size = int(parts[4])
                filename = " ".join(parts[8:])

                if filename.endswith(".zst"):
                    zst_files_with_sizes.append((filename, size))
                elif filename.endswith(".parquet"):
                    parquet_files_with_sizes.append((filename, size))
                else:
                    other_files_with_sizes.append((filename, size))

            except (ValueError, IndexError):
                continue

        logging.info(
            f"FTP LIST parsing complete. Found {len(zst_files_with_sizes)} ZST, {len(parquet_files_with_sizes)} Parquet, {len(other_files_with_sizes)} others."
        )
        return zst_files_with_sizes, parquet_files_with_sizes, other_files_with_sizes

    def list_remote_files(self) -> tuple[list[tuple[str, int]], set[str], set[str]]:
        logging.info(f"Listing remote directory {REMOTE_DIR} using FTP LIST...")
        try:
            ftp = self._get_ftp()
            try:
                zst_files_with_sizes = []
                parquet_files = set()
                other_files = set()
                for name, facts in ftp.mlsd():
                    if facts.get("type") != "file":
                        continue
                    size = int(facts.get("size", "0"))
                    if name.endswith(".zst"):
                        zst_files_with_sizes.append((name, size))
                    elif name.endswith(".parquet"):
                        parquet_files.add(name)
                    else:
                        other_files.add(name)
                return zst_files_with_sizes, parquet_files, other_files
            except Exception:
                logging.debug("FTP MLSD unavailable; falling back to LIST.")

            lines: list[str] = []
            ftp.retrlines("LIST", lines.append)  # Use LIST if MLSD fails/not implemented
            zst, parquet, other = self._parse_ftp_list_output(lines)
            return zst, {p[0] for p in parquet}, {o[0] for o in other}

        except Exception as e:
            logging.error(f"Error listing remote files using FTP: {e}")
            # If we fail, try closing connection to reset state for next call
            self.close()
            return [], set(), set()

    def list_remote_files_with_all_sizes(
        self,
    ) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[tuple[str, int]]]:
        """Returns lists of (filename, size) for ZST, Parquet, and other files."""
        logging.info(f"Auditing remote directory {REMOTE_DIR} (all sizes) using FTP...")
        try:
            ftp = self._get_ftp()
            try:
                zst_files_with_sizes = []
                parquet_files_with_sizes = []
                other_files_with_sizes = []
                for name, facts in ftp.mlsd():
                    if facts.get("type") != "file":
                        continue
                    size = int(facts.get("size", "0"))
                    if name.endswith(".zst"):
                        zst_files_with_sizes.append((name, size))
                    elif name.endswith(".parquet"):
                        parquet_files_with_sizes.append((name, size))
                    else:
                        other_files_with_sizes.append((name, size))
                return zst_files_with_sizes, parquet_files_with_sizes, other_files_with_sizes
            except Exception:
                logging.debug("FTP MLSD unavailable for size audit; falling back to LIST.")

            lines: list[str] = []
            ftp.retrlines("LIST", lines.append)
            return self._parse_ftp_list_output(lines)
        except Exception as e:
            logging.error(f"Error auditing remote files with sizes: {e}")
            self.close()
            return [], [], []

    def delete_file(self, remote_filename: str) -> bool:
        logging.debug(f"Deleting remote file: {remote_filename} via FTP")
        try:
            ftp = self._get_ftp()
            ftp.delete(remote_filename)
            if remote_filename.endswith(".claim.json"):
                with contextlib.suppress(Exception):
                    ftp.rmd(f"{remote_filename}.lock")
            logging.debug(f"Successfully deleted remote file: {remote_filename}")
            return True
        except Exception as e:
            logging.error(f"Error deleting remote file {remote_filename} via FTP: {e}")
            # Don't close here, deletion might fail for other reasons (file missing)
            return False

    def download_to_string(self, remote_filename: str) -> str:
        logging.debug(f"Downloading remote file to string: {remote_filename} via FTP")
        try:
            ftp = self._get_ftp()
            bio = io.BytesIO()
            ftp.retrbinary(f"RETR {remote_filename}", bio.write)
            bytes_content = bio.getvalue()
            try:
                return bytes_content.decode("utf-8")
            except UnicodeDecodeError:
                # Fallback to latin-1 which handles all byte sequences
                return bytes_content.decode("latin-1")
        except Exception as e:
            logging.error(f"Error downloading remote file {remote_filename} to string: {e}")
            return ""

    def file_exists(self, remote_filename: str) -> bool:
        logging.debug(f"Checking if remote file exists via FTP: {remote_filename}")
        try:
            ftp = self._get_ftp()
            # size() is usually supported and returns size if file exists,
            # otherwise raises an error (usually 550)
            ftp.size(remote_filename)
            return True
        except Exception:
            # Fallback to nlst if size fails
            try:
                ftp = self._get_ftp()
                files = ftp.nlst(remote_filename)
                return remote_filename in files or any(f.endswith(remote_filename) for f in files)
            except Exception:
                return False

    def download_file(self, remote_filename: str, local_path: str, expected_size: int) -> tuple[bool, float]:
        logging.debug(f"Downloading {remote_filename} via FTP to {local_path}...")
        start_time = time.time()
        try:
            ftp = self._get_ftp()
            # Ensure local directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            progress_reporter = FtpProgressReporter(expected_size, description=f"Downloading {remote_filename}")
            with open(local_path, "wb") as fp:
                # Combine file writing and progress reporting in the callback
                ftp.retrbinary(
                    f"RETR {remote_filename}",
                    lambda block: [fp.write(block), progress_reporter.callback(block)],
                )

            elapsed_time = time.time() - start_time
            # Print final summary using the reporter
            progress_reporter.finish()

            # --- Basic Size Verification ---
            if os.path.exists(local_path):
                local_size = os.path.getsize(local_path)
                if expected_size > 0 and local_size != expected_size:
                    logging.error(
                        f"FTP downloaded file size mismatch for {remote_filename}. Expected: {expected_size}, Got: {local_size}."
                    )
                    return False, elapsed_time
                return True, elapsed_time
            else:
                logging.error(f"Downloaded file {local_path} not found after FTP download command.")
                return False, elapsed_time

        except Exception as e:
            elapsed_time = time.time() - start_time
            if os.path.exists(local_path):
                local_size = os.path.getsize(local_path)
                if expected_size > 0 and local_size == expected_size:
                    logging.warning(
                        f"FTP download encountered an error ({e}), but file size matches expected ({local_size}). Accepting as success."
                    )
                    return True, elapsed_time

            logging.exception(f"Error downloading {remote_filename} via FTP ({type(e).__name__}): {e}")
            if os.path.exists(local_path):
                with contextlib.suppress(OSError):
                    os.remove(local_path)
            # Close connection on transfer error to be safe
            self.close()
            return False, elapsed_time

    def upload_file(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        logging.debug(f"Uploading {local_path} via FTP to {remote_filename}...")
        start_time = time.time()
        try:
            local_size = os.path.getsize(local_path)
            ftp = self._get_ftp()

            # Increase timeout for the transfer if possible
            old_timeout = None
            if ftp.sock:
                old_timeout = ftp.sock.gettimeout()
                ftp.sock.settimeout(3600)

            progress_reporter = FtpProgressReporter(local_size, description=f"Uploading {os.path.basename(local_path)}")

            with open(local_path, "rb") as fp:
                ftp.storbinary(f"STOR {remote_filename}", fp, callback=progress_reporter.callback)

            if ftp.sock and old_timeout is not None:
                ftp.sock.settimeout(old_timeout)

            elapsed_time = time.time() - start_time
            progress_reporter.finish()

            try:
                remote_size = ftp.size(remote_filename)
                if remote_size is not None and remote_size != local_size:
                    logging.error(
                        f"FTP uploaded file size mismatch for {remote_filename}. Expected: {local_size}, Got: {remote_size}."
                    )
                    with contextlib.suppress(Exception):
                        ftp.delete(remote_filename)
                    return False, elapsed_time
            except Exception as e:
                logging.warning(f"Could not verify uploaded FTP file size for {remote_filename}: {e}")
            return True, elapsed_time

        except Exception as e:
            elapsed_time = time.time() - start_time
            logging.error(f"Error uploading {local_path} via FTP: {e}")
            with contextlib.suppress(Exception):
                self._get_ftp().delete(remote_filename)
            self.close()  # Close on upload error
            return False, elapsed_time

    def try_create_claim(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        """Creates an FTP claim if absent.

        Uses a sidecar lock directory because FTP has no portable atomic named
        file-create primitive. MKD is atomic on standard FTP servers.
        """
        start_time = time.time()
        lock_name = f"{remote_filename}.lock"
        try:
            ftp = self._get_ftp()
            if self.file_exists(remote_filename):
                return False, time.time() - start_time
            try:
                ftp.mkd(lock_name)
            except Exception:
                return False, time.time() - start_time

            success, _ = self.upload_file(local_path, remote_filename)
            if not success:
                with contextlib.suppress(Exception):
                    ftp.rmd(lock_name)
                return False, time.time() - start_time
            with contextlib.suppress(Exception):
                ftp.rmd(lock_name)
            return True, time.time() - start_time
        except Exception as e:
            logging.error(f"Error creating FTP claim {remote_filename}: {e}")
            self.close()
            return False, time.time() - start_time

    def check_prerequisites(self) -> bool:
        # ftplib is part of Python's standard library, so no external commands to check.
        logging.info("FTP prerequisites check: ftplib is part of standard library (assumed available).")
        return True  # Always true unless targeting very old/minimal Python env

    def check_connection(self) -> bool:
        logging.info("Checking FTP connectivity...")
        ftp = None
        try:
            ftp = self._connect()
            # Simple check: can we get the welcome message or list root?
            ftp.voidcmd("NOOP")  # Send a NOOP command
            logging.info("FTP connection test successful (NOOP command OK).")
            return True
        except NotImplementedError:
            logging.error("FTP connection check cannot proceed: connection logic not implemented.")
            return False
        except error_perm as e:
            logging.error(f"FTP connection test failed (permission error): {e}")
            return False
        except Exception as e:
            logging.error(f"FTP connection test failed: {e}")
            return False
        finally:
            self._disconnect(ftp)
