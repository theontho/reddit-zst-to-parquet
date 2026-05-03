"""Handles the file processing logic, including status tracking and workflow steps."""

import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core import config, logger  # Use logger module for log functions
from core.converter import convert_to_parquet
from core.utils import cleanup_local_temp, update_terminal_title
from transfer.base_transfer import TransferHandler


def initialize_log_entries(log_data: dict, remote_zst_files: set[str]) -> bool:
    """Adds entries for new .zst files found remotely with 'pending' status."""
    if "files" not in log_data:
        log_data["files"] = {}
    changed = False
    log_files = log_data["files"]  # Get reference to the files dict
    for filename in remote_zst_files:
        if filename not in log_files:
            # Use logger functions directly
            logger.update_log_entry(log_data, filename, "pending")
            changed = True
    return changed


def get_files_to_process(
    log_data: dict,
    remote_zst_files_with_sizes: list[tuple[str, int]],
    remote_parquet_files: set[str],
    remote_other_files: set[str],
    transfer_handler: TransferHandler,
    machine_meta: dict,
) -> list[tuple[str, int]]:
    """Identifies files needing processing based on log status, remote existence, and claims.

    Returns:
        List of (zst_filename, remote_size) tuples for files to process.
    """
    files_to_process = []
    skipped_conversions = []
    log_files = log_data.get("files", {})
    remote_zst_filenames = {fname for fname, _ in remote_zst_files_with_sizes}
    remote_zst_size_map = dict(remote_zst_files_with_sizes)

    process_states = {
        "pending",
        "downloading",
        "download_failed",
        "downloaded",
        "converting",
        "uploading",
        "upload_failed",
        "converted",
        # Removed "conversion_failed" - requires manual intervention/reset
    }

    skipped_remote_missing = []
    skipped_parquet_exists: dict[str, list[str]] = {}
    skipped_claimed_by_others = []

    for filename, entry in log_files.items():
        status = entry.get("status")

        # Skip if file no longer exists remotely (unless it failed previously in a recoverable way)
        recoverable_failure_states = {"download_failed", "upload_failed"}
        if filename not in remote_zst_filenames and status not in recoverable_failure_states:
            skipped_remote_missing.append(filename)
            continue

        # Skip if completed
        if status == "completed":
            continue

        # Check for claims
        claim_filename = Path(filename).stem + ".claim.json"
        if claim_filename in remote_other_files:
            # Download and inspect claim
            claim_str = transfer_handler.download_to_string(claim_filename)
            if claim_str:
                try:
                    claim_data = json.loads(claim_str)
                    claim_machine = claim_data.get("machine_meta", {})
                    claim_time_str = claim_data.get("started_at")

                    # 1. Is it our own claim?
                    is_own_claim = claim_machine.get("machine") == machine_meta.get("machine") and claim_machine.get(
                        "ip"
                    ) == machine_meta.get("ip")

                    if is_own_claim:
                        logging.info(f"Found our own previous claim for {filename}. Proceeding.")
                    else:
                        # 2. Is it expired (> 24h)?
                        is_expired = False
                        if claim_time_str:
                            try:
                                claim_time = datetime.fromisoformat(claim_time_str)
                                if datetime.now() - claim_time > timedelta(hours=24):
                                    is_expired = True
                            except ValueError:
                                is_expired = True  # Treat malformed time as expired

                        if is_expired:
                            logging.info(f"Claim for {filename} is expired (>24h). Deleting and proceeding.")
                            transfer_handler.delete_file(claim_filename)
                        else:
                            # 3. Valid claim by someone else
                            skipped_claimed_by_others.append(filename)
                            continue
                except json.JSONDecodeError:
                    logging.warning(f"Malformed claim file {claim_filename}. Deleting and proceeding.")
                    transfer_handler.delete_file(claim_filename)

        # Explicitly skip conversion failures
        if status == "conversion_failed":
            skipped_conversions.append(filename)
            continue

        # Skip if Parquet file exists remotely and status isn't a failure/incomplete state
        expected_parquet = "new-" + Path(filename).stem + ".parquet"
        # Allow processing if download/upload failed, even if parquet exists (might be old/partial)
        if expected_parquet in remote_parquet_files and status not in recoverable_failure_states:
            status_str = status or "N/A"
            if status_str not in skipped_parquet_exists:
                skipped_parquet_exists[status_str] = []
            skipped_parquet_exists[status_str].append(filename)
            continue

        # Add to list if status indicates it needs processing or is a recoverable failure
        if status in process_states:
            size = remote_zst_size_map.get(filename, 0)
            if size == 0 and filename in remote_zst_filenames:
                logging.warning(
                    f"File {filename} has size 0 or lookup failed, but exists remotely. Adding to process list."
                )
            files_to_process.append((filename, size))

    if skipped_remote_missing:
        logging.debug(
            f"Skipped {len(skipped_remote_missing)} files: No longer exists remotely. Files: {', '.join(skipped_remote_missing)}"
        )

    for status_str, files in skipped_parquet_exists.items():
        # Truncate list if it's too long
        display_files = files if len(files) <= 10 else [*files[:10], f"...and {len(files) - 10} more"]
        logging.info(
            f"Skipped {len(files)} files: Corresponding parquet exists remotely and status is '{status_str}'. Files: {', '.join(display_files)}"
        )

    if skipped_claimed_by_others:
        logging.info(f"Skipped {len(skipped_claimed_by_others)} files claimed by other machines.")

    if skipped_conversions:
        logging.warning(
            f"Skipped {len(skipped_conversions)} files with 'conversion_failed' status requiring manual check/reset: {', '.join(skipped_conversions[:5])}"
            + (f" and {len(skipped_conversions) - 5} more" if len(skipped_conversions) > 5 else "")
        )

    logging.info(f"Identified {len(files_to_process)} files to process/retry.")
    return files_to_process


