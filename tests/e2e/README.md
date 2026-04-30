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
env.template            non-secret defaults; override via env
postgres-init/          SQL run after migrations: seed test routes
kamailio-cert/          self-signed cert (auto-generated, gitignored)
sipp_scenarios/         SIPp XML scenarios
conftest.py             pytest fixtures (stack lifecycle + SIPp helper)
test_basic_call.py      iteration 1: single INVITE/ACK/BYE
```
