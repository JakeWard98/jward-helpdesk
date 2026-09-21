"""TLS contexts for the mail connections.

Certificates are verified by default and there is no global "turn it off"
switch: an unverified session to a mail server hands the mailbox credentials
to anything on the path.

Two escape hatches exist for Proton Mail Bridge and similar local relays,
which present a self-signed certificate:

* ``*_CA_CERT`` - point at the bridge's exported certificate. Verification
  stays on, against that certificate. This is the one to use.
* ``*_TLS_INSECURE`` - skip verification entirely. Only accepted when the
  host is loopback, a private address, or a container name on the local
  docker network, because that is the only situation where there is no
  meaningful path to sit on.
"""

from __future__ import annotations

import ipaddress
import logging
import ssl
from pathlib import Path

log = logging.getLogger(__name__)

LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa", ".localdomain")
LOCAL_NAMES = {"localhost", "host.docker.internal", "gateway.docker.internal"}


def is_local_host(host: str) -> bool:
    """Is this host unreachable from outside the machine or its docker network?"""
    host = (host or "").strip().lower().strip("[]")
    if not host:
        return False
    if host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES):
        return True
    # A bare name with no dots is a docker service or /etc/hosts entry.
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


class InsecureTlsRefused(ValueError):
    pass


def build_context(*, host: str, ca_cert: str = "", insecure: bool = False) -> ssl.SSLContext:
    """Build an SSL context for a mail connection."""
    if insecure:
        if not is_local_host(host):
            raise InsecureTlsRefused(
                f"refusing to skip certificate verification for {host!r}: "
                "it is not a loopback, private or local-network host. "
                "Supply the server's certificate through the matching "
                "*_CA_CERT setting instead."
            )
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        log.warning(
            "TLS certificate verification is disabled for %s - only acceptable "
            "because it is a local host",
            host,
        )
        return context

    if ca_cert:
        path = Path(ca_cert)
        if not path.is_file():
            raise FileNotFoundError(f"CA certificate not found: {ca_cert}")
        return ssl.create_default_context(cafile=str(path))

    return ssl.create_default_context()
