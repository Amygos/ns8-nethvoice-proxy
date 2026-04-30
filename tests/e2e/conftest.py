"""Pytest fixtures for ns8-nethvoice-proxy e2e tests.

Brings up the full module stack (postgres + redis + rtpengine + kamailio) via
podman-compose, seeds test routes, and provides a SIPp helper for tests.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = E2E_DIR.parent.parent
SIPP_VERSION = "3.7.7"
SIPP_BIN = E2E_DIR / "sipp"
COMPOSE_FILE = E2E_DIR / "compose.yml"
ENV_FILE = E2E_DIR / "env.template"


def _run(cmd, **kw):
    print(f"$ {' '.join(map(str, cmd))}", flush=True)
    kw.setdefault("check", False)
    return subprocess.run(cmd, **kw)


def _wait_tcp(host: str, port: int, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"TCP {host}:{port} not reachable in {timeout}s")


def _wait_udp_listen(port: int, timeout: float = 60.0) -> None:
    """Wait until something is bound on UDP `port` on the host (loopback)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["ss", "-lun", f"sport = :{port}"], capture_output=True, text=True
        )
        if f":{port}" in out.stdout:
            return
        time.sleep(0.5)
    raise TimeoutError(f"UDP :{port} not listening in {timeout}s")


def _ensure_sipp() -> Path:
    if SIPP_BIN.exists() and os.access(SIPP_BIN, os.X_OK):
        return SIPP_BIN
    url = (
        f"https://github.com/SIPp/sipp/releases/download/v{SIPP_VERSION}/"
        f"sipp-{SIPP_VERSION}.tar.gz"
    )
    # Easier: use system sipp if available
    sys_sipp = shutil.which("sipp")
    if sys_sipp:
        SIPP_BIN.symlink_to(sys_sipp)
        return SIPP_BIN
    raise RuntimeError(
        f"SIPp binary not found at {SIPP_BIN} and not in PATH. "
        f"Install sipp or place a static binary at {SIPP_BIN}. "
        f"See {url}"
    )


def _ensure_tls_cert() -> None:
    cert_dir = E2E_DIR / "kamailio-cert"
    files_dir = cert_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    # Remove any stale PEM files left in the root (Kamailio scans the dir as
    # config files and any stray .pem will cause a syntax error).
    for stale in cert_dir.glob("*.pem"):
        stale.unlink()
    cert = files_dir / "cert.pem"
    key = files_dir / "key.pem"
    cfg = cert_dir / "tls.cfg"
    if not cert.exists() or not key.exists():
        _run(
            [
                "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                "-keyout", str(key), "-out", str(cert), "-days", "365",
                "-subj", "/CN=e2e.local",
            ],
            check=True,
        )
    cfg.write_text(
        "[server:default]\n"
        "method = TLSv1.2+\n"
        "private_key = /etc/kamailio/tls/files/key.pem\n"
        "certificate = /etc/kamailio/tls/files/cert.pem\n"
        "verify_certificate = no\n"
        "require_certificate = no\n"
    )


def _compose(*args: str) -> subprocess.CompletedProcess:
    cmd = [
        "podman-compose",
        "--env-file", str(ENV_FILE),
        "-f", str(COMPOSE_FILE),
        *args,
    ]
    return _run(cmd, cwd=str(E2E_DIR))


def _kamailio_alive() -> bool:
    res = subprocess.run(
        ["podman", "inspect", "-f", "{{.State.Status}}", "e2e-kamailio"],
        capture_output=True, text=True,
    )
    return res.stdout.strip() == "running"


