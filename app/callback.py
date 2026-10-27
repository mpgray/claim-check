"""Delivering a finished report to the address its check asked for.

A check started with `callback_url` POSTs its report there once it is done
(or failed). The body is the same JSON as `/api/r/<id>`, signed so the
receiver can tell it came from this server:

    X-Claim-Check-Timestamp: <unix seconds>
    X-Claim-Check-Signature: v1=<hex HMAC-SHA256(CC_WEBHOOK_SECRET, "<timestamp>\\n<body>")>

`verify` is the receiving side, for anyone writing one in Python.

The server makes this request itself, so where it may go is limited: public
addresses (the fetcher's rule), or hosts listed in CC_CALLBACK_HOSTS for a
receiver on your own network.
"""
import hashlib
import hmac
import time
from urllib.parse import urlsplit

import httpx

from app.fetch import FetchError, _check_public

TIMESTAMP_HEADER = 'X-Claim-Check-Timestamp'
SIGNATURE_HEADER = 'X-Claim-Check-Signature'
ATTEMPTS = 3
TIMEOUT = 15.0


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b'\n' + body, hashlib.sha256).hexdigest()


def headers(secret: str, body: bytes, now: float | None = None) -> dict:
    ts = str(int(now if now is not None else time.time()))
    return {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: f'v1={signature(secret, ts, body)}',
            'Content-Type': 'application/json'}


def verify(secret: str, body: bytes, timestamp: str, header: str, max_skew: int = 300,
           now: float | None = None) -> bool:
    if not secret or not header.startswith('v1='):
        return False
    try:
        skew = abs((now if now is not None else time.time()) - int(timestamp))
    except ValueError:
        return False
    return skew <= max_skew and hmac.compare_digest(header[3:], signature(secret, timestamp, body))


def refused(url: str, hosts: list[str]) -> str:
    """Why the server may not POST to `url`, or '' if it may."""
    parts = urlsplit(url or '')
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return 'callback_url must be an http(s) address.'
    host = parts.hostname.lower()
    if host in hosts:
        return ''
    try:
        _check_public(host)
    except FetchError:
        return f'{host} is not a public address; list it in CC_CALLBACK_HOSTS to allow it.'
    return ''


def deliver(url: str, body: bytes, secret: str, hosts: list[str],
            client: httpx.Client | None = None, pause: float = 2.0) -> dict:
    """POST the report; a few tries for a receiver that is restarting.
    Returns what happened, for the report."""
    out = {'url': url, 'attempts': 0, 'status': 0, 'error': ''}
    why = refused(url, hosts)
    if why:
        out['error'] = why
        return out
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    try:
        for attempt in range(1, ATTEMPTS + 1):
            out['attempts'] = attempt
            try:
                resp = client.post(url, content=body, headers=headers(secret, body))
                out['status'], out['error'] = resp.status_code, ''
                if resp.status_code < 500:
                    break
                out['error'] = f'HTTP {resp.status_code}'
            except httpx.HTTPError as exc:
                out['error'] = f'{exc.__class__.__name__}: {exc}'[:200]
            if attempt < ATTEMPTS:
                time.sleep(pause * attempt)
    finally:
        if own:
            client.close()
    return out
