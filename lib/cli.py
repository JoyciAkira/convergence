"""CLI dispatch: exit 0 applied, 2 malformed, 3 a gate refused."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from cli_parser import _build_parser, _options
from constants import BLOCKED, LOGS, OK, USAGE
from exceptions import Blocked, IntakeError, UsageError
from ledger import Ledger
from state import req

def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except Blocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return BLOCKED
    except IntakeError as exc:
        print(f"INTAKE: {exc}", file=sys.stderr)
        return USAGE
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return USAGE

def _one_shot(args):
    """The minimal honest sequence for a small, fully reproduced defect, batched.

    Every step delegates to the same gate-checked Ledger methods the individual
    commands use — nothing is weakened. What is removed is the ceremony: one
    invocation instead of eight. --search defaults to narrow, so the disclosure
    ("fix chosen without a search over rivals") still lands in the report.
    Idempotent per step: re-running a completed step skips it instead of
    refusing, so an interrupted batch resumes rather than wedges.
    """
    root = Path(args.ledger)
    if not (root / LOGS["control"]).exists():
        intent = args.intent or f"fix {args.incident}: {args.symptom}"
        criteria = list(args.criterion or [])
        try:
            Ledger.validate_intent(intent, criteria)
        except IntakeError as exc:
            print(json.dumps({"shape_ok": False, "error": str(exc)}, indent=2))
            return USAGE
        Ledger.create(root, intent, criteria)
    ledger = Ledger(root)
    incident = req(args.incident, "--incident")
    steps = []
    if incident not in ledger.state["loops"]:
        ledger.declare_loop(incident, args.loop_command, args.symptom)
        steps.append("declare-loop")
    if not ledger.state["loops"][incident]["ran"]:
        ledger.run_loop(incident, True)
        steps.append("run-loop(red=true)")
    hyp_id = f"{incident}-H1"
    if hyp_id not in ledger.state["hypotheses"]:
        ledger.open_hypothesis(hyp_id, incident, args.claim, args.prediction)
        steps.append("open-hypothesis")
    if ledger.state["hypotheses"][hyp_id]["status"] == "open":
        ledger.close_hypothesis(hyp_id, args.probe, args.observed, survives=True)
        steps.append("survive-hypothesis")
    ledger.begin_fix(incident, args.fix_note, search=args.search)
    steps.append(f"begin-fix(--search {args.search})")
    criterion = args.evidence_criterion or (
        ledger.state["criteria"][0] if ledger.state["criteria"] else None
    )
    if criterion is None:
        raise UsageError(
            "no criterion to close: pass --evidence-criterion, or create the ledger "
            "with --criterion"
        )
    ledger.record_evidence(
        criterion,
        args.evidence_command,
        args.evidence_tier,
        expected_source=args.expected_source,
    )
    steps.append(f"record-evidence({criterion!r})")
    print(json.dumps(
        {"steps": steps, "next": ledger.next_action()},
        indent=2, sort_keys=True,
    ))
    return OK

def _dispatch(args):
    if args.subcommand == "init":
        from prompt import criteria_from_text, load_text, parse_prompt

        prompt_shape = None
        if args.prompt:
            prompt_shape = parse_prompt(args.prompt)
            intent = prompt_shape["intent"]
            criteria = [c["text"] for c in prompt_shape["criteria"]]
        elif args.prompt_file:
            prompt_shape = parse_prompt(load_text(args.prompt_file))
            intent = prompt_shape["intent"]
            criteria = [c["text"] for c in prompt_shape["criteria"]]
        else:
            intent = load_text(args.intent_file) if args.intent_file else args.intent
            criteria = []
        if args.criteria_file:
            criteria += [c["text"] for c in criteria_from_text(load_text(args.criteria_file))]
        criteria += list(args.criterion or [])
        # merge sources without duplicates; order preserved, first spelling wins
        seen = set()
        criteria = [c for c in (str(c).strip() for c in criteria)
                    if c and not (c.casefold() in seen or seen.add(c.casefold()))]

        if getattr(args, "intake", False):
            try:
                validated = Ledger.validate_intent(intent, criteria)
            except IntakeError as exc:
                print(json.dumps({"shape_ok": False, "error": str(exc)}, indent=2))
                return USAGE
            from prompt import critic_flags, interview_questions
            questions = interview_questions(validated["intent"], validated["criteria"])
            critic = critic_flags(validated["intent"], validated["criteria"])
            shape = {
                "intent": validated["intent"],
                "criteria": validated["criteria"],
                "shape_ok": True,
                "needs_interview": bool(questions or critic),
                "questions": questions,
                "critic": critic,
                "next": ("answer the questions and re-run --intake until needs_interview "
                           "is false" if questions or critic else
                           "freeze the ledger with init, then drive the loop"),
            }
            if prompt_shape is not None:
                shape["criteria_provenance"] = prompt_shape["criteria"]
                shape["hypotheses"] = prompt_shape["hypotheses"]
                shape["exclusions"] = prompt_shape["exclusions"]
            print(json.dumps(shape, indent=2))
            return OK
        from prompt import critic_flags
        critic = critic_flags(intent, criteria)
        Ledger.create(args.ledger, intent, criteria,
                      exploratory=getattr(args, "exploratory", False), critic=critic)
        if prompt_shape is not None:
            print(json.dumps({"created": args.ledger, "shape_ok": True,
                              "intent": prompt_shape["intent"],
                              "criteria": [c["text"] for c in prompt_shape["criteria"]],
                              "criteria_provenance": prompt_shape["criteria"],
                              "hypotheses": prompt_shape["hypotheses"],
                              "exclusions": prompt_shape["exclusions"]}, indent=2))
        else:
            print(f"initialised {args.ledger}")
        return OK

    if args.subcommand == "one-shot":
        return _one_shot(args)

    ledger = Ledger(args.ledger)
    command = args.subcommand

    if command == "declare-loop":
        ledger.declare_loop(args.incident, args.command, args.symptom)
    elif command == "run-loop":
        ledger.run_loop(args.incident, args.red == "true")
    elif command == "open-hypothesis":
        ledger.open_hypothesis(args.id, args.incident, args.claim, args.prediction, args.rank)
    elif command == "refute-hypothesis":
        ledger.close_hypothesis(args.id, args.probe, args.observed, survives=False)
    elif command == "survive-hypothesis":
        ledger.close_hypothesis(args.id, args.probe, args.observed, survives=True)
    elif command == "merge-hypotheses":
        ledger.merge_hypotheses(args.into, args.source, args.note)
    elif command == "begin-fix":
        ledger.begin_fix(args.incident, args.note, search=args.search)
    elif command == "classify":
        ledger.classify(
            args.signature, args.kind, args.missing_fact, args.smallest_input,
            tried=args.tried, park=args.park, note=args.note, only_route=args.only_route,
        )
    elif command == "resolve-block":
        ledger.resolve_block(args.signature, args.provided)
    elif command == "open-fork":
        ledger.open_fork(
            args.id,
            args.question,
            args.trigger,
            args.assumed,
            _options(args.option),
            args.invalidation_rationale,
            criterion_from=args.criterion_from,
            criterion_to=args.criterion_to,
        )
    elif command == "dispute":
        ledger.dispute(args.fork, args.challenger, args.author, args.steelman, args.verdict)
    elif command == "decide":
        ledger.decide(
            args.fork,
            args.chosen,
            args.by,
            human_ack=args.human_ack,
            reversibility=args.reversibility,
            anchors=args.anchor,
            invalidated=args.invalidated,
            rationale=args.rationale,
            challenger_overruled=args.challenger_overruled,
            override_reason=args.override_reason,
        )
    elif command == "record-evidence":
        ledger.record_evidence(
            args.criterion,
            args.command,
            args.tier,
            recorder=args.recorder,
            reproduced_by=args.reproduced_by,
            expected_source=args.expected_source,
            reason=args.reason,
            output_ref=args.output_ref,
        )
    elif command == "record-candidates":
        ledger.record_candidates(
            args.run_id, args.source, args.count,
            note=args.note, files=args.file,
        )
        print(f"recorded {args.count} candidate(s) from {args.source} run {args.run_id!r}")
        return OK
    elif command == "select-candidate":
        ledger.select_candidate(
            args.run_id, args.id, args.by, args.method, fork=args.fork,
        )
        print(f"selected candidate {args.id!r} of run {args.run_id!r} (by {args.by})")
        return OK
    elif command == "record-risk":
        ledger.record_risk(args.text, args.blocking)
    elif command == "provision-env":
        ledger.provision_env(args.spec, args.output_var, args.command)
    elif command == "provision-env-catalog":
        ledger.provision_env_catalog(args.spec, args.output_var, args.command)
    elif command == "inject-build-context":
        ledger.inject_build_context(
            args.target, args.source, args.artifacts, args.command,
        )
    elif command == "store-secret":
        ledger.store_secret(args.name, args.command)
    elif command == "boot-runtime":
        ledger.boot_runtime(
            args.name, args.command, cwd=args.cwd, port=args.port, path=args.path,
            timeout=args.timeout, resolve_signature=args.resolve_signature,
            criterion=args.criterion,
        )
    elif command == "stop-runtime":
        ledger.stop_runtime(args.name)
    elif command == "validate-infra":
        result = ledger.validate_infra(fix=getattr(args, 'fix', False))
        print(json.dumps(result, indent=2, sort_keys=True))
        return OK if result["ready"] else BLOCKED
    elif command == "status":
        state = ledger.state
        section = getattr(args, "section", None)
        criterion = getattr(args, "criterion", None)
        limit = getattr(args, "limit", None)
        if criterion:
            ev = [e for e in state.get("evidence", []) if e.get("criterion") == criterion]
            latest = ev[-1]["tier"] if ev else None
            out = {"criterion": criterion,
                   "verified": latest is not None and latest != "unavailable",
                   "latest_tier": latest,
                   "evidence": ev if not limit else ev[:limit]}
        elif section:
            if section == "frontier":
                from derivation import compute_frontier
                out = {"frontier": compute_frontier(state)}
            elif section not in state:
                raise UsageError(f"unknown section {section!r}; top-level keys: {sorted(list(state) + ['frontier'])}")
            else:
                val = state[section]
                out = {section: (val[:limit] if isinstance(val, list) and limit else val)}
        else:
            out = state
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return OK
    elif command == "next-action":
        print(json.dumps(ledger.next_action(), indent=2, sort_keys=True))
        return OK
    elif command == "route":
        print(json.dumps(ledger.pipeline_route(), indent=2, sort_keys=True))
        return OK
    elif command == "import-tickets":
        ledger.import_tickets(args.tickets)
        for t in ledger.state["tickets"]:
            print(f"ticket {t['id']}: {t['title']} -> {len(t['criteria'])} criterion/criteria")
        return OK
    elif command == "import-obligations":
        ledger.import_obligations(args.obligations)
        for o in ledger.state.get("obligations", []):
            print(f"obligation {o['id']}: {o['title']} -> {len(o['criteria'])} criterion/criteria")
        return OK
    elif command == "freeze-intent":
        ledger.freeze_intent()
        print(f"intent frozen at {args.ledger}")
        return OK
    elif command == "close":
        report = ledger.report()
        (ledger.root / "report.md").write_text(report, encoding="utf-8")
        print(report)
        status = ledger.outcome()[0]
        if status != "REACHED":
            ledger.record_meta_fail(
                signature=f"run-{int(time.time())}",
                failure_kind="not_reached",
            )
        return OK if status == "REACHED" else BLOCKED
    else:  # pragma: no cover - argparse already constrains this
        raise UsageError(f"unknown command {command!r}")

    print(f"{command}: ok")
    return OK
