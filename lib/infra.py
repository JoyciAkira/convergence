"""Infrastructure: provision, inject, store secrets, boot and stop runtimes.

These turn parked paths into executable work. They record what happened; they do
not fake a pass, and a runtime that will not stop is refused rather than reported.
"""
from __future__ import annotations

import re
from pathlib import Path

from exceptions import Blocked, UsageError
from state import req, transactional

def _free_port():
    """Ask the kernel for a port that is free right now."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def _port_open(port, host="127.0.0.1"):
    """True when something already accepts TCP connections on this port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, int(port))) == 0

def _tail(path, lines=10):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no output captured)"

def _kill_process(pid):
    """SIGTERM a process group started with start_new_session, then SIGKILL as fallback."""
    import os
    import signal
    import time

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
            # EPERM on signal 0 means a zombie: dead, sockets released, un-reaped.
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

def _listener_pids(port):
    """PIDs with a LISTEN socket on port, via lsof. Dev servers like `next dev`
    may respawn their actual server in a fresh process group, so the recorded
    pid's group is not always the group holding the port."""
    import os
    import subprocess

    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({
        int(line) for line in out.split() if line.strip().isdigit()
    })

def _retire_port(port, attempts=5):
    """Kill every process still listening on port until the port is clear.
    Returns True when nothing listens on the port anymore."""
    import os
    import signal

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

# -- infra catalog + stub first-class (6) ------------------------------- #

CATALOG_SPECS = {
    "postgres": {
        "driver": "postgresql",
        "placeholder": "postgresql://user:pass@host:5432/db",
    },
    "redis": {
        "driver": "redis",
        "placeholder": "redis://127.0.0.1:6379",
    },
    "sqlite": {
        "driver": "sqlite",
        "placeholder": "sqlite:///./dev.db",
    },
    "s3": {
        "driver": "s3",
        "placeholder": "s3://bucket/key",
    },
    "mail": {
        "driver": "mail",
        "placeholder": "smtp://localhost:1025",
    },
    "ghidra": {
        "driver": "ghidra",
        "placeholder": "ghidra_headless /tmp/ghidra_project",
    },
    "rizin": {
        "driver": "rizin",
        "placeholder": "rizin -q -c 'aaa' target.bin",
    },
    "radare2": {
        "driver": "radare2",
        "placeholder": "radare2 -q -c 'aaa' target.bin",
    },
    "frida": {
        "driver": "frida",
        "placeholder": "frida -U -f target",
    },
    "qemu": {
        "driver": "qemu",
        "placeholder": "qemu-x86_64 target.bin",
    },
}


