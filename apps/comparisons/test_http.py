"""End-to-end test of the matching call over a real socket.

The other tests patch `chat()`, which proves the logic but never exercises the
part that actually talks to OpenRouter: the request body, the headers, the HTTP
round trip and the parsing of a real response envelope.

Here a small HTTP server speaks OpenRouter's protocol on localhost, the base URL
is pointed at it, and the whole path runs for real — run.analyse() to a request
on the wire to a parsed response to the stored analysis to the rendered page.
What the server returns is fixed, so this checks the contract rather than the
quality of any model's judgement; the quality check is
`python manage.py check_matching`, which calls the real API.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.comparisons.models import ComparisonRun, Task
from apps.comparisons.services.runner import analyse

CLINICIAN = "Any blood in your stool?\nWhich medication are you taking?"
MODEL_TEXT = "1. Have you noticed any rectal bleeding?\n2. Are you on any pills at the moment?"

received: list[dict] = []     # every request body the server saw
REPLY = {"covered": [1, 2]}
STATUS = 200


class FakeOpenRouter(BaseHTTPRequestHandler):
    """Speaks the subset of OpenRouter's protocol this app uses."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        received.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})

        if STATUS != 200:
            self.send_response(STATUS)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "provider busy"}}).encode())
            return

        envelope = {
            "id": "gen-test",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(REPLY)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8},
        }
        payload = json.dumps(envelope).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass          # keep the test output clean


class HttpContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def setUp(self):
        global REPLY, STATUS
        REPLY, STATUS = {"covered": [1, 2]}, 200
        received.clear()
        self.run_obj = ComparisonRun.objects.create(
            message_text="I have been bleeding and I take two tablets a day.",
            task=Task.FOLLOWUP, reference_text=CLINICIAN)
        self.out = self.run_obj.outputs.create(model_name="Test model", openrouter_id="x/test", text=MODEL_TEXT)

    def settings(self):
        return override_settings(OPENROUTER_BASE_URL=self.base, OPENROUTER_API_KEY="sk-or-test",
                                 LLM_MOCK_MODE=False, MATCH_MODEL="x/matcher", MATCH_METHOD="both")

    # --- the request we send ------------------------------------------------

    def test_request_is_well_formed(self):
        with self.settings():
            analyse(self.run_obj)

        self.assertEqual(len(received), 1)
        request = received[0]
        self.assertEqual(request["path"], "/chat/completions")
        self.assertEqual(request["auth"], "Bearer sk-or-test")

        body = request["body"]
        self.assertEqual(body["model"], "x/matcher")
        self.assertEqual(body["temperature"], 0)              # reproducible
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][0]["role"], "system")

        prompt = body["messages"][1]["content"]
        self.assertIn("1. Any blood in your stool?", prompt)   # clinician items, numbered
        self.assertIn("2. Which medication are you taking?", prompt)
        self.assertIn("rectal bleeding", prompt)               # the candidate text
        self.assertNotIn("Test model", prompt)                 # never told whose answer it is

    # --- the response we parse ---------------------------------------------

    def test_a_real_response_envelope_reaches_the_stored_analysis(self):
        with self.settings():
            analyse(self.run_obj)
        self.out.refresh_from_db()

        self.assertEqual(self.out.analysis["gt_method"], "meaning")
        self.assertEqual(self.out.analysis["gt_matched"], 2)
        self.assertEqual(self.out.analysis["gt_score"], 1.0)
        self.assertEqual(self.out.analysis["gt_missed"], [])
        # the word-overlap baseline is kept beside it, and still fails on the paraphrase
        self.assertEqual(self.out.analysis["gt_matched_lexical"], 0)

    def test_partial_coverage_round_trips(self):
        global REPLY
        REPLY = {"covered": [2]}
        with self.settings():
            analyse(self.run_obj)
        self.out.refresh_from_db()
        self.assertEqual(self.out.analysis["gt_missed"], ["Any blood in your stool?"])
        self.assertEqual(self.out.analysis["gt_covered"], ["Which medication are you taking?"])

    def test_the_numbers_reach_the_page(self):
        with self.settings():
            analyse(self.run_obj)
            page = self.client.get(reverse("comparisons:run", args=[self.run_obj.pk])).content.decode()
        self.assertIn("Clinician Match", page)
        self.assertIn("Clinician Match (words)", page)
        self.assertIn("100%", page)     # the meaning score
        self.assertIn("0%", page)       # the baseline it beat

    # --- what happens when the provider misbehaves --------------------------

    def test_a_429_falls_back_to_the_baseline_without_retrying(self):
        global STATUS
        STATUS = 429
        with self.settings():
            analyse(self.run_obj)
        self.out.refresh_from_db()
        self.assertEqual(len(received), 1)                    # one attempt, no retry storm
        self.assertEqual(self.out.analysis["gt_method"], "lexical")
        self.assertTrue(self.out.analysis["gt_fallback"])
        self.assertIn("gt_score", self.out.analysis)          # the card still has a number

    def test_a_model_that_chats_instead_of_answering_falls_back(self):
        global REPLY
        REPLY = "I'd be happy to help with that!"              # not the JSON we asked for
        with self.settings():
            analyse(self.run_obj)
        self.out.refresh_from_db()
        self.assertEqual(self.out.analysis["gt_method"], "lexical")

    def test_the_self_test_command_runs_against_the_api(self):
        """check_matching is what the user runs to verify the real endpoint.

        Here the server answers every case correctly, so what is under test is the
        command — its calls, its scoring and its exit status — not a model.
        """
        from io import StringIO

        from django.core.management import call_command

        global REPLY
        covers = {"Have you noticed any rectal bleeding?",
                  "Are you on any pills or tablets at the moment?",
                  "How long has this been going on for?",
                  "Have you had any diarrhoea or constipation recently?"}

        class Answerer(FakeOpenRouter):
            pass

        def do_POST(self):            # noqa: N802 - matching BaseHTTPRequestHandler
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            received.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
            prompt = body["messages"][1]["content"]
            answer = {"covered": [1]} if any(c in prompt for c in covers) else {"covered": []}
            payload = json.dumps({"choices": [{"message": {"content": json.dumps(answer)},
                                               "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        Answerer.do_POST = do_POST
        self.server.RequestHandlerClass = Answerer
        try:
            out = StringIO()
            with self.settings():
                call_command("check_matching", stdout=out)
            text = out.getvalue()
            self.assertIn("by meaning: 8/8", text)
            self.assertIn("by words:", text)
            self.assertIn("Matching works", text)
            self.assertEqual(len(received), 8)
        finally:
            self.server.RequestHandlerClass = FakeOpenRouter

    def test_junk_numbers_in_the_reply_are_ignored(self):
        global REPLY
        REPLY = {"covered": [1, 7, -2, "two"]}                 # only 1 is a real item
        with self.settings():
            analyse(self.run_obj)
        self.out.refresh_from_db()
        self.assertEqual(self.out.analysis["gt_matched"], 1)
        self.assertEqual(self.out.analysis["gt_covered"], ["Any blood in your stool?"])
