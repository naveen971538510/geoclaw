"""Baseline security response headers shared by ``main.py`` and
``dashboard_api.py``.

These are defense-in-depth headers — they don't replace authn/authz,
they just cut off a handful of browser-side attack classes:

* ``X-Content-Type-Options: nosniff`` — stops the browser from
  reinterpreting a JSON response as HTML based on bytes that look
  script-like.
* ``X-Frame-Options: DENY`` — blocks clickjacking by refusing to be
  embedded in a cross-origin iframe. (The dashboard is a standalone
  SPA; nothing here is designed to be embedded.)
* ``Referrer-Policy: strict-origin-when-cross-origin`` — strips the
  path / query when a page links out cross-origin so tokens we might
  carry in ``?token=…`` don't leak into third-party referer logs.
* ``Permissions-Policy`` — denies the browser's camera / microphone /
  geolocation features wholesale; GeoClaw has no need for them.
* ``Strict-Transport-Security`` — pins HTTPS once the site is served
  over TLS.  Browsers ignore the header on plain HTTP, so it's safe to
  always advertise.

Content-Security-Policy is deliberately **NOT** set here: the existing
``_render_operator_status`` and ``render_page`` HTML views rely on
inline ``<style>`` blocks, so a strict CSP would require an auditable
refactor pass before rollout.  Tracked as a follow-up.

The middleware is a plain ASGI-free helper that returns a dict; each
app attaches it to ``starlette``'s ``BaseHTTPMiddleware`` via its own
``@app.middleware("http")`` decorator so we don't have to share a
framework dependency here.
"""
from __future__ import annotations

from typing import Dict, Iterable


_DEFAULT_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "accelerometer=(), "
        "camera=(), "
        "geolocation=(), "
        "gyroscope=(), "
        "magnetometer=(), "
        "microphone=(), "
        "payment=(), "
        "usb=()"
    ),
    # Browsers ignore HSTS on plain HTTP so including it here is safe
    # even for local dev.  One-year max-age matches common baselines;
    # do NOT add ``preload`` without user intent to submit to the HSTS
    # preload list.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def default_security_headers() -> Dict[str, str]:
    """Return a fresh copy of the baseline header set.

    Callers are free to mutate the returned dict (e.g. to drop HSTS
    during local-only testing) without affecting other consumers.
    """
    return dict(_DEFAULT_HEADERS)


def apply_security_headers(
    existing: Iterable[tuple[bytes, bytes]] | None,
    overrides: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Compute the final header set.

    We merge the baseline with any per-route overrides, then skip any
    header that's already been explicitly set upstream so a handler can
    still opt out by writing its own value (e.g. a legitimate embed use
    case overriding ``X-Frame-Options: SAMEORIGIN``).

    ``existing`` is the raw header list off a Starlette
    ``Response.raw_headers`` (bytes-pair sequence).  ``overrides`` lets
    a specific app tune the defaults without editing this module.
    """
    merged = dict(_DEFAULT_HEADERS)
    if overrides:
        merged.update(overrides)
    if existing:
        already = {k.decode("latin-1").lower() for (k, _v) in existing}
        merged = {k: v for k, v in merged.items() if k.lower() not in already}
    return merged
