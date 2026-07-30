"""The notify helper: silent when unconfigured, fires SMTP when configured.

The local-dev case is the no-op (NOTIFY_TO unset); the configured case is
exercised against a stubbed SMTP so the actual connect/login/send path is
checked without touching the network.

The stub proves the message is built and the calls are made in order, but it
cannot prove the deployed configuration works: it never speaks SMTP, never does
STARTTLS, and never runs through ``build_dist.py``'s failure handler. The last
test here closes that gap against a throwaway SMTP server on localhost -- a real
socket, real STARTTLS, real AUTH LOGIN, and no mail leaving the machine.
"""
from __future__ import annotations

import email
import os
import smtplib
import socket
import ssl
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.notify import send_failure

ROOT = Path(__file__).parent.parent


def test_unconfigured_is_silent(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFY_TO", raising=False)
    # No SMTP calls happen -- if any did, the lack of SMTP_HOST would raise.
    send_failure(RuntimeError("boom"), "scripts/build_dist.py --no-ingest")


def test_configured_sends(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_TO", "ops@example.org")
    monkeypatch.setenv("NOTIFY_FROM", "pipeline@example.org")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_USERNAME", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")

    sent: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, host, port):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, pw):
            self.user, self.pw = user, pw

        def send_message(self, msg):
            sent.append(msg)

    with patch.object(smtplib, "SMTP", FakeSMTP):
        send_failure(ValueError("bad row"), "scripts/build_dist.py")

    assert len(sent) == 1
    msg = sent[0]
    assert msg["From"] == "pipeline@example.org"
    assert msg["To"] == "ops@example.org"
    assert "ValueError" in msg["Subject"]
    assert "scripts/build_dist.py" in msg.get_content()
    assert "bad row" in msg.get_content()


def _self_signed(tmp_path: Path) -> tuple[Path, Path]:
    """A one-day cert for `localhost`, so STARTTLS verification can actually pass.

    smtplib.starttls() with no context builds ssl.create_default_context(), which
    verifies the chain and the hostname. Handing the subprocess SSL_CERT_FILE is
    what makes this cert trusted -- notify.py is not relaxed for the test.
    """
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(key), "-out", str(cert),
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.smtp
def test_a_failed_run_delivers_mail_to_a_real_smtp_server(tmp_path, monkeypatch) -> None:
    """The deployed path end to end: pipeline fails -> mail arrives, over a socket.

    The failure is forced with a --db that does not exist, so this costs no network
    and no load: publish raises while reading the history, which is exactly the
    class of failure the notification exists for.
    """
    pytest.importorskip("aiosmtpd", reason="dev dependency; `uv sync` installs it")
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult

    cert, key = _self_signed(tmp_path)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)

    received: list[EmailMessage] = []
    credentials: list[tuple[bytes, bytes]] = []

    class Sink:
        async def handle_DATA(self, server, session, envelope):  # noqa: N802, ARG002
            received.append(email.message_from_bytes(envelope.content, policy=email.policy.default))
            return "250 OK"

    def authenticator(mechanism, login, password, *args):  # noqa: ARG001
        credentials.append((login, password))
        return AuthResult(success=True)

    port = _free_port()
    controller = Controller(
        Sink(),
        hostname="127.0.0.1",
        port=port,
        tls_context=tls,
        authenticator=lambda server, session, envelope, mechanism, auth_data: authenticator(
            mechanism, auth_data.login, auth_data.password
        ),
    )
    controller.start()
    try:
        env = {
            **os.environ,
            "NOTIFY_TO": "ops@example.org",
            "NOTIFY_FROM": "pipeline@example.org",
            "SMTP_HOST": "localhost",
            "SMTP_PORT": str(port),
            "SMTP_USERNAME": "u",
            "SMTP_PASSWORD": "p",
            # Trust the throwaway cert -- read by ssl.create_default_context().
            "SSL_CERT_FILE": str(cert),
        }
        proc = subprocess.run(
            [
                sys.executable, "scripts/build_dist.py",
                "--no-ingest",
                "--db", str(tmp_path / "does-not-exist.duckdb"),
                "--out", str(tmp_path / "dist"),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
    finally:
        controller.stop()

    # The run must still fail loudly. Notifying is not allowed to swallow it, and
    # a `could not notify:` line means the send raised and was suppressed.
    assert proc.returncode != 0, proc.stdout
    assert "could not notify" not in proc.stderr, proc.stderr

    assert len(received) == 1, proc.stderr
    msg = received[0]
    assert msg["To"] == "ops@example.org"
    assert msg["From"] == "pipeline@example.org"
    assert "[fdb-scraper] pipeline failed:" in msg["Subject"]
    body = msg.get_content()
    # The traceback is the point of the mail: a subject alone does not say what broke.
    assert "scripts/build_dist.py --no-ingest" in body
    assert "Traceback" in body
    # bytes: what the server read off the wire, not what notify.py was handed.
    assert credentials == [(b"u", b"p")]