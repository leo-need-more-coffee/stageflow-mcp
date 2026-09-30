"""The same questions, asked of a backend that really answers them.


Skipped unless `STAGEFLOW_BACKEND` is set, because a test suite that needs the
internet to pass is a test suite people stop running. What it covers is the
half the fake cannot: that the contract this package assumes is the contract a
real StageFlow backend keeps — in particular that `POST /api/run` refuses a bad
graph before it admits a run, which is what makes checking free.

    STAGEFLOW_BACKEND=https://stageflow.lazy.su .venv/bin/python -m unittest tests.test_live
"""
import os
import unittest

from stageflow_mcp.backend import Backend
from stageflow_mcp.catalog import EXAMPLES, capabilities, pipeline_schema, stage_catalog
from stageflow_mcp.tools import run_pipeline, validate_pipeline

ADDRESS = os.environ.get("STAGEFLOW_BACKEND", "")


@unittest.skipUnless(ADDRESS, "set STAGEFLOW_BACKEND to run these")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = Backend(
            ADDRESS,
            token=os.environ.get("STAGEFLOW_TOKEN", ""),
            lang=os.environ.get("STAGEFLOW_LANG", "en"),
        )

    def test_the_stage_catalog_arrives_in_one_language(self):
        stages = stage_catalog(self.backend, "ru")
        self.assertTrue(stages)
        for name, spec in stages.items():
            self.assertIsInstance(spec.get("description", ""), str, name)
            for row in spec.get("arguments", []):
                self.assertIsInstance(row.get("description", ""), str, f"{name}.{row['name']}")

    def test_the_guide_names_node_types_this_backend_has(self):
        """The guide is written prose and the only thing here that can fall
        behind the core. If a name in it is no longer real, this says so."""
        types = set(capabilities(self.backend).get("node_types") or [])
        named = {"entry", "stage", "condition", "switch", "terminal",
                 "parallel", "try", "map", "subpipeline"}
        self.assertTrue(named <= types, f"the guide names what this backend lacks: {named - types}")

    def test_the_schema_comes_back_narrowed(self):
        schema = pipeline_schema(self.backend)
        self.assertIn("enum", schema["$defs"]["stage_node"]["properties"]["stage"])
        self.assertIn("enum", schema["$defs"]["node"]["properties"]["type"])

    def test_every_shipped_example_is_accepted(self):
        """They are shipped to be copied, so a backend has to take them."""
        for name, graph in EXAMPLES.items():
            with self.subTest(example=name):
                verdict = validate_pipeline(self.backend, graph)
                self.assertTrue(verdict["ok"], verdict.get("errors"))

    def test_a_broken_graph_comes_back_as_a_list(self):
        broken = {"nodes": [
            {"id": "start", "type": "entry", "next": "nowhere"},
            {"id": "orphan", "type": "terminal"},
        ]}
        verdict = validate_pipeline(self.backend, broken)
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["errors"])
        self.assertTrue(any("nowhere" in item for item in verdict["errors"]), verdict["errors"])

    def test_an_example_runs_and_reports_its_path(self):
        report = run_pipeline(self.backend, EXAMPLES["straight-line"], timeout=60)
        self.assertEqual(report["status"], "finished", report.get("error"))
        self.assertEqual(report["path"][0], "start")
        self.assertIn("bump", report["path"])


if __name__ == "__main__":
    unittest.main()
