"""RuntimeManager: adapter at the Infrastructure seam.

Exposes higher-level operations that encapsulate port allocation, process
spawning, signal delivery, and liveness verification. Ledger calls these;
it never touches sockets or subprocesses directly.

Two adapters justify this seam:
- RealOSRuntimeManager: production adapter using real OS primitives
- FakeRuntimeManager: stateful simulator for deterministic tests
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ListenerHandle:
    """Opaque handle to a started listener, returned by start_listener."""
    pid: int
    port: int
    log_path: Path
    command: str


@dataclass
class RuntimeStatus:
    """Result of check_runtime: whether a listener is alive on a port."""
    alive: bool
    pids: list[int] = field(default_factory=list)
    detail: str = ""


class RuntimeManager(ABC):
    """Interface at the Infrastructure seam.

    Higher-level operations that hide OS primitive composition from Ledger.
    """

    @abstractmethod
    def allocate_free_port(self) -> int:
        """Return a TCP port that is free right now."""

    @abstractmethod
    def is_port_open(self, port: int, host: str = "127.0.0.1") -> bool:
        """True when something already accepts TCP connections on this port."""

    @abstractmethod
    def start_listener(
        self,
        command: str,
        port: int,
        log_path: Path,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ) -> ListenerHandle:
        """Spawn a process, wait for it to accept connections on port, return handle.

        Raises RuntimeError if the process exits before listening or if the
        timeout elapses without a listener appearing.
        """

    @abstractmethod
    def stop_listener(self, handle: ListenerHandle) -> None:
        """Kill the listener process and verify the port is released.

        Raises RuntimeError if the port cannot be freed after retries.
        """

    @abstractmethod
    def check_runtime(self, port: int) -> RuntimeStatus:
        """Check whether anything is listening on the given port."""

    @abstractmethod
    def read_log_tail(self, log_path: Path, lines: int = 10) -> str:
        """Return the last N lines of a log file, or a placeholder on error."""


class RealOSRuntimeManager(RuntimeManager):
    """Production adapter: real sockets, subprocesses, signals."""

    def allocate_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def is_port_open(self, port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, int(port))) == 0

    def start_listener(
        self,
        command: str,
        port: int,
        log_path: Path,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ) -> ListenerHandle:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.is_port_open(port):
                    return ListenerHandle(
                        pid=proc.pid, port=port, log_path=log_path, command=command
                    )
                if proc.poll() is not None:
                    tail = self.read_log_tail(log_path)
                    raise RuntimeError(
                        f"listener exited before accepting connections "
                        f"(exit code {proc.returncode}). Tail: {tail}"
                    )
                time.sleep(0.25)
            _kill_process_group(proc.pid)
            _retire_port(port)
            tail = self.read_log_tail(log_path)
            raise RuntimeError(
                f"listener did not accept connections on port {port} "
                f"within {timeout:g}s. Tail: {tail}"
            )

    def stop_listener(self, handle: ListenerHandle) -> None:
        _kill_process_group(handle.pid)
        if not _retire_port(handle.port):
            remaining = _listener_pids(handle.port)
            raise RuntimeError(
                f"port {handle.port} still bound by processes {remaining} "
                f"that could not be stopped"
            )

    def check_runtime(self, port: int) -> RuntimeStatus:
        pids = _listener_pids(port)
        if pids:
            return RuntimeStatus(alive=True, pids=pids, detail=f"listeners: {pids}")
        return RuntimeStatus(alive=False, detail="no listeners on port")

    def read_log_tail(self, log_path: Path, lines: int = 10) -> str:
        try:
            return "\n".join(
                log_path.read_text(errors="replace").splitlines()[-lines:]
            )
        except OSError:
            return "(no output captured)"


# --- Private OS helpers (real adapter internals, not part of the seam) --- #


def _kill_process_group(pid: int) -> None:
    """SIGTERM a process group started with start_new_session, SIGKILL fallback."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _listener_pids(port: int) -> list[int]:
    """PIDs with a LISTEN socket on port, via lsof."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({int(line) for line in out.split() if line.strip().isdigit()})


def _retire_port(port: int, attempts: int = 5) -> bool:
    """Kill every process still listening on port until clear. True when free."""
    for _ in range(attempts):
        pids = _listener_pids(port)
        if not pids:
            return True
        for pid in pids:
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError):
                pgid = None
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    if pgid is not None:
                        os.killpg(pgid, sig)
                    else:
                        os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
        time.sleep(0.5)
    return not _listener_pids(port)


class FakeRuntimeManager(RuntimeManager):
    """Stateful simulator for deterministic tests.

    Models the invariants that existing tests exercise:
    - Port allocation refuses double-binding
    - Listener lifecycle (start/stop/check)
    - Timeout when listener never appears
    - Parked-block release on successful boot
    """

    def __init__(self) -> None:
        self._allocated_ports: set[int] = set()
        self._listeners: dict[int, ListenerHandle] = {}  # port -> handle
        self._next_pid = 10000
        self._log_contents: dict[str, str] = {}  # log_path -> content
        self._fail_start_after: Optional[int] = None  # fail after N successful starts
        self._start_count = 0

    # --- Test hooks ------------------------------------------------------- #

    def set_log_content(self, log_path: Path, content: str) -> None:
        """Pre-load log content so read_log_tail returns it."""
        self._log_contents[str(log_path)] = content

    def fail_start_after(self, n: int) -> None:
        """Make start_listener raise RuntimeError after n successful calls."""
        self._fail_start_after = n

    # --- Interface implementation ----------------------------------------- #

    def allocate_free_port(self) -> int:
        port = 49152
        while port in self._allocated_ports:
            port += 1
            if port > 65535:
                raise RuntimeError("fake port space exhausted")
        self._allocated_ports.add(port)
        return port

    def is_port_open(self, port: int, host: str = "127.0.0.1") -> bool:
        return port in self._listeners

    def start_listener(
        self,
        command: str,
        port: int,
        log_path: Path,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ) -> ListenerHandle:
        if port in self._listeners:
            raise RuntimeError(f"port {port} already bound in fake runtime")
        if self._fail_start_after is not None and self._start_count >= self._fail_start_after:
            raise RuntimeError("simulated start failure")
        self._start_count += 1
        pid = self._next_pid
        self._next_pid += 1
        handle = ListenerHandle(pid=pid, port=port, log_path=log_path, command=command)
        self._listeners[port] = handle
        return handle

    def stop_listener(self, handle: ListenerHandle) -> None:
        if handle.port in self._listeners:
            del self._listeners[handle.port]

    def check_runtime(self, port: int) -> RuntimeStatus:
        if port in self._listeners:
            h = self._listeners[port]
            return RuntimeStatus(alive=True, pids=[h.pid], detail="fake listener active")
        return RuntimeStatus(alive=False, detail="no fake listener")

    def read_log_tail(self, log_path: Path, lines: int = 10) -> str:
        content = self._log_contents.get(str(log_path), "(no output captured)")
        return "\n".join(content.splitlines()[-lines:])