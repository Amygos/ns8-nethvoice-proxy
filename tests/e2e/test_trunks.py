"""Phase 3 e2e: trunk routing.

Trunk routes (dialplan dpid=2) match against the full Request-URI ($ru) and
are used as a fallback when the domain match (dpid=1) fails. Models an
external carrier delivering DIDs that should be routed to a specific PBX.
"""
from __future__ import annotations


def test_inbound_trunk_call_to_pbx1(sipp, uas_factory):
    """sip:9001@e2e.local matches trunk pattern ^sip:9[0-9]+@e2e\\.local$ → pbx1."""
    uas_factory(5080)
    result = sipp.run(
        "uac_basic_call.xml",
        target="127.0.0.1:5060",
        local_ip="127.0.0.2",
        local_port=5075,
        calls=1,
        rate=1,
        timeout=20,
        service="9001",
        keys={"target_domain": "e2e.local"},
        log_tag="trunk_inbound",
    )
    assert result.returncode == 0, (
        f"Trunk call failed rc={result.returncode}\n{result.stdout}\n{result.stderr}"
    )


def test_trunk_does_not_swallow_unmatched_did(sipp):
    """Number 7000 does NOT match 9xxx trunk pattern → Kamailio replies 404."""
    result = sipp.run(
        "uac_expect_reject.xml",
        target="127.0.0.1:5060",
        local_ip="127.0.0.2",
        local_port=5076,
        calls=1,
        rate=1,
        timeout=10,
        service="7000",
        keys={"target_domain": "e2e.local"},
        log_tag="trunk_unmatched",
    )
    assert result.returncode == 0, (
        f"Expected 404 for 7000@e2e.local, got rc={result.returncode}\n"
        f"{result.stdout}\n{result.stderr}"
    )
