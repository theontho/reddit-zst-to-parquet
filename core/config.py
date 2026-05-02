
"""Configuration loader for the remote parquet conversion script."""

import os
import platform
from typing import Any

import tomllib

# --- Default Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Config files are in the parent directory of 'core/'
EXAMPLE_CONFIG = os.path.join(SCRIPT_DIR, "..", "config.example.toml")
LOCAL_CONFIG = os.path.join(SCRIPT_DIR, "..", "config.local.toml")

# --- Base Values (Generic only) ---
config_data: dict[str, Any] = {
    "remote": {"host": None, "user": None, "directory": None},
    "ftp": {
        "host": None,
        "user": None,
        "password": None,
        "port": 21,
        "timeout_seconds": 300,
        "passive_mode": True,
        "use_ftps": False,
    },
    "transfer": {
        "method": "ftp",
        "max_retries": 2,
        "retry_delay_seconds": 5,
        "timeout_seconds": 300,
    },
    "paths": {
        "temp_dir_fallbacks": ["./conversion_temp", "~/Documents/reddit_parquet_temp"],
        "nfs_mount_path": "~/qnap",
        "duckdb_path": None,
        "zstd_path": None,
    },
    "pipeline": {
        "max_consecutive_failures": 6,
        "enable_terminal_title_update": True,
        "log_file": "conversion_log.json",
        "chunk_size": 1000000,
        "test_run_chunk_size": 10000,
        "compression_ratio_estimate": 16,
        "conversion_method": "chunked", # "chunked" is recommended for stability. "streamed" is faster but prone to OOM on large files.
        "fallback_to_chunked": True,
    },

    "duckdb": {
        "threads": None,  # Will be auto-detected if None
        "memory_limit_gb": None,  # Will be auto-detected if None
        "preserve_insertion_order": False,
        "maximum_object_size": 33554432,
        "row_group_size": 100000,
        "parquet_compression_codec": "ZSTD",
        "large_file_threshold_gb": 1.0,
        "ram_usage_factor": 0.8,
    },
    "zstd": {
        "long_range_bits": 31,
        "termination_timeout_seconds": 5,
    },
}


def get_dynamic_defaults():
    """Detects hardware specs and returns recommended defaults."""
    import os

    try:
        import psutil

        total_ram_gb = psutil.virtual_memory().total / (1024**3)
    except ImportError:
        total_ram_gb = 16.0  # Safe fallback

    cpu_count = os.cpu_count() or 4

    # Default to 80% of RAM, capped at 90% of physical to leave room for OS
    rec_memory = int(total_ram_gb * config_data["duckdb"]["ram_usage_factor"])
    rec_threads = max(1, cpu_count - 1)  # Leave 1 core for OS/Decompression

    return rec_threads, rec_memory




def load_toml_config(path, current_config):
    """Loads and merges TOML config into current_config."""
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                new_data = tomllib.load(f)
                for section, values in new_data.items():
                    if section in current_config and isinstance(values, dict):
                        current_config[section].update(values)
                    else:
                        current_config[section] = values
        except Exception as e:
            print(f"Warning: Error loading config from {path}: {e}")


# Load order: Base (generic) -> Example (defaults) -> Local (overrides)
load_toml_config(EXAMPLE_CONFIG, config_data)
load_toml_config(LOCAL_CONFIG, config_data)

# --- Validation (Fail Fast) ---
def validate_config(config_data):
    method = config_data["transfer"]["method"].lower()

    # Common required remote info (for multi-node or remote systems)
    if method in ["rsync", "ftp", "nfs"]:
        required_remote = ["host", "user", "directory"]
        missing_remote = [f"remote.{k}" for k in required_remote if not config_data["remote"].get(k)]
        if missing_remote:
             print(f"Warning: Missing recommended configuration for {method}: {', '.join(missing_remote)}")

    if method == "ftp" and not config_data["ftp"].get("password"):
        raise ValueError("FTP method selected but ftp.password is missing in configuration.")

    if method == "local" and not config_data["remote"].get("directory"):
        raise ValueError("Local method selected but remote.directory (used as local source) is missing.")

# validate_config(config_data) # Removed auto-validation on import

