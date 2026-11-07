"""Argument surface of the ledger CLI.

Flag names are part of the skill's contract: SKILL.md documents them, and
`scripts/skill_drift.py` fails when a documented command has no parser entry.
"""
from __future__ import annotations

import argparse
import json

from constants import (
    CANDIDATE_SOURCES,
    DIRECTIVE_REQUIRES,
    EVIDENCE_TIERS,
    EXPECTED_SOURCES,
    REVERSIBILITIES,
    SEARCH_WIDTH,
)
from exceptions import UsageError

def _options(values):
    parsed = []
    for raw in values or []:
        try:
            option = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UsageError(
                f"--option must be a JSON object with id, cost, risk, reversibility: {raw!r}"
            ) from exc
        if not isinstance(option, dict):
            raise UsageError(f"--option must be a JSON object, got {type(option).__name__}")
        parsed.append(option)
    return parsed

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="convergence",
        description="Append-only ledger enforcing hypothesis elimination, fork disputes, "
        "non-convergence classification and evidence tiers.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    def add(name, help_text):
        node = sub.add_parser(name, help=help_text)
        node.add_argument("ledger")
        return node

    node = add("init", "create a ledger")
    source = node.add_mutually_exclusive_group(required=True)
    source.add_argument("--intent", help="what outcome is actually wanted")
    source.add_argument("--intent-file",
                        help="read the intent from a file, or '-' for stdin: no argv "
                             "size limit, multiline, unicode and quotes survive intact")
    source.add_argument("--prompt",
                        help="decompose a raw prompt string into intent + acceptance criteria")
    source.add_argument("--prompt-file",
                        help="decompose a whole prompt (file, or '-' for stdin) into "
                             "intent + acceptance criteria + candidate hypotheses; "
                             "every prompt shape is accepted, the shape is reported")
    node.add_argument("--criterion", action="extend", nargs="+", default=[])
    node.add_argument("--criteria-file",
                      help="read criteria from a file, or '-' for stdin: bullets, "
                           "checkboxes and plain lines each become one criterion")
    node.add_argument("--intake", action="store_true",
                      help="validate the requested magnet without creating a ledger")
    node.add_argument("--exploratory", action="store_true",
                      help="declare the run exploratory: a fix may not begin until "
                           "candidate solutions are recorded (record-candidates) and "
                           "one is selected (select-candidate)")

    node = add("record-candidates",
               "record candidate solutions from an exploration pass (arena, parallel "
               "agents, manual drafts)")
    node.add_argument("--run-id", required=True)
    node.add_argument("--source", required=True, choices=CANDIDATE_SOURCES)
    node.add_argument("--count", required=True, type=int)
    node.add_argument("--file", action="extend", nargs="+", default=[],
                      help="where the candidates live; repeatable")
    node.add_argument("--note")

    node = add("select-candidate", "record which candidate solution the run pursues")
    node.add_argument("--run-id", required=True)
    node.add_argument("--id", required=True)
    node.add_argument("--by", required=True, choices=("disputed", "author"))
    node.add_argument("--method", required=True,
                      help="how the choice was made, e.g. 'arena run r7 winner' "
                           "or 'rubric scores after review'")
    node.add_argument("--fork", help="the disputed fork the selection was made on "
                                     "(required with --by disputed)")

    node = add("one-shot",
               "the minimal honest sequence for a small, fully reproduced defect in "
               "one command: init + declare/run loop + one settled hypothesis + "
               "begin-fix (--search narrow) + evidence. --red-observed must be true.")
    node.add_argument("--incident", required=True)
    node.add_argument("--loop-command", required=True,
                      help="the red-capable command, run on the exact symptom")
    node.add_argument("--symptom", required=True)
    node.add_argument("--red-observed", required=True, choices=("true",),
                      help="the loop must actually have gone red; 'false' has no "
                           "one-shot path")
    node.add_argument("--claim", required=True)
    node.add_argument("--prediction", required=True)
    node.add_argument("--probe", required=True)
    node.add_argument("--observed", required=True,
                      help="what the probe actually showed (green)")
    node.add_argument("--fix-note", required=True)
    node.add_argument("--search", default="narrow", choices=tuple(SEARCH_WIDTH))
    node.add_argument("--evidence-command", required=True)
    node.add_argument("--evidence-tier", default="executed_here", choices=EVIDENCE_TIERS)
    node.add_argument("--expected-source", default="literal", choices=EXPECTED_SOURCES)
    node.add_argument("--evidence-criterion",
                      help="which criterion the evidence closes; defaults to the first")
    node.add_argument("--intent", help="used only when the ledger does not exist yet")
    node.add_argument("--criterion", action="extend", nargs="+", default=[],
                      help="used only when the ledger does not exist yet")

    node = add("freeze-intent", "seal the right edge after init")

    node = add("declare-loop", "declare the feedback loop for an incident")
    node.add_argument("--incident", required=True)
    node.add_argument("--command", required=True)
    node.add_argument("--symptom", required=True)

    node = add("run-loop", "record that the declared loop was actually run")
    node.add_argument("--incident", required=True)
    node.add_argument("--red", required=True, choices=("true", "false"))

    node = add("open-hypothesis", "record a falsifiable hypothesis")
    node.add_argument("--id", required=True)
    node.add_argument("--incident", required=True)
    node.add_argument("--claim", required=True)
    node.add_argument("--prediction", required=True)
    node.add_argument("--rank", type=int)

    for name in ("refute-hypothesis", "survive-hypothesis"):
        node = add(name, "close a hypothesis with an executed probe")
        node.add_argument("--id", required=True)
        node.add_argument("--probe", required=True)
        node.add_argument("--observed", required=True)

    node = add("merge-hypotheses", "two lanes reduced to one mechanism")
    node.add_argument("--into", required=True)
    node.add_argument("--from", dest="source", required=True)
    node.add_argument("--note", required=True)

    node = add("begin-fix", "open the fix gate for an incident")
    node.add_argument("--incident", required=True)
    node.add_argument("--note", required=True)
    node.add_argument("--search", default="full", choices=tuple(SEARCH_WIDTH),
                      help="full: search over rival explanations (default). "
                           "narrow: the defect is fully reproduced and its mechanism is "
                           "unambiguous; the run then cannot close as a clean COMPLETE.")

    node = add("classify", "classify a repeated failure signature")
    node.add_argument("--signature", required=True)
    node.add_argument(
        "--kind", required=True, choices=tuple(sorted(DIRECTIVE_REQUIRES))
    )
    node.add_argument("--missing-fact")
    node.add_argument("--smallest-input")
    node.add_argument("--tried", action="extend", nargs="+", default=[],
                      help="distinct routes already attempted; a block must be earned")
    node.add_argument("--park",
                      help="the path this block parks (a fork id, incident id, or tag). "
                           "The run continues elsewhere.")
    node.add_argument("--only-route",
                      help="when there is genuinely one way to reach the fact — a missing "
                           "binary, a permission you do not hold — say why here instead of "
                           "listing routes that do not exist. Disclosed in the report.")
    node.add_argument("--note")

    node = add("resolve-block", "clear a missing-fact block once the input arrives")
    node.add_argument("--signature", required=True)
    node.add_argument("--provided", required=True)

    node = add("open-fork", "record a decision fork discovered mid-execution")
    node.add_argument("--id", required=True)
    node.add_argument("--question", required=True)
    node.add_argument("--trigger", required=True)
    node.add_argument("--assumed", required=True)
    node.add_argument("--option", action="extend", nargs="+", default=[])
    node.add_argument("--invalidation-rationale")
    node.add_argument("--criterion-from",
                      help="move the right edge: the criterion being revised")
    node.add_argument("--criterion-to", help="move the right edge: its replacement")

    node = add("dispute", "record an adversarial pass against the favoured option")
    node.add_argument("--fork", required=True)
    node.add_argument("--challenger", required=True)
    node.add_argument("--author", required=True)
    node.add_argument("--steelman", required=True)
    node.add_argument("--verdict")

    node = add("decide", "close a fork")
    node.add_argument("--fork", required=True)
    node.add_argument("--chosen", required=True)
    node.add_argument("--by", required=True, choices=("agent", "human"))
    node.add_argument("--human-ack", action="store_true")
    node.add_argument("--reversibility", choices=REVERSIBILITIES)
    node.add_argument("--anchor", action="extend", nargs="+", default=[])
    node.add_argument("--invalidated", action="extend", nargs="+", default=[])
    node.add_argument("--rationale")
    node.add_argument("--challenger-overruled", action="store_true",
                      help="the dispute's objection did not hold; requires --override-reason "
                           "and downgrades the final verdict to COMPLETE WITH RISKS")
    node.add_argument("--override-reason")

    node = add("record-evidence", "record a verification result")
    node.add_argument("--criterion", required=True)
    node.add_argument("--command", required=True)
    node.add_argument("--tier", required=True, choices=EVIDENCE_TIERS)
    node.add_argument("--recorder")
    node.add_argument("--reproduced-by")
    node.add_argument("--expected-source", choices=EXPECTED_SOURCES)
    node.add_argument("--reason")
    node.add_argument("--output-ref")

    node = add("record-risk", "disclose residual or blocking risk")
    node.add_argument("--text", required=True)
    node.add_argument("--blocking", action="store_true")

    node = add("provision-env", "provision an external dependency on-demand")
    node.add_argument("--spec", required=True,
                      help="what was provisioned (e.g. neon-db, vercel-project)")
    node.add_argument("--output-var", required=True,
                      help="environment variable name holding the result")
    node.add_argument("--command", required=True,
                      help="the command that created the resource")

    node = add("provision-env-catalog", "provision from the built-in spec catalog")
    node.add_argument("--spec", required=True,
                      help="catalog spec id (e.g. postgres, redis, sqlite, s3, mail)")
    node.add_argument("--output-var", required=True,
                      help="environment variable name holding the result")
    node.add_argument("--command",
                      help="if omitted, uses the catalog placeholder command")

    node = add("inject-build-context", "copy host build deps into isolated test dir")
    node.add_argument("--target", required=True,
                      help="directory receiving the injected context")
    node.add_argument("--source", required=True,
                      help="host project directory providing the context")
    node.add_argument("--artifacts", action="extend", nargs="+", default=[],
                      help="items injected (node_modules, tsconfig.json, type defs)")
    node.add_argument("--command", required=True,
                      help="the command that performed the injection")

    node = add("store-secret", "store a secret for secure criterion verification")
    node.add_argument("--name", required=True,
                      help="secret identifier referenced in evidence commands")
    node.add_argument("--command", required=True,
                      help="retrieval command (value never touches ledger or transcript)")

    node = add("boot-runtime", "start a local runtime on a free port and wait for its listener")
    node.add_argument("--name", required=True,
                      help="runtime identifier (e2e-sandbox, preview-server)")
    node.add_argument("--command", required=True,
                      help='startup command; a {port} placeholder is substituted, '
                           'e.g. "npx next dev -p {port}"')
    node.add_argument("--cwd", help="working directory for the command")
    node.add_argument("--port", type=int,
                      help="pin the port (must be free); default: pick a free one")
    node.add_argument("--path", default="/",
                      help="URL path the readiness evidence points at (default /)")
    node.add_argument("--timeout", type=float, default=30.0,
                      help="seconds to wait for the listener (default 30)")
    node.add_argument("--resolve-signature",
                      help="also resolve-block this signature once the listener is up")
    node.add_argument("--criterion",
                      help="also record executed_here listener evidence for this criterion")

    node = add("stop-runtime", "stop a runtime previously started with boot-runtime")
    node.add_argument("--name", required=True,
                      help="the runtime identifier given to boot-runtime")

    node = add("validate-infra", "preflight check: verify all infra prerequisites are available")
    node.add_argument("--fix", action="store_true",
                      help="attempt to restore missing prerequisites by re-running "
                           "recorded provision/injection commands")

    node = add("status", "print the derived state as JSON")
    node.add_argument("--section", help="limit output to one top-level section (e.g. criteria)")
    node.add_argument("--criterion", help="show only this criterion and its evidence")
    node.add_argument("--limit", type=int, help="cap list sections to N rows (default: all)")
    add("next-action", "print what the ledger requires next")
    add("route", "print the pipeline lane and its fallback as JSON")
    add("close", "compute the final status and print the report")

    node = add("import-tickets", "map a to-tickets issue directory onto criteria")
    node.add_argument("--tickets", required=True, help="directory of ticket files")

    node = add("import-obligations", "map reverse engineering obligations onto criteria")
    node.add_argument("--obligations", required=True, help="path to obligations JSON or directory")
    return parser