def update_remote_claim(
    transfer_handler: TransferHandler,
    file_temp_dir: str,
    claim_filename: str,
    claim_data: dict,
    stage: str,
):
    """Updates the stage in the claim data and re-uploads it to the remote server."""
    claim_data["stage"] = stage
    claim_data["updated_at"] = datetime.now().isoformat()

    # Filter out internal tracking keys before saving
    save_data = {k: v for k, v in claim_data.items() if not k.startswith("_")}

    local_claim_path = os.path.join(file_temp_dir, claim_filename)
    try:
        with open(local_claim_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2)
        transfer_handler.upload_file(local_claim_path, claim_filename)
        logging.info(f"Updated remote claim for {claim_data.get('zst_filename')} to stage: {stage}")
    except Exception as e:
        logging.warning(f"Failed to update remote claim for stage {stage}: {e}")


def process_file(
    zst_filename: str,
    remote_size: int,
    log_data: dict,
    transfer_handler: TransferHandler,
    current_index: int = 0,
    total_files: int = 0,
    machine_meta: dict[str, Any] | None = None,
    temp_dir_used: str | None = None,
    force: bool = False,
) -> str:
    """Processes a single file: download, convert, upload.
    Includes machine metadata and detailed performance tracking.

    Returns:
        str: "success", "failed", or "skipped".
    """
    progress_prefix = f"[{current_index + 1}/{total_files}] " if total_files > 0 else ""
    update_terminal_title(f"{progress_prefix}Processing: {zst_filename}")

    def _update_status(stage: str):
        update_terminal_title(f"{progress_prefix}Processing: {zst_filename} [{stage}]")

    base_name = Path(zst_filename).stem
    parquet_filename = f"new-{base_name}.parquet"
    claim_filename = f"{base_name}.claim.json"

    # Initialize performance tracking
    perf_metrics: dict[str, Any] = {
        "machine": machine_meta,
        "temp_path": temp_dir_used,
        "started_at": datetime.now().isoformat(),
        "stages": {},
    }

    # Use a persistent temp directory per file under the base temp dir
    conversion_temp_base_dir = temp_dir_used or config.CONVERSION_TEMP_BASE_DIR
    file_temp_dir = os.path.join(conversion_temp_base_dir, f"{base_name}.zst_parquet_tmp")
    try:
        os.makedirs(file_temp_dir, exist_ok=True)
        logging.debug(f"Ensured persistent temp directory exists: {file_temp_dir}")
    except OSError as e:
        error_msg = f"Failed to create temp directory {file_temp_dir}: {e}"
        logging.error(error_msg)
        logger.update_log_entry(log_data, zst_filename, "download_failed", error=error_msg)
        logger.save_log(log_data)
        update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Temp dir error)")
        return "failed"

    # --- Last-second Concurrency Check ---
    if not force:
        # Check if a parquet file or a manifest appeared since we last listed
        manifest_filename = f"{parquet_filename}.manifest.json"
        if transfer_handler.file_exists(parquet_filename) or transfer_handler.file_exists(manifest_filename):
            logging.info(f"Concurrency check: {parquet_filename} (or manifest) already exists remotely. Skipping.")
            # Mark as completed in log to avoid checking again
            logger.update_log_entry(log_data, zst_filename, "completed")
            logger.save_log(log_data)
            return "skipped"

        # Check for existing claims (that aren't ours)
        if transfer_handler.file_exists(claim_filename):
            claim_str = transfer_handler.download_to_string(claim_filename)
            if claim_str:
                try:
                    claim_data_remote = json.loads(claim_str)
                    claim_machine = claim_data_remote.get("machine_meta", {})
                    is_own_claim = (
                        machine_meta is not None
                        and claim_machine.get("machine") == machine_meta.get("machine")
                        and claim_machine.get("ip") == machine_meta.get("ip")
                    )
                    if not is_own_claim:
                        logging.info(
                            f"Concurrency check: {zst_filename} was just claimed by another machine. Skipping."
                        )
                        return "skipped"
                    else:
                        logging.debug(f"Resuming our own active claim for {zst_filename}.")
                except Exception:
                    logging.warning(f"Malformed remote claim {claim_filename}. Proceeding with caution.")
    else:
        logging.info(f"Force mode enabled: Bypassing concurrency checks for {zst_filename}")

    # --- Create and Upload Claim ---
    claim_data = {
        "zst_filename": zst_filename,
        "machine_meta": machine_meta,
        "started_at": datetime.now().isoformat(),
    }
    local_claim_path = os.path.join(file_temp_dir, claim_filename)
    try:
        with open(local_claim_path, "w", encoding="utf-8") as f:
            json.dump(claim_data, f, indent=2)
        success_claim, _ = transfer_handler.try_create_claim(local_claim_path, claim_filename)
        if not success_claim:
            logging.info(f"Failed to create claim for {zst_filename}; it may have been claimed by another worker.")
            return "failed"
    except Exception as e:
        logging.error(f"Error creating/uploading claim for {zst_filename}: {e}")
        return "failed"
    # ------------------------------

    local_zst_path = os.path.join(file_temp_dir, zst_filename)
    local_parquet_path = os.path.join(file_temp_dir, parquet_filename)

    # --- Smart Resume & Cleanup ---
    # Delete everything except a VERIFIED complete ZST download
    for f_name in os.listdir(file_temp_dir):
        f_path = os.path.join(file_temp_dir, f_name)
        if f_name == zst_filename:
            if os.path.getsize(f_path) == remote_size:
                logging.info(f"Verified complete local ZST: {zst_filename}. Skipping download.")
                continue
            else:
                logging.warning(f"Local ZST size mismatch ({os.path.getsize(f_path)} != {remote_size}). Deleting.")

        # Delete any other files (partial parquets, logs, old claims, etc)
        with contextlib.suppress(Exception):
            if os.path.isfile(f_path):
                os.remove(f_path)
            elif os.path.isdir(f_path):
                shutil.rmtree(f_path)

    current_status = log_data.get("files", {}).get(zst_filename, {}).get("status", "pending")
    # If ZST exists now, it's verified
    if os.path.exists(local_zst_path):
        current_status = "downloaded"

    try:
        # 1. Download Stage
        if current_status in ["pending", "download_failed", "downloading"]:
            _update_status("downloading")
            logger.update_log_entry(log_data, zst_filename, "downloading")
            logger.save_log(log_data)
            update_remote_claim(transfer_handler, file_temp_dir, claim_filename, claim_data, "downloading")

            start_t = time.time()
            download_ok, duration = transfer_handler.download_file(zst_filename, local_zst_path, remote_size)
            perf_metrics["stages"]["download"] = {
                "duration_sec": round(duration, 2),
                "speed_mb_s": round((remote_size / (1024 * 1024)) / duration, 2) if duration > 0 else 0,
            }

            if not download_ok:
                error_msg = f"Download failed for {zst_filename}. See previous logs for details."
                logging.error(error_msg)
                logger.update_log_entry(log_data, zst_filename, "download_failed", error=error_msg)
                logger.save_log(log_data)
                update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Download)")
                return "failed"

            logging.info(f"Download completed for {zst_filename}.")
            logger.update_log_entry(log_data, zst_filename, "downloaded")
            logger.save_log(log_data)
            current_status = "downloaded"

        elif not os.path.exists(local_zst_path):
            error_msg = f"Expected local file {local_zst_path} not found despite status '{current_status}'. Resetting to re-download."
            logging.warning(error_msg)
            logger.update_log_entry(log_data, zst_filename, "pending", error=error_msg)
            logger.save_log(log_data)
            update_terminal_title(f"{progress_prefix}RETRYING: {zst_filename} (Missing local ZST)")
            return "failed"  # Let main loop retry with reset status
        else:
            logging.info(
                f"Skipping download for {zst_filename}, status is '{current_status}'. Verifying local file: {local_zst_path}"
            )
            # Optional: Add size check here too?

        # 2. Conversion Stage
        # Allow retrying conversion if status is downloaded or converting (or potentially failed, handled by get_files_to_process)
        if current_status in ["downloaded", "converting"]:
            _update_status("converting")
            # If parquet file exists from a previous failed run, remove it first
            if os.path.exists(local_parquet_path):
                logging.warning(
                    f"Local parquet file {local_parquet_path} already exists before conversion (status='{current_status}'). Removing before proceeding."
                )
                try:
                    os.remove(local_parquet_path)
                    logging.info("Removed existing local parquet file.")
                except OSError as e_rem:
                    error_msg = f"Could not remove existing local parquet file {local_parquet_path}: {e_rem}. Conversion may fail."
                    logging.error(error_msg)
                    # Update log but proceed with conversion attempt
                    logger.update_log_entry(log_data, zst_filename, "converting", error=error_msg)  # Keep as converting
                    logger.save_log(log_data)
                    # Don't return False here, let conversion attempt handle it

            logger.update_log_entry(log_data, zst_filename, "converting")
            logger.save_log(log_data)
            update_remote_claim(transfer_handler, file_temp_dir, claim_filename, claim_data, "converting")

            start_t = time.time()

            def _stage_callback(stage: str):
                _update_status(stage)
                update_remote_claim(transfer_handler, file_temp_dir, claim_filename, claim_data, stage)
                if "fallback" in stage:
                    perf_metrics["fallback_occurred"] = True
                    perf_metrics["fallback_stage"] = stage

            try:
                # Use callback to capture internal stages from the script
                convert_ok = convert_to_parquet(
                    local_zst_path,
                    local_parquet_path,
                    file_temp_dir,
                    on_claim_stage_change=_stage_callback,
                )

            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                error_msg = f"Conversion script execution failed: {e}"
                logging.error(error_msg)  # Detailed logs within converter.py
                logger.update_log_entry(log_data, zst_filename, "conversion_failed", error=str(e)[:500])
                logger.save_log(log_data)
                update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Conversion Script Error)")
                # Do not clean up temp dir on conversion failure
                return "failed"  # Explicitly return "failed", requires manual check

            duration = time.time() - start_t
            perf_metrics["stages"]["conversion"] = {"duration_sec": round(duration, 2)}

            if not convert_ok:
                # Error should have been logged by converter.py if file not found
                error_msg = (
                    f"Conversion failed: Parquet file {local_parquet_path} not created or other error (see logs)."
                )
                logging.error(error_msg)
                logger.update_log_entry(log_data, zst_filename, "conversion_failed", error=error_msg, perf=perf_metrics)
                logger.save_log(log_data)
                update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Conversion Output Missing)")
                # Do not clean up temp dir on conversion failure
                return "failed"  # Explicitly return "failed", requires manual check

            logger.update_log_entry(log_data, zst_filename, "converted", perf=perf_metrics)
            logger.save_log(log_data)
            current_status = "converted"

            # Safety Check: Only delete if the parquet file is valid and has content
            if os.path.exists(local_parquet_path) and os.path.getsize(local_parquet_path) > 10 * 1024:
                if os.path.exists(local_zst_path):
                    try:
                        os.remove(local_zst_path)
                        logging.info(f"Deleted source ZST after verified conversion: {local_zst_path}")
                    except OSError as e_rem:
                        logging.warning(f"Could not delete source ZST {local_zst_path}: {e_rem}")
            else:
                logging.warning(
                    f"Parquet file {local_parquet_path} is suspiciously small ({os.path.getsize(local_parquet_path) if os.path.exists(local_parquet_path) else 'N/A'} bytes). NOT deleting source ZST."
                )

        elif not os.path.exists(local_parquet_path):
            # If status is past conversion but file missing, reset to re-convert
            error_msg = f"Expected local parquet file {local_parquet_path} not found despite status '{current_status}'. Resetting to re-convert."
            logging.warning(error_msg)
            logger.update_log_entry(
                log_data, zst_filename, "downloaded", error=error_msg
            )  # Go back to downloaded state
            logger.save_log(log_data)
            update_terminal_title(f"{progress_prefix}RETRYING: {zst_filename} (Missing local Parquet)")
            return "failed"  # Let main loop retry conversion
        else:
            logging.info(
                f"Skipping conversion for {zst_filename}, status is '{current_status}'. Verifying local parquet: {local_parquet_path}"
            )

        # 3. Upload Stage
        if current_status in ["converted", "uploading", "upload_failed"]:
            _update_status("uploading")
            logger.update_log_entry(log_data, zst_filename, "uploading")
            logger.save_log(log_data)
            update_remote_claim(transfer_handler, file_temp_dir, claim_filename, claim_data, "uploading")

            start_t = time.time()
            upload_ok, duration = transfer_handler.upload_file(local_parquet_path, parquet_filename)

            parquet_size = os.path.getsize(local_parquet_path) if os.path.exists(local_parquet_path) else 0
            perf_metrics["stages"]["upload"] = {
                "duration_sec": round(duration, 2),
                "speed_mb_s": round((parquet_size / (1024 * 1024)) / duration, 2) if duration > 0 else 0,
            }

            if not upload_ok:
                error_msg = f"Upload failed for {parquet_filename}. See previous logs for details."
                logging.error(error_msg)
                logger.update_log_entry(log_data, zst_filename, "upload_failed", error=error_msg, perf=perf_metrics)
                logger.save_log(log_data)
                update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Upload)")
                return "failed"  # Failed this step

            # --- Handle Manifest Upload ---
            local_manifest_path = local_parquet_path + ".manifest.json"
            if os.path.exists(local_manifest_path):
                # Inject performance/history metrics into the manifest before uploading
                try:
                    with open(local_manifest_path) as f:
                        manifest_data = json.load(f)

                    perf_metrics["finished_at"] = datetime.now().isoformat()
                    manifest_data["conversion_history"] = perf_metrics

                    with open(local_manifest_path, "w") as f:
                        json.dump(manifest_data, f, indent=2)
                    logging.info(f"Injected conversion history into manifest: {local_manifest_path}")
                except Exception as e:
                    logging.warning(f"Could not inject history into manifest {local_manifest_path}: {e}")

                manifest_filename = parquet_filename + ".manifest.json"
                logging.info(f"Uploading manifest: {manifest_filename}")
                # We don't track manifest upload as a separate stage for simplicity,
                # but we'll log its failure if it happens
                m_upload_ok, _ = transfer_handler.upload_file(local_manifest_path, manifest_filename)
                if m_upload_ok:
                    # Success! Manifest replaces claim.
                    transfer_handler.delete_file(claim_filename)
                else:
                    error_msg = f"Manifest upload failed for {manifest_filename}."
                    logging.error(error_msg)
                    logger.update_log_entry(log_data, zst_filename, "upload_failed", error=error_msg, perf=perf_metrics)
                    logger.save_log(log_data)
                    update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Manifest Upload)")
                    current_status = "upload_failed"
                    return "failed"
            else:
                error_msg = f"Manifest file missing after conversion: {local_manifest_path}."
                logging.error(error_msg)
                logger.update_log_entry(log_data, zst_filename, "upload_failed", error=error_msg, perf=perf_metrics)
                logger.save_log(log_data)
                update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Manifest Missing)")
                current_status = "upload_failed"
                return "failed"
            # ------------------------------

            logging.info(f"Upload complete for {parquet_filename}.")
            # Final completion log including all performance metrics and machine info
            logger.update_log_entry(log_data, zst_filename, "completed", perf=perf_metrics)
            logger.save_log(log_data)
            current_status = "completed"
            update_terminal_title(f"{progress_prefix}COMPLETED: {zst_filename}")
        else:
            # Should only be status = completed if logic is correct
            logging.info(f"Skipping upload for {zst_filename}, status is '{current_status}'.")
            if current_status == "completed":
                update_terminal_title(f"{progress_prefix}Already Completed: {zst_filename}")

        # 4. Local Cleanup Stage (Aggressive)
        if current_status in ["completed", "conversion_failed", "upload_failed", "download_failed"]:
            cleanup_local_temp(file_temp_dir)
            if current_status != "completed":
                logging.info(f"Cleaned up local temp and releasing claim for FAILED file: {zst_filename}")
                transfer_handler.delete_file(claim_filename)

        return "success" if current_status == "completed" else "failed"

    except KeyboardInterrupt:
        logging.info(f"Process interrupted by user for {zst_filename}. Cleaning up claim and local temp...")
        try:
            # Delete remote claim
            transfer_handler.delete_file(claim_filename)
            # Clean up local temp files
            cleanup_local_temp(file_temp_dir)
        except Exception:
            pass
        raise
    except Exception as e:
        # Catch-all for unexpected errors during the process stages
        error_msg = f"An unexpected error occurred processing {zst_filename}: {e}"
        logging.exception(error_msg)

        # Determine best failure status based on last known good state
        fail_status = "unknown_error"
        if current_status == "downloading":
            fail_status = "download_failed"
        elif current_status == "converting":
            fail_status = "conversion_failed"
        elif current_status == "uploading" or current_status == "converted":
            fail_status = "upload_failed"
        elif current_status == "downloaded":
            fail_status = "conversion_failed"  # Error likely during conversion setup/call

        logger.update_log_entry(log_data, zst_filename, fail_status, error=str(e)[:500])
        logger.save_log(log_data)
        update_terminal_title(f"{progress_prefix}FAILED: {zst_filename} (Unexpected Error)")

        # Ensure claim is released and temp is cleaned up even on unexpected exceptions
        try:
            transfer_handler.delete_file(claim_filename)
            cleanup_local_temp(file_temp_dir)
        except Exception:
            pass

        return "failed"


def cleanup_own_claims(remote_other_files: set[str], transfer_handler: TransferHandler, machine_meta: dict):
    """Deletes all claim files on the remote server that belong to this machine."""
    logging.debug("Checking for abandoned claims from this machine...")
    own_claims_deleted = 0
    for filename in remote_other_files:
        if filename.endswith(".claim.json"):
            claim_str = transfer_handler.download_to_string(filename)
            if claim_str:
                try:
                    claim_data = json.loads(claim_str)
                    claim_machine = claim_data.get("machine_meta", {})
                    if claim_machine.get("machine") == machine_meta.get("machine") and claim_machine.get(
                        "ip"
                    ) == machine_meta.get("ip"):
                        logging.info(f"Deleting abandoned claim: {filename}")
                        if transfer_handler.delete_file(filename):
                            own_claims_deleted += 1
                except Exception:
                    pass
    if own_claims_deleted > 0:
        logging.info(f"Cleaned up {own_claims_deleted} abandoned claims.")
    else:
        logging.debug("No abandoned claims found.")
