import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time

import psutil

from core.config import ENABLE_TERMINAL_TITLE_UPDATE, TEMP_DIR_FALLBACKS


def get_machine_metadata():
    """Gathers hardware and OS metadata for logging, including detailed core counts."""
    try:
        cpu_info = platform.processor()
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)

        # Core counts
        total_cores = psutil.cpu_count(logical=True)
        physical_cores = psutil.cpu_count(logical=False)

        cpu_details: dict[str, str | int | None] = {
            "total_logical": total_cores,
            "total_physical": physical_cores,
            "p_cores": "unknown",
            "e_cores": "unknown",
            "super_cores": "unknown",
        }

        # Specific enhancements for macOS (Apple Silicon / Hybrid)
        if sys.platform == "darwin":
            try:
                cpu_info = (
                    subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], stderr=subprocess.DEVNULL)
                    .decode()
                    .strip()
                )
                # Attempt to get P/E core split
                try:
                    p_cores = int(
                        subprocess.check_output(["sysctl", "-n", "hw.perflevel0.logicalcpu"], stderr=subprocess.DEVNULL)
                        .decode()
                        .strip()
                    )
                    cpu_details["p_cores"] = p_cores
                except Exception:
                    pass

                try:
                    e_cores = int(
                        subprocess.check_output(["sysctl", "-n", "hw.perflevel1.logicalcpu"], stderr=subprocess.DEVNULL)
                        .decode()
                        .strip()
                    )
                    cpu_details["e_cores"] = e_cores
                except Exception:
                    pass

            except Exception:
                pass

        def get_best_hostname():
            candidates = []
            try:
                candidates.append(platform.node())
            except Exception:
                pass
            try:
                candidates.append(socket.gethostname())
            except Exception:
                pass
            if platform.system() == "Darwin":
                try:
                    sc_name = (
                        subprocess.check_output(["scutil", "--get", "LocalHostName"], stderr=subprocess.DEVNULL)
                        .decode()
                        .strip()
                    )
                    if sc_name:
                        candidates.append(sc_name)
                except Exception:
                    pass

            valid_candidates = []
            for c in candidates:
                if not c or not isinstance(c, str):
                    continue
                score = 0
                if "\ufffd" in c:
                    score -= 100
                if any(ord(char) > 127 for char in c):
                    score -= 10
                valid_candidates.append((score, c))

            if valid_candidates:
                valid_candidates.sort(key=lambda x: x[0], reverse=True)
                best = valid_candidates[0][1]
                if "\ufffd" in best:
                    best = "".join(char for char in best if ord(char) < 128)
                return best
            return "unknown-node"

        machine_name = get_best_hostname()
        hostname = machine_name

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_address = s.getsockname()[0]
            s.close()
        except Exception:
            ip_address = "unknown"

        return {
            "machine": machine_name,
            "hostname": hostname,
            "ip": ip_address,
            "os": f"{platform.system()} {platform.release()}",
            "cpu": cpu_info,
            "cpu_cores": cpu_details,
            "ram_gb": ram_gb,
            "python": platform.python_version(),
        }
    except Exception as e:
        logging.warning(f"Could not gather full machine metadata: {e}")
        try:
            fallback_node = platform.node().replace("\ufffd", "")
        except Exception:
            fallback_node = "unknown"
        return {"machine": fallback_node, "error": str(e)}


def select_temp_dir() -> str:
    """Selects the first available/writable temp directory from the fallbacks."""
    for path in TEMP_DIR_FALLBACKS:
        try:
            expanded_path = os.path.expanduser(path)
            os.makedirs(expanded_path, exist_ok=True)
            test_file = os.path.join(expanded_path, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            logging.info(f"Selected temporary directory: {expanded_path}")
            return str(expanded_path)
        except Exception as e:
            logging.warning(f"Temp directory option {path} is unavailable: {e}")
            continue

    local_default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../conversion_temp")
    os.makedirs(local_default, exist_ok=True)
    return str(local_default)


def cleanup_orphan_temp_dirs(log_data, temp_base_dir):
    """Removes temporary directories that aren't accounted for in the current process."""
    if not os.path.exists(temp_base_dir):
        return

    logging.info(f"Checking for orphan temporary directories in {temp_base_dir}...")
    try:
        entries = os.listdir(temp_base_dir)
        files_log = log_data.get("files", {})

        for entry in entries:
            entry_path = os.path.join(temp_base_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            if not entry.endswith("_parquet_tmp"):
                continue

            orig_filename = entry.replace("_parquet_tmp", "")
            status = files_log.get(orig_filename, {}).get("status")

            mtime = os.path.getmtime(entry_path)
            is_old = (time.time() - mtime) > 900  # 15 minutes

            if status not in ["downloading", "converting"] or is_old:
                logging.info(f"Cleaning up orphan temp directory: {entry} (Status: {status or 'unknown'})")
                shutil.rmtree(entry_path, ignore_errors=True)
    except Exception as e:
        logging.warning(f"Error during orphan cleanup: {e}")


def update_terminal_title(message: str):
    """Updates the terminal title if enabled in config and not skipped by env var."""
    if ENABLE_TERMINAL_TITLE_UPDATE and os.environ.get("SKIP_TERMINAL_TITLE") != "1":
        # Use sys.stdout.write for direct printing without newline
        sys.stdout.write(f"\033]0;{message}\007")
        sys.stdout.flush()


def format_size(size_bytes):
    """Format file size in human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / (1024**2):.1f} MB"
    else:
        return f"{size_bytes / (1024**3):.1f} GB"


def format_speed(bytes_transferred, seconds):
    """Format transfer speed in human-readable format."""
    if seconds <= 0:
        return "N/A"
    bytes_per_sec = bytes_transferred / seconds
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.1f} B/s"
    elif bytes_per_sec < 1024**2:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    elif bytes_per_sec < 1024**3:
        return f"{bytes_per_sec / (1024**2):.1f} MB/s"
    else:
        return f"{bytes_per_sec / (1024**3):.1f} GB/s"


def cleanup_local_temp(temp_dir: str):
    """Removes the local temporary directory for a file."""
    logging.info(f"Cleaning up local directory: {temp_dir}")
    try:
        shutil.rmtree(temp_dir)
        logging.info(f"Successfully removed {temp_dir}")
    except OSError as e:
        logging.warning(f"Could not remove local directory {temp_dir}: {e}. Manual cleanup might be needed.")


class Heartbeat:
    """Context manager to run a background heartbeat for long-running operations."""

    def __init__(self, label: str, interval: int = 60):
        self.label = label
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        start_t = time.time()
        while not self.stop_event.is_set():
            elapsed = int(time.time() - start_t)
            # Update terminal title and print CLAIM_STAGE for orchestration
            # Use minutes:seconds for readability if > 60s
            time_str = f"{elapsed // 60}m {elapsed % 60}s" if elapsed >= 60 else f"{elapsed}s"

            logging.info(f"Heartbeat: {self.label} (elapsed {time_str})")
            print(f"CLAIM_STAGE: {self.label} (elapsed {time_str})", flush=True)
            update_terminal_title(f"Reddit ZST to Parquet - {self.label} ({time_str})")
            self.stop_event.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        # No need to join daemon thread, but we can wait briefly
        self.thread.join(timeout=0.5)