# --- Map TOML to Constants for Compatibility ---
LOG_FILE = config_data["pipeline"]["log_file"]
TEMP_DIR_FALLBACKS = config_data["paths"]["temp_dir_fallbacks"]
CONVERSION_TEMP_BASE_DIR = TEMP_DIR_FALLBACKS[0]

_os_is_windows = platform.system() == "Windows"
DUCKDB_PATH = config_data["paths"].get("duckdb_path") or ("duckdb.exe" if _os_is_windows else "duckdb")
ZSTD_PATH = config_data["paths"].get("zstd_path") or ("zstd.exe" if _os_is_windows else "zstd")

MAX_RSYNC_RETRIES = config_data["transfer"]["max_retries"]
RSYNC_RETRY_DELAY_SECONDS = config_data["transfer"]["retry_delay_seconds"]
RSYNC_TIMEOUT_SECONDS = config_data["transfer"]["timeout_seconds"]
TRANSFER_METHOD = config_data["transfer"]["method"]
NFS_MOUNT_PATH = config_data["paths"]["nfs_mount_path"]
MAX_CONSECUTIVE_FAILURES = config_data["pipeline"]["max_consecutive_failures"]
REMOTE_HOST = config_data["remote"]["host"]
REMOTE_USER = config_data["remote"]["user"]
REMOTE_DIR = config_data["remote"]["directory"]

FTP_HOST = config_data["ftp"].get("host", REMOTE_HOST)
FTP_USER = config_data["ftp"].get("user", REMOTE_USER)
FTP_PASSWORD = config_data["ftp"].get("password", "")
FTP_PORT = config_data["ftp"]["port"]
FTP_TIMEOUT_SECONDS = config_data["ftp"]["timeout_seconds"]
FTP_PASSIVE_MODE = config_data["ftp"]["passive_mode"]
USE_FTPS = config_data["ftp"]["use_ftps"]

CONVERSION_METHOD = config_data["pipeline"]["conversion_method"]
FALLBACK_TO_CHUNKED = config_data["pipeline"]["fallback_to_chunked"]

STREAMED_CONVERTER_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "engines", "streamed_engine.py"))
CHUNKED_CONVERTER_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "engines", "chunked_engine.py"))
PYARROW_CONVERTER_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "engines", "pyarrow_engine.py"))

ENABLE_TERMINAL_TITLE_UPDATE = config_data["pipeline"]["enable_terminal_title_update"]


# --- DuckDB Config with Dynamic Fallbacks ---
try:
    import psutil
    TOTAL_RAM_GB = psutil.virtual_memory().total / (1024**3)
except ImportError:
    TOTAL_RAM_GB = 16.0

_rec_threads, _rec_memory = get_dynamic_defaults()


DUCKDB_THREADS = config_data["duckdb"]["threads"] or _rec_threads
DUCKDB_MEMORY_LIMIT_GB = config_data["duckdb"]["memory_limit_gb"] or _rec_memory
DUCKDB_PRESERVE_INSERTION_ORDER = config_data["duckdb"]["preserve_insertion_order"]
DUCKDB_MAXIMUM_OBJECT_SIZE = config_data["duckdb"]["maximum_object_size"]
DUCKDB_ROW_GROUP_SIZE = config_data["duckdb"]["row_group_size"]
DUCKDB_PARQUET_COMPRESSION_CODEC = config_data["duckdb"]["parquet_compression_codec"]
DUCKDB_LARGE_FILE_THRESHOLD_GB = config_data["duckdb"]["large_file_threshold_gb"]
DUCKDB_RAM_USAGE_FACTOR = config_data["duckdb"]["ram_usage_factor"]


# --- ZSTD Config ---
ZSTD_LONG_RANGE_BITS = config_data["zstd"]["long_range_bits"]
ZSTD_TERMINATION_TIMEOUT_SECONDS = config_data["zstd"]["termination_timeout_seconds"]

# --- Pipeline Config ---
CHUNK_SIZE = config_data["pipeline"]["chunk_size"]
TEST_RUN_CHUNK_SIZE = config_data["pipeline"]["test_run_chunk_size"]
COMPRESSION_RATIO_ESTIMATE = config_data["pipeline"]["compression_ratio_estimate"]

