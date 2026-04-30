# E2E local tests

Local end-to-end SIP tests for **ns8-nethvoice-proxy** that bring up the full
production stack (Kamailio + rtpengine + PostgreSQL + Redis) via `podman-compose`
and drive it with [SIPp](https://github.com/SIPp/sipp) — no NS8 node required.

## Why loopback aliases?

`modules/kamailio/config/kamailio.cfg` classifies any traffic from `127.0.0.1`
or from `INTERNAL_NETWORK` as **outbound** and skips dispatcher routing for it.
To simulate an *external* SIP client locally, the UAC must therefore use a
non-`127.0.0.1` source IP. We use `127.0.0.2` (the entire `127.0.0.0/8` range
is routed to `lo` on Linux by default — no setup needed).

## Topology

```
┌──────────────────────────────────────────────────────────────┐
│ Host (loopback)                                              │
│  127.0.0.2:5070  SIPp UAC  (test client)                     │
│  127.0.0.1:5060  Kamailio  (container, host net)             │
│  127.0.0.1:5080  SIPp UAS  (PBX simulator on host)           │
│  127.0.0.1:5432  PostgreSQL (container)                      │
│  127.0.0.1:6379  Redis      (container)                      │
│  127.0.0.1:19999 rtpengine  (container, host net)            │
└──────────────────────────────────────────────────────────────┘
```

## Prerequisites

- `podman` ≥ 4.x, `podman-compose`
- `python` ≥ 3.8 with `pytest`
- `sipp` — either in `PATH`, or place a static binary at `tests/e2e/sipp`
- `openssl` (for self-signed TLS cert generation)

## Run

```bash
cd tests/e2e
./run.sh                # = pytest -q
```

To use a specific image tag:

```bash
IMAGE_TAG=1.6.2-testing.3 ./run.sh
```

To keep container logs after a failed run:

```bash
E2E_KEEP_LOGS=1 ./run.sh -x
```

## Layout

```
compose.yml             podman-compose definition (host network)
compose.nat.yml         NAT-mode variant (advertise IP ≠ local IP)
env.template            non-secret defaults; override via env
env.nat.template        NAT-mode env (PUBLIC_IP=192.0.2.1, BEHIND_NAT=true)
postgres-init/          SQL run after migrations: seed test routes
kamailio-cert/          self-signed cert (auto-generated, gitignored)
sipp_scenarios/         SIPp XML scenarios
conftest.py             pytest fixtures (stack lifecycle + SIPp helper)
test_basic_call.py      single INVITE/ACK/BYE
test_multi_pbx.py       two PBX domains, concurrent calls, unknown domain
test_trunks.py          inbound trunk routing
test_tls_call.py        TLS signaling on port 5061
test_nat.py             NAT advertise sockets + UDP/TLS calls in NAT mode
test_rtpengine.py       rtpengine session lifecycle + SDP rewrite
```

## SIPp helper image

The pytest harness can drive SIPp from a published OCI image
(`ghcr.io/nethesis/sipp:<tag>`) or from a host binary. Build it locally:

```bash
make -C ../sipp-image build           # produces ghcr.io/nethesis/sipp:dev
E2E_SIPP_IMAGE=ghcr.io/nethesis/sipp:dev pytest -v
```

The same image is published from this repo's CI (see
`build-images.sh` and `.github/workflows/publish-images.yml`) and is the
one consumed by the Robot Framework smoke suite on real NS8 nodes
(`tests/20_sipp_smoke.robot`).

## NAT-mode tests

`test_nat.py` brings up a separate compose project (`e2e_nat`) where
Kamailio is configured with `BEHIND_NAT=true`, `PUBLIC_IP=192.0.2.1`,
`PRIVATE_IP=127.0.0.3`, and an INTERNAL_NETWORK that classifies the
default UAS as internal. Tests assert:

* `corex.list_sockets` advertises the public IP on UDP/5060 and TLS/5061
* a UDP call routed via the private socket succeeds
* a TLS call from the WAN side succeeds

The default and NAT stacks share host ports 5432/6379 (PostgreSQL/Redis
images do not honour port-override env vars), so they are mutually
exclusive on the host network. `pytest_collection_modifyitems` orders
`test_nat.py` last and the fixtures tear down the other stack before
starting their own.

## rtpengine tests

`test_rtpengine.py` runs a 3 s call and asserts that:

* `rtpengine-ctl list numsessions` reports ≥1 active session during the dialog
* the SDP relayed to the UAS has had its `c=`/`m=audio` rewritten to
  rtpengine's address and a port in the configured range (30000-30100)

> **Note** — kamailio in this codebase does not call `rtpengine_delete`
> on BYE; sessions are reaped by rtpengine's `silent-timeout`. The tests
> therefore do not assert post-call teardown.
