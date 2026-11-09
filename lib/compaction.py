"""Atomic, lossless compaction for the append-only ledger.

A compaction replaces four logs with one snapshot. A crash between truncating a
log and publishing the snapshot would otherwise lose the run, so the archive is
written and a pending marker is published before any log is touched. The marker
is the commit point: while it exists, the next reader restores the archived
originals. It is removed only after the compacted ledger has replayed to the
same state.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import time
from pathlib import Path

from constants import LOGS
from exceptions import Blocked, UsageError

#: Transaction marker. Its presence means the archive is authoritative; the
#: logs may be half-truncated. It is deliberately outside LOGS so replay ignores it.
MARKER = ".compact.pending"


def _marker_path(root):
    return Path(root) / MARKER


def _restore_file(source, target):
    """Replace one log atomically, leaving the marker for a retry on a crash."""
    temporary = target.with_name(target.name + ".recover")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


def _archive_file(source, target):
    """Copy an original and flush it before the marker can make it authoritative."""
    shutil.copy2(source, target)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())


def _write_marker(root, archive, present):
    marker = _marker_path(root)
    temporary = root / (MARKER + ".tmp")
    payload = {"archive": str(archive.relative_to(root)), "present": present}
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, marker)


def recover_pending(root):
    """Restore a compaction interrupted before its marker was removed.

    The caller holds the ledger lock. The marker is written only after every
    original log has been archived and before the first log is truncated, so its
    presence means the archive is authoritative. Restoring is idempotent: a
    crash during recovery leaves the marker in place and the next open retries.
    """
    root = Path(root)
    marker = _marker_path(root)
    if not marker.exists():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        archive = (root / payload["archive"]).resolve()
        present = list(payload["present"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise UsageError(
            f"cannot recover pending compaction: malformed marker: {exc}"
        ) from exc
    root_resolved = root.resolve()
    if root_resolved not in archive.parents:
        raise UsageError(
            "cannot recover pending compaction: archive is outside the ledger"
        )
    if not archive.is_dir():
        raise UsageError(
            f"cannot recover pending compaction: archive missing: {archive}"
        )
    unknown = set(present) - set(LOGS)
    if unknown:
        raise UsageError(
            f"cannot recover pending compaction: unknown logs: {sorted(unknown)}"
        )
    for name in LOGS:
        target = root / LOGS[name]
        if name in present:
            source = archive / LOGS[name]
            if not source.exists():
                raise UsageError(
                    f"cannot recover pending compaction: archived log missing: {source}"
                )
            _restore_file(source, target)
        elif target.exists():
            target.unlink()
    marker.unlink()
    return True


def compact_locked(ledger):
    """Compact logs into one snapshot; the caller holds the ledger lock.

    The archive and marker are durable before the first truncation. Verification
    is a fresh replay of the snapshot without recovery, because the marker is
    still present while the new state is being checked. Only a matching replay
    removes the marker; a mismatch restores the originals and refuses the call.
    """
    # state imports this module, so import its helpers only when this function
    # runs; at that point state is fully initialized.
    from state import _comparable, empty_state

    clean = empty_state()
    for name in LOGS:
        ledger._replay(ledger.root / LOGS[name], clean)
    if _comparable(clean) != _comparable(ledger.state):
        raise Blocked(
            "compact refused: replayed state diverges from current state — the "
            "ledger changed under this process. Re-open and retry."
        )

    snapshot = {"ev": "snapshot", "at": time.time(), "state": copy.deepcopy(ledger.state)}
    archive = ledger.root / "archive" / str(time.time_ns())
    archive.mkdir(parents=True, exist_ok=True)
    present = [name for name in LOGS if (ledger.root / LOGS[name]).exists()]
    for name in present:
        _archive_file(ledger.root / LOGS[name], archive / LOGS[name])
    _write_marker(ledger.root, archive, present)
    for name in present:
        (ledger.root / LOGS[name]).write_text("", encoding="utf-8")
    with (ledger.root / LOGS["control"]).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, sort_keys=True) + "\n")

    try:
        reopened = type(ledger)(
            ledger.root,
            runtime_manager=ledger.runtime_manager,
            claim_comparator=ledger.claim_comparator,
            _lock_held=True,
            _recover=False,
        )
    except Exception:
        recover_pending(ledger.root)
        raise
    if _comparable(reopened.state) != _comparable(ledger.state):
        recover_pending(ledger.root)
        raise Blocked(
            "compact refused: the compacted ledger diverges from the original; "
            "originals restored"
        )
    _marker_path(ledger.root).unlink()
    return reopened