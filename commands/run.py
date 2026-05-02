import logging
import os
import shutil
import time
from pathlib import Path

from core import config, logger
from core.processor import (
    cleanup_own_claims,
    get_files_to_process,
    initialize_log_entries,
    process_file,
)
from core.utils import (
    cleanup_orphan_temp_dirs,
    format_size,
    get_machine_metadata,
    select_temp_dir,
    update_terminal_title,
)
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler
from transfer.nfs_transfer import NfsTransferHandler
from transfer.rsync_ssh_transfer import RsyncSshTransferHandler


def run_conversion_loop():
    """Main function to coordinate the remote Zstandard to Parquet conversion process."""
    # --- Configuration Validation ---
    config.validate_config(config.config_data)

    # --- Basic Setup ---
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(module)s] %(message)s")
    start_time = time.time()
    logging.info("Starting remote parquet conversion process.")
    update_terminal_title("Remote Parquet Conversion - Initializing")

    # --- Machine & Path Setup ---
    machine_meta = get_machine_metadata()
    active_temp_dir = select_temp_dir()
    # Override config for the duration of this run
    config.CONVERSION_TEMP_BASE_DIR = active_temp_dir

    # --- Choose and Initialize Transfer Handler ---
    transfer_handler: TransferHandler
    transfer_method_lower = config.TRANSFER_METHOD.lower()

    if transfer_method_lower == "rsync":
        transfer_handler = RsyncSshTransferHandler()
    elif transfer_method_lower == "ftp":
        transfer_handler = FtpTransferHandler()
    elif transfer_method_lower == "nfs":
        transfer_handler = NfsTransferHandler()
    elif transfer_method_lower == "local":
        transfer_handler = LocalTransferHandler()
    else:
        logging.error(
            f"Invalid TRANSFER_METHOD configured: '{config.TRANSFER_METHOD}'. "
            "Choose 'rsync', 'ftp', 'nfs', or 'local'. Exiting."
        )
        return

    if not transfer_handler:
        logging.error("Failed to initialize transfer handler. Exiting.")
        return

    logging.info(f"Using transfer handler: {transfer_handler.__class__.__name__}")

    # --- Prerequisite Checks ---
    update_terminal_title("Remote Parquet Conversion - Checking Prerequisites")
    if not transfer_handler.check_prerequisites():
        logging.error("Prerequisite check failed for the selected transfer handler. Exiting.")
        return
    logging.info("Prerequisites check successful.")

    update_terminal_title("Remote Parquet Conversion - Checking Connection")
    if not transfer_handler.check_connection():
        logging.error("Connection check failed for the selected transfer handler. Exiting.")
        return
    logging.info("Connection check successful.")

    # Ensure persistent conversion base directory exists
    try:
        os.makedirs(config.CONVERSION_TEMP_BASE_DIR, exist_ok=True)
        logging.info(f"Ensured conversion temp base directory exists: {config.CONVERSION_TEMP_BASE_DIR}")
    except OSError as e:
        logging.error(
            f"Could not create conversion temp base directory {config.CONVERSION_TEMP_BASE_DIR}: {e}. Exiting."
        )
        return

    # --- Load Log and List Files ---
    update_terminal_title("Remote Parquet Conversion - Loading Log")
    log_data = logger.load_log()
    cleanup_orphan_temp_dirs(log_data, active_temp_dir)

    update_terminal_title("Remote Parquet Conversion - Listing Remote Files")
    try:
        zst_files_with_sizes, parquet_files, other_files = transfer_handler.list_remote_files()
    except Exception as e:
        logging.error(f"Failed to list remote files using {transfer_handler.__class__.__name__}: {e}")
        logging.exception("Traceback:")
        transfer_handler.close()
        return

    if not zst_files_with_sizes and not parquet_files and not log_data.get("files"):
        logging.warning(
            "Could not retrieve file lists or remote directory is empty, and log is empty/invalid. Exiting."
        )
        return

    # --- Clean up abandoned claims from this machine ---
    cleanup_own_claims(other_files, transfer_handler, machine_meta)

    # --- Update Log and Identify Files to Process ---
    update_terminal_title("Remote Parquet Conversion - Updating Log")
    zst_filenames = {fname for fname, _ in zst_files_with_sizes}
    log_changed = initialize_log_entries(log_data, zst_filenames)
    if log_changed:
        logger.save_log(log_data)

    update_terminal_title("Remote Parquet Conversion - Identifying Files")
    files_to_process_with_sizes = get_files_to_process(
        log_data, zst_files_with_sizes, parquet_files, other_files, transfer_handler, machine_meta
    )

    # Sort by size (ascending - smallest first)
    files_to_process_with_sizes.sort(key=lambda item: item[1], reverse=False)
    logging.info(
        f"Processing order prioritized by size (smallest first). Found {len(files_to_process_with_sizes)} files to process."
    )

    if not files_to_process_with_sizes:
        logging.info("All applicable remote .zst files appear to be processed or skipped. Nothing to do.")
        update_terminal_title("Remote Parquet Conversion - Complete (No Action)")
        return

    # --- Processing Loop ---
    processed_count = 0
    failed_count = 0
    skipped_count = 0
    total_to_process = len(files_to_process_with_sizes)
    consecutive_failures = 0

    logging.info(f"Starting processing loop. Total files to process/retry: {total_to_process}.")
    update_terminal_title(f"Remote Parquet Conversion - Processing 0/{total_to_process}")

    for i, (zst_file, size) in enumerate(files_to_process_with_sizes):
        size_str = f"({format_size(size)})" if size > 0 else "(size unknown)"
        header_log_content = f"--- Processing {zst_file} {size_str} ({i + 1}/{total_to_process}) ---"
        logging.info("-" * len(header_log_content))
        logging.info(header_log_content)
        logging.info("-" * len(header_log_content))

        # --- Global Cleanup of other stale temp folders ---
        try:
            current_tmp_name = f"{Path(zst_file).stem}.zst_parquet_tmp"
            for item in os.listdir(config.CONVERSION_TEMP_BASE_DIR):
                item_path = os.path.join(config.CONVERSION_TEMP_BASE_DIR, item)
                if os.path.isdir(item_path) and item.endswith(".zst_parquet_tmp") and item != current_tmp_name:
                    logging.info(f"Cleaning up stale temp folder from other file: {item}")
                    shutil.rmtree(item_path)
        except Exception as e:
            logging.debug(f"Non-critical error during global temp cleanup: {e}")

        # Pass necessary components to process_file
        result = process_file(
            zst_filename=zst_file,
            remote_size=size,
            log_data=log_data,
            transfer_handler=transfer_handler,
            current_index=i,
            total_files=total_to_process,
            machine_meta=machine_meta,
            temp_dir_used=active_temp_dir,
        )

        if result == "success":
            processed_count += 1
            consecutive_failures = 0
        elif result == "skipped":
            skipped_count += 1
        else:  # failed
            failed_count += 1
            consecutive_failures += 1
            logging.warning(
                f"Failed to process {zst_file}. Log updated. Moving to next file. "
                f"(Consecutive failures: {consecutive_failures}/{config.MAX_CONSECUTIVE_FAILURES})"
            )

        if consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
            logging.error(f"Stopping script due to {consecutive_failures} consecutive processing failures.")
            update_terminal_title("Remote Parquet Conversion - STOPPED (Failures)")
            break

    # --- Final Summary ---
    end_time = time.time()
    duration = end_time - start_time
    final_status = "Complete"
    if consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
        final_status = f"Terminated Early ({consecutive_failures} consecutive failures)"

    logging.info(
        f"Processing loop complete. Processed this run: {processed_count}, "
        f"Skipped this run: {skipped_count}, Failed this run: {failed_count}. "
        f"Duration: {duration:.2f}s"
    )
    update_terminal_title(
        f"Remote Parquet Conversion - {final_status} (P: {processed_count}, S: {skipped_count}, F: {failed_count})"
    )
    logging.info("Script finished.")
    transfer_handler.close()
