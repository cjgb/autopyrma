"""autopyrma CLI: minimal Afirma-compatible PAdES PDF signer."""

import argparse
import getpass
import os
import sys

from . import config as appconfig
from .config import (
    ENV_P12,
    ENV_P12_PASSWORD,
    KEYRING_SERVICE,
    configured_p12_path,
    forget_password,
    get_stored_password,
    keyring_account,
    keyring_available,
    save_config,
    store_password,
)
from .pades import DEFAULT_BOX, DEFAULT_FIELD_NAME, DEFAULT_MD, sign_pdf

# CLI flags that --save-config persists into the [signer] config section.
# Maps flag spelling -> (argparse dest, config key).
SAVEABLE_FLAGS = (
    ("--p12", "p12", "p12_path"),
    ("--reason", "reason", "reason"),
    ("--location", "location", "location"),
    ("--contact", "contact", "contact"),
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autopyrma",
        description="Sign a PDF (PAdES-Basic, visible stamp on last page).",
    )
    p.add_argument(
        "--p12",
        default=None,
        help=f"PKCS#12 (.p12/.pfx) file (else ${ENV_P12} or config file)",
    )
    p.add_argument(
        "--password",
        default=None,
        help=(
            f"P12 password (else ${ENV_P12_PASSWORD}, OS keyring, "
            "or interactive prompt)"
        ),
    )
    p.add_argument("--in", dest="src", default=None, help="Input PDF")
    p.add_argument("--out", dest="dst", default=None, help="Output signed PDF")
    p.add_argument("--reason", default=None)
    p.add_argument("--location", default=None)
    p.add_argument("--contact", default=None)
    p.add_argument(
        "--box",
        nargs=4,
        type=float,
        default=list(DEFAULT_BOX),
        metavar=("LLX", "LLY", "URX", "URY"),
        help="Stamp rectangle in points (default: 100 100 200 200)",
    )
    p.add_argument("--page", type=int, default=-1, help="0-based page, -1=last")
    p.add_argument("--field", default=DEFAULT_FIELD_NAME)
    p.add_argument("--md", default=DEFAULT_MD, help="sha256/sha384/sha512")
    p.add_argument(
        "--save-config",
        action="store_true",
        help="Save --p12/--reason/--location/--contact to the config file, then sign",
    )
    p.add_argument(
        "--store-password",
        action="store_true",
        help=f"Prompt for the P12 password and store it in the OS keyring ({KEYRING_SERVICE}), then exit",
    )
    p.add_argument(
        "--forget-password",
        action="store_true",
        help="Delete the stored keyring password for this certificate, then exit",
    )
    return p


def _explicit_flags(raw_argv: list[str]) -> set[str]:
    """Flag spellings (e.g. {'--p12','--reason'}) present on the command line."""
    found = set()
    for token in raw_argv:
        name = token.split("=", 1)[0]
        if name.startswith("--"):
            found.add(name)
    return found


def _resolve_password(explicit: str | None, account: str) -> str | None:
    """Password precedence: --password > env > keyring > prompt."""
    if explicit is not None:
        return explicit
    env = os.environ.get(ENV_P12_PASSWORD)
    if env:
        return env
    if keyring_available():
        stored = get_stored_password(account)
        if stored is not None:
            return stored
    if not sys.stdin.isatty():
        raise SystemExit(
            "error: no --password given (and none in env/keyring) "
            "and stdin is not a TTY; refusing to prompt"
        )
    return getpass.getpass("P12 password: ")


def _config_defaults(args: argparse.Namespace) -> None:
    """Fill unset --reason/--location/--contact from the config file."""
    signer = appconfig.load_config().get("signer", {})
    if not isinstance(signer, dict):
        return
    for dest in ("reason", "location", "contact"):
        if getattr(args, dest) is None and signer.get(dest):
            setattr(args, dest, str(signer[dest]))


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    p12 = configured_p12_path(args.p12)

    if args.store_password or args.forget_password:
        if not p12:
            parser.error(
                f"cannot identify the certificate; pass --p12, "
                f"set ${ENV_P12}, or save it first with --save-config"
            )
        account = keyring_account(p12)
        if args.store_password:
            store_password(account)
        else:
            forget_password(account)
        return

    if not args.src or not args.dst:
        parser.error("the following arguments are required: --in, --out")

    if args.save_config:
        present = _explicit_flags(raw_argv)
        values = {}
        for flag, dest, key in SAVEABLE_FLAGS:
            if flag in present and getattr(args, dest) is not None:
                values[key] = str(getattr(args, dest))
        if not values:
            raise SystemExit(
                "error: --save-config needs at least one of "
                "--p12/--reason/--location/--contact on the command line"
            )
        path = save_config(values)
        print(f"Saved {', '.join(sorted(values))} to {path}")

    if not p12:
        raise SystemExit(
            f"error: no certificate configured; pass --p12, set ${ENV_P12}, "
            "or save one with --p12 ... --save-config"
        )
    _config_defaults(args)

    out = sign_pdf(
        p12,
        _resolve_password(args.password, keyring_account(p12)),
        args.src,
        args.dst,
        reason=args.reason,
        location=args.location,
        contact=args.contact,
        box=tuple(args.box),  # type: ignore[arg-type]
        page=args.page,
        field_name=args.field,
        md_algorithm=args.md,
    )
    print(f"Signed PDF written to {out}")
