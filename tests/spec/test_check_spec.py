"""Tests for scripts/check_spec.py: the real spec passes, and each check catches its fault."""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_spec", ROOT / "scripts" / "check_spec.py")
check_spec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_spec)


class RepoSpecTest(unittest.TestCase):
    def test_repository_spec_has_no_errors(self):
        self.assertEqual(check_spec.run_checks(ROOT), [])

    def test_plan_is_executable_in_phase_order(self):
        order = check_spec.topological_order(ROOT)
        work = json.loads((ROOT / "docs/spec/work.json").read_text())["work"]
        by_id = {w["id"]: w for w in work}
        self.assertEqual(len(order), len(work), "every work item must appear in the topological order")
        phases = [by_id[i]["phase"] for i in order]
        self.assertEqual(phases, sorted(phases), "phase order must be non-decreasing")
        position = {ident: n for n, ident in enumerate(order)}
        for item in work:
            for dep in item["depends_on"]:
                self.assertLess(position[dep], position[item["id"]])

    def test_implementation_blocked_until_spec_approved(self):
        work = {w["id"]: w for w in json.loads((ROOT / "docs/spec/work.json").read_text())["work"]}
        self.assertIn("h-approve-spec", work["p1-scaffold"]["depends_on"])
        approval = work["h-approve-spec"]
        self.assertIn(approval["status"], {"HUMAN_REQUIRED", "PASS"})
        if approval["status"] == "PASS":
            self.assertTrue(approval.get("evidence"), "approval PASS needs recorded evidence")

    def test_live_tasks_declare_api_capability(self):
        work = json.loads((ROOT / "docs/spec/work.json").read_text())["work"]
        for item in work:
            if "h-provide-api-key" in item["depends_on"]:
                self.assertIn("anthropic_api", item.get("requires", []), item["id"])


class MutationTest(unittest.TestCase):
    """Each test copies the spec, injects one fault, and expects check_spec to report it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "docs", self.tmp / "docs")
        for name in ("README.md", "AGENTS.md", "CLAUDE.md"):
            shutil.copy(ROOT / name, self.tmp / name)
        self.assertEqual(check_spec.run_checks(self.tmp), [], "baseline copy must be clean")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _edit_json(self, rel, mutate):
        path = self.tmp / rel
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def _append(self, rel, text):
        path = self.tmp / rel
        path.write_text(path.read_text() + text)

    def assertReports(self, fragment):
        errors = check_spec.run_checks(self.tmp)
        self.assertTrue(any(fragment in e for e in errors), f"expected {fragment!r} in {errors}")

    def test_detects_later_phase_dependency(self):
        def mutate(d):
            item = next(w for w in d["work"] if w["id"] == "p1-scaffold")
            item["depends_on"].append("p6-report")
        self._edit_json("docs/spec/work.json", mutate)
        self.assertReports("depends on later-phase p6-report")

    def test_detects_cycle(self):
        def mutate(d):
            item = next(w for w in d["work"] if w["id"] == "p1-config")
            item["depends_on"].append("p1-llm")
        self._edit_json("docs/spec/work.json", mutate)
        self.assertReports("dependency cycle")

    def test_detects_pass_without_evidence(self):
        def mutate(d):
            item = next(w for w in d["work"] if w["id"] == "p3-tools")
            item["status"] = "PASS"
            item.pop("evidence", None)
        self._edit_json("docs/spec/work.json", mutate)
        self.assertReports("PASS requires an evidence list")

    def test_detects_parameter_mismatch(self):
        self._edit_json("docs/spec/parameters.json",
                        lambda d: d["parameters"].__setitem__("max_repair_attempts", 5))
        self.assertReports("parameter `max_repair_attempts`")

    def test_detects_undefined_tool(self):
        self._append("docs/spec/BASELINES.md", "\nA1 may also call `execute_arbitrary_sql`.\n")
        self.assertReports("undefined tool `execute_arbitrary_sql`")

    def test_detects_undefined_metric_and_failure_code(self):
        self._append("docs/spec/EXPERIMENTS.md", "\nWe also report M13 and F-MADE-UP.\n")
        self.assertReports("undefined metric M13")
        self.assertReports("undefined failure code F-MADE-UP")

    def test_detects_overclaim(self):
        self._append("README.md", "\nA2 is state-of-the-art.\n")
        self.assertReports("over-claim phrase")

    def test_detects_placeholder(self):
        self._append("docs/spec/RISKS.md", "\nTBD: more risks.\n")
        self.assertReports("placeholder token")

    def test_detects_requirement_with_unknown_gate(self):
        self._edit_json("docs/spec/requirements/phase-1.json",
                        lambda d: d["requirements"][0]["gate_ids"].append("no-such-gate"))
        self.assertReports("gate no-such-gate not in phase-1 contract")

    def test_detects_invalid_contract_gate(self):
        def mutate(d):
            d["gates"][0]["command"] = "python3 scripts/check_spec.py && rm -rf /"
        self._edit_json("docs/spec/contracts/phase-1.json", mutate)
        self.assertReports("command must be a nonempty argv list")

    def test_detects_broken_link(self):
        self._append("docs/spec/PRD.md", "\nSee [missing](NOPE.md).\n")
        self.assertReports("broken relative link: NOPE.md")


if __name__ == "__main__":
    unittest.main()