def _kamailio_ready(timeout: float = 90.0) -> None:
    """Wait for kamailio on UDP/5060.

    Kamailio races postgres init in podman-compose (depends_on with healthy
    only checks `pg_isready`, not the migration scripts).  Detect the crash
    and restart up to a few times.
    """
    deadline = time.time() + timeout
    restarts = 0
    while time.time() < deadline:
        out = subprocess.run(
            ["ss", "-lun", "sport = :5060"], capture_output=True, text=True
        )
        if ":5060" in out.stdout:
            time.sleep(2.0)  # let dialplan/dispatcher load
            return
        if not _kamailio_alive() and restarts < 5:
            print(f"kamailio crashed, restarting (attempt {restarts + 1})", flush=True)
            subprocess.run(["podman", "logs", "--tail=10", "e2e-kamailio"])
            subprocess.run(["podman", "start", "e2e-kamailio"], check=False)
            restarts += 1
            time.sleep(3.0)
        time.sleep(0.5)
    raise TimeoutError(f"kamailio not listening on UDP/5060 in {timeout}s")


@pytest.fixture(scope="session")
def stack():
    _ensure_sipp()
    _ensure_tls_cert()
    # Clean previous run if any
    _compose("down", "-v")
    rc = _compose("up", "-d").returncode
    if rc != 0:
        _compose("logs")
        raise RuntimeError("podman-compose up failed")
    try:
        _wait_tcp("127.0.0.1", 5432, timeout=60)
        _wait_tcp("127.0.0.1", 6379, timeout=60)
        _kamailio_ready(timeout=90)
        # Force dialplan + dispatcher reload (in case kamailio started before our SQL ran)
        _kamcmd("dialplan.reload")
        _kamcmd("dispatcher.reload")
        time.sleep(1.0)
        yield
    finally:
        # Always show logs on failure to aid debugging
        if os.environ.get("E2E_KEEP_LOGS"):
            _compose("logs", "kamailio")
        if not os.environ.get("E2E_NO_TEARDOWN"):
            _compose("down", "-v")


def _kamcmd(*args: str) -> str:
    res = subprocess.run(
        ["podman", "exec", "e2e-kamailio", "kamcmd", *args],
        capture_output=True, text=True,
    )
    print(f"$ kamcmd {' '.join(args)} → rc={res.returncode}\n{res.stdout}{res.stderr}")
    return res.stdout


class SippRunner:
    """Run a SIPp scenario and assert on its result."""

    def __init__(self, sipp_bin: Path, scenarios_dir: Path):
        self.sipp = sipp_bin
        self.scenarios = scenarios_dir

    def run(
        self,
        scenario: str,
        *,
        target: str,
        local_ip: str,
        local_port: int | None = None,
        calls: int = 1,
        rate: int = 1,
        timeout: int = 30,
        extra: list[str] | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = [
            str(self.sipp),
            target,
            "-sf", str(self.scenarios / scenario),
            "-i", local_ip,
            "-m", str(calls),
            "-r", str(rate),
            "-trace_err",
            "-trace_msg",
            "-trace_screen",
            "-message_file", str(E2E_DIR / f"{Path(scenario).stem}.msg.log"),
            "-error_file", str(E2E_DIR / f"{Path(scenario).stem}.err.log"),
            "-screen_file", str(E2E_DIR / f"{Path(scenario).stem}.screen.log"),
        ]
        if local_port:
            cmd += ["-p", str(local_port)]
        if extra:
            cmd += extra
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def run_uas_background(
        self,
        scenario: str,
        *,
        local_ip: str,
        local_port: int,
    ) -> subprocess.Popen:
        log_prefix = E2E_DIR / f"{Path(scenario).stem}_uas"
        cmd = [
            str(self.sipp),
            "-sf", str(self.scenarios / scenario),
            "-i", local_ip,
            "-p", str(local_port),
            "-trace_err", "-trace_msg", "-trace_screen",
            "-message_file", f"{log_prefix}.msg.log",
            "-error_file", f"{log_prefix}.err.log",
            "-screen_file", f"{log_prefix}.screen.log",
        ]
        print(f"$ (bg) {' '.join(cmd)}", flush=True)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture(scope="session")
def sipp(stack) -> SippRunner:
    return SippRunner(_ensure_sipp(), E2E_DIR / "sipp_scenarios")
