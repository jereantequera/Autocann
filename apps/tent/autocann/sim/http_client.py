"""
The simulator's HTTP client: JSON in, JSON out, stdlib only.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Mapping, Optional


def http_json(method: str, url: str, body: Optional[Mapping[str, Any]] = None, timeout: float = 3.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "null")
