"""Preflight: are the registered prerequisites actually available?

A run that would immediately block is cheaper to diagnose now than to discover at
the first criterion.
"""
from __future__ import annotations

from pathlib import Path

from infra import _port_open

class PreflightMixin:
    """Check provisions, injections, secrets, runtimes, tools."""

    def validate_infra(self, fix=False):
        """Check that all infrastructure prerequisites are available before
        starting or resuming a run.

        Returns a dict with `ready` (bool), `checks` (list of per-item results),
        and `summary` (human-readable). When `fix=True`, attempts to restore
        missing prerequisites by re-running the recorded provision/injection
        commands. Fixes are best-effort and recorded in the check detail.
        Exit code 0 means all checks passed; exit code 3 means at least one
        prerequisite is missing and could not be restored.
        """
        import os
        import subprocess
        import shutil

        infra = self.state.get("infra", {})
        checks = []

        # Check provisioned environments: verify the output var is set
        for prov in infra.get("provisions", []):
            var = prov["output_var"]
            spec = prov["spec"]
            cmd = f"printenv {var}"
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                ok = result.returncode == 0 and result.stdout.strip() != ""
                checks.append({
                    "type": "provision",
                    "spec": spec,
                    "var": var,
                    "ok": ok,
                    "detail": "set" if ok else f"{var} not in environment",
                })
            except Exception as exc:
                checks.append({
                    "type": "provision",
                    "spec": spec,
                    "var": var,
                    "ok": False,
                    "detail": str(exc),
                })

        # Check injected build contexts: verify artifacts exist at target
        for inj in infra.get("injections", []):
            target = inj["target"]
            artifacts = inj.get("artifacts", [])
            missing = []
            for art in artifacts:
                path = Path(target) / art
                if not path.exists():
                    missing.append(art)
            ok = len(missing) == 0
            checks.append({
                "type": "injection",
                "target": target,
                "artifacts": artifacts,
                "ok": ok,
                "detail": "all present" if ok else f"missing: {', '.join(missing)}",
            })

        # Check stored secrets: verify the retrieval command executes
        for sec in infra.get("secrets", []):
            name = sec["name"]
            cmd = sec["command"]
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                ok = result.returncode == 0 and result.stdout.strip() != ""
                checks.append({
                    "type": "secret",
                    "name": name,
                    "ok": ok,
                    "detail": "retrievable" if ok else "command failed or empty",
                })
            except Exception as exc:
                checks.append({
                    "type": "secret",
                    "name": name,
                    "ok": False,
                    "detail": str(exc),
                })

        # Check booted runtimes: verify the recorded listener still accepts connections
        for rt in infra.get("runtimes", []):
            if rt.get("stopped"):
                continue
            ok = _port_open(rt["port"])
            checks.append({
                "type": "runtime",
                "name": rt["name"],
                "url": rt["url"],
                "ok": ok,
                "detail": (
                    "listening"
                    if ok
                    else f"not listening on port {rt['port']} (pid {rt.get('pid')})"
                ),
            })

        # Check external tools referenced in loops and evidence commands.
        # Commands may start with env-var assignments (VAR=val cmd) or command
        # substitutions (START=$(date ...) cmd); skip tokens that are not bare
        # binary names so we do not report "START=$(date" as a missing tool.
        _SHELL_OPS = frozenset(("&&", "||", "|", ";", ">>", ">", "<", "2>&1", "&"))

        def _extract_tool_name(cmd):
            for token in cmd.split():
                if token in _SHELL_OPS:
                    continue
                if any(c in token for c in "=$(){}><|;&"):
                    continue
                return token
            return None

        tool_names = set()
        for loop in self.state.get("loops", {}).values():
            name = _extract_tool_name(loop.get("command", ""))
            if name:
                tool_names.add(name)
        for ev in self.state.get("evidence", []):
            name = _extract_tool_name(ev.get("command", ""))
            if name:
                tool_names.add(name)
        # Filter to only external binaries (not builtins)
        builtins = {"echo", "cat", "ls", "grep", "test", "true", "false", "cd", "pwd"}
        externals = tool_names - builtins
        for tool in sorted(externals):
            found = shutil.which(tool) is not None
            checks.append({
                "type": "tool",
                "name": tool,
                "ok": found,
                "detail": "found" if found else "not in PATH",
            })

        # Auto-fix pass: attempt to restore failed checks using recorded commands
        if fix:
            for c in checks:
                if c["ok"]:
                    continue
                if c["type"] == "provision":
                    prov = next(
                        (p for p in infra.get("provisions", []) if p["output_var"] == c["var"]),
                        None,
                    )
                    if prov:
                        try:
                            result = subprocess.run(
                                prov["command"], shell=True, capture_output=True,
                                text=True, timeout=60,
                            )
                            if result.returncode == 0 and result.stdout.strip():
                                # Export the var into current env for subsequent checks
                                os.environ[c["var"]] = result.stdout.strip()
                                c["ok"] = True
                                c["detail"] = "restored via --fix"
                            else:
                                c["detail"] += f"; fix failed: {result.stderr.strip()[:120]}"
                        except Exception as exc:
                            c["detail"] += f"; fix error: {exc}"
                elif c["type"] == "injection":
                    inj = next(
                        (i for i in infra.get("injections", []) if i["target"] == c["target"]),
                        None,
                    )
                    if inj:
                        try:
                            result = subprocess.run(
                                inj["command"], shell=True, capture_output=True,
                                text=True, timeout=60,
                            )
                            if result.returncode == 0:
                                # Re-check artifacts
                                missing_after = [
                                    a for a in inj.get("artifacts", [])
                                    if not (Path(inj["target"]) / a).exists()
                                ]
                                if not missing_after:
                                    c["ok"] = True
                                    c["detail"] = "restored via --fix"
                                else:
                                    c["detail"] = f"fix ran but still missing: {', '.join(missing_after)}"
                            else:
                                c["detail"] += f"; fix failed: {result.stderr.strip()[:120]}"
                        except Exception as exc:
                            c["detail"] += f"; fix error: {exc}"
                # secrets and tools cannot be auto-fixed reliably

        ready = all(c["ok"] for c in checks) if checks else True
        failed = [c for c in checks if not c["ok"]]
        fixed = [c for c in checks if c.get("detail") == "restored via --fix"]
        summary = (
            f"{len(checks) - len(failed)}/{len(checks)} checks passed"
            if checks
            else "no infrastructure registered"
        )
        if fixed:
            summary += f"; {len(fixed)} restored via --fix"
        if failed:
            summary += "; failing: " + ", ".join(
                f"{c['type']}:{c.get('spec') or c.get('name') or c.get('target')} ({c['detail']})"
                for c in failed
            )

        return {"ready": ready, "checks": checks, "summary": summary}
