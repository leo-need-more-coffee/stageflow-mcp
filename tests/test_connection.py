"""Where the address comes from when nobody put one in the config.

The rules worth pinning down are about what is NOT done: a credential is never
written to disk, a client that cannot be asked gets an error that says what to
do rather than a guess, and a refusal to answer is not treated as an answer.
"""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from stageflow_mcp.backend import Backend
from stageflow_mcp.connection import BackendUnknown, Connection, WhichBackend, config_path


class FakeResult:
    def __init__(self, action, data=None):
        self.action = action
        self.data = data


class CanAsk:
    """A client that can be asked, and what it answers."""

    def __init__(self, action="accept", backend="sf.example", token=""):
        self.client_capabilities = SimpleNamespace(elicitation=SimpleNamespace())
        self._answer = FakeResult(
            action, WhichBackend(backend=backend, token=token) if action == "accept" else None
        )
        self.asked = []

    async def elicit(self, message, schema):
        self.asked.append((message, schema))
        return self._answer


class CannotAsk:
    """A client that declares no elicitation capability — the Desktop case."""

    client_capabilities = SimpleNamespace(elicitation=None)


class ConfigTestCase(unittest.TestCase):
    """Every test here writes a config file, so none of them write yours."""

    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self._was = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._home.name
        self.addCleanup(self._restore)

    def _restore(self):
        if self._was is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._was


class AskingTests(ConfigTestCase):
    def test_a_configured_backend_is_never_asked_about(self):
        client = CanAsk()
        connection = Connection(Backend("http://127.0.0.1:1"))
        backend = asyncio.run(connection.resolve(client))
        self.assertEqual(backend.url, "http://127.0.0.1:1")
        self.assertEqual(client.asked, [])

    def test_without_one_the_client_is_asked(self):
        client = CanAsk(backend="sf.example", token="tok")
        connection = Connection()
        backend = asyncio.run(connection.resolve(client))
        self.assertEqual(len(client.asked), 1)
        self.assertEqual(backend.url, "https://sf.example")
        self.assertEqual(backend.token, "tok")

    def test_the_settings_from_the_command_line_survive_the_asking(self):
        """The header a credential goes in, the language and the plan are not
        things to ask a person about — they came from whoever wrote the config."""
        connection = Connection(auth_header="X-Api-Key", lang="ru", plan="full")
        backend = asyncio.run(connection.resolve(CanAsk()))
        self.assertEqual(backend.auth_header, "X-Api-Key")
        self.assertEqual(backend.lang, "ru")
        self.assertEqual(backend.plan, "full")

    def test_a_client_that_cannot_be_asked_is_told_what_to_do(self):
        with self.assertRaises(BackendUnknown) as caught:
            asyncio.run(Connection().resolve(CannotAsk()))
        self.assertIn("--backend", str(caught.exception))

    def test_no_context_at_all_is_the_same_case(self):
        with self.assertRaises(BackendUnknown):
            asyncio.run(Connection().resolve(None))

    def test_a_refusal_is_not_an_answer(self):
        for action in ("decline", "cancel"):
            with self.subTest(action=action):
                with self.assertRaises(BackendUnknown):
                    asyncio.run(Connection().resolve(CanAsk(action=action)))

    def test_an_empty_address_is_refused(self):
        with self.assertRaises(BackendUnknown):
            asyncio.run(Connection().resolve(CanAsk(backend="   ")))


class RememberingTests(ConfigTestCase):
    def test_the_address_is_remembered_and_the_credential_is_not(self):
        client = CanAsk(backend="sf.example", token="super-secret")
        asyncio.run(Connection().resolve(client))

        saved = json.loads(config_path().read_text(encoding="utf-8"))
        self.assertEqual(saved, {"backend": "https://sf.example"})
        self.assertNotIn("super-secret", config_path().read_text(encoding="utf-8"))

    def test_what_was_remembered_is_used_without_asking_again(self):
        asyncio.run(Connection().resolve(CanAsk(backend="sf.example")))
        client = CanAsk(backend="somewhere.else")
        backend = asyncio.run(Connection().resolve(client))
        self.assertEqual(backend.url, "https://sf.example")
        self.assertEqual(client.asked, [])

    def test_remembering_can_be_turned_off(self):
        asyncio.run(Connection(remember=False).resolve(CanAsk(backend="sf.example")))
        self.assertFalse(config_path().exists())

    def test_a_config_written_by_something_else_is_ignored_rather_than_fatal(self):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        for rubbish in ("not json at all", '{"backend": 7}', "{}"):
            path.write_text(rubbish, encoding="utf-8")
            self.assertIsNone(Connection().recall(), rubbish)

    def test_an_unwritable_home_costs_a_question_and_nothing_else(self):
        """A read-only home is somebody's real machine, and being asked again
        is a smaller problem than a tool that will not start."""
        locked = Path(self._home.name) / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        os.environ["XDG_CONFIG_HOME"] = str(locked)

        Connection().keep("https://sf.example")  # must not raise
        self.assertFalse(config_path().exists())


if __name__ == "__main__":
    unittest.main()
