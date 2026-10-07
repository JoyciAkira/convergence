"""Prompt intake: decompose an arbitrary request into the ledger's magnet.

A prompt can be anything: one line, or a very complex multi-goal brief with
nested lists, mixed languages, unicode, headings and constraints. Whatever its
shape, the ledger needs the same magnet — one intent and a flat set of
verifiable acceptance criteria — and it refuses to invent it. This module is
the deterministic bridge: it reports the shape it found, with provenance for
every extracted check, so a human can audit the decomposition at `init --intake`
before anything is written.

The rules are fixed and loss-aware:

* **Intent** — the first `Goal:`/`Intent:`/`Objective:`/`Obiettivo:`/`Scopo:`
  marker (with its continuation lines), else the first block (a title heading
  joined with its first paragraph), else refused.
* **Criteria** — markdown checkboxes anywhere; bullets and plain lines under
  acceptance headings (`Acceptance criteria`, `Requirements`, `Criteri`,
  `Requisiti`, `Definition of done`, ...); body sentences that state a
  requirement (`must`, `shall`, `has to`, `deve`, `ensure ...`, with or
  without negation — "must not double-charge" is a check, not a wish). If
  nothing else yields a criterion, body bullets and indicative sentences are
  promoted, so a bare list prompt still works.
* **Hypotheses** — bullets under `Hypotheses`/`Risks`/`Unknowns`/`Ipotesi`/
  `Rischi` headings come back as candidates, ready for `open-hypothesis`; the
  ledger opens them later, at Entry A, with their own predictions.
* **Exclusions** — bullets under `Out of scope`/`Non-goals`/`Fuori ambito` are
  reported as exclusions, never promoted to criteria.

An imperative sentence ("Build a REST API") is a task instruction, not a check;
a prompt that states only tasks is refused at intake with the question the asker
needs to answer: what would prove it done? The shape is reported, not guessed.

Nothing here truncates: prompts are read whole from a file or stdin (`-`), so
argv limits, shell quoting and line length are not part of this contract.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from exceptions import IntakeError, UsageError

BULLET_RE = re.compile(r"^\s*(?:[-*•▪]|\d+[.)])\s+(.+)$")
CHECKBOX_RE = re.compile(r"^\s*(?:[-*•▪]|\d+[.)])\s*\[[ xX]\]\s*(.+)$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
INTENT_MARKER_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:goal|intent|objective|obiettivo|scopo)\s*:\s*(.+)$",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
ENSURE_PREFIX_RE = re.compile(
    r"^\s*(?:please\s+)?(?:ensure|make sure|verify|check|test)\s+(?:that\s+)?(.+)$",
    re.IGNORECASE,
)
ENSURE_PREFIX_IT_RE = re.compile(
    r"^\s*(?:assicurati|verifica|controlla)\s+(?:che|di)\s+(.+)$",
    re.IGNORECASE,
)
DEONTIC_RE = re.compile(
    r"\b(?:must|shall|has to|have to|need to|needs to|required to|deve|devono|obbligatorio)\b",
    re.IGNORECASE,
)
IMPERATIVE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:build|make|create|implement|add|fix|ship|write|refactor|"
    r"update|remove|delete|port|set up|porta|crea|implementa|aggiungi|fixa|scrivi|"
    r"rifattorizza|aggiorna|rimuovi|elimina)\b",
    re.IGNORECASE,
)

CRITERIA_HEADINGS = (
    "acceptance criteria", "acceptance", "criterion", "criteria",
    "requirements", "requirement", "definition of done", "done when",
    "success criteria", "deliverables", "criteri di accettazione", "criteri",
    "requisiti", "fatto quando", "verifiche",
)
HYPOTHESIS_HEADINGS = (
    "hypotheses", "hypothesis", "risks", "risk", "unknowns", "open questions",
    "ipotesi", "rischi", "incognite", "domande aperte",
)
EXCLUSION_HEADINGS = (
    "out of scope", "non-goals", "non goals", "exclusions", "excluded",
    "fuori ambito", "non-obiettivi", "non obiettivi", "non goals",
)

KIND_CRITERIA = "criteria"
KIND_HYPOTHESIS = "hypothesis"
KIND_EXCLUSION = "exclusion"
KIND_INTENT = "intent"

INTENT_HEADINGS = (
    "goal", "goals", "objective", "objectives", "intent", "scope",
    "obiettivo", "obiettivi", "scopo", "scopi",
)


def load_text(path):
    """Read a prompt source without limits: a file path, or '-' for stdin.

    utf-8-sig: a BOM is a transport artefact, not content — a tracker export or
    a Windows editor must not smuggle it into the first criterion.
    """
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"not a readable file: {path}")
    return p.read_text(encoding="utf-8-sig")


def _classify_heading(text):
    lowered = text.strip().rstrip(":.").strip().lower()
    for keyword in EXCLUSION_HEADINGS:
        if keyword in lowered:
            return KIND_EXCLUSION
    for keyword in HYPOTHESIS_HEADINGS:
        if keyword in lowered:
            return KIND_HYPOTHESIS
    for keyword in CRITERIA_HEADINGS:
        if keyword in lowered:
            return KIND_CRITERIA
    for keyword in INTENT_HEADINGS:
        if lowered == keyword:
            return KIND_INTENT
    return None


def _is_requirement(text):
    """Does this line state a check rather than describe, exclude or speculate?

    A requirement sentence belongs to the magnet wherever it appears — even at
    the tail of an Out-of-scope section — because a check the prompt states is
    never silently dropped.
    """
    return bool(
        ENSURE_PREFIX_RE.match(text)
        or ENSURE_PREFIX_IT_RE.match(text)
        or DEONTIC_RE.search(text)
    )


def _norm(text):
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _finalise(items, kind):
    """Dedupe (keep first) and enforce the naming rule for extracted items."""
    seen = set()
    out = []
    for item in items:
        text = re.sub(r"\s+", " ", item["text"]).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        if kind == KIND_CRITERIA and len(text) < 4:
            raise IntakeError(
                f"extracted criterion {text!r} (from {item['source']}) is shorter "
                f"than 4 characters. A check nobody can name is not a check."
            )
        out.append({"text": text, "source": item["source"]})
    return out


def _sentence_items(body_items, source):
    """Requirement sentences in body text -> criteria; task imperatives skipped.

    An ensure/verify/check prefix is stripped ("ensure retries are capped" is
    the check "retries are capped"). Negated requirements stay criteria: "must
    not double-charge" is as checkable as "must charge once".
    """
    items = []
    for entry in body_items:
        for sentence in SENTENCE_SPLIT_RE.split(entry["text"]):
            sentence = sentence.strip()
            if not sentence:
                continue
            ensure = ENSURE_PREFIX_RE.match(sentence) or ENSURE_PREFIX_IT_RE.match(sentence)
            if ensure:
                items.append({"text": ensure.group(1).strip(), "source": source})
            elif DEONTIC_RE.search(sentence):
                items.append({"text": sentence, "source": source})
    return items


def criteria_from_text(text, label="--criteria-file"):
    """One check per item from a criteria file: bullets, checkboxes or plain
    lines. Headings are skipped, continuation lines fold into their item."""
    items = []
    current = None
    for line in str(text).splitlines():
        stripped = line.strip()
        if not stripped or HEADING_RE.match(stripped):
            current = None
            continue
        checkbox = CHECKBOX_RE.match(line)
        bullet = checkbox or BULLET_RE.match(line)
        if bullet:
            items.append({"text": bullet.group(1).strip(), "source": label})
            current = items[-1]
        elif current is not None and line[:1].isspace():
            # Only an indented line continues its item; an unindented line is
            # the next check, one line per criterion.
            current["text"] += " " + stripped
        else:
            items.append({"text": stripped, "source": label})
            current = items[-1]
    return _finalise(items, KIND_CRITERIA)


def parse_prompt(text):
    """Decompose a prompt into {intent, criteria, hypotheses, exclusions}.

    criteria/hypotheses are lists of {"text", "source"} with the rule that
    extracted each; exclusions are plain strings. Nothing is invented: if the
    prompt states only tasks, IntakeError names the missing question.
    """
    if text is None or not str(text).strip():
        raise IntakeError(
            "empty prompt: there is nothing to decompose. A run needs at least "
            "an outcome and one check that would prove it done."
        )

    intent = None
    section_kind = None
    section_heading = None
    raw_items = {KIND_CRITERIA: [], KIND_HYPOTHESIS: [], KIND_EXCLUSION: []}
    body_items = []
    current = None  # dict being folded into, or None after a blank line

    for line in str(text).splitlines():
        stripped = line.strip()
        heading = HEADING_RE.match(stripped)
        if heading:
            section_heading = heading.group(1).strip()
            section_kind = _classify_heading(section_heading)
            marker = INTENT_MARKER_RE.match(section_heading) if section_kind is None else None
            if marker is not None and intent is None:
                # `## Goal: ...` is a heading AND an intent marker; the shape
                # is read as written, not one or the other.
                intent = marker.group(1).strip()
                current = None
                continue
            if section_kind is None:
                body_items.append({"text": section_heading, "source": "heading",
                                   "kind": "heading"})
                current = None
            else:
                current = None
            continue
        if not stripped:
            current = None
            continue
        checkbox = CHECKBOX_RE.match(line)
        bullet = checkbox or BULLET_RE.match(line)
        if section_kind == KIND_INTENT:
            # Goal/Objective section: prose lives in the outcome — but a
            # checkbox is unambiguous anywhere: it is a check.
            if checkbox:
                raw_items[KIND_CRITERIA].append({"text": checkbox.group(1).strip(),
                                                 "source": "checkbox"})
            else:
                piece = bullet.group(1).strip() if bullet else stripped
                intent = piece if intent is None else intent + " " + piece
            current = None
            continue
        marker = INTENT_MARKER_RE.match(stripped) if section_kind is None else None
        if marker is not None and intent is None:
            intent = marker.group(1).strip()
            body_items.append({"text": intent, "source": "intent-marker", "kind": "paragraph"})
            current = body_items[-1]
            continue
        if checkbox and section_kind is None:
            raw_items[KIND_CRITERIA].append({"text": checkbox.group(1).strip(),
                                             "source": "checkbox"})
            current = None
            continue
        if bullet and section_kind is not None:
            target = raw_items[section_kind]
            target.append({"text": bullet.group(1).strip(),
                           "source": f"section:{section_heading}"})
            current = target[-1]
            continue
        if bullet:
            body_items.append({"text": bullet.group(1).strip(), "source": "body-bullet",
                               "kind": "bullet"})
            current = body_items[-1]
            continue
        if section_kind in (KIND_HYPOTHESIS, KIND_EXCLUSION) and _is_requirement(stripped):
            # A stated check never becomes an exclusion or a speculation: the
            # tail of a section is not a demotion.
            body_items.append({"text": stripped, "source": "body-line", "kind": "paragraph"})
            current = body_items[-1]
            continue
        if current is not None and (current.get("kind") is not None or line[:1].isspace()):
            # Body items fold like prose (sentence-splitting separates them
            # later); a section item folds only when indented — two unindented
            # lines under a section are two checks, not one.
            current["text"] += " " + stripped
            continue
        if section_kind is not None:
            target = raw_items[section_kind]
            target.append({"text": stripped,
                           "source": f"section:{section_heading}"})
            current = target[-1]
        else:
            body_items.append({"text": stripped, "source": "body-line", "kind": "paragraph"})
            current = body_items[-1]

    if intent is None:
        for i, entry in enumerate(body_items):
            if entry["kind"] == "heading":
                following = body_items[i + 1] if i + 1 < len(body_items) else None
                if following is not None and following["kind"] == "paragraph":
                    intent = f"{entry['text']}: {following['text']}"
                else:
                    intent = entry["text"]
                break
            if entry["kind"] in ("paragraph", "bullet"):
                intent = entry["text"]
                break
    if intent is None:
        raise IntakeError(
            "the prompt names no outcome: no Goal/Intent marker, no title and "
            "no opening paragraph. Say what outcome is wanted before the checks."
        )
    intent = re.sub(r"\s+", " ", intent).strip()

    criteria = list(raw_items[KIND_CRITERIA])
    criteria += _sentence_items(body_items, "requirement-sentence")
    if not criteria:
        # A bare prompt: promote what states the system's behaviour, refuse
        # what only assigns work. The intent text is not skipped — in a
        # one-sentence prompt the outcome IS the check, and losing it would
        # silently narrow the magnet.
        for entry in body_items:
            text = entry["text"].strip()
            if not text:
                continue
            if entry["kind"] == "bullet":
                criteria.append({"text": text, "source": "body-bullet"})
                continue
            if entry["kind"] == "heading" or IMPERATIVE_RE.match(text):
                continue
            for sentence in SENTENCE_SPLIT_RE.split(text):
                sentence = sentence.strip()
                if sentence and not IMPERATIVE_RE.match(sentence):
                    criteria.append({"text": sentence, "source": "body-line"})

    criteria = _finalise(criteria, KIND_CRITERIA)
    if not criteria:
        raise IntakeError(
            "the prompt states an outcome but names no check that would prove "
            "it done. Acceptance criteria, checkboxes, or requirement sentences "
            "('the parser must handle BOM') are read; task instructions "
            "('build a REST API') are not checks and cannot enter the ledger."
        )

    return {
        "intent": intent,
        "criteria": criteria,
        "hypotheses": _finalise(raw_items[KIND_HYPOTHESIS], KIND_HYPOTHESIS),
        "exclusions": [i["text"] for i in _finalise(raw_items[KIND_EXCLUSION], "exclusion")],
    }
