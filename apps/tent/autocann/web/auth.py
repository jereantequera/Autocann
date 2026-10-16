"""
A PIN in front of the dashboard, and the three endpoints it must never cover.

The API switches hardware. Anybody on the LAN could close a relay, pause the
controller or wipe the feed chart, which is backlog #7 and the thing to fix
before this is reachable from anywhere but the tent's own network.

Three rules shape everything here:

1. **The nodes are exempt, and they have to be.** See `EXEMPT` below. One of
   them de-energises the tent sixty seconds after a 401.
2. **Without `AUTOCANN_PIN` the behaviour is exactly what it was.** Nothing
   asks for anything. A deploy that forgets the variable must not lock the
   grower out of their own dashboard; being unable to see the tent is a worse
   failure than being readable from the LAN.
3. **The session lasts.** The dashboard is used from a phone, standing in a
   tent, with wet hands. Re-typing a PIN every day would get it written on the
   wall next to the door.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Dict, Optional, Tuple

from flask import jsonify, render_template, request, session

#: Endpoints that are never behind the PIN, as `(method, path)`.
#:
#: **Do not "fix" this by removing an entry.** These are the two ESP32 nodes.
#: Their firmware is flashed by hand, over USB, standing in the tent — it cannot
#: grow a cookie jar or a new header because the server started asking for one.
#:
#: `GET /api/outputs/desired` is the dangerous one. The relay node polls it
#: every 2 seconds, and **de-energises the whole tent after 60 seconds without
#: a successful poll** — a deliberate failsafe, so that a dead Raspberry cannot
#: leave a humidifier running. A 401 there is indistinguishable from a dead
#: Raspberry: putting this endpoint behind the PIN opens every relay in the tent
#: one minute later, including in the middle of the night, with nothing on
#: screen to explain it.
#:
#: The two POSTs are the sensor node. A 401 there is quieter but not harmless:
#: the control loop stops getting readings, declares the sensor stale after 60
#: seconds and goes to failsafe, which also drops the relays.
#:
#: What makes this acceptable rather than a hole: none of the three can switch
#: anything. The first is read-only. The second takes two numbers inside
#: physical ranges and a sensor id matched against a strict pattern before it
#: becomes part of a Redis key. The third takes log lines into a capped ring
#: buffer. The thing worth protecting is the half of the API that moves relays,
#: and that half is covered.
EXEMPT: Tuple[Tuple[str, str], ...] = (
    ("GET", "/api/outputs/desired"),
    ("POST", "/api/sensor/indoor"),
    ("POST", "/api/node/log"),
)

#: Paths the PIN gate lets through so the login page can render and be answered.
_LOGIN_PATHS = ("/api/login", "/api/auth")

#: The manifest and the service worker must load before anyone has typed the PIN:
#: the browser fetches them on its own, and a 401 there means no install and no
#: offline shell. Neither holds a secret.
_PWA_PATHS = ("/manifest.webmanifest", "/sw.js")

#: How long a session lasts before the PIN is asked for again.
PIN_SESSION_DAYS = 30

#: Failed attempts allowed from one address before it has to wait.
MAX_ATTEMPTS = 10
#: How long the wait is, and the window the attempts are counted over.
LOCKOUT_SECONDS = 300

#: Session key holding a fingerprint of the PIN that was accepted. Storing a
#: fingerprint rather than a flag is what makes changing the PIN end every
#: session that was opened with the old one.
_SESSION_KEY = "autocann_pin"


def pin_from_env() -> Optional[str]:
    """
    The configured PIN, or None when there is none.

    An empty or whitespace-only value reads as "not configured" rather than as
    a PIN of zero length: `AUTOCANN_PIN=` in a unit file is somebody turning it
    off, not somebody asking for a lock that opens to anything.
    """
    pin = (os.getenv("AUTOCANN_PIN") or "").strip()
    return pin or None


def fingerprint(pin: str) -> str:
    return hashlib.sha256(f"autocann-pin-v1:{pin}".encode()).hexdigest()


def secret_key(pin: str) -> str:
    """
    The key the session cookie is signed with.

    Derived from the PIN unless `AUTOCANN_SECRET_KEY` says otherwise, so that
    restarting the backend does not sign everybody out — a wall tablet and a
    phone would both land on the login screen after every deploy. Deriving it
    from the PIN gives away nothing: anybody who could forge a cookie from it
    already knows the PIN, which is the whole of the authorisation.
    """
    configured = (os.getenv("AUTOCANN_SECRET_KEY") or "").strip()
    if configured:
        return configured
    return hmac.new(b"autocann-session-v1", pin.encode(), hashlib.sha256).hexdigest()


class AttemptLimiter:
    """
    A short wait after repeated wrong PINs.

    A four-digit PIN is ten thousand guesses, which a script on the same LAN
    gets through in seconds. This does not make the PIN strong; it makes
    guessing it slow enough to be noticed. In memory on purpose: it protects a
    single-process backend on a Raspberry, and a counter that survives restarts
    would be one more thing to clear by hand after fat-fingering it.
    """

    def __init__(self, max_attempts: int = MAX_ATTEMPTS,
                 lockout_seconds: int = LOCKOUT_SECONDS) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._failures: Dict[str, Tuple[int, float]] = {}

    def _now(self) -> float:
        return time.monotonic()

    def retry_after(self, key: str) -> int:
        """Seconds this address must wait, or 0 when it may try now."""
        count, last = self._failures.get(key, (0, 0.0))
        if count < self.max_attempts:
            return 0
        remaining = self.lockout_seconds - (self._now() - last)
        if remaining <= 0:
            self._failures.pop(key, None)
            return 0
        return int(remaining) + 1

    def record_failure(self, key: str) -> None:
        count, last = self._failures.get(key, (0, 0.0))
        if self._now() - last > self.lockout_seconds:
            count = 0
        self._failures[key] = (count + 1, self._now())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def is_exempt(method: str, path: str) -> bool:
    """Whether a request is one the PIN must never stand in front of."""
    if path.startswith("/static/"):
        return True            # the login page's own stylesheet
    if path in _LOGIN_PATHS or path in _PWA_PATHS:
        return True
    return (method.upper(), path) in EXEMPT


def register_auth(app, pin: Optional[str] = None) -> Optional[str]:
    """
    Put the PIN in front of the app, when there is one.

    Returns the PIN that was installed, or None when the app was left open —
    which is the default, and is what every existing installation gets until
    somebody sets the variable.
    """
    if pin is None:
        pin = pin_from_env()

    @app.route("/api/auth", methods=["GET"])
    def auth_status():
        """
        Whether a PIN is configured, and whether this browser has passed it.

        Served even when there is no PIN so the frontend has one answer to
        read instead of inferring the configuration from a 401 it may never see.
        """
        return jsonify({
            "pin_required": pin is not None,
            "authenticated": pin is None or session.get(_SESSION_KEY) == fingerprint(pin),
        })

    if pin is None:
        # No PIN configured: nothing else is installed, so every route behaves
        # exactly as it did before this module existed.
        return None

    app.secret_key = secret_key(pin)
    app.permanent_session_lifetime = PIN_SESSION_DAYS * 24 * 3600
    expected = fingerprint(pin)
    limiter = AttemptLimiter()

    @app.route("/api/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True) or {}
        offered = data.get("pin")

        caller = request.remote_addr or "unknown"
        waiting = limiter.retry_after(caller)
        if waiting:
            return jsonify({
                "error": f"Demasiados intentos. Probá de nuevo en {waiting} s.",
                "retry_after_seconds": waiting,
            }), 429

        # compare_digest, not ==: string comparison returns as soon as two
        # characters differ, and the time it took says how much of the PIN was
        # right.
        if not isinstance(offered, str) or not hmac.compare_digest(offered.strip(), pin):
            limiter.record_failure(caller)
            return jsonify({"error": "PIN incorrecto"}), 401

        limiter.reset(caller)
        session.permanent = True
        session[_SESSION_KEY] = expected
        return jsonify({"success": True, "days": PIN_SESSION_DAYS})

    @app.route("/api/logout", methods=["POST"])
    def logout():
        session.pop(_SESSION_KEY, None)
        return jsonify({"success": True})

    @app.before_request
    def require_pin():
        if is_exempt(request.method, request.path):
            return None
        if session.get(_SESSION_KEY) == expected:
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Hace falta el PIN", "auth_required": True}), 401
        return render_template("login.html"), 401

    return pin
