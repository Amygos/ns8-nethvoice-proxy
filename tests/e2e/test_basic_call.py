"""Phase 1 e2e: a single basic INVITE/ACK/BYE call through the proxy."""
from __future__ import annotations

import time

import pytest


def _wait_uas_ready(port: int, timeout: float = 5.0) -> None:
    import subprocess
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["ss", "-lun", f"sport = :{port}"], capture_output=True, text=True
        )
        if f":{port}" in out.stdout:
            return
        time.sleep(0.2)
    raise TimeoutError(f"UAS not ready on UDP :{port}")


def test_basic_invite_to_pbx1(sipp):
    """UAC on 127.0.0.2 → Kamailio @127.0.0.1:5060 → UAS @127.0.0.1:5080."""
    uas = sipp.run_uas_background(
        "uas_answer.xml", local_ip="127.0.0.1", local_port=5080
    )
    try:
        _wait_uas_ready(5080)
        result = sipp.run(
            "uac_basic_call.xml",
            target="127.0.0.1:5060",
            local_ip="127.0.0.2",
            local_port=5070,
            calls=1,
            rate=1,
            timeout=20,
        )
        # SIPp returns 0 on success, 1 if at least one call failed.
        assert result.returncode == 0, (
            f"UAC failed (rc={result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )
    finally:
        uas.terminate()
        try:
            uas.wait(timeout=5)
        except Exception:
            uas.kill()
