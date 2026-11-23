#!/usr/bin/env python3
"""Deterministic consistency gate for the agent-sql specification (stdlib only).

Checks:
  1. required documents exist, are non-empty, and carry a Status line
  2. no placeholder tokens and no over-claim phrases
  3. relative markdown links resolve
  4. tool names referenced in BASELINES/ARCHITECTURE/IMPLEMENTATION_PLAN are defined in TOOLS
  5. metric ids (M01-M12, S01-S07) referenced anywhere are defined in BENCHMARK
  6. failure codes (F-*) referenced anywhere are defined in BENCHMARK
  7. risk ids (R01..) referenced anywhere are defined in RISKS
  8. systems B0/B1/A1/A2 are defined in BASELINES; ablations AB1..AB9 are defined in EXPERIMENTS
  9. canonical parameters table in EXPERIMENTS equals docs/spec/parameters.json
 10. phase contracts are valid (ai-native-engineering contract rules + approved_paths)
 11. requirement manifests reference gates that exist in their phase contract
 12. work frontier: valid owners/statuses, human fields, acyclic, deps exist, no dependency on a later
     phase, autonomous items point at an existing contract

Exit code 0 when no errors; 1 otherwise. Prints one error per line.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPEC_DOCS = ["PRD.md", "ARCHITECTURE.md", "BASELINES.md", "TOOLS.md", "BENCHMARK.md", "EXPERIMENTS.md",
             "DATA_MODEL.md", "IMPLEMENTATION_PLAN.md", "RISKS.md"]
REQUIRED_TOOLS = ["list_tables", "describe_table", "inspect_relationships", "sample_rows",
                  "execute_readonly_sql", "get_query_result_metadata", "validate_sql", "compare_query_results"]
CONTROL_TOOLS = ["submit_plan", "complete_step", "submit_answer"]
TOOL_PREFIXES = ("list_", "describe_", "inspect_", "sample_", "execute_", "get_", "validate_", "compare_",
                 "submit_", "complete_")
SYSTEMS = ["B0", "B1", "A1", "A2"]
PRIMARY_METRICS = [f"M{i:02d}" for i in range(1, 13)]
SECONDARY_METRICS = [f"S{i:02d}" for i in range(1, 8)]
PHASES = range(7)

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum)\b", re.IGNORECASE)
OVERCLAIM_RE = re.compile(r"state[- ]of[- ]the[- ]art|\bSOTA\b|guaranteed (accuracy|improvement)|proven to "
                          r"outperform|outperforms? (?:b0|b1|baselines?) by", re.IGNORECASE)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
BACKTICK_RE = re.compile(r"`([a-z][a-z0-9_]*)`")
METRIC_REF_RE = re.compile(r"\b([MS]\d{2})\b")
FAILURE_REF_RE = re.compile(r"\bF-[A-Z]+(?:-[A-Z]+)*\b")
RISK_REF_RE = re.compile(r"\bR(\d{2})\b")
ABLATION_REF_RE = re.compile(r"\bAB(\d+)\b")
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
GATE_LEVELS = {"static", "unit", "integration", "e2e", "device", "visual", "performance"}
WORK_STATUSES = {"NOT_RUN", "RUNNING", "PASS", "FAIL", "BLOCKED_EXTERNAL", "HUMAN_REQUIRED", "NOT_APPLICABLE"}
OWNERS = {"AUTONOMOUS", "HUMAN_PHYSICAL", "HUMAN_DECISION", "HUMAN_CREDENTIAL", "EXTERNAL_RESOURCE"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_code(text: str) -> str:
    """Remove fenced code blocks (examples there are not prose claims)."""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def check_documents(root: Path) -> list[str]:
    errors = []
    spec = root / "docs" / "spec"
    for name in SPEC_DOCS:
        path = spec / name
        if not path.is_file() or not _read(path).strip():
            errors.append(f"missing or empty spec document: docs/spec/{name}")
            continue
        head = "\n".join(_read(path).splitlines()[:5])
        if "**Status:**" not in head:
            errors.append(f"docs/spec/{name}: missing '**Status:**' line in the first 5 lines")
    if not (root / "README.md").is_file():
        errors.append("missing README.md")
    return errors


def all_markdown(root: Path) -> list[Path]:
    files = [root / "README.md", root / "AGENTS.md"]
    files += [root / "docs" / "spec" / n for n in SPEC_DOCS]
    return [f for f in files if f.is_file()]


def check_language(root: Path) -> list[str]:
    errors = []
    for path in all_markdown(root):
        rel = path.relative_to(root)
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            if PLACEHOLDER_RE.search(line):
                errors.append(f"{rel}:{lineno}: placeholder token: {line.strip()[:80]}")
            if OVERCLAIM_RE.search(line):
                errors.append(f"{rel}:{lineno}: over-claim phrase: {line.strip()[:80]}")
    return errors


def check_links(root: Path) -> list[str]:
    errors = []
    for path in all_markdown(root):
        rel = path.relative_to(root)
        for target in LINK_RE.findall(_strip_code(_read(path))):
            if re.match(r"^[a-z]+://", target) or target.startswith(("#", "mailto:")):
                continue
            file_part = target.split("#", 1)[0]
            if file_part and not (path.parent / file_part).exists():
                errors.append(f"{rel}: broken relative link: {target}")
    return errors


def defined_tools(tools_md: str) -> set[str]:
    return set(re.findall(r"^###\s+`([a-z_]+)`", tools_md, flags=re.MULTILINE))


def check_tools(root: Path) -> list[str]:
    spec = root / "docs" / "spec"
    defined = defined_tools(_read(spec / "TOOLS.md"))
    errors = [f"TOOLS.md: required tool not defined as '### `{t}`'" for t in REQUIRED_TOOLS + CONTROL_TOOLS
              if t not in defined]
    for name in ("BASELINES.md", "ARCHITECTURE.md", "IMPLEMENTATION_PLAN.md", "PRD.md"):
        for token in BACKTICK_RE.findall(_read(spec / name)):
            if token.startswith(TOOL_PREFIXES) and token not in defined:
                errors.append(f"{name}: references undefined tool `{token}`")
    return errors


def check_ids(root: Path) -> list[str]:
    errors = []
    spec = root / "docs" / "spec"
    bench = _read(spec / "BENCHMARK.md")
    risks = _read(spec / "RISKS.md")
    experiments = _read(spec / "EXPERIMENTS.md")
    baselines = _read(spec / "BASELINES.md")

    metric_defs = set(re.findall(r"^###\s+(M\d{2})\b", bench, flags=re.MULTILINE))
    metric_defs |= set(re.findall(r"^\|\s*(S\d{2})\s*\|", bench, flags=re.MULTILINE))
    for m in PRIMARY_METRICS + SECONDARY_METRICS:
        if m not in metric_defs:
            errors.append(f"BENCHMARK.md: metric {m} not defined")
    failure_defs = set(re.findall(r"^\|\s*(F-[A-Z-]+)\s*\|", bench, flags=re.MULTILINE))
    risk_defs = set(re.findall(r"^\|\s*R(\d{2})\s*\|", risks, flags=re.MULTILINE))
    ablation_defs = set(re.findall(r"^###\s+AB(\d+)\b", experiments, flags=re.MULTILINE))
    for system in SYSTEMS:
        if not re.search(rf"^###\s+{system}\b", baselines, flags=re.MULTILINE):
            errors.append(f"BASELINES.md: system {system} not defined as a '### {system}' heading")

    for path in all_markdown(root):
        rel = path.relative_to(root)
        text = _read(path)
        for ref in set(METRIC_REF_RE.findall(text)):
            if ref not in metric_defs:
                errors.append(f"{rel}: references undefined metric {ref}")
        for ref in set(FAILURE_REF_RE.findall(text)):
            if ref not in failure_defs:
                errors.append(f"{rel}: references undefined failure code {ref}")
        for ref in set(RISK_REF_RE.findall(text)):
            if ref not in risk_defs:
                errors.append(f"{rel}: references undefined risk R{ref}")
        for ref in set(ABLATION_REF_RE.findall(text)):
            if ref not in ablation_defs:
                errors.append(f"{rel}: references undefined ablation AB{ref}")
    return errors


def parse_parameter_table(experiments_md: str) -> dict[str, str]:
    section = experiments_md.split("## 4. Canonical configuration", 1)
    if len(section) < 2:
        return {}
    body = section[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*([^|]+?)\s*\|", body, flags=re.MULTILINE)
    return {k: v for k, v in rows}


def check_parameters(root: Path) -> list[str]:
    spec = root / "docs" / "spec"
    try:
        params = json.loads(_read(spec / "parameters.json"))["parameters"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        return [f"parameters.json unreadable: {exc}"]
    table = parse_parameter_table(_read(spec / "EXPERIMENTS.md"))
    errors = []
    if not table:
        errors.append("EXPERIMENTS.md: canonical configuration table (section 4) not found")
    for key, value in params.items():
        if key not in table:
            errors.append(f"EXPERIMENTS.md section 4 missing parameter `{key}`")
        elif table[key] != str(value):
            errors.append(f"parameter `{key}`: parameters.json={value!r} but EXPERIMENTS.md={table[key]!r}")
    for key in table:
        if key not in params:
            errors.append(f"EXPERIMENTS.md section 4 has `{key}` not present in parameters.json")
    return errors


def validate_contract(contract: dict) -> list[str]:
    """Mirror of ai-native-engineering ai_native.validate_contract plus governance path fields."""
    errors = []
    if contract.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not TASK_RE.fullmatch(str(contract.get("task_id", ""))):
        errors.append("task_id must be a safe slug")
    for field in ("goal", "acceptance"):
        if not isinstance(contract.get(field), str) or not contract[field].strip():
            errors.append(f"{field} must be a nonempty string")
    gates = contract.get("gates")
    if not isinstance(gates, list) or not gates:
        return errors + ["nonempty gates array required"]
    seen = set()
    for gate in gates:
        gid = gate.get("id") if isinstance(gate, dict) else None
        if not isinstance(gid, str) or not TASK_RE.fullmatch(gid):
            errors.append("each gate needs a safe id")
            continue
        if gid in seen:
            errors.append(f"duplicate gate id {gid}")
        seen.add(gid)
        if gate.get("level") not in GATE_LEVELS:
            errors.append(f"gate {gid}: level must be one of {sorted(GATE_LEVELS)}")
        cmd = gate.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(s, str) and s for s in cmd):
            errors.append(f"gate {gid}: command must be a nonempty argv list")
        if not isinstance(gate.get("timeout_seconds", 180), (int, float)) or gate.get("timeout_seconds", 180) <= 0:
            errors.append(f"gate {gid}: timeout must be positive")
    if not any(g.get("required", True) for g in gates if isinstance(g, dict)):
        errors.append("at least one required gate is mandatory")
    budgets = contract.get("budgets", {})
    if not isinstance(budgets, dict) or not 1 <= budgets.get("max_attempts", 3) <= 10:
        errors.append("budgets.max_attempts must be 1..10")
    if isinstance(budgets, dict) and budgets.get("max_total_seconds", 900) <= 0:
        errors.append("budgets.max_total_seconds must be positive")
    approved = contract.get("approved_paths")
    if not isinstance(approved, list) or not approved or not all(isinstance(p, str) and p for p in approved):
        errors.append("approved_paths must be a nonempty string list")
    forbidden = contract.get("forbidden_paths", [])
    if not isinstance(forbidden, list) or not all(isinstance(p, str) and p for p in forbidden):
        errors.append("forbidden_paths must be a string list")
    return errors


def check_contracts(root: Path) -> tuple[list[str], dict[int, dict]]:
    errors, contracts = [], {}
    base = root / "docs" / "spec" / "contracts"
    for phase in PHASES:
        path = base / f"phase-{phase}.json"
        try:
            contract = json.loads(_read(path))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"contracts/phase-{phase}.json unreadable: {exc}")
            continue
        errors += [f"contracts/phase-{phase}.json: {e}" for e in validate_contract(contract)]
        if contract.get("task_id") != f"phase-{phase}":
            errors.append(f"contracts/phase-{phase}.json: task_id must be phase-{phase}")
        contracts[phase] = contract
    return errors, contracts


def check_requirements(root: Path, contracts: dict[int, dict]) -> list[str]:
    errors = []
    base = root / "docs" / "spec" / "requirements"
    for phase in PHASES:
        path = base / f"phase-{phase}.json"
        try:
            manifest = json.loads(_read(path))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"requirements/phase-{phase}.json unreadable: {exc}")
            continue
        gates = {g["id"]: g for g in contracts.get(phase, {}).get("gates", [])}
        reqs = manifest.get("requirements")
        if manifest.get("schema_version") != 1 or not isinstance(reqs, list) or not reqs:
            errors.append(f"requirements/phase-{phase}.json: schema_version 1 and nonempty requirements needed")
            continue
        order = ["static", "unit", "integration", "e2e", "device", "visual", "performance"]
        for req in reqs:
            rid = req.get("id", "?")
            if req.get("min_level") not in order:
                errors.append(f"requirements/phase-{phase}.json {rid}: invalid min_level")
                continue
            ids = req.get("gate_ids")
            if not isinstance(ids, list) or not ids:
                errors.append(f"requirements/phase-{phase}.json {rid}: gate_ids required")
                continue
            for gid in ids:
                if gid not in gates:
                    errors.append(f"requirements/phase-{phase}.json {rid}: gate {gid} not in phase-{phase} contract")
            levels = [gates[g]["level"] for g in ids if g in gates]
            if levels and max(order.index(lv) for lv in levels) < order.index(req["min_level"]):
                errors.append(f"requirements/phase-{phase}.json {rid}: no gate reaches min_level {req['min_level']}")
    return errors


def check_frontier(root: Path) -> list[str]:
    spec = root / "docs" / "spec"
    try:
        data = json.loads(_read(spec / "work.json"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"work.json unreadable: {exc}"]
    if data.get("schema_version") != 1 or not isinstance(data.get("work"), list) or not data["work"]:
        return ["work.json: schema_version 1 and nonempty work list required"]
    errors, by_id = [], {}
    for item in data["work"]:
        ident = item.get("id")
        if not isinstance(ident, str) or not ident:
            errors.append("work.json: item without id")
            continue
        if ident in by_id:
            errors.append(f"work.json: duplicate id {ident}")
        by_id[ident] = item
        if item.get("owner") not in OWNERS or item.get("status") not in WORK_STATUSES:
            errors.append(f"work.json {ident}: invalid owner or status")
        if not isinstance(item.get("depends_on"), list):
            errors.append(f"work.json {ident}: depends_on must be a list")
        if not str(item.get("action", "")).strip():
            errors.append(f"work.json {ident}: action required")
        if not isinstance(item.get("phase"), int) or item["phase"] not in PHASES:
            errors.append(f"work.json {ident}: phase must be an integer 0..6")
        status, owner = item.get("status"), item.get("owner")
        if status == "PASS" and (not isinstance(item.get("evidence"), list) or not item["evidence"]):
            errors.append(f"work.json {ident}: PASS requires an evidence list")
        if status == "BLOCKED_EXTERNAL" and not item.get("blocked_by"):
            errors.append(f"work.json {ident}: BLOCKED_EXTERNAL requires blocked_by")
        if status == "HUMAN_REQUIRED":
            if not str(owner).startswith("HUMAN_"):
                errors.append(f"work.json {ident}: HUMAN_REQUIRED needs a HUMAN_* owner")
            for field in ("automation_blocker", "procedure", "expected_return", "pass_criteria"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"work.json {ident}: missing human field {field}")
            if owner == "HUMAN_DECISION" and not item.get("quantified"):
                errors.append(f"work.json {ident}: HUMAN_DECISION requires quantified options")
        if owner == "AUTONOMOUS":
            cpath = item.get("contract_path")
            if not isinstance(cpath, str) or not (spec / cpath).is_file():
                errors.append(f"work.json {ident}: autonomous item needs an existing contract_path")
            elif isinstance(item.get("phase"), int) and cpath != f"contracts/phase-{item['phase']}.json":
                errors.append(f"work.json {ident}: contract_path does not match phase {item['phase']}")
    for ident, item in by_id.items():
        for dep in item.get("depends_on", []):
            if dep not in by_id:
                errors.append(f"work.json {ident}: unknown dependency {dep}")
            elif isinstance(by_id[dep].get("phase"), int) and isinstance(item.get("phase"), int) \
                    and by_id[dep]["phase"] > item["phase"]:
                errors.append(f"work.json {ident} (phase {item['phase']}) depends on later-phase {dep} "
                              f"(phase {by_id[dep]['phase']})")
    # cycle detection (iterative DFS)
    state: dict[str, int] = {}
    for start, start_item in by_id.items():
        if state.get(start):
            continue
        stack = [(start, iter(start_item.get("depends_on", [])))]
        state[start] = 1
        while stack:
            node, deps = stack[-1]
            nxt = next(deps, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif nxt in by_id:
                if state.get(nxt) == 1:
                    errors.append(f"work.json: dependency cycle through {nxt}")
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(by_id[nxt].get("depends_on", []))))
    return errors


def topological_order(root: Path) -> list[str]:
    """Kahn order preferring lower phase then priority; used by tests to prove sequential executability."""
    data = json.loads(_read(root / "docs" / "spec" / "work.json"))
    by_id = {w["id"]: w for w in data["work"]}
    indeg = {k: len(v["depends_on"]) for k, v in by_id.items()}
    order, ready = [], [k for k, d in indeg.items() if d == 0]
    while ready:
        ready.sort(key=lambda k: (by_id[k]["phase"], by_id[k].get("priority", 100), k))
        node = ready.pop(0)
        order.append(node)
        for k, v in by_id.items():
            if node in v["depends_on"]:
                indeg[k] -= 1
                if indeg[k] == 0:
                    ready.append(k)
    return order


def run_checks(root: Path) -> list[str]:
    errors = check_documents(root)
    if errors:
        return errors
    errors += check_language(root)
    errors += check_links(root)
    errors += check_tools(root)
    errors += check_ids(root)
    errors += check_parameters(root)
    contract_errors, contracts = check_contracts(root)
    errors += contract_errors
    errors += check_requirements(root, contracts)
    errors += check_frontier(root)
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parent.parent
    errors = run_checks(root)
    for err in errors:
        print("ERROR", err)
    if not errors:
        order = topological_order(root)
        print(f"spec check OK: {len(SPEC_DOCS)} documents, 7 contracts, {len(order)} work items in "
              f"sequential order")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
