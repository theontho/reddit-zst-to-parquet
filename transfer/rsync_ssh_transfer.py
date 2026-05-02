
"""Implements the TransferHandler using rsync for transfers and ssh for listing."""

import logging
import os
import subprocess
import time

from core.config import (
    MAX_RSYNC_RETRIES,
    REMOTE_DIR,
    REMOTE_HOST,
    REMOTE_USER,
    RSYNC_RETRY_DELAY_SECONDS,
    RSYNC_TIMEOUT_SECONDS,
)
from core.utils import format_size, format_speed

from .base_transfer import TransferHandler


class RsyncSshTransferHandler(TransferHandler):
    """Handles file transfers and listing using rsync and ssh."""

    def _run_rsync_command(
        self, source: str, destination: str, show_progress: bool = True, operation: str = "transfer"
    ) -> tuple[bool, bool, str, str]:
        """Runs rsync command, handling progress display and capturing output/errors."""
        rsync_cmd = [
            "rsync",
            "-av",
            "--partial",
            "--append-verify",
            f"--timeout={RSYNC_TIMEOUT_SECONDS}",
        ]
        if show_progress:
            rsync_cmd.append("--progress")
        rsync_cmd.extend([source, destination])

        logging.info(f"Running rsync command ({operation}): {' '.join(rsync_cmd)}")

        timed_out = False
        stdout_str = ""
        stderr_str = ""

        try:
            capture_output = not show_progress
            process = subprocess.run(
                rsync_cmd,
                capture_output=capture_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,  # Don't raise exception on non-zero exit
            )
            returncode = process.returncode
            if capture_output:
                stdout_str = process.stdout
                stderr_str = process.stderr
            else:
                stdout_str = "[Output shown on console]"
                stderr_str = "[Errors shown on console]"

            if returncode == 30:
                logging.warning(f"rsync command timed out (exit code 30) during {operation}.")
                timed_out = True
                if capture_output:
                    stderr_str += "\n(rsync reported I/O timeout)"

            success = returncode == 0

            if not success:
                log_func = logging.error if returncode != 30 else logging.warning
                log_func(f"rsync command failed with exit code: {returncode} during {operation}.")
                if capture_output and stderr_str.strip():
                    log_func(f"rsync stderr: {stderr_str.strip()}")
                elif capture_output and not stderr_str.strip():
                    log_func("rsync produced no stderr output.")
            elif capture_output and stderr_str.strip():
                logging.warning(
                    f"rsync command succeeded but has stderr output: {stderr_str.strip()}"
                )

            if capture_output:
                logging.debug(f"rsync stdout: {stdout_str.strip()}")

            return success, timed_out, stdout_str, stderr_str

        except FileNotFoundError:
            logging.error(
                "Error: 'rsync' command not found. Please ensure it's installed and in your PATH."
            )
            return False, False, "", "rsync command not found"
        except Exception as e:
            logging.exception(f"Error running rsync command: {e}")
            return False, False, "", str(e)

    def _run_rsync_with_retry(
        self,
        source: str,
        destination: str,
        operation: str,
        filename_for_log: str,
        local_dest_path_for_verify: str | None = None,
        expected_size: int = 0,
    ) -> tuple[bool, float]:
        """Runs rsync with retry logic, handling verification for downloads."""
        success = False
        elapsed_time = 0.0
        last_stderr = ""

        for attempt in range(MAX_RSYNC_RETRIES + 1):
            logging.info(
                f"Attempt {attempt + 1}/{MAX_RSYNC_RETRIES + 1}: {operation.capitalize()}ing {source} to {destination} using rsync..."
            )
            start_time = time.time()

            op_success, timed_out, _, stderr = self._run_rsync_command(
                source,
                destination,
                show_progress=True,  # Always show progress for user feedback
                operation=operation,
            )
            end_time = time.time()
            elapsed_time = end_time - start_time
            last_stderr = stderr.strip()  # Store last stderr

            if op_success:
                logging.info(
                    f"rsync {operation} successful for {filename_for_log} on attempt {attempt + 1}."
                )
                success = True
                break
            elif timed_out:
                logging.warning(
                    f"rsync {operation} attempt {attempt + 1} timed out for {filename_for_log}. Retrying after {RSYNC_RETRY_DELAY_SECONDS}s..."
                )
                if attempt < MAX_RSYNC_RETRIES:
                    time.sleep(RSYNC_RETRY_DELAY_SECONDS)
                else:
                    logging.error(
                        f"rsync {operation} failed for {filename_for_log} after {MAX_RSYNC_RETRIES + 1} attempts due to timeouts. Last stderr: {last_stderr}"
                    )
            else:
                logging.error(
                    f"rsync {operation} failed for {filename_for_log} on attempt {attempt + 1}. Stderr: {last_stderr}"
                )
                break

        if success and operation == "download":
            if not local_dest_path_for_verify:
                logging.error(
                    "Internal error: local_dest_path_for_verify not provided for download verification."
                )
                return False, elapsed_time

            if not os.path.exists(local_dest_path_for_verify):
                logging.error(
                    f"Downloaded file {local_dest_path_for_verify} not found after rsync reported success."
                )
                return False, elapsed_time

            if expected_size > 0:
                try:
                    local_size = os.path.getsize(local_dest_path_for_verify)
                    if local_size != expected_size:
                        error_msg = f"Downloaded file size mismatch for {filename_for_log}. Expected: {expected_size}, Got: {local_size}."
                        logging.error(error_msg)
                        try:
                            os.remove(local_dest_path_for_verify)
                        except OSError as e_rem:
                            logging.warning(
                                f"Could not remove mismatched file {local_dest_path_for_verify}: {e_rem}"
                            )
                        return False, elapsed_time
                    else:
                        logging.debug(
                            f"Downloaded size verified for {filename_for_log} ({local_size} bytes)."
                        )
                except OSError as e_size:
                    logging.error(
                        f"Could not get size of downloaded file {local_dest_path_for_verify}: {e_size}."
                    )
                    return False, elapsed_time
            else:
                logging.warning(
                    f"Skipping download size verification for {filename_for_log} as remote size is unknown or zero ({expected_size})."
                )

            if elapsed_time > 0:
                local_size_final = (
                    os.path.getsize(local_dest_path_for_verify)
                    if os.path.exists(local_dest_path_for_verify)
                    else 0
                )
                if local_size_final > 0:
                    speed = format_speed(local_size_final, elapsed_time)
                    size_str = format_size(local_size_final)
                    print(
                        f"Download verified: {size_str} in {elapsed_time:.1f}s ({speed})",
                        flush=True,
                    )
                else:
                    print(
                        f"Download completed in {elapsed_time:.1f}s (size unknown or verification failed)",
                        flush=True,
                    )
            else:
                print("Download completed (verification skipped or time zero)", flush=True)

        elif success and operation == "upload":
            if elapsed_time > 0:
                try:
                    # Source is the local path for upload
                    local_size = os.path.getsize(source)
                    speed = format_speed(local_size, elapsed_time)
                    size_str = format_size(local_size)
                    print(
                        f"Upload completed: {size_str} in {elapsed_time:.1f}s ({speed})", flush=True
                    )
                except OSError:
                    print(
                        f"Upload completed in {elapsed_time:.1f}s (local size unknown)", flush=True
                    )
            else:
                print("Upload completed (time zero)", flush=True)

        return success, elapsed_time

    def _parse_ls_output(self, ls_output: str) -> tuple[list[tuple[str, int]], set[str], set[str]]:
        """Parses the output of 'ls -l' to extract file names and sizes."""
        zst_files_with_sizes = []
        parquet_files = set()
        other_files = set()
        lines = ls_output.strip().splitlines()
        logging.debug(f"Parsing {len(lines)} lines from ls output.")
        matched_count = 0

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("total "):  # Skip headers/empty
                continue

            parts = line_stripped.split()
            if len(parts) < 9:  # Need at least 9 parts for standard ls -l
                logging.debug(f"Skipping line {i} (too few parts): {line_stripped}")
                continue

            try:
                size = int(parts[4])
                # Filename starts after date/time (usually index 8)
                # Handle filenames with spaces by joining remaining parts
                filename = " ".join(parts[8:])

                if filename.endswith(".zst"):
                    zst_files_with_sizes.append((filename, size))
                    matched_count += 1
                    logging.debug(f"  Added ZST: {filename}, Size: {size}")
                elif filename.endswith(".parquet"):
                    parquet_files.add(filename)
                    matched_count += 1
                    logging.debug(f"  Added Parquet: {filename}")
                else:
                    other_files.add(filename)
                    matched_count += 1
                    logging.debug(f"  Added Other: {filename}")
            except (ValueError, IndexError) as e:
                logging.warning(f"Could not parse line {i}: '{line_stripped}'. Error: {e}")
                continue

        logging.info(f"Finished parsing ls output. Matched {matched_count} target files.")
        return zst_files_with_sizes, parquet_files, other_files

    def list_remote_files(self) -> tuple[list[tuple[str, int]], set[str], set[str]]:
        logging.info(f"Listing remote directory {REMOTE_DIR} using SSH 'ls -l'...")
        ssh_cmd = ["ssh", f"{REMOTE_USER}@{REMOTE_HOST}", f"ls -l {REMOTE_DIR}"]

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            stdout = result.stdout
            stderr = result.stderr
            returncode = result.returncode

            logging.debug(f"Raw SSH 'ls -l' stdout:\n---\n{stdout}\n---")
            logging.debug(f"Raw SSH 'ls -l' stderr:\n---\n{stderr}\n---")

            if returncode != 0:
                if stdout:
                    logging.warning(
                        f"SSH command failed (code {returncode}) but produced stdout. Attempting to parse anyway.\nStderr: {stderr.strip()}\nStdout:{stdout[:200]}..."
                    )
                else:
                    logging.error(
                        f"Failed to list remote directory {REMOTE_DIR} (code {returncode}). Error: {stderr.strip()}"
                    )
                    return [], set(), set()
            elif stderr:
                logging.warning(f"SSH command succeeded but produced stderr: {stderr.strip()}")

            zst_files_with_sizes, parquet_files, other_files = self._parse_ls_output(stdout)
            logging.info(
                f"Found {len(zst_files_with_sizes)} .zst files, {len(parquet_files)} .parquet files, and {len(other_files)} others via SSH."
            )
            return zst_files_with_sizes, parquet_files, other_files

        except Exception as e:
            logging.error(f"Error listing remote files using SSH: {e}")
            return [], set(), set()

    def delete_file(self, remote_filename: str) -> bool:
        logging.info(f"Deleting remote file: {remote_filename} via SSH 'rm'")
        ssh_cmd = ["ssh", f"{REMOTE_USER}@{REMOTE_HOST}", f"rm {REMOTE_DIR}/{remote_filename}"]
        try:
            result = subprocess.run(ssh_cmd, check=False, capture_output=True)
            if result.returncode == 0:
                logging.info(f"Successfully deleted remote file: {remote_filename}")
                return True
            else:
                logging.error(
                    f"Failed to delete remote file {remote_filename} (code {result.returncode}): {result.stderr.decode().strip()}"
                )
                return False
        except Exception as e:
            logging.error(f"Error deleting remote file {remote_filename} via SSH: {e}")
            return False

    def download_to_string(self, remote_filename: str) -> str:
        logging.info(f"Downloading remote file to string: {remote_filename} via SSH 'cat'")
        ssh_cmd = ["ssh", f"{REMOTE_USER}@{REMOTE_HOST}", f"cat {REMOTE_DIR}/{remote_filename}"]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return result.stdout
            else:
                logging.error(
                    f"Failed to cat remote file {remote_filename} (code {result.returncode}): {result.stderr.strip()}"
                )
                return ""
        except Exception as e:
            logging.error(f"Error cat-ing remote file {remote_filename} via SSH: {e}")
            return ""

    def file_exists(self, remote_filename: str) -> bool:
        logging.debug(f"Checking if remote file exists via SSH: {remote_filename}")
        ssh_cmd = ["ssh", f"{REMOTE_USER}@{REMOTE_HOST}", f"ls {REMOTE_DIR}/{remote_filename}"]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, check=False)
            return result.returncode == 0
        except Exception:
            return False

    def download_file(
        self, remote_filename: str, local_path: str, expected_size: int
    ) -> tuple[bool, float]:
        remote_full_path = f"{REMOTE_DIR}/{remote_filename}"
        source = f"{REMOTE_USER}@{REMOTE_HOST}:{remote_full_path}"
        return self._run_rsync_with_retry(
            source,
            local_path,
            "download",
            remote_filename,
            local_dest_path_for_verify=local_path,  # Pass local path for verification
            expected_size=expected_size,
        )

    def upload_file(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        remote_full_path = f"{REMOTE_DIR}/{remote_filename}"
        destination = f"{REMOTE_USER}@{REMOTE_HOST}:{remote_full_path}"
        return self._run_rsync_with_retry(
            local_path,
            destination,
            "upload",
            os.path.basename(local_path),  # Use local filename for log context
        )

    def check_prerequisites(self) -> bool:
        logging.info("Checking prerequisite commands (rsync, ssh)...")
        rsync_ok = False
        ssh_ok = False
        try:
            rsync_check = subprocess.run(
                ["rsync", "--version"], capture_output=True, text=True, check=False
            )
            if rsync_check.returncode == 0:
                logging.info("rsync found.")
                rsync_ok = True
            else:
                logging.error(
                    "Prerequisite check failed: The 'rsync' command was not found or returned an error."
                )
        except FileNotFoundError:
            logging.error("Prerequisite check failed: 'rsync' command not found in PATH.")

        try:
            ssh_check = subprocess.run(["ssh", "-V"], capture_output=True, text=True, check=False)
            if ssh_check.returncode == 0:
                logging.info("ssh found.")
                ssh_ok = True
            else:
                logging.error(
                    f"Prerequisite check failed: The 'ssh' command returned an error. Stderr: {ssh_check.stderr.strip()}"
                )
        except FileNotFoundError:
            logging.error("Prerequisite check failed: 'ssh' command not found in PATH.")

        return rsync_ok and ssh_ok

    def check_connection(self) -> bool:
        logging.info("Checking SSH connectivity...")
        try:
            ssh_connect_check = subprocess.run(
                [
                    "ssh",
                    f"{REMOTE_USER}@{REMOTE_HOST}",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "echo 'SSH connection test successful'",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,  # Add overall timeout
            )
            if ssh_connect_check.returncode == 0:
                logging.info("SSH connection test successful.")
                return True
            else:
                logging.error(
                    f"SSH connection test failed (exit code {ssh_connect_check.returncode}). Please check SSH setup (keys, agent, host config). Error: {ssh_connect_check.stderr.strip()}"
                )
                return False
        except subprocess.TimeoutExpired:
            logging.error("SSH connection test timed out after 15 seconds.")
            return False
        except FileNotFoundError:
            logging.error(
                "SSH connection test failed: 'ssh' command not found."
            )  # Should be caught by prerequisites but check again
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred during SSH connection test: {e}")
            logging.exception("Traceback:")
            return False
