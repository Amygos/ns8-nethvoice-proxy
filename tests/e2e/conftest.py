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
NAT_COMPOSE_FILE = E2E_DIR / "compose.nat.yml"
NAT_ENV_FILE = E2E_DIR / "env.nat.template"
NAT_PROJECT = "e2e_nat"


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
    """Locate a SIPp binary or container image.

    Priority:
      1. ``E2E_SIPP_IMAGE`` env (use container — required for TLS/PCAP).
      2. local ``./sipp`` symlink/binary.
      3. system ``sipp`` in PATH (linked to ./sipp).

    Returns the path to a SIPp binary, or ``Path("@image:<ref>")`` to signal
    that callers should run via ``podman run``.
    """
    img = os.environ.get("E2E_SIPP_IMAGE")
    if img:
        return Path(f"@image:{img}")
    if SIPP_BIN.exists() and os.access(SIPP_BIN, os.X_OK):
        return SIPP_BIN
    sys_sipp = shutil.which("sipp")
    if sys_sipp:
        SIPP_BIN.symlink_to(sys_sipp)
        return SIPP_BIN
    raise RuntimeError(
        f"SIPp binary not found at {SIPP_BIN} and not in PATH. "
        f"Either build the container image (cd tests/sipp-image && make build) "
        f"and set E2E_SIPP_IMAGE=localhost/sipp:dev, or install sipp."
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
    # NAT stack uses the same host ports — make sure it's gone first.
    subprocess.run(
        ["podman-compose", "--env-file", str(NAT_ENV_FILE),
         "-p", NAT_PROJECT, "-f", str(NAT_COMPOSE_FILE), "down", "-v"],
        cwd=str(E2E_DIR), capture_output=True,
    )
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
    """Run a SIPp scenario and assert on its result.

    `sipp_bin` may be either a local executable path or a sentinel of the
    form ``@image:<ref>`` returned by :func:`_ensure_sipp`. In the latter
    case commands are wrapped in ``podman run --rm --network host`` and the
    scenarios + cert directories are mounted read-only.
    """

    def __init__(self, sipp_bin: Path, scenarios_dir: Path):
        self.sipp = sipp_bin
        self.scenarios = scenarios_dir
        s = str(sipp_bin)
        if s.startswith("@image:"):
            self.image = s[len("@image:") :]
        else:
            self.image = None

    def _wrap_cmd(self, args: list[str], *, container_name: str | None = None) -> list[str]:
        if not self.image:
            return args
        cert_dir = E2E_DIR / "kamailio-cert" / "files"
        rewritten: list[str] = []
        for a in args:
            if a.startswith(str(self.scenarios)):
                rewritten.append(a.replace(str(self.scenarios), "/scn"))
            elif a.startswith(str(cert_dir)):
                rewritten.append(a.replace(str(cert_dir), "/tls"))
            elif a.startswith(str(E2E_DIR)):
                rewritten.append(a.replace(str(E2E_DIR), "/logs"))
            else:
                rewritten.append(a)
        wrapper = [
            "podman", "run", "--rm", "--network", "host",
            "--userns=keep-id",
            # lowercase :z = shared SELinux relabel (multi-container).  The
            # compose stack already uses :Z which assigns a unique MCS pair,
            # so we can't relabel exclusively here without locking the dirs
            # out of the other containers.
            "-v", f"{self.scenarios}:/scn:ro,z",
            "-v", f"{cert_dir}:/tls:ro,z",
            "-v", f"{E2E_DIR}:/logs:z",
        ]
        if container_name:
            wrapper += ["--name", container_name]
        wrapper += [self.image, *rewritten[1:]]
        return wrapper

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
        service: str | None = None,
        keys: dict[str, str] | None = None,
        log_tag: str | None = None,
        extra: list[str] | None = None,
        transport: str = "udp",
        tls_cert: Path | None = None,
        tls_key: Path | None = None,
    ) -> subprocess.CompletedProcess:
        tag = log_tag or Path(scenario).stem
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
            "-message_file", str(E2E_DIR / f"{tag}.msg.log"),
            "-error_file", str(E2E_DIR / f"{tag}.err.log"),
            "-screen_file", str(E2E_DIR / f"{tag}.screen.log"),
        ]
        if local_port:
            cmd += ["-p", str(local_port)]
        if service is not None:
            cmd += ["-s", service]
        for k, v in (keys or {}).items():
            cmd += ["-key", k, v]
        # Transport selection: u1 = UDP one-socket, t1 = TCP, l1 = TLS (one socket)
        # Default UDP requires no flag.
        if transport == "tls":
            cmd += ["-t", "l1"]
            if tls_cert:
                cmd += ["-tls_cert", str(tls_cert)]
            if tls_key:
                cmd += ["-tls_key", str(tls_key)]
        elif transport == "tcp":
            cmd += ["-t", "t1"]
        if extra:
            cmd += extra
        cmd = self._wrap_cmd(cmd)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def run_uas_background(
        self,
        scenario: str,
        *,
        local_ip: str,
        local_port: int,
        log_tag: str | None = None,
    ) -> "_UasHandle":
        tag = log_tag or f"{Path(scenario).stem}_{local_port}"
        log_prefix = E2E_DIR / f"{tag}_uas"
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
        container_name = f"e2e-uas-{local_port}" if self.image else None
        if container_name:
            # Make sure no leftover container claims the port.
            subprocess.run(["podman", "rm", "-f", container_name],
                           capture_output=True)
        cmd = self._wrap_cmd(cmd, container_name=container_name)
        print(f"$ (bg) {' '.join(cmd)}", flush=True)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _UasHandle(proc, container_name)


