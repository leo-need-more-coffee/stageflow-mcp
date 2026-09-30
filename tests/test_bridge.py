"""The bridge: the locks on it, and that both directions carry.

It is a listening socket on somebody's machine, so most of this is about what
it refuses. The rest is the one property that makes it usable at all: a graph
pushed before the editor connected is still there when it does.
"""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from stageflow_mcp.bridge import HOSTED_EDITOR, Bridge

GRAPH = {"nodes": [{"id": "start", "type": "entry"}]}


class BridgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = Bridge()
        self.url = self.bridge.start(0)
        self.addCleanup(self.bridge.stop)

    def request(self, path, *, method="GET", body=None, origin=None, token=None, timeout=5):
        token = self.bridge.token if token is None else token
        joiner = "&" if "?" in path else "?"
        url = f"{self.url}{path}{joiner}token={token}" if token != "" else f"{self.url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, json.loads(answer.read() or b"{}")


class LocksTests(BridgeTestCase):
    def test_without_the_token_nothing_opens(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/hello", token="")
        self.assertEqual(caught.exception.code, 401)

    def test_a_wrong_token_is_the_same_refusal(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/hello", token="not-it")
        self.assertEqual(caught.exception.code, 401)

    def test_the_hosted_editor_and_this_machine_are_allowed(self):
        for origin in (HOSTED_EDITOR, "http://localhost:8080", "http://127.0.0.1:3000"):
            status, _ = self.request("/hello", origin=origin)
            self.assertEqual(status, 200, origin)

    def test_any_other_page_is_refused_even_with_the_token(self):
        """The token travels in a link a person pastes, so it can be shoulder-read
        or land in a screen recording. The second lock costs nothing."""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/hello", origin="https://evil.example")
        self.assertEqual(caught.exception.code, 403)

    def test_the_private_network_preflight_is_answered(self):
        """An https page reaching 127.0.0.1 is a private-network request in
        Chrome, and without this header it is never sent at all."""
        request = urllib.request.Request(
            f"{self.url}/events", method="OPTIONS",
            headers={"Origin": HOSTED_EDITOR,
                     "Access-Control-Request-Method": "GET",
                     "Access-Control-Request-Private-Network": "true"},
        )
        with urllib.request.urlopen(request, timeout=5) as answer:
            self.assertEqual(answer.status, 204)
            self.assertEqual(answer.headers.get("Access-Control-Allow-Private-Network"), "true")
            self.assertEqual(answer.headers.get("Access-Control-Allow-Origin"), HOSTED_EDITOR)

    def test_it_listens_on_loopback_only(self):
        self.assertTrue(self.url.startswith("http://127.0.0.1:"), self.url)


class CarryingTests(BridgeTestCase):
    def test_the_editor_posts_its_graph_and_the_agent_reads_it(self):
        status, answer = self.request("/graph", method="POST", body={"pipeline": GRAPH})
        self.assertEqual(status, 200)
        self.assertTrue(answer["ok"])
        graph, seen = self.bridge.editor_graph
        self.assertEqual(graph, GRAPH)
        self.assertGreater(seen, 0)

    def test_connected_means_somebody_is_reading_it_now(self):
        """Not "an editor said something once": a page closed an hour ago would
        have an agent announcing it had put a graph on a canvas nobody has
        open."""
        self.request("/graph", method="POST", body={"pipeline": GRAPH})
        self.assertFalse(self.bridge.connected, "a graph posted is not a reader")

        seen = []
        reading = threading.Event()

        def listen():
            url = f"{self.url}/events?token={self.bridge.token}"
            request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(request, timeout=10) as stream:
                reading.set()
                for line in stream:
                    if line.decode().startswith("data:"):
                        seen.append(line)
                        return

        reader = threading.Thread(target=listen, daemon=True)
        reader.start()
        reading.wait(2)
        for _ in range(40):  # the handler counts itself in as it starts
            if self.bridge.connected:
                break
            time.sleep(0.05)
        self.assertTrue(self.bridge.connected, "a stream being read is a connection")
        self.assertEqual(self.bridge.listening, 1)

        self.bridge.push("graph", pipeline=GRAPH)  # let the reader finish
        reader.join(timeout=10)

        # a page that went away is noticed when something is next written to it
        # — that write fails and the handler lets go. Until then, or until the
        # heartbeat, a closed tab still counts, which is the honest limit of
        # knowing anything about the other end of a socket.
        for _ in range(60):
            if not self.bridge.connected:
                break
            self.bridge.push("ping")
            time.sleep(0.1)
        self.assertFalse(self.bridge.connected, "a reader that left stops counting")

    def test_a_body_that_is_not_a_graph_is_refused(self):
        for body in ({"pipeline": "a string"}, {"nothing": 1}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/graph", method="POST", body=body)
            self.assertEqual(caught.exception.code, 400)

    def test_a_graph_pushed_before_anyone_listened_still_arrives(self):
        """The reason this is a log and not a queue: the page connects over a
        separate request, and an agent that pushed first would otherwise be
        looking at an empty canvas it thought it had filled."""
        self.bridge.push("graph", pipeline=GRAPH, note="drawn before you looked")
        received = self._read_one_event()
        self.assertEqual(received["type"], "graph")
        self.assertEqual(received["pipeline"], GRAPH)
        self.assertEqual(received["note"], "drawn before you looked")

    def test_a_push_reaches_a_listener_that_is_already_there(self):
        got: list[dict] = []
        ready = threading.Event()

        def listen():
            ready.set()
            got.append(self._read_one_event())

        reader = threading.Thread(target=listen, daemon=True)
        reader.start()
        ready.wait(2)
        self.bridge.push("graph", pipeline=GRAPH, note="live")
        reader.join(timeout=10)
        self.assertEqual(got[0]["note"], "live")

    def _read_one_event(self) -> dict:
        url = f"{self.url}/events?token={self.bridge.token}"
        request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(request, timeout=10) as stream:
            for line in stream:
                text = line.decode().strip()
                if text.startswith("data:"):
                    return json.loads(text[len("data:"):])
        raise AssertionError("the stream ended without an event")


class LinkTests(unittest.TestCase):
    def test_the_token_is_in_the_fragment(self):
        """A fragment is never sent to the server. The editor is a page on
        somebody else's host, and a token in the query string would be in their
        access log and in the referrer of everything the page fetches."""
        bridge = Bridge(token="secret-token")
        bridge.url = "http://127.0.0.1:7433"
        link = bridge.editor_link("https://host/editor/", "https://sf.example")
        before, _, fragment = link.partition("#")
        self.assertNotIn("secret-token", before)
        self.assertIn("bridge-token=secret-token", fragment)
        self.assertIn("backend=https://sf.example", before)
        self.assertIn("bridge=http://127.0.0.1:7433", before)

    def test_an_editor_url_that_already_has_a_query_is_extended(self):
        bridge = Bridge(token="t")
        bridge.url = "http://127.0.0.1:1"
        link = bridge.editor_link("https://host/editor/?lang=ru", "https://sf.example")
        self.assertIn("?lang=ru&backend=", link)


if __name__ == "__main__":
    unittest.main()
