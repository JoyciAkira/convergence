"""Ticket intake: bridge from a to-tickets issue directory to the ledger.

The to-tickets skill splits work into tracer-bullet tickets whose acceptance
criteria are markdown checkboxes, in one of two shapes: the **local template**
(`# <id>: <title>` with inline `**Field:**` lines) and the **issue template**
used on real trackers (`## What to build` / `## Acceptance criteria` /
`## Blocked by` sections). Both are read here, with native ticket ids —
`01`, `42`, `ENG-123` — and blocking edges given as numbers, native keys or
tracker URLs.

The ledger's magnet is coarser: one intent, a flat set of acceptance criteria,
each closeable by a re-runnable command. This module maps the first onto the
second without losing the ticket boundaries: every criterion keeps its ticket,
so the report can say which ticket has *earned* its close and which has merely
been merged.

Merging a ticket is a statement about the issue tracker. Earning it is a
statement about evidence. Only the second closes a run — and a criterion that
no command can close is refused here, at intake, instead of failing silently
as NOT REACHED at close.
"""
from __future__ import annotations

import re
from pathlib import Path

from exceptions import IntakeError, UsageError

#: Ticket identity: `# <id>: <title>`, or `# <title>` when the id lives in the
#: filename (issue-template exports from a real tracker).
TICKET_HEADING_RE = re.compile(r"^#\s+(?:([\w-]*\d[\w-]*)\s*:\s*)?(.+?)\s*$")
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$")
#: A native id at the start of a filename: `01-parser.md`, `ENG-42.md`, `7.md`.
ID_PREFIX_RE = re.compile(r"^([A-Za-z]+-\d+|\d+)(?:[-_.]|$)")
#: Refs inside a Blocked-by value: hyphenated keys (`ENG-123`), bare numbers.
REF_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*-\d+|\b\d+\b")
URL_RE = re.compile(r"https?://[^\s,)]+")

BLOCKED_BY_MARKER = "**blocked by:**"
WHAT_TO_BUILD_MARKER = "**what to build:**"
STATUS_MARKER = "**status:**"
NO_BLOCKERS = "none"

_SECTION_WHAT = "what to build"
_SECTION_CRITERIA = "acceptance criteria"
_SECTION_BLOCKED = "blocked by"
_SECTION_STATUS = "status"


def _canonical(token: str) -> str:
    """Canonical form for id matching: numeric ids compare by value, not padding."""
    token = token.strip().lstrip("#")
    return str(int(token)) if token.isdigit() else token


def _resolve_refs(text: str, known_ids: set[str], known_titles: list[tuple[str, str]]) -> list[str]:
    """Resolve a Blocked-by value to ticket ids; refuse anything unresolvable.

    Accepts bare numbers, hyphenated native keys and tracker URLs. Numbers match
    by value regardless of zero-padding (`1` finds `01`); keys match exactly;
    titles match only in full (case-insensitive), so a stray word never becomes
    a phantom edge.
    """
    text = text.strip().strip("*").strip()
    lowered = text.lower()
    if not text or lowered.startswith(NO_BLOCKERS):
        return []

    by_canonical = {_canonical(i): i for i in known_ids}
    refs: list[str] = []
    for url in URL_RE.findall(text):
        m = re.search(r"/(?:issues?|pull)/(\d+)", url) or re.search(r"/([A-Za-z][A-Za-z0-9]*-\d+)/?$", url)
        if m:
            token = _canonical(m.group(1))
            if token in by_canonical and by_canonical[token] not in refs:
                refs.append(by_canonical[token])
    for token in REF_TOKEN_RE.findall(text):
        canon = _canonical(token)
        if canon in by_canonical:
            if by_canonical[canon] not in refs:
                refs.append(by_canonical[canon])
        else:
            raise UsageError(
                f"Blocked by references unknown ticket {token!r}; known: {sorted(known_ids)}. "
                f"A dangling edge means the ticket graph was exported incomplete."
            )
    if refs:
        return refs
    for tid, title in known_titles:
        if title.lower() in lowered and tid not in refs:
            refs.append(tid)
    if not refs:
        raise UsageError(
            f"cannot resolve any ticket in Blocked by line: {text!r}. Reference "
            f"blockers by number, native key (e.g. 'ENG-123'), tracker URL, or full "
            f"title, or write 'None (can start immediately)'."
        )
    return refs


def _identity(path: Path) -> tuple[str | None, str]:
    """(id, title) for a ticket file: id from the heading, else the filename.

    A tracker export names the file after the issue (`ENG-42.md`, `7.md`); a
    local-template file carries the id in its heading. Either way the id is the
    tracker's own — nothing is renumbered here.
    """
    path = Path(path)
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        m = TICKET_HEADING_RE.match(stripped)
        if m:
            if m.group(1) is not None:
                return m.group(1).strip(), m.group(2)
            prefix = ID_PREFIX_RE.match(path.stem)
            if prefix:
                return prefix.group(1), m.group(2)
            raise UsageError(
                f"{path.name}: heading {stripped!r} carries no ticket id and the "
                f"filename does not look like one. Use '# <id>: <title>' or name "
                f"the file after its id (e.g. 'ENG-42.md')."
            )
    raise UsageError(
        f"{path.name}: not a ticket file: expected a '# <id>: <title>' or "
        f"'# <title>' heading (to-tickets template)"
    )


