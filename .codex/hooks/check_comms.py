#!/usr/bin/env python3
"""Require an end-of-turn coordination pass against the shared COMMS log."""

from __future__ import annotations

import json
import sys
from typing import Any


def main() -> int:
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"Invalid Stop hook input: {exc}", file=sys.stderr)
        return 1

    if payload.get("stop_hook_active"):
        print(json.dumps({"continue": True}))
        return 0

    reason = (
        "End-of-turn coordination check required. Before finishing, read the "
        "latest COMMS.md from the shared PanGalactic/botbeef repository (not "
        "only a potentially stale working-tree copy), follow any new requests "
        "or file claims, and append a short COMMS entry if this turn changed "
        "your status, touched-file claim, needs, or blockers. Preserve the "
        "append-only format and do not include secrets. Then finish the turn."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
