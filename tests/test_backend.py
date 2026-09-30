"""The client of the seven endpoints: addresses, headers, refusals."""
import unittest

from stageflow_mcp.backend import Backend, BackendError, accept_language, normalize_url
from tests.fake import FakeBackend


class NormalizeTests(unittest.TestCase):
    """The same rule as the editor's, because people paste the same address."""

    def test_the_three_spellings_are_one_backend(self):
        for typed in ("localhost:8765", "http://localhost:8765/", "http://localhost:8765/api"):
            self.assertEqual(normalize_url(typed), "http://localhost:8765")

    def test_a_bare_hostname_gets_https(self):
        """Unlike the editor, which fills in http because a browser stops it
        from reaching one anyway. Nothing stops this process, and it carries a
        token."""
        self.assertEqual(normalize_url("example.org"), "https://example.org")

    def test_a_bare_loopback_address_gets_http(self):
        for typed in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765"):
            self.assertTrue(normalize_url(typed).startswith("http://"), typed)

    def test_https_survives(self):
        self.assertEqual(normalize_url("https://stageflow.lazy.su/"), "https://stageflow.lazy.su")

    def test_a_path_that_is_not_api_is_kept(self):
        """A backend behind a prefix is a backend; only `/api` is ours to cut."""
        self.assertEqual(normalize_url("https://host/sf/"), "https://host/sf")

    def test_a_credential_bound_for_the_clear_is_noticed(self):
        """Not refused — a backend behind a TLS-terminating proxy on a private
        network is real, and this process cannot know. Noticed, though."""
        self.assertTrue(Backend("http://example.org", token="t").credential_in_the_clear)
        self.assertFalse(Backend("http://localhost:8765", token="t").credential_in_the_clear)
        self.assertFalse(Backend("example.org", token="t").credential_in_the_clear)
        self.assertFalse(Backend("http://example.org").credential_in_the_clear)

    def test_what_is_not_an_address_is_refused(self):
        for bad in ("", "   ", "ftp://host", "http://"):
            with self.assertRaises(ValueError):
                normalize_url(bad)


class AcceptLanguageTests(unittest.TestCase):
    def test_english_behind_the_asked_language(self):
        self.assertEqual(accept_language("ru"), "ru, en;q=0.8")

    def test_english_asks_for_itself_only(self):
        self.assertEqual(accept_language("en"), "en")
        self.assertEqual(accept_language(""), "en")


class RequestTests(unittest.TestCase):
    def test_stages_unwraps_and_the_headers_travel(self):
        with FakeBackend() as fake:
            backend = Backend(fake.url, token="tok", auth_header="X-Api-Key", lang="ru")
            stages = backend.stages()
            self.assertIn("SetValueStage", stages)
            sent = fake.script.headers[-1]
            self.assertEqual(sent["x-api-key"], "tok")
            self.assertEqual(sent["accept-language"], "ru, en;q=0.8")

    def test_no_credential_means_no_header(self):
        with FakeBackend() as fake:
            Backend(fake.url).stages()
            self.assertNotIn("authorization", fake.script.headers[-1])

    def test_plan_is_asked_on_what_is_drawn_and_never_on_a_run(self):
        """`?plan=` is a request to be SHOWN something. A run takes its plan
        from the credential, and offering one would be offering the caller a
        say in its own ceiling."""
        with FakeBackend() as fake:
            backend = Backend(fake.url, plan="full")
            backend.stages()
            backend.meta()
            backend.start_run({"nodes": []})
            asked = fake.paths()
            self.assertIn("/api/stages?plan=full", asked)
            self.assertIn("/api/meta?plan=full", asked)
            self.assertIn("/api/run", asked)

    def test_a_refusal_comes_back_as_its_error_field(self):
        with FakeBackend() as fake:
            fake.script.refuse_message = "this graph was prepared for plan 'pro'"
            fake.script.refuse_status = 403
            backend = Backend(fake.url)
            with self.assertRaises(BackendError) as caught:
                backend.start_run({"nodes": []})
            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(caught.exception.message, "this graph was prepared for plan 'pro'")

    def test_nothing_answering_is_not_a_status(self):
        """Status 0 is "nothing was there", which is a different problem from
        "it refused" and has a different fix."""
        backend = Backend("http://127.0.0.1:9", timeout=2.0)
        with self.assertRaises(BackendError) as caught:
            backend.stages()
        self.assertEqual(caught.exception.status, 0)


if __name__ == "__main__":
    unittest.main()