def parse_ticket_file(path: Path, known_ids: set[str] | None = None,
                      known_titles: list[tuple[str, str]] | None = None) -> dict:
    """Parse one ticket file into {id, title, what_to_build, status, blocked_by, criteria}.

    Handles both to-tickets shapes: inline `**Field:**` lines (local template)
    and `## Field` sections (issue template). Checkboxes anywhere count as
    acceptance criteria.
    """
    path = Path(path)
    ticket_id, title = _identity(path)
    what_to_build = ""
    status = ""
    blocked_by: list[str] = []
    criteria: list[str] = []
    section = None
    section_lines: dict[str, list[str]] = {}

    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        sec = SECTION_RE.match(stripped)
        if sec:
            section = sec.group(1).strip().lower()
            section_lines.setdefault(section, [])
            continue
        checkbox = CHECKBOX_RE.match(line)
        if checkbox:
            criteria.append(checkbox.group(1))
            continue
        if section is not None:
            if stripped:
                section_lines[section].append(stripped)
            continue
        if lowered.startswith(BLOCKED_BY_MARKER):
            blocked_by = _resolve_refs(
                stripped[len(BLOCKED_BY_MARKER):], known_ids or set(), known_titles or []
            )
        elif lowered.startswith(WHAT_TO_BUILD_MARKER):
            what_to_build = stripped[len(WHAT_TO_BUILD_MARKER):].strip().strip("*").strip()
        elif lowered.startswith(STATUS_MARKER):
            status = stripped[len(STATUS_MARKER):].strip().strip("*").strip()

    if _SECTION_WHAT in section_lines and not what_to_build:
        what_to_build = next((l for l in section_lines[_SECTION_WHAT] if l), "")
    if _SECTION_STATUS in section_lines and not status:
        status = next((l for l in section_lines[_SECTION_STATUS] if l), "")
    if _SECTION_BLOCKED in section_lines:
        resolved = []
        for line in section_lines[_SECTION_BLOCKED]:
            bullet = re.sub(r"^[-*]\s+", "", line)
            for ref in _resolve_refs(bullet, known_ids or set(), known_titles or []):
                if ref not in resolved:
                    resolved.append(ref)
        blocked_by = resolved

    if not criteria:
        raise IntakeError(
            f"ticket {ticket_id} ({title!r}) has no acceptance criterion. A ticket "
            f"nobody can verify cannot be closed by any command, so it cannot enter "
            f"the ledger: add at least one '- [ ]' line saying what would prove it done."
        )
    return {
        "id": ticket_id,
        "title": title,
        "what_to_build": what_to_build,
        "status": status,
        "blocked_by": blocked_by,
        "criteria": criteria,
    }


def parse_tickets(directory) -> list[dict]:
    """Parse and cross-validate every ticket in a to-tickets issue directory.

    Returns tickets sorted by id (numeric ids first by value, then keys).
    Order is not load-bearing: the ledger flattens criteria and keeps the
    ticket boundaries as provenance.

    Refuses: empty directories, duplicate ids, criteria duplicated across
    tickets (traceability would silently break), dangling blocking edges, and
    criteria no one could ever verify (too short to be a check).
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise UsageError(f"not a directory: {directory}")
    files = sorted(directory.glob("*.md"))
    if not files:
        raise UsageError(f"no ticket files (*.md) in {directory}")

    identities = [(f, *_identity(f)) for f in files]
    seen: set[str] = set()
    dupes = sorted({i for _, i, _ in identities if i in seen or seen.add(i)})
    if dupes:
        raise UsageError(f"duplicate ticket id(s) {dupes} in {directory}")
    known_ids = {i for _, i, _ in identities}
    known_titles = [(i, t) for _, i, t in identities]

    tickets = [
        parse_ticket_file(f, known_ids=known_ids, known_titles=known_titles)
        for f, _, _ in identities
    ]
    tickets.sort(
        key=lambda t: (0, int(t["id"]), "") if t["id"].isdigit() else (1, 0, t["id"])
    )

    seen_criteria: dict[str, str] = {}
    for t in tickets:
        for c in t["criteria"]:
            if c in seen_criteria:
                raise UsageError(
                    f"criterion {c!r} appears in both ticket {seen_criteria[c]} and "
                    f"{t['id']}. Each criterion must map to exactly one ticket, or "
                    f"the report cannot say which ticket earned it."
                )
            seen_criteria[c] = t["id"]
        short = [c for c in t["criteria"] if len(c.strip()) < 4]
        if short:
            raise IntakeError(
                f"ticket {t['id']} has criterion(s) shorter than 4 characters: "
                f"{short}. A check nobody can name is not a check."
            )
    return tickets


def tickets_to_criteria(tickets: list[dict]) -> list[str]:
    """Flatten tickets into the ledger's flat criterion list, order preserved."""
    return [c for t in tickets for c in t["criteria"]]
