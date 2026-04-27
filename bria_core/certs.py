"""TLS certificate environment helpers shared across DCC bootstraps."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


MACOS_CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/cert.pem",
    "/private/etc/ssl/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
)

LINUX_CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",    # RHEL/CentOS/Fedora
    "/etc/ssl/ca-bundle.pem",              # openSUSE
    "/etc/ssl/cert.pem",                   # Alpine, others
)


def _log(logger: Any, level: str, message: str, *args: object) -> None:
    if logger is None:
        return
    fn = getattr(logger, level, None)
    if callable(fn):
        fn(message, *args)


def ensure_tls_cert_env(logger: Any = None) -> str | None:
    """Normalize TLS certificate env vars for embedded Python runtimes.

    Behavior:
    - macOS: if SSL_CERT_FILE is missing/invalid, auto-select first existing
      candidate bundle and export SSL_CERT_FILE, REQUESTS_CA_BUNDLE, BRIA_CA_BUNDLE.
    - non-macOS: if SSL_CERT_FILE points to known macOS placeholder that does not
      exist, clear it and matching REQUESTS_CA_BUNDLE/BRIA_CA_BUNDLE values.

    Returns selected cert bundle path when one is active, else None.
    """
    current = (os.getenv("SSL_CERT_FILE") or "").strip()

    if sys.platform == "darwin":
        if current and Path(current).exists():
            return current

        for candidate in MACOS_CA_BUNDLE_CANDIDATES:
            if Path(candidate).exists():
                os.environ["SSL_CERT_FILE"] = candidate
                os.environ.setdefault("REQUESTS_CA_BUNDLE", candidate)
                os.environ.setdefault("BRIA_CA_BUNDLE", candidate)
                _log(logger, "info", "Bria TLS resolver set SSL_CERT_FILE=%s", candidate)
                return candidate

        _log(logger, "warning", "Bria TLS resolver could not find a macOS CA bundle")
        return None

    # Windows: try certifi bundle, otherwise Python's ssl module uses the
    # Windows system certificate store automatically.
    if sys.platform == "win32" and not current:
        try:
            import certifi  # type: ignore
            cert_file = certifi.where()
            if cert_file and Path(cert_file).exists():
                os.environ["SSL_CERT_FILE"] = cert_file
                os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_file)
                os.environ.setdefault("BRIA_CA_BUNDLE", cert_file)
                _log(logger, "info", "Bria TLS resolver set SSL_CERT_FILE=%s (certifi)", cert_file)
                return cert_file
        except ImportError:
            pass
        _log(logger, "debug", "Bria TLS resolver: no certifi on Windows, using system cert store")
        return None

    # Linux: check common distro cert bundle locations.
    if sys.platform == "linux" and not current:
        for candidate in LINUX_CA_BUNDLE_CANDIDATES:
            if Path(candidate).exists():
                os.environ["SSL_CERT_FILE"] = candidate
                os.environ.setdefault("REQUESTS_CA_BUNDLE", candidate)
                os.environ.setdefault("BRIA_CA_BUNDLE", candidate)
                _log(logger, "info", "Bria TLS resolver set SSL_CERT_FILE=%s", candidate)
                return candidate
        _log(logger, "debug", "Bria TLS resolver: no known Linux CA bundle found, using system defaults")
        return None

    if current in MACOS_CA_BUNDLE_CANDIDATES and not Path(current).exists():
        os.environ.pop("SSL_CERT_FILE", None)
        if (os.getenv("REQUESTS_CA_BUNDLE") or "").strip() == current:
            os.environ.pop("REQUESTS_CA_BUNDLE", None)
        if (os.getenv("BRIA_CA_BUNDLE") or "").strip() == current:
            os.environ.pop("BRIA_CA_BUNDLE", None)
        _log(logger, "info", "Bria TLS resolver cleared invalid macOS SSL_CERT_FILE placeholder")

    current = (os.getenv("SSL_CERT_FILE") or "").strip()
    return current or None
