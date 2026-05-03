"""Preflight checks for conversion hosts."""

from __future__ import annotations

import logging
import shutil
from typing import NamedTuple

from core import config
from core.utils import select_temp_dir
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler
from transfer.nfs_transfer import NfsTransferHandler
from transfer.rsync_ssh_transfer import RsyncSshTransferHandler

TRANSFER_METHODS = ("local", "ftp", "rsync", "nfs")


class CheckResult(NamedTuple):
    name: str
    ok: bool
    detail: str


def apply_method_override(method: str | None) -> None:
    """Apply a CLI transfer-method override to the loaded config."""
    if not method:
        return
    config.TRANSFER_METHOD = method
    config.config_data["transfer"]["method"] = method


def create_transfer_handler(method: str) -> TransferHandler:
    """Create the transfer handler for a configured method."""
    method = method.lower()
    if method == "local":
        return LocalTransferHandler()
    if method == "ftp":
        return FtpTransferHandler()
    if method == "rsync":
        return RsyncSshTransferHandler()
    if method == "nfs":
        return NfsTransferHandler()
    raise ValueError(f"Unsupported transfer method: {method}")


def _check_tool(label: str, executable: str) -> CheckResult:
    resolved = shutil.which(executable)
    if resolved:
        return CheckResult(label, True, resolved)
    return CheckResult(label, False, f"'{executable}' was not found on PATH")


def _print_results(results: list[CheckResult]) -> None:
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")


def run_precheck(method: str | None = None, skip_connection: bool = False) -> int:
    """Run host readiness checks and return a process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(module)s] %(message)s")
    apply_method_override(method)

    results: list[CheckResult] = []

    try:
        config.validate_config(config.config_data)
        results.append(CheckResult("configuration", True, f"transfer.method={config.TRANSFER_METHOD}"))
        config_ok = True
    except Exception as exc:
        results.append(CheckResult("configuration", False, str(exc)))
        config_ok = False

    results.append(_check_tool("zstd executable", config.ZSTD_PATH))
    results.append(_check_tool("duckdb executable", config.DUCKDB_PATH))

    try:
        temp_dir = select_temp_dir()
        results.append(CheckResult("temporary directory", True, temp_dir))
    except Exception as exc:
        results.append(CheckResult("temporary directory", False, str(exc)))

    if config_ok:
        handler: TransferHandler | None = None
        try:
            handler = create_transfer_handler(config.TRANSFER_METHOD)
            results.append(
                CheckResult(
                    "transfer prerequisites",
                    handler.check_prerequisites(),
                    f"method={config.TRANSFER_METHOD}",
                )
            )
            if skip_connection:
                results.append(CheckResult("transfer connection", True, "skipped by --skip-connection"))
            else:
                results.append(
                    CheckResult(
                        "transfer connection",
                        handler.check_connection(),
                        f"method={config.TRANSFER_METHOD}",
                    )
                )
        except Exception as exc:
            results.append(CheckResult("transfer handler", False, str(exc)))
        finally:
            if handler is not None:
                handler.close()

    _print_results(results)
    return 0 if all(result.ok for result in results) else 1
