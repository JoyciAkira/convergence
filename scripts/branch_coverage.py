#!/usr/bin/env python3
"""Reproducible branch ("arm") coverage for the pure modules - stdlib only.

Run:  python3 scripts/branch_coverage.py
      python3 scripts/branch_coverage.py --show-missing
      python3 scripts/branch_coverage.py --fail-under 80
      python3 scripts/branch_coverage.py --json-out reports/coverage.json
      python3 scripts/branch_coverage.py --evaluate reports/coverage.json --fail-under 80

Definition (read before comparing these numbers with coverage.py):

  An *arm* is one outcome of a statement-level decision:
    - ``if`` / ``elif``  -> the body arm and the else / fall-through arm,
                            even when there is no ``else``
    - ``for`` / ``while``-> the body arm and the loop-exit arm
    - ``try``            -> the body arm ("no exception") and one arm per
                            ``except`` handler
  An arm counts as covered when the tracer observed execution *entering* it:
    - body arms: a transition from the decision's header lines into the arm's
      line span;
    - else / fall-through arms: a transition from the header lines to a line
      outside the body span, or a function return straight from the condition;
    - loop-exit arms: a transition from the header or the body to a line outside
      both;
    - except handlers: any executed line inside the handler;
    - try body arms: every statement of the body was executed.

  Not scored, because line events cannot distinguish the outcomes:
    - ``if`` whose body starts on the header line (one-liners);
    - conditional expressions (``a if c else b``);
    - ``with`` / ``assert`` / boolean short-circuits.

The suite is executed in-process so the tracer sees the imported modules; the
CLI subprocess tests still count for the assertions they make, but their own
process is not traced. Output is a table plus, optionally, the uncovered arms.

CI runs the suite once and gates twice: `--json-out PATH` traces and writes the
report, exiting with pytest's own code when the suite failed; `--evaluate PATH`
re-applies a threshold to that report without re-running the suite. Exit code is
pytest's when the suite fails, otherwise 1 when any module is under the
threshold, 0 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import traceback
from collections import defaultdict
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

DEFAULT_MODULES = ("derivation", "prompt", "obligations", "gates", "decide", "state")

RETURN = -1  # pseudo-line for "the frame returned"


def _json_default(value):
    """Arms carry their header as a set; serialize it deterministically."""
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


# --------------------------------------------------------------------------- #
# static side: where are the arms?
# --------------------------------------------------------------------------- #

def _span(body):
    """(first, last) line of a statement block, or None when empty."""
    starts = [stmt.lineno for stmt in body if getattr(stmt, "lineno", None)]
    if not starts:
        return None
    return min(starts), max(getattr(stmt, "end_lineno", stmt.lineno) for stmt in body)


def _header_lines(node, body_span):
    """Lines of the decision header (condition), before the body starts."""
    if body_span is None:
        return {node.lineno}
    return set(range(node.lineno, body_span[0])) or {node.lineno}


class _ArmCollector(ast.NodeVisitor):
    """Collect decision arms together with the spans needed to score them."""

    def __init__(self):
        self.function = "<module>"
        self.arms = []
        self.unscored = 0

    def _arm(self, kind, line, label, **meta):
        self.arms.append({
            "function": self.function, "kind": kind, "line": line,
            "label": label, **meta,
        })

    def visit_FunctionDef(self, node):
        previous = self.function
        self.function = node.name
        for child in node.body:
            self.visit(child)
        self.function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        body = _span(node.body)
        if body is None or body[0] == node.lineno:
            self.unscored += 1  # one-liner: line events cannot split the outcomes
        else:
            header = _header_lines(node, body)
            self._arm("if", node.lineno, "body", header=header, body_span=body)
            self._arm("if", node.lineno, "else/fall-through",
                      header=header, body_span=body)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)
        self.visit(node.test)

    def _loop(self, node):
        body = _span(node.body)
        if body is None:
            self.unscored += 1
            return
        header = _header_lines(node, body)
        label = "for" if isinstance(node, (ast.For, ast.AsyncFor)) else "while"
        self._arm(label, node.lineno, "body", header=header, body_span=body)
        self._arm(label, node.lineno, "exit", header=header, body_span=body)
        self.generic_visit(node)

    def visit_For(self, node):
        self._loop(node)

    visit_AsyncFor = visit_For
    visit_While = visit_For

    def visit_Try(self, node):
        body = _span(node.body)
        if body is None:
            self.unscored += 1
        else:
            self._arm("try", node.lineno, "body (no exception)",
                      body_span=body,
                      statement_lines=[stmt.lineno for stmt in node.body])
        for handler in node.handlers:
            span = _span(handler.body)
            if span is not None:
                self._arm("try", handler.lineno, "except handler",
                          body_span=body, handler_span=span)
        self.generic_visit(node)


def collect_arms(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    collector = _ArmCollector()
    collector.visit(tree)
    return collector.arms, collector.unscored


# --------------------------------------------------------------------------- #
# dynamic side: what ran?
# --------------------------------------------------------------------------- #

def trace_suite(targets):
    """Run the whole test suite under settrace, keeping edges for targets only."""
    edges = defaultdict(set)
    executed = defaultdict(set)
    last_line = {}

    def local_trace(frame, event, _arg):
        filename = frame.f_code.co_filename
        if event == "line":
            current = frame.f_lineno
            previous = last_line.get(id(frame))
            if previous is not None:
                edges[filename].add((previous, current))
            last_line[id(frame)] = current
            executed[filename].add(current)
        elif event == "return":
            previous = last_line.pop(id(frame), None)
            if previous is not None:
                edges[filename].add((previous, RETURN))
            executed[filename].add(frame.f_lineno)
        return local_trace

    def global_trace(frame, event, _arg):
        if event == "call" and frame.f_code.co_filename in targets:
            return local_trace
        return None

    import pytest

    previous_trace = sys.gettrace()
    sys.settrace(global_trace)
    try:
        code = pytest.main([os.path.join(ROOT, "tests"), "-q", "--tb=short",
                            "-p", "no:cacheprovider"])
    finally:
        sys.settrace(previous_trace)
    return code, edges, executed


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

def arm_hit(arm, edges, executed):
    """True when the tracer observed execution entering this arm."""
    kind = arm["kind"]
    if kind == "if":
        header, body = arm["header"], arm["body_span"]
        if arm["label"] == "body":
            return any(src in header and body[0] <= dst <= body[1]
                       for src, dst in edges)
        # else / fall-through, including a return straight from the condition
        return any(src in header and dst not in header
                   and not (body[0] <= dst <= body[1]) and dst != RETURN
                   for src, dst in edges) or any(
            src in header and dst == RETURN for src, dst in edges)

    if kind in ("for", "while"):
        header, body = arm["header"], arm["body_span"]
        if arm["label"] == "body":
            return any(src in header and body[0] <= dst <= body[1]
                       for src, dst in edges)
        loop = header | set(range(body[0], body[1] + 1))
        return any((src in header or src in loop) and dst not in loop
                   for src, dst in edges)

    if kind == "try":
        if arm["label"] == "body (no exception)":
            statements = arm["statement_lines"]
            return bool(statements) and all(line in executed for line in statements)
        low, high = arm["handler_span"]
        return any(low <= line <= high for line in executed)

    return False


def score(arms, edges, executed):
    covered, missing = 0, []
    for arm in arms:
        if arm_hit(arm, edges, executed):
            covered += 1
        else:
            missing.append(arm)
    return covered, missing


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def _write_payload(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )


def _print_payload(payload, show_missing, json_only):
    """Render a report and return the modules below its threshold."""
    results = payload["modules"]
    threshold = payload["threshold"]
    failed = [row["module"] for row in results if row["percent"] < threshold]
    if json_only:
        print(json.dumps(payload, indent=2, default=_json_default))
        return failed
    print(f"{'module':<14}{'arms':>6}{'covered':>9}{'coverage':>11}{'unscored':>10}")
    for row in results:
        print(f"{row['module']:<14}{row['arms']:>6}{row['covered']:>9}"
              f"{row['percent']:>10.1f}%{row['unscored']:>10}")
    if show_missing:
        for row in results:
            if not row.get("missing"):
                continue
            print(f"\nuncovered arms - {row['module']}:")
            for arm in row["missing"]:
                print(f"  line {arm['line']:>5}  {arm['function']}  "
                      f"{arm['kind']}  {arm['label']}")
    if failed:
        print(f"\nFAIL: below {threshold:g}% -> {', '.join(failed)}")
    else:
        print(f"\nOK: every module is at or above {threshold:g}%")
    print(f"(suite exit code {payload.get('pytest', 0)})")
    return failed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modules", default=",".join(DEFAULT_MODULES),
                        help="comma-separated module names under lib/ (default: %(default)s)")
    parser.add_argument("--fail-under", type=float, default=80.0,
                        help="exit 1 when a module is below this percentage (default: %(default)s)")
    parser.add_argument("--show-missing", action="store_true",
                        help="list the uncovered arms")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--json-out", metavar="PATH",
                        help="also write the machine-readable report to PATH")
    parser.add_argument("--evaluate", metavar="PATH",
                        help="gate an existing --json-out report instead of tracing the suite")
    args = parser.parse_args(argv)

    if args.evaluate:
        try:
            with open(args.evaluate, encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["threshold"] = args.fail_under
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            print(f"branch-coverage: cannot evaluate {args.evaluate}: {exc}",
                  file=sys.stderr)
            return 2
        failed = _print_payload(payload, args.show_missing, args.json)
        return 1 if failed else 0

    modules = [name.strip() for name in args.modules.split(",") if name.strip()]
    paths = {os.path.realpath(os.path.join(ROOT, "lib", f"{name}.py")): name
             for name in modules}
    for path in paths:
        if not os.path.exists(path):
            print(f"branch-coverage: no such module: {path}", file=sys.stderr)
            return 2

    arms_by_module = {}
    unscored = {}
    for path, name in paths.items():
        arms, skipped = collect_arms(path)
        arms_by_module[name] = arms
        unscored[name] = skipped

    code, edges, executed = trace_suite(set(paths))

    results = []
    failed = []
    for path, name in paths.items():
        arms = arms_by_module[name]
        covered, missing = score(arms, edges[path], executed[path])
        total = len(arms)
        percent = (100.0 * covered / total) if total else 100.0
        results.append({
            "module": name,
            "arms": total,
            "covered": covered,
            "percent": round(percent, 1),
            "unscored": unscored[name],
            "missing": missing,
        })
        if percent < args.fail_under:
            failed.append(name)

    payload = {"threshold": args.fail_under, "pytest": int(code), "modules": results}
    if args.json_out:
        _write_payload(args.json_out, payload)
    failed = _print_payload(payload, args.show_missing, args.json)
    if int(code) != 0:
        return int(code)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - diagnostics only
        traceback.print_exc()
        sys.exit(2)
