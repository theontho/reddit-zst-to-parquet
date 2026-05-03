"""Safe configuration inspection helpers for the CLI."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core import config

SECRET_KEY_PARTS = ("password", "token", "secret", "key")


def _is_secret_key(key: str) -> bool:
    key_lower = key.lower()
    return any(part in key_lower for part in SECRET_KEY_PARTS)


def _format_value(key: str, value: Any) -> str:
    if _is_secret_key(key) and value:
        return "<redacted>"
    return repr(value)


def _print_paths() -> None:
    print(f"Example config: {Path(config.EXAMPLE_CONFIG).resolve()}")
    print(f"Local config:   {Path(config.LOCAL_CONFIG).resolve()}")


def _print_summary(config_data: Mapping[str, Any]) -> None:
    for section, values in config_data.items():
        print(f"[{section}]")
        if isinstance(values, Mapping):
            for key, value in values.items():
                print(f"{key} = {_format_value(key, value)}")
        else:
            print(repr(values))


def run_config_command(show_paths: bool = False, validate: bool = False, show: bool = False) -> int:
    """Run the config helper command and return a process exit code."""
    if show_paths or not (validate or show):
        _print_paths()

    if show:
        _print_summary(config.config_data)

    if validate:
        try:
            config.validate_config(config.config_data)
        except Exception as exc:
            print(f"Configuration invalid: {exc}")
            return 1
        print("Configuration valid")

    return 0
