"""Handles loading, saving, and updating the processing log file."""

import json
import logging
import os
from datetime import datetime

# Load LOG_FILE path from config
# Note: This creates a dependency cycle if config imports logger.
# Consider passing LOG_FILE path as an argument if this becomes an issue.
# For now, direct import is simpler.
from core.config import LOG_FILE


def load_log() -> dict:
    """Loads the JSON log file. Returns an empty dict if file not found or invalid JSON."""
    filepath = LOG_FILE
    try:
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                log_data = json.load(f)
                if not isinstance(log_data, dict) or "files" not in log_data:
                    logging.warning(f"Log file {filepath} has invalid format. Starting fresh.")
                    return {"files": {}}
                # Ensure 'files' key exists and is a dict
                if not isinstance(log_data.get("files"), dict):
                    logging.warning(f"Log file {filepath} 'files' key is not a dictionary. Starting fresh.")
                    log_data["files"] = {}
                return log_data
        else:
            logging.info(f"Log file {filepath} not found. Starting fresh.")
            return {"files": {}}
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from log file {filepath}. Starting fresh.")
        return {"files": {}}
    except Exception as e:
        logging.error(f"Error loading log file {filepath}: {e}. Starting fresh.")
        return {"files": {}}


def save_log(data: dict):
    """Saves the log data to a JSON file atomically."""
    filepath = LOG_FILE
    temp_filepath = filepath + ".tmp"
    try:
        with open(temp_filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        # Atomic replace (works on Windows and POSIX)
        os.replace(temp_filepath, filepath)
        logging.debug(f"Log saved successfully to {filepath}")
    except Exception as e:
        logging.error(f"Error saving log file {filepath}: {e}")
        # Attempt to remove temp file if rename failed
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError:
                pass  # Ignore error during cleanup


def update_log_entry(log_data: dict, filename: str, status: str, error: str | None = None, **kwargs):
    """Updates a single file's entry in the log data with support for extra metadata."""
    if "files" not in log_data:
        log_data["files"] = {}
    entry = log_data["files"].get(filename, {})
    entry["status"] = status
    entry["last_update"] = datetime.now().isoformat()
    entry["error"] = error

    # Merge additional metadata (e.g., perf metrics, machine info)
    for key, value in kwargs.items():
        entry[key] = value

    log_data["files"][filename] = entry
    logging.debug(f"Log updated for {filename}: status={status}" + (f", error='{error}'" if error else ""))
