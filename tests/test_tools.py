"""Checking without running, running, and what is reported about either."""
import unittest

from stageflow_mcp.backend import Backend
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

    def test_located_shape_complaints_become_items(self):
        """The core puts the place in front of each one, so there is something
        to split on that a message cannot be mistaken for."""
        message = ("PipelineDefinitionError: Pipeline schema validation failed: "
                   "guard.except[0]: 'next' is a required property; "
                   "risky.arguments: Additional properties are not allowed")
        self.assertEqual(split_errors(message), [
            "guard.except[0]: 'next' is a required property",
            "risky.arguments: Additional properties are not allowed",
        ])

    def test_a_semicolon_inside_one_complaint_does_not_cut_it(self):
        """A schema message quotes the value it rejected, and that value may
        contain the separator. A piece that does not begin with a place is the
        rest of the last complaint, not a new one."""
        message = ("PipelineDefinitionError: Pipeline schema validation failed: "
                   "a.type: 'x' is not one of ['p'; 'q']; b: is a required property")
        self.assertEqual(split_errors(message), [
            "a.type: 'x' is not one of ['p'; 'q']",
            "b: is a required property",
        ])

    def test_an_unlocated_complaint_stays_whole(self):
        """An older core sends one sentence with no place in it, and cutting it
        up would invent violations that do not exist."""
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
        agent rewriting a graph that was never read — and raising would reach a
        client as "the tool failed", with the reason in a log nobody reads."""
        with FakeBackend() as fake:
            fake.script.refuse_message = "4 runs are already going"
            fake.script.refuse_status = 429
            answer = validate_pipeline(Backend(fake.url), GRAPH)
            self.assertEqual(answer["blocked"], "backend-busy")
            self.assertFalse(answer["checked"])
            self.assertNotIn("ok", answer, "a blocked check must not look like a verdict")
            self.assertIn("wait", answer["hint"])

    def test_nothing_answering_says_so_and_names_the_address(self):
        """The failure a person meets first — the backend is not running — used
        to reach the client as "Error executing tool" and nothing else."""
        answer = validate_pipeline(Backend("http://127.0.0.1:9", timeout=2.0), GRAPH)
        self.assertEqual(answer["blocked"], "backend-unreachable")
        self.assertIn("127.0.0.1:9", answer["hint"])
        self.assertNotIn("ok", answer)

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

    def test_a_run_says_what_it_computed(self):
        """The first question after a run, and the answer was being dropped:
        `result` is only what a terminal node returns, so a graph that writes
        its answer into a variable — most of them — looked like it had produced
        nothing."""
        with FakeBackend() as fake:
            fake.script.frame = {"total": 16, "line": "fizz, 2, 4"}
            report = run_pipeline(Backend(fake.url), GRAPH, timeout=10)
            self.assertEqual(report["variables"], {"total": 16, "line": "fizz, 2, 4"})

    def test_an_enormous_value_is_trimmed_and_the_others_survive(self):
        """Per value, not across the frame: cutting the object would take away
        names the author is looking for."""
        with FakeBackend() as fake:
            fake.script.frame = {"huge": ["x" * 50] * 500, "small": 7}
            report = run_pipeline(Backend(fake.url), GRAPH, timeout=10)
            self.assertEqual(report["variables"]["small"], 7)
            self.assertIn("characters in all", report["variables"]["huge"])

    def test_a_graph_the_backend_refuses_is_not_an_outage(self):
        with FakeBackend() as fake:
            fake.script.refuse_message = (
                "PipelineValidationError: Pipeline validation failed: a: bad; b: worse")
            report = run_pipeline(Backend(fake.url), GRAPH, timeout=10)
            self.assertFalse(report["ran"])
            self.assertEqual(report["errors"], ["a: bad", "b: worse"])
            self.assertNotIn("blocked", report)

    def test_an_outage_during_a_run_is_not_a_graph_problem(self):
        report = run_pipeline(Backend("http://127.0.0.1:9", timeout=2.0), GRAPH, timeout=5)
        self.assertEqual(report["blocked"], "backend-unreachable")
        self.assertNotIn("errors", report)

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
