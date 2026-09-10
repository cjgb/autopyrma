"""Persistent configuration (TOML file) and password storage (OS keyring).

Split of responsibilities (multi-platform):

* Non-secrets (certificate path, reason, location, contact) live in a TOML
  config file under the platform-appropriate user config directory
  (via ``platformdirs``): ``%APPDATA%\\autopyrma\\config.toml`` on Windows,
  ``~/Library/Application Support/autopyrma/config.toml`` on macOS,
  ``~/.config/autopyrma/config.toml`` on Linux.
* The P12 password (secret) lives in the OS keyring (via ``keyring``):
  Windows Credential Manager, macOS Keychain, Linux Secret Service.
  It is never written to the config file, logs, or shell history.
"""

from __future__ import annotations

import getpass
import os
import tomllib
from pathlib import Path

import platformdirs

APP_NAME = "autopyrma"
KEYRING_SERVICE = "autopyrma"

ENV_P12 = "AUTOPYRMA_P12"
ENV_P12_PASSWORD = "AUTOPYRMA_P12_PASSWORD"


def config_path() -> Path:
    """Path of the user config file for this platform."""
    return platformdirs.user_config_path(APP_NAME) / "config.toml"


def load_config() -> dict:
    """Load the TOML config file; missing file means empty config."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f"error: cannot read config file {path}: {e}")


def save_config(values: dict) -> Path:
    """Merge ``values`` into the ``[signer]`` section and write it back.

    Only ever stores non-secret settings; callers must never pass a password.
    The config directory is created with mode 0o700 and the file with 0o600.
    """
    path = config_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as e:
        raise SystemExit(f"error: cannot create config directory {path.parent}: {e}")
    data = load_config()
    signer = data.get("signer", {})
    if not isinstance(signer, dict):
        raise SystemExit(f"error: [signer] section in {path} is not a table")
    signer.update(values)
    data["signer"] = signer
    lines = ["[signer]"]
    for key, value in signer.items():
        lines.append(f"{key} = {quote_toml_string(str(value))}")
    try:
        # Restrict permissions before writing the content.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        raise SystemExit(f"error: cannot write config file {path}: {e}")
    return path


def quote_toml_string(value: str) -> str:
    """Render a basic TOML string (double-quoted with escapes)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def configured_p12_path(explicit: str | None) -> str | None:
    """Resolve the P12 path: --p12 > $AUTOPYRMA_P12 > config file."""
    if explicit:
        return explicit
    env = os.environ.get(ENV_P12)
    if env:
        return env
    signer = load_config().get("signer", {})
    if isinstance(signer, dict) and signer.get("p12_path"):
        return str(signer["p12_path"])
    return None


def keyring_account(p12_path: str) -> str:
    """Stable keyring account id for a certificate: its P12 path."""
    return p12_path


def keyring_available() -> bool:
    """True when a usable (non-fail) keyring backend exists."""
    try:
        import keyring
        from keyring.backends import fail

        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def get_stored_password(account: str) -> str | None:
    """Return the keyring password for ``account``, or None if absent.

    Raises SystemExit with guidance when no keyring backend is usable.
    """
    if not keyring_available():
        raise SystemExit(
            "error: no keyring backend available on this machine "
            "(headless Linux usually lacks a Secret Service daemon). "
            f"Use --password or ${ENV_P12_PASSWORD}, or type the password interactively."
        )
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, account)
    except Exception as e:
        raise SystemExit(f"error: cannot read password from keyring: {e}")


def store_password(account: str) -> None:
    """Prompt (hidden) for the password and store it in the keyring."""
    if not keyring_available():
        raise SystemExit(
            "error: no keyring backend available on this machine; "
            "cannot store the password."
        )
    password = getpass.getpass(f"P12 password for {account}: ")
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, account, password)
    except Exception as e:
        raise SystemExit(f"error: cannot store password in keyring: {e}")
    print(f"Password stored in keyring ({KEYRING_SERVICE}/{account}).")


def forget_password(account: str) -> None:
    """Delete the keyring entry for ``account``."""
    if not keyring_available():
        raise SystemExit(
            "error: no keyring backend available on this machine; nothing to forget."
        )
    try:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYRING_SERVICE, account)
        except PasswordDeleteError:
            print(f"No stored password for {account}; nothing to do.")
            return
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"error: cannot delete password from keyring: {e}")
    print(f"Password forgotten ({KEYRING_SERVICE}/{account}).")
