from __future__ import annotations

import os

from autocann.web.app import app


def main() -> None:
    host = os.getenv("AUTOCANN_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("AUTOCANN_WEB_PORT", "5000"))
    debug = os.getenv("AUTOCANN_WEB_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()

