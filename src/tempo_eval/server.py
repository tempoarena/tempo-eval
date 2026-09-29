"""Talking to a tempo match server, and starting one when the caller has none.

The server is tempo's (`spec/protocol.md`); this module only speaks its HTTP half. Starting one
locally means `tempo.serve(...)` from the pinned `tempo-arena` in a child process, so a crash or
hang in the server never takes the evaluator down with it and killing it is always possible.
`$TEMPO_SERVER_BIN` (e.g. `../tempo/target/release/tempo-server`) is used instead when set.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TERMINAL = frozenset({"finished", "aborted"})


class ServerError(RuntimeError):
    pass


@dataclass
class ServerClient:
    """Synchronous HTTP client for one server. Thread-safe enough for our use (one per thread)."""

    base_url: str
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=self.timeout_s)

    @property
    def ws_url(self) -> str:
        return (
            "ws" + self.base_url[len("http") :]
            if self.base_url.startswith("http")
            else self.base_url
        )

    def close(self) -> None:
        self._http.close()

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ServerError(f"{method} {path}: {e}") from e
        if r.status_code >= 400:
            raise ServerError(f"{method} {path}: HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def health(self) -> dict:
        return self._json("GET", "/health")

    def game(self, game: str) -> dict:
        return self._json("GET", f"/games/{game}")

    def create_match(self, config: dict) -> dict:
        return self._json("POST", "/matches", json=config)

    def match(self, match_id: str) -> dict:
        return self._json("GET", f"/matches/{match_id}")

    def result(self, match_id: str) -> dict:
        return self._json("GET", f"/matches/{match_id}/result")

    def replay(self, match_id: str) -> dict | None:
        try:
            return self._json("GET", f"/matches/{match_id}/replay")
        except ServerError:
            return None

    def wait_healthy(self, timeout_s: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout_s
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.health()
            except ServerError as e:
                last = e
                time.sleep(0.2)
        raise ServerError(f"server at {self.base_url} not healthy after {timeout_s}s: {last}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


#: where scripts/build-server.sh puts the pinned tempo-server binary
TOOLS_BIN = Path(__file__).resolve().parents[2] / ".tools" / "bin" / "tempo-server"


def _python_serve_available() -> bool:
    try:
        from tempo import _native  # type: ignore[import-not-found]
    except ImportError:
        return False
    return hasattr(_native, "serve")


def server_command(host: str, port: int, runs_dir: Path) -> list[str]:
    """How to start a server: `$TEMPO_SERVER_BIN`, else `tempo.serve` from the pinned package,
    else the binary `scripts/build-server.sh` built at the same pin."""
    binary = os.environ.get("TEMPO_SERVER_BIN")
    if not binary and not _python_serve_available() and TOOLS_BIN.exists():
        binary = str(TOOLS_BIN)
    if binary:
        return [binary, "--host", host, "--port", str(port), "--runs", str(runs_dir)]
    code = f"import tempo; tempo.serve(host={host!r}, port={port}, runs_dir={str(runs_dir)!r})"
    return [sys.executable, "-c", code]


@contextlib.contextmanager
def local_server(
    runs_dir: Path, host: str = "127.0.0.1", port: int | None = None, log_path: Path | None = None
) -> Iterator[ServerClient]:
    """Start a tempo server in a child process for the duration of the block."""
    port = port or free_port()
    runs_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "wb") if log_path else subprocess.DEVNULL  # noqa: SIM115
    proc = subprocess.Popen(server_command(host, port, runs_dir), stdout=log, stderr=log)
    client = ServerClient(f"http://{host}:{port}")
    try:
        try:
            client.wait_healthy()
        except ServerError:
            if proc.poll() is not None:
                raise ServerError(
                    f"tempo server exited with {proc.returncode}; is tempo-arena installed "
                    f"(scripts/dev-link.sh) or $TEMPO_SERVER_BIN set? log: {log_path}"
                ) from None
            raise
        yield client
    finally:
        client.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if log is not subprocess.DEVNULL:
            log.close()


@contextlib.contextmanager
def connect_or_start(
    server_url: str | None, runs_dir: Path, log_path: Path | None = None
) -> Iterator[ServerClient]:
    if server_url:
        client = ServerClient(server_url)
        try:
            client.wait_healthy(timeout_s=10)
            yield client
        finally:
            client.close()
    else:
        with local_server(runs_dir, log_path=log_path) as client:
            yield client
