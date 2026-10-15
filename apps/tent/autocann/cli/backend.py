from __future__ import annotations

import os

from autocann.web.app import app


def main() -> None:
    host = os.getenv("AUTOCANN_WEB_HOST", "0.0.0.0")
    # PORT is the conventional name most tooling and hosts set; AUTOCANN_WEB_PORT
    # wins when both are present. Port 5000 is taken by AirPlay Receiver on macOS,
    # so being able to move it matters for local development.
    port = int(os.getenv("AUTOCANN_WEB_PORT") or os.getenv("PORT") or "5000")
    debug = os.getenv("AUTOCANN_WEB_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()

