"""Phase 2 e2e: multiple PBXs, one PBX per domain.

Scope (per project): no load balancing, no failover, exactly one dispatcher
destination per setid.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def _call(sipp, *, service, domain, local_port, log_tag):
    return sipp.run(
        "uac_basic_call.xml",
        target="127.0.0.1:5060",
        local_ip="127.0.0.2",
        local_port=local_port,
        calls=1,
        rate=1,
        timeout=20,
        service=service,
        keys={"target_domain": domain},
        log_tag=log_tag,
    )


def test_call_to_pbx2(sipp, uas_factory):
    """Domain pbx2.local must route to UAS on 127.0.0.1:5082."""
    uas_factory(5082)
    result = _call(sipp, service="2000", domain="pbx2.local",
                   local_port=5071, log_tag="multi_pbx2")
    assert result.returncode == 0, (
        f"UAC to pbx2 failed rc={result.returncode}\n{result.stdout}\n{result.stderr}"
    )


def test_concurrent_calls_two_pbx(sipp, uas_factory):
    """Parallel calls to pbx1 and pbx2 — each must succeed independently."""
    uas_factory(5080)
    uas_factory(5082)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(
            _call, sipp, service="1000", domain="pbx1.local",
            local_port=5072, log_tag="concurrent_pbx1",
        )
        f2 = pool.submit(
            _call, sipp, service="2000", domain="pbx2.local",
            local_port=5073, log_tag="concurrent_pbx2",
        )
        r1 = f1.result()
        r2 = f2.result()

    assert r1.returncode == 0, f"pbx1 failed: {r1.stdout}\n{r1.stderr}"
    assert r2.returncode == 0, f"pbx2 failed: {r2.stdout}\n{r2.stderr}"


def test_unknown_domain_rejected(sipp):
    """No dialplan entry for 1000@e2e.local → Kamailio replies 404 Not Found.

    e2e.local is a known domain (myself check passes) but neither the domain
    dialplan (dpid=1) nor the trunk pattern (dpid=2 / 9xxx) match.
    """
    result = sipp.run(
        "uac_expect_reject.xml",
        target="127.0.0.1:5060",
        local_ip="127.0.0.2",
        local_port=5074,
        calls=1,
        rate=1,
        timeout=10,
        service="1000",
        keys={"target_domain": "e2e.local"},
        log_tag="unknown_domain",
    )
    assert result.returncode == 0, (
        f"Expected 404 for 1000@e2e.local, got rc={result.returncode}\n"
        f"{result.stdout}\n{result.stderr}"
    )
