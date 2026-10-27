"""Reconstruction obligations intake: bridge reverse engineering artifacts to the ledger.

REA (Reverse Engineering Assistant), Ghidra, Hopper, and clean-room specifications
produce ReconstructionObligationLedgers: verifiable structural, control-flow, and
behavioral obligations required to prove a clean-room reimplementation or defect fix.

This module maps those obligations onto acceptance criteria, preserving obligation
boundaries as provenance so the final report discloses which reverse engineering
obligations earned their close on evidence and which remain unproven.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from exceptions import IntakeError, UsageError

#: Obligation heading in markdown: `# <id>: <title>`, or `# <title>`
OBLIGATION_HEADING_RE = re.compile(r"^#\s+(?:([\w-]*\d[\w-]*)\s*:\s*)?(.+?)\s*$")
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
ID_PREFIX_RE = re.compile(r"^([A-Za-z]+-\d+|\d+)(?:[-_.]|$)")

_CRITERIA_SECTIONS = frozenset((
    "acceptance criteria",
    "obligations",
    "criteria",
    "requirements",
    "reconstruction obligations",
))


def _extract_json_items(data: dict | list) -> tuple[str | None, list[dict]]:
    """Extract raw obligation dictionaries and optional target from decoded JSON."""
    target = None
    if isinstance(data, dict):
        target = data.get("target") or data.get("binary") or data.get("target_artifact")
        raw_list = (
            data.get("obligations")
            or data.get("reconstruction_obligations")
            or (data.get("reconstruction_manifest", {}).get("obligations")
                if isinstance(data.get("reconstruction_manifest"), dict) else None)
            or (data.get("reconstruction_ledger", {}).get("obligations")
                if isinstance(data.get("reconstruction_ledger"), dict) else None)
            or (data.get("evidence_bundle", {}).get("obligations")
                if isinstance(data.get("evidence_bundle"), dict) else None)
        )
        if raw_list is None:
            if "id" in data and ("title" in data or "name" in data):
                raw_list = [data]
            else:
                raw_list = []
    elif isinstance(data, list):
        raw_list = data
    else:
        raw_list = []

    out = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        obj = dict(item)
        if target and not obj.get("target"):
            obj["target"] = target
        b_by = obj.get("blocked_by") or []
        if isinstance(b_by, str):
            b_by = [tok.strip().lstrip("#") for tok in re.split(r"[,\s]+", b_by) if tok.strip()]
        obj["blocked_by"] = [str(b).strip() for b in b_by if str(b).strip() and str(b).strip().lower() != "none"]
        out.append(obj)
    return str(target) if target else None, out


def parse_obligation_markdown(text: str, filename: str = "") -> list[dict]:
    """Parse a markdown obligation file into a list of obligation dicts."""
    lines = text.splitlines()
    ident, title = None, None
    target, category = None, None
    blocked_by = []
    criteria = []
    current_section = None

    for line in lines:
        sline = line.strip()
        if not sline:
            continue
        m_head = OBLIGATION_HEADING_RE.match(sline)
        if m_head and title is None:
            ident, title = m_head.group(1), m_head.group(2).strip()
            continue
        m_sec = SECTION_RE.match(sline)
        if m_sec:
            current_section = m_sec.group(1).strip().lower()
            continue
        if sline.lower().startswith("**target:**"):
            target = sline[len("**target:**"):].strip()
            continue
        if sline.lower().startswith("**category:**"):
            category = sline[len("**category:**"):].strip()
            continue
        if sline.lower().startswith("**type:**"):
            category = sline[len("**type:**"):].strip()
            continue
        if sline.lower().startswith("**blocked by:**") or sline.lower().startswith("**blocked_by:**"):
            val = sline.split(":", 1)[1].strip()
            if val.lower() != "none":
                blocked_by = [tok.strip().lstrip("#") for tok in re.split(r"[,\s]+", val) if tok.strip()]
            continue
        if current_section in _CRITERIA_SECTIONS:
            m_cb = CHECKBOX_RE.match(line)
            if m_cb:
                criteria.append(m_cb.group(1).strip())

    if not ident and filename:
        m_file = ID_PREFIX_RE.match(filename)
        if m_file:
            ident = m_file.group(1)

    if not ident and not title and not criteria:
        return []

    return [{
        "id": ident,
        "title": title,
        "target": target,
        "category": category,
        "blocked_by": blocked_by,
        "criteria": criteria,
    }]


def parse_obligation_file(path: Path) -> list[dict]:
    """Parse a single JSON or Markdown obligation file."""
    content = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise UsageError(f"malformed JSON in obligation file {path}: {exc}") from exc
        _, items = _extract_json_items(data)
        return items
    if suffix in (".md", ".markdown"):
        return parse_obligation_markdown(content, filename=path.name)

    # Fallback: attempt JSON first, then markdown
    try:
        data = json.loads(content)
        _, items = _extract_json_items(data)
        return items
    except Exception:
        return parse_obligation_markdown(content, filename=path.name)


def validate_obligations(raw_obligations: list[dict], source_name: str = "obligations") -> list[dict]:
    """Validate raw obligation items, rejecting gaps, duplicates, or trivial criteria."""
    if not raw_obligations:
        raise IntakeError(f"no obligations found in {source_name}")

    seen_ids = set()
    seen_criteria: dict[str, str] = {}
    validated = []

    for item in raw_obligations:
        if not isinstance(item, dict):
            raise IntakeError(f"obligation must be an object, got {type(item).__name__}")

        raw_id = item.get("id")
        if raw_id is None or not str(raw_id).strip():
            raise IntakeError(f"obligation missing id: {item!r}")
        ident = str(raw_id).strip()

        title = item.get("title") or item.get("name")
        if not title or not str(title).strip():
            raise IntakeError(f"obligation {ident!r} missing title: {item!r}")
        title = str(title).strip()

        target = item.get("target") or item.get("binary")
        target = str(target).strip() if target else None

        category = item.get("category") or item.get("type")
        category = str(category).strip() if category else None

        raw_crit = item.get("criteria", [])
        if isinstance(raw_crit, str):
            raw_crit = [raw_crit]
        elif not isinstance(raw_crit, list):
            raw_crit = []

        criteria = [str(c).strip() for c in raw_crit if str(c).strip()]
        if not criteria:
            raise IntakeError(
                f"obligation {ident!r} has no acceptance criteria; nobody can verify it"
            )

        for c in criteria:
            if len(c) < 4:
                raise IntakeError(
                    f"criterion {c!r} in obligation {ident!r} is shorter than 4 characters; "
                    f"a check nobody can name is not a check"
                )
            if c in seen_criteria and seen_criteria[c] != ident:
                raise UsageError(
                    f"criterion {c!r} appears in multiple obligations ({seen_criteria[c]!r} and {ident!r}); "
                    f"each check must belong to exactly one obligation"
                )
            seen_criteria[c] = ident

        if ident in seen_ids:
            raise UsageError(f"duplicate obligation id {ident!r}")
        seen_ids.add(ident)

        b_by = item.get("blocked_by") or []
        if isinstance(b_by, str):
            b_by = [tok.strip().lstrip("#") for tok in re.split(r"[,\s]+", b_by) if tok.strip()]
        b_by = [str(b).strip() for b in b_by if str(b).strip() and str(b).strip().lower() != "none"]

        validated.append({
            "id": ident,
            "title": title,
            "target": target,
            "category": category,
            "blocked_by": b_by,
            "criteria": criteria,
        })

    known_ids = {o["id"] for o in validated}
    for o in validated:
        for b in o.get("blocked_by", []):
            if b not in known_ids:
                raise UsageError(f"obligation {o['id']} is blocked by unknown obligation {b!r}")
    return validated


def parse_obligations(path_or_dir: str | Path) -> list[dict]:
    """Parse and validate obligations from a file or directory."""
    path = Path(path_or_dir)
    if not path.exists():
        raise UsageError(f"obligations path {path} does not exist")

    raw_items = []
    if path.is_dir():
        entries = sorted(
            [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".json", ".md", ".markdown")],
            key=lambda p: p.name,
        )
        if not entries:
            raise IntakeError(f"no obligation files (.json or .md) found in directory {path}")
        for entry in entries:
            raw_items.extend(parse_obligation_file(entry))
    else:
        raw_items = parse_obligation_file(path)

    return validate_obligations(raw_items, source_name=str(path))


def obligations_to_criteria(obligations: list[dict]) -> list[str]:
    """Flatten obligations into the ledger's criterion list, order preserved."""
    return [c for o in obligations for c in o["criteria"]]