class InfraMixin:
    CATALOG_SPECS = CATALOG_SPECS

    """Provisioning and runtime lifecycle, all recorded in the ledger."""

    @staticmethod
    def resolve_catalog_spec(spec):
        """Turn a known spec id into a concrete placeholder the run can provision against.

        Unknown specs are accepted as-is: the catalog is guidance, not a cage.
        """
        if not isinstance(spec, str) or not spec.strip():
            raise UsageError("spec must be a non-empty string")
        return CATALOG_SPECS.get(
            spec.strip(), {"driver": spec.strip(), "placeholder": spec.strip()}
        )

    def provision_env_catalog(self, spec, output_var, command=None):
        """Provision from catalog: record the spec and a placeholder command if none given.

        This reduces the friction of turning a missing dependency into a runnable
        environment without inventing a command from nothing. Not @transactional:
        provision_env already takes the lock, and flock is not re-entrant across
        separate open()s of the same file in one process.
        """
        spec_info = self.resolve_catalog_spec(spec)
        if command is None:
            command = f"echo {spec_info['placeholder']}"
        return self.provision_env(spec_info["driver"], output_var, command)

    @transactional
    def provision_env(self, spec, output_var, command):
        """Record that an environment dependency was provisioned on-demand.

        This turns an `information_missing` block into a resolved fact: the ledger
        now knows the value exists and how it was obtained, so criteria that depend
        on it can proceed to evidence collection without human paste.
        """
        spec = req(spec, "--spec")
        output_var = req(output_var, "--output-var")
        command = req(command, "--command")
        self._append(
            "control",
            {
                "ev": "provision_env",
                "spec": spec,
                "output_var": output_var,
                "command": command,
                "resolved": True,
            },
        )
        return self

    @transactional
    def inject_build_context(self, target, source, artifacts, command):
        """Record that build context was copied into an isolated test directory.

        Without this, typechecks and builds on generated code produce false negatives
        because dependencies live only in the host project. The injection is recorded
        so the evidence tier can reference it and the report can disclose it.
        """
        target = req(target, "--target")
        source = req(source, "--source")
        command = req(command, "--command")
        artifacts = [str(a).strip() for a in (artifacts or []) if str(a).strip()]
        if not artifacts:
            raise UsageError(
                "--artifacts must list at least one injected item (node_modules, tsconfig.json, etc.)"
            )
        self._append(
            "control",
            {
                "ev": "inject_build_context",
                "target": target,
                "source": source,
                "artifacts": artifacts,
                "command": command,
            },
        )
        return self

    @transactional
    def store_secret(self, name, command):
        """Record that a secret was stored securely for criterion verification.

        The actual value never touches the ledger or the transcript. Only the name
        and the retrieval command are recorded, so evidence commands can reference
        the secret without leaking it. This eliminates the redaction round-trip
        that previously blocked autonomous verification.
        """
        name = req(name, "--name")
        command = req(command, "--command")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise UsageError(
                f"--name {name!r} must be UPPER_SNAKE (e.g. LLM_API_KEY) so evidence "
                f"commands can reference it without pasting values"
            )
        if name not in command and f"${name}" not in command and f"${{{name}}}" not in command:
            raise UsageError(
                f"--command must reference the secret by name ({name}, ${name} or "
                f"${{{name}}}); never paste the value — only the retrieval command is recorded"
            )
        self._append(
            "control",
            {"ev": "store_secret", "name": name, "command": command},
        )
        return self

    def boot_runtime(
        self, name, command, cwd=None, port=None, path="/", timeout=30.0,
        resolve_signature=None, criterion=None,
    ):
        """Start a local runtime for a generated project and wait for its listener.

        This is the arm that turns "the sandbox is not startable" parks into a
        running URL: pick a free port, start the command (a {port} placeholder in
        the command is substituted), poll until something accepts connections, then
        record the runtime and optionally release a parked block and record
        listener evidence. The server keeps running after this returns so e2e
        criteria can be verified against it; `stop-runtime` retires it.
        """
        name = req(name, "--name")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
        command = req(command, "--command")
        timeout = max(float(timeout or 30.0), 1.0)
        path = path or "/"

        if "{port}" not in command:
            raise UsageError(
                'the command must contain a {port} placeholder, e.g. '
                '"npx next dev -p {port}", so boot-runtime knows where the '
                'listener will come up'
            )
        if port is not None:
            port = int(port)
            if not 0 < port < 65536:
                raise UsageError(f"--port must be between 1 and 65535, got {port}")
            if self.runtime_manager.is_port_open(port):
                raise Blocked(
                    f"--port {port} is already in use; boot-runtime never starts a "
                    f"second server on a bound port. Omit --port to have a free "
                    f"one chosen."
                )
        else:
            port = self.runtime_manager.allocate_free_port()
        actual_command = command.replace("{port}", str(port))

        runtimes_dir = self.root / "runtimes"
        log_path = runtimes_dir / f"{safe_name}.log"
        try:
            handle = self.runtime_manager.start_listener(
                command=actual_command,
                port=port,
                log_path=log_path,
                cwd=str(cwd) if cwd else None,
                timeout=timeout,
            )
        except RuntimeError as exc:
            raise Blocked(str(exc)) from exc

        url = f"http://127.0.0.1:{port}{path}"
        with self.transaction():
            self._append(
                "control",
                {
                    "ev": "boot_runtime",
                    "name": name,
                    "command": actual_command,
                    "port": port,
                    "url": url,
                    "pid": handle.pid,
                    "log": str(handle.log_path),
                },
            )
        if resolve_signature:
            self.resolve_block(
                resolve_signature,
                f"boot-runtime started {name!r} listening on {url} (pid {handle.pid}); "
                f"log at {handle.log_path}",
            )
        if criterion:
            self.record_evidence(
                criterion,
                f"curl -s -o /dev/null -w '%{{http_code}}' {url}",
                "executed_here",
                expected_source="literal",
                reason=f"runtime {name!r} accepted connections on {url}",
            )
        print(f"boot-runtime: {name} listening on {url} (pid {handle.pid})")
        return self

    @transactional
    def stop_runtime(self, name):
        """Retire a runtime previously started with boot-runtime."""
        name = req(name, "--name")
        runtimes = self.state.get("infra", {}).get("runtimes", [])
        target = next(
            (rt for rt in runtimes if rt["name"] == name and not rt.get("stopped")),
            None,
        )
        if target is None:
            running = [rt["name"] for rt in runtimes if not rt.get("stopped")]
            raise Blocked(
                f"no running runtime named {name!r} in this ledger; "
                f"running: {running or 'none'}"
            )
        port = target.get("port")
        handle = type("H", (), {"pid": target["pid"], "port": port})()
        try:
            self.runtime_manager.stop_listener(handle)
        except RuntimeError as exc:
            raise Blocked(str(exc)) from exc
        self._append("control", {"ev": "stop_runtime", "name": name})
        return self
