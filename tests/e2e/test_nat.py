"""Phase 5: NAT mode (BEHIND_NAT=true).

Distinct loopback IPs model a NAT'd deployment:
  PUBLIC_IP  = 192.0.2.1   (advertise-only — TEST-NET-1, no socket bound)
  PRIVATE_IP = 127.0.0.3   (kamailio binds 5060/6060/6061 here)
  SERVICE_IP = 127.0.0.4   (internal service socket on 5060/5061)

INTERNAL_NETWORK = 127.0.0.5/32 → only 127.0.0.5 is treated as LAN; a SIPp
UAC at 127.0.0.2 is therefore a "WAN" peer (direction=in).
"""
from __future__ import annotations

import subprocess
import time


def _wait_uas_ready(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["ss", "-lun", f"sport = :{port}"], capture_output=True, text=True
        )
        if f":{port}" in out.stdout:
            return
        time.sleep(0.2)
    raise TimeoutError(f"UAS not ready on UDP :{port}")


def _reload_dispatcher():
    subprocess.run(
        ["podman", "exec", "e2e-nat-kamailio", "kamcmd", "dispatcher.reload"],
        capture_output=True,
    )
    time.sleep(0.5)


def test_nat_kamailio_socket_layout(nat_stack):
    """In NAT mode kamailio binds PRIVATE on 5060/6060/6061 + SERVICE on 5060/5061
    and external-facing sockets advertise PUBLIC_IP (192.0.2.1)."""
    res = subprocess.run(
        ["podman", "exec", "e2e-nat-kamailio", "kamcmd", "corex.list_sockets"],
        capture_output=True, text=True,
    )
    out = res.stdout
    assert "127.0.0.3:5060" in out, out
    assert "127.0.0.3:6060" in out, out
    assert "127.0.0.3:6061" in out, out
    assert "127.0.0.4:5060" in out, out
    assert "advertise: udp:192.0.2.1:5060" in out, out
    assert "advertise: tls:192.0.2.1:5061" in out, out


def test_nat_call_routed_via_private_socket(nat_sipp):
    """A WAN INVITE (127.0.0.2 → 127.0.0.3:5060) must reach the dispatcher
    target (127.0.0.1:5080). End-to-end success proves NAT-mode listen/route."""
    log_tag = "nat_basic"
    uas = nat_sipp.run_uas_background(
        "uas_answer.xml", local_ip="127.0.0.1", local_port=5080,
        log_tag=f"uas_{log_tag}",
    )
    try:
        _wait_uas_ready(5080)
        _reload_dispatcher()
        result = nat_sipp.run(
            "uac_basic_call.xml",
            target="127.0.0.3:5060",
            local_ip="127.0.0.2",
            local_port=5170,
            calls=1, rate=1, timeout=20,
            service="1000",
            keys={"target_domain": "pbx1.local"},
            log_tag=log_tag,
        )
        assert result.returncode == 0, (
            f"NAT WAN call rc={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    finally:
        uas.terminate()
        try: uas.wait(timeout=5)
        except Exception: uas.kill()


def test_nat_tls_call_routed(nat_sipp, tls_client_cert):
    """Same as above but TLS to 127.0.0.3:5061."""
    cert, key = tls_client_cert
    log_tag = "nat_tls"
    uas = nat_sipp.run_uas_background(
        "uas_answer.xml", local_ip="127.0.0.1", local_port=5080,
        log_tag=f"uas_{log_tag}",
    )
    try:
        _wait_uas_ready(5080)
        _reload_dispatcher()
        result = nat_sipp.run(
            "uac_basic_call.xml",
            target="127.0.0.3:5061",
            local_ip="127.0.0.2",
            local_port=5171,
            calls=1, rate=1, timeout=25,
            service="1000",
            keys={"target_domain": "pbx1.local"},
            log_tag=log_tag,
            transport="tls",
            tls_cert=cert,
            tls_key=key,
        )
        assert result.returncode == 0, (
            f"NAT TLS call rc={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    finally:
        uas.terminate()
        try: uas.wait(timeout=5)
        except Exception: uas.kill()
