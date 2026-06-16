"""머신 식별자 — vault 네임스페이스/라벨 공용.

checkpoints ingest와 exporter front-matter가 동일 규칙을 공유한다(DRY).
"""
from __future__ import annotations

import os
import re
import socket


def machine_id() -> str:
    """머신 식별자. `SESSION_ARCHIVE_MACHINE` env 우선, 없으면 hostname.

    경로/키 안전 문자만 남기고, 비면 'unknown'.
    """
    name = os.environ.get("SESSION_ARCHIVE_MACHINE") or socket.gethostname()
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-") or "unknown"