class _UasHandle:
    """Wraps a SIPp UAS process; stops the container too when applicable."""
    def __init__(self, proc: subprocess.Popen, container_name: str | None):
        self.proc = proc
        self.container_name = container_name

    def terminate(self):
        if self.container_name:
            subprocess.run(["podman", "stop", "-t", "2", self.container_name],
                           capture_output=True)
        self.proc.terminate()

    def wait(self, timeout=None):
        return self.proc.wait(timeout=timeout)

    def kill(self):
        if self.container_name:
            subprocess.run(["podman", "rm", "-f", self.container_name],
                           capture_output=True)
        self.proc.kill()


@pytest.fixture(scope="session")
def sipp(stack) -> SippRunner:
    return SippRunner(_ensure_sipp(), E2E_DIR / "sipp_scenarios")


@pytest.fixture(scope="session")
def tls_client_cert():
    """Path to the (cert, key) pair used by SIPp clients (reuse server's).

    When SIPp runs in the container the harness rewrites these to /tls/...
    """
    cert = E2E_DIR / "kamailio-cert" / "files" / "cert.pem"
    key = E2E_DIR / "kamailio-cert" / "files" / "key.pem"
    if not cert.exists() or not key.exists():
        _ensure_tls_cert()
    return cert, key


def wait_udp_listen(port: int, timeout: float = 5.0) -> None:
    """Public helper for tests: wait until UDP `port` is bound on host."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["ss", "-lun", f"sport = :{port}"], capture_output=True, text=True
        )
        if f":{port}" in out.stdout:
            return
        time.sleep(0.1)
    raise TimeoutError(f"UDP :{port} not listening in {timeout}s")


@pytest.fixture
def uas_factory(sipp):
    """Spawn one or more SIPp UAS instances; clean up automatically."""
    started: list = []

    def _start(local_port: int, local_ip: str = "127.0.0.1"):
        # Wait for the port to actually be free first (previous test may have
        # just torn down a container holding it).
        for _ in range(20):
            out = subprocess.run(
                ["ss", "-lun", f"sport = :{local_port}"],
                capture_output=True, text=True,
            )
            if f":{local_port}" not in out.stdout:
                break
            time.sleep(0.25)
        proc = sipp.run_uas_background(
            "uas_answer.xml",
            local_ip=local_ip,
            local_port=local_port,
            log_tag=f"uas_{local_port}",
        )
        started.append(proc)
        wait_udp_listen(local_port, timeout=8)
        return proc

    yield _start

    for p in started:
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
        # Wait until the OS releases the port so the next test can rebind.
        if hasattr(p, "container_name") and p.container_name:
            for _ in range(20):
                out = subprocess.run(
                    ["podman", "ps", "-aq", "--filter",
                     f"name={p.container_name}"],
                    capture_output=True, text=True,
                )
                if not out.stdout.strip():
                    break
                time.sleep(0.25)


def _nat_compose(*args: str) -> subprocess.CompletedProcess:
    cmd = [
        "podman-compose",
        "--env-file", str(NAT_ENV_FILE),
        "-p", NAT_PROJECT,
        "-f", str(NAT_COMPOSE_FILE),
        *args,
    ]
    return _run(cmd, cwd=str(E2E_DIR))


def _nat_kamailio_alive() -> bool:
    res = subprocess.run(
        ["podman", "inspect", "-f", "{{.State.Status}}", "e2e-nat-kamailio"],
        capture_output=True, text=True,
    )
    return res.stdout.strip() == "running"


def _nat_kamailio_ready(timeout: float = 90.0) -> None:
    """Wait for NAT-stack kamailio on PRIVATE_IP:5060."""
    deadline = time.time() + timeout
    restarts = 0
    while time.time() < deadline:
        out = subprocess.run(
            ["ss", "-lun", "sport = :5060"], capture_output=True, text=True
        )
        if "127.0.0.3:5060" in out.stdout:
            time.sleep(2.0)
            return
        if not _nat_kamailio_alive() and restarts < 5:
            print(f"nat-kamailio crashed, restarting (attempt {restarts + 1})", flush=True)
            subprocess.run(["podman", "logs", "--tail=10", "e2e-nat-kamailio"])
            subprocess.run(["podman", "start", "e2e-nat-kamailio"], check=False)
            restarts += 1
            time.sleep(3.0)
        time.sleep(0.5)
    raise TimeoutError(f"nat-kamailio not listening on 127.0.0.3:5060 in {timeout}s")


@pytest.fixture(scope="session")
def nat_stack():
    """Bring up the BEHIND_NAT=true variant in a separate compose project."""
    _ensure_sipp()
    _ensure_tls_cert()
    # Default stack uses the same host ports — make sure it's gone first.
    _compose("down", "-v")
    _nat_compose("down", "-v")
    rc = _nat_compose("up", "-d").returncode
    if rc != 0:
        _nat_compose("logs")
        raise RuntimeError("podman-compose nat up failed")
    try:
        _wait_tcp("127.0.0.1", 5432, timeout=60)
        _wait_tcp("127.0.0.1", 6379, timeout=60)
        _nat_kamailio_ready(timeout=90)
        # Reload routes — same dialplan/dispatcher as the default stack.
        subprocess.run(
            ["podman", "exec", "e2e-nat-kamailio", "kamcmd", "dialplan.reload"],
            capture_output=True,
        )
        subprocess.run(
            ["podman", "exec", "e2e-nat-kamailio", "kamcmd", "dispatcher.reload"],
            capture_output=True,
        )
        time.sleep(1.0)
        yield
    finally:
        if os.environ.get("E2E_KEEP_LOGS"):
            _nat_compose("logs", "kamailio")
        if not os.environ.get("E2E_NO_TEARDOWN"):
            _nat_compose("down", "-v")


@pytest.fixture
def nat_sipp(nat_stack) -> SippRunner:
    return SippRunner(_ensure_sipp(), E2E_DIR / "sipp_scenarios")


def pytest_collection_modifyitems(config, items):
    """Ensure default-stack tests run before NAT-stack tests so each session
    only brings up one stack at a time (both share host ports 5060/5432/...).
    """
    items.sort(key=lambda it: 1 if "test_nat.py" in str(it.fspath) else 0)
