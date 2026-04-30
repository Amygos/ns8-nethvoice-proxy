"""Phase 6: rtpengine media bridging.

We don't directly assert on RTP packet counts (the basic UAC doesn't play
audio); instead we verify two things during a single call:
  - rtpengine reports at least one active session, AND
  - rtpengine rewrites the SDP c= and m= so the UAS sees rtpengine's
    address/port instead of the UAC's.

Note: this codebase's kamailio config does not call rtpengine_delete on BYE
(sessions expire via rtpengine's silent-timeout), so we do not assert
post-call teardown.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path


def _rtp_sessions(container: str = "e2e-rtpengine") -> int:
    res = subprocess.run(
        ["podman", "exec", container, "rtpengine-ctl", "list", "numsessions"],
        capture_output=True, text=True,
    )
    for line in res.stdout.splitlines():
        if line.startswith("Current sessions total:"):
            return int(line.split(":")[1].strip())
    return -1


def test_rtpengine_session_and_sdp_rewrite(sipp, uas_factory):
    """Single combined check: during a 3s call, rtpengine must hold ≥1
    active session, AND the UAS must receive an INVITE whose SDP has been
    rewritten to use rtpengine's media address/port."""
    uas_factory(5080)

    peak = {"value": 0}
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            n = _rtp_sessions()
            if n > peak["value"]:
                peak["value"] = n
            time.sleep(0.1)

    t = threading.Thread(target=poller, daemon=True)
    t.start()
    try:
        result = sipp.run(
            "uac_basic_call_long.xml",
            target="127.0.0.1:5060",
            local_ip="127.0.0.2",
            local_port=5070,
            calls=1, rate=1, timeout=20,
            service="1000",
            keys={"target_domain": "pbx1.local"},
            log_tag="rtp",
        )
    finally:
        stop.set()
        t.join(timeout=2)

    assert result.returncode == 0, (
        f"UAC failed rc={result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    assert peak["value"] >= 1, (
        f"rtpengine reported no active session during the call "
        f"(peak={peak['value']})"
    )

    log = (Path(__file__).resolve().parent / "uas_5080_uas.msg.log").read_text()
    c_lines = [l for l in log.splitlines() if l.startswith("c=IN IP4")]
    m_lines = [l for l in log.splitlines() if l.startswith("m=audio")]
    assert c_lines, f"no SDP c= line in UAS log:\n{log}"
    assert "127.0.0.2" not in " ".join(c_lines), (
        f"c= still references UAC IP — rtpengine didn't rewrite SDP: {c_lines}"
    )
    port = int(re.match(r"m=audio (\d+)", m_lines[0]).group(1))
    assert 30000 <= port <= 30100, f"media port {port} outside rtpengine range"
