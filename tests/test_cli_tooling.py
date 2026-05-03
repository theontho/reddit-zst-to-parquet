from __future__ import annotations

import copy

from commands.config_cmd import _format_value
from commands.precheck import apply_method_override, create_transfer_handler
from core import config
from transfer.local_transfer import LocalTransferHandler


def test_config_summary_redacts_password():
    assert _format_value("password", "secret-value") == "<redacted>"


def test_apply_method_override_updates_loaded_config(monkeypatch):
    original_config = copy.deepcopy(config.config_data)
    original_method = config.TRANSFER_METHOD
    monkeypatch.setattr(config, "config_data", original_config)
    monkeypatch.setattr(config, "TRANSFER_METHOD", original_method)

    apply_method_override("local")

    assert config.TRANSFER_METHOD == "local"
    assert config.config_data["transfer"]["method"] == "local"


def test_create_local_transfer_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REMOTE_DIR", str(tmp_path))

    handler = create_transfer_handler("local")

    assert isinstance(handler, LocalTransferHandler)


def test_validate_config_rejects_example_ftp_password():
    config_data = copy.deepcopy(config.config_data)
    config_data["transfer"]["method"] = "ftp"
    config_data["ftp"]["password"] = "YOUR_FTP_PASSWORD_HERE"

    try:
        config.validate_config(config_data)
    except ValueError as exc:
        assert "example placeholder" in str(exc)
    else:
        raise AssertionError("Expected placeholder FTP password to fail validation")
