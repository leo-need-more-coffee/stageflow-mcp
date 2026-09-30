"""Checking without running, running, and what is reported about either."""
import unittest

from stageflow_mcp.backend import Backend, BackendError
from stageflow_mcp.tools import run_pipeline, split_errors, validate_pipeline
from tests.fake import FakeBackend

GRAPH = {"nodes": [{"id": "start", "type": "entry"}]}


class SplitErrorsTests(unittest.TestCase):
    """The backend's one line, back into the list the core made it from."""

    def test_a_validation_refusal_becomes_its_items(self):
        message = ("PipelineValidationError: Pipeline validation failed: "
                   "a: next 'x' not found; b: duplicate id")
        self.assertEqual(
            split_errors(message),
            ["a: next 'x' not found", "b: duplicate id"],
        )

    def test_the_russian_preamble_is_handled_the_same(self):
        """The class name and the `; ` separator are not translated; the
        sentence between them is."""
        message = "PipelineValidationError: Пайплайн не прошёл проверку: раз; два"
        self.assertEqual(split_errors(message), ["раз", "два"])

    def test_a_schema_complaint_stays_one_item(self):
        """It quotes the value it rejected and may well contain a semicolon of
        its own; cutting it up would invent violations."""
        message = ("PipelineDefinitionError: Pipeline schema validation failed: "
                   "'nodes' is a required property; see {'a': 1; 'b': 2}")
        items = split_errors(message)
        self.assertEqual(len(items), 1)
        self.assertIn("'nodes' is a required property", items[0])

    def test_anything_else_comes_back_whole(self):
        self.assertEqual(split_errors("this stage is not on your plan"),
                         ["this stage is not on your plan"])


class ValidateTests(unittest.TestCase):
    def test_a_good_graph_is_checked_and_the_run_it_parked_is_stopped(self):
        """The check costs no execution, but it does start a run in step mode —
        which stands before the first node holding one of the caller's slots
        until somebody stops it. Somebody is us, on every path out."""
        with FakeBackend() as fake:
            verdict = validate_pipeline(Backend(fake.url), GRAPH)
            self.assertTrue(verdict["ok"])
            self.assertEqual(verdict["errors"], [])
            started = [body for verb, path, body in fake.calls
                       if verb == "POST" and path == "/api/run"]
            self.assertEqual(started[0]["mode"], "step")
            self.assertTrue(any(path.endswith("/control") for path in fake.paths("POST")))

    def test_a_refused_graph_is_a_verdict_not_an_exception(self):
        with FakeBackend() as fake:
            fake.script.refuse_message = (
                "PipelineValidationError: Pipeline validation failed: a: bad; b: worse")
            verdict = validate_pipeline(Backend(fake.url), GRAPH)
            self.assertFalse(verdict["ok"])
            self.assertEqual(verdict["errors"], ["a: bad", "b: worse"])
            self.assertEqual(verdict["status"], 400)

    def test_an_invalid_graph_parks_nothing(self):
        """Both reference backends validate before a thread, a slot or an id
        exists — so there is nothing to stop, and nothing to stop it with."""
        with FakeBackend() as fake:
            fake.script.refuse_message = "PipelineValidationError: Pipeline validation failed: a"
            validate_pipeline(Backend(fake.url), GRAPH)
            self.assertEqual([p for p in fake.paths("POST") if p.endswith("/control")], [])

    def test_a_busy_stand_is_not_a_verdict_about_the_graph(self):
        """429 means nobody looked at it. Calling that "invalid" would send an
        agent rewriting a graph that was never read."""
        with FakeBackend() as fake:
            fake.script.refuse_message = "4 runs are already going"
            fake.script.refuse_status = 429
            with self.assertRaises(BackendError):
                validate_pipeline(Backend(fake.url), GRAPH)

    def test_a_plan_refusal_is_a_verdict(self):
        """403 IS about the graph: it uses something this caller may not."""
        with FakeBackend() as fake:
            fake.script.refuse_message = "roll: stage 'DiceStage' is not allowed by the policy"
            fake.script.refuse_status = 403
            verdict = validate_pipeline(Backend(fake.url), GRAPH)
            self.assertFalse(verdict["ok"])
            self.assertEqual(verdict["errors"], [fake.script.refuse_message])


class RunTests(unittest.TestCase):
    def test_a_run_is_waited_out_and_reported(self):
        with FakeBackend() as fake:
            fake.script.running_polls = 2
            fake.script.events = [
                {"type": "node_enter", "node": "start"},
                {"type": "node_exit", "node": "start"},
                {"type": "node_enter", "node": "done"},
            ]
            report = run_pipeline(Backend(fake.url), GRAPH, timeout=10)
            self.assertEqual(report["status"], "finished")
            self.assertEqual(report["result"], {"done": True})
            self.assertEqual(report["path"], ["start", "done"])
            self.assertEqual(report["failures"], [])
            started = [body for verb, path, body in fake.calls
                       if verb == "POST" and path == "/api/run"]
            self.assertEqual(started[0]["mode"], "run")

    def test_what_failed_is_kept_out_of_the_path(self):
        with FakeBackend() as fake:
            fake.script.final_status = "failed"
            fake.script.events = [
                {"type": "node_enter", "node": "boom"},
                {"type": "stage_failed", "node": "boom", "payload": {"error": "on purpose"}},
            ]
            report = run_pipeline(Backend(fake.url), GRAPH, timeout=10)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["path"], ["boom"])
            self.assertEqual(report["failures"][0]["type"], "stage_failed")

    def test_a_run_that_outlives_its_timeout_is_stopped_and_said_so(self):
        with FakeBackend() as fake:
            fake.script.running_polls = 1000
            report = run_pipeline(Backend(fake.url), GRAPH, timeout=1.0)
            self.assertEqual(report["status"], "timed_out")
            self.assertIn("note", report)
            self.assertTrue(any(p.endswith("/control") for p in fake.paths("POST")))


if __name__ == "__main__":
    unittest.main()
