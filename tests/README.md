# Tests

Two complementary test layers cover ns8-nethvoice-proxy:

| Layer | Location | Driver | Target |
| --- | --- | --- | --- |
| Local end-to-end (SIP / RTP) | `tests/e2e/` | `pytest` + `podman-compose` | host loopback |
| Real-node integration | `tests/*.robot` | Robot Framework + SSH | live NS8 node |

## Local end-to-end (`tests/e2e/`)

Brings up the full stack (Kamailio + rtpengine + Postgres + Redis) with
`podman-compose` on the host's loopback and drives it with SIPp. Covers
basic calls, multi-PBX routing, trunks, TLS, NAT mode and rtpengine
session/SDP rewrite. See [`tests/e2e/README.md`](e2e/README.md).

```bash
make -C tests/sipp-image build           # ghcr.io/nethesis/sipp:dev
cd tests/e2e
E2E_SIPP_IMAGE=ghcr.io/nethesis/sipp:dev pytest -v
```

## Real-node Robot Framework

The `00_*.robot` … `99_*.robot` suites are executed by NS8's standard
test workflow against a freshly-installed module on a real node. They
exercise the public API (`api-cli`) and validate the configured stack.

The new `20_sipp_smoke.robot` suite exercises the published SIPp helper
image (`ghcr.io/nethesis/sipp`) on the node:

* pulls the image
* uploads `tests/sipp_scenarios/` (a symlink into `tests/e2e/sipp_scenarios/`)
* sends a basic INVITE to verify SIPp ↔ kamailio reachability

The shared SIPp keywords live in [`tests/api.resource`](api.resource):
`Pull SIPp image on node`, `Upload SIPp scenarios`,
`Start SIPp UAS on node`, `Stop SIPp UAS`, `Run SIPp UAC on node`.

### Running locally against a node

```bash
robot --variable NODE_ADDR:1.2.3.4 --variable IMAGE_URL:ghcr.io/nethesis/nethvoice-proxy:main tests/
```

## SIPp helper image

`tests/sipp-image/` builds a minimal Debian-based SIPp with SSL + PCAP +
SCTP. The image is built and pushed by `build-images.sh` alongside the
module images and tagged `ghcr.io/nethesis/sipp:<branch>` (and `latest`
on `main`). Both the local pytest harness and the Robot suite consume
exactly the same image so behaviour is consistent across environments.
