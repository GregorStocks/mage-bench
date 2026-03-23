"""Helpers for validated HTTPS requests."""

import urllib.parse
import urllib.request
from collections.abc import Mapping
from http.client import HTTPMessage
from typing import IO

_HTTPS_PORTS = {None, 443}


def _validate_https_url(url: str, *, allowed_hosts: frozenset[str]) -> None:
    """Reject unexpected schemes, hosts, and ports before opening a URL."""
    parsed = urllib.parse.urlsplit(url)
    assert parsed.scheme == "https", f"Expected https URL, got {url!r}"
    assert parsed.username is None and parsed.password is None, f"Credentials are not allowed in URL: {url!r}"
    hostname = parsed.hostname
    assert hostname is not None, f"Expected hostname in URL: {url!r}"
    assert hostname in allowed_hosts, (
        f"Unexpected HTTPS host {hostname!r} for {url!r}; expected one of {sorted(allowed_hosts)}"
    )
    assert parsed.port in _HTTPS_PORTS, (
        f"Unexpected HTTPS port {parsed.port!r} for {url!r}; expected 443 or no explicit port"
    )


class _ValidatedHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that escape the allowed HTTPS hosts."""

    def __init__(self, *, allowed_hosts: frozenset[str]) -> None:
        self._allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_https_url(newurl, allowed_hosts=self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_https_bytes(
    url: str,
    *,
    allowed_hosts: set[str] | frozenset[str],
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> bytes:
    """Fetch bytes from an HTTPS URL after validating scheme, host, and redirects."""
    allowed_host_set = frozenset(allowed_hosts)
    _validate_https_url(url, allowed_hosts=allowed_host_set)
    opener = urllib.request.build_opener(_ValidatedHttpsRedirectHandler(allowed_hosts=allowed_host_set))
    if headers is None:
        request_headers: dict[str, str] = {}
    else:
        request_headers = dict(headers)
    request = urllib.request.Request(  # noqa: S310 - scheme, host, port, and redirects are validated above
        url,
        data=data,
        headers=request_headers,
    )
    response = opener.open(request) if timeout is None else opener.open(request, timeout=timeout)
    with response as resp:
        body = resp.read()
    assert isinstance(body, bytes), f"Expected bytes response body from {url!r}, got {type(body).__name__}"
    return body


def fetch_https_text(
    url: str,
    *,
    allowed_hosts: set[str] | frozenset[str],
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    encoding: str = "utf-8",
) -> str:
    """Fetch text from an HTTPS URL after validating scheme, host, and redirects."""
    return fetch_https_bytes(
        url,
        allowed_hosts=allowed_hosts,
        data=data,
        headers=headers,
        timeout=timeout,
    ).decode(encoding)
