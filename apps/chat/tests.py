"""Chat tests. No real API calls: the HTTP layer is faked, or mock mode is on."""
import json
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.llms.models import LLMModel
from apps.llms.services.openrouter import ChatResult, ChatStream, OpenRouterError

from .models import ChatMessage, Conversation
from .services import chat as chat_service
from .services.chat import build_history, stream_reply, title_from
from .services.prompts import SYSTEM_PROMPT


def sse_lines(*pieces, usage=None, finish="stop"):
    """The lines OpenRouter sends for a streamed reply."""
    lines = [": OPENROUTER PROCESSING", ""]
    for p in pieces:
        lines += ["data: " + json.dumps({"choices": [{"delta": {"content": p}, "finish_reason": None}]}), ""]
    last = {"choices": [{"delta": {}, "finish_reason": finish}]}
    if usage:
        last["usage"] = usage
    lines += ["data: " + json.dumps(last), "", "data: [DONE]", ""]
    return lines


def fake_response(status=200, lines=None, body=None, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.iter_lines.return_value = iter(lines or [])
    resp.json.return_value = body or {}
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class FakeStream:
    """Stands in for ChatStream and records the history it was given."""

    calls = []

    def __init__(self, model_id, messages, temperature=None):
        FakeStream.calls.append((model_id, messages))
        self.result = None
        self.pieces = ["Hello ", "there."]

    def __iter__(self):
        yield from self.pieces
        self.result = ChatResult(text="".join(self.pieces), latency_ms=42, prompt_tokens=10, completion_tokens=3,
                                 finish_reason="stop")


@override_settings(OPENROUTER_API_KEY="test", OPENROUTER_MAX_ATTEMPTS=3, OPENROUTER_RETRY_BACKOFF=0)
class ChatStreamTests(TestCase):
    """The streaming client, with requests.post faked."""

    def test_streams_pieces_and_fills_result(self):
        resp = fake_response(lines=sse_lines("Hi", " there", usage={"prompt_tokens": 12, "completion_tokens": 2}))
        with patch("apps.llms.services.openrouter.requests.post", return_value=resp) as post:
            stream = ChatStream("x/model", [{"role": "user", "content": "hello"}])
            self.assertEqual(list(stream), ["Hi", " there"])
        self.assertEqual(stream.result.text, "Hi there")
        self.assertEqual(stream.result.prompt_tokens, 12)
        self.assertEqual(stream.result.completion_tokens, 2)
        self.assertEqual(stream.result.finish_reason, "stop")
        payload = post.call_args.kwargs["json"]
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])
        self.assertTrue(post.call_args.kwargs["stream"])

    def test_length_finish_marks_truncated(self):
        resp = fake_response(lines=sse_lines("Cut", finish="length"))
        with patch("apps.llms.services.openrouter.requests.post", return_value=resp):
            stream = ChatStream("x/model", [])
            list(stream)
        self.assertTrue(stream.result.truncated)

    def test_retries_busy_provider_before_first_piece(self):
        busy = fake_response(status=429, body={"error": {"message": "busy"}})
        ok = fake_response(lines=sse_lines("ok"))
        with patch("apps.llms.services.openrouter.requests.post", side_effect=[busy, ok]):
            stream = ChatStream("x/model", [])
            self.assertEqual(list(stream), ["ok"])
        self.assertEqual(stream.result.attempts, 2)

    def test_retries_network_error(self):
        ok = fake_response(lines=sse_lines("ok"))
        with patch("apps.llms.services.openrouter.requests.post", side_effect=[requests.ConnectionError("down"), ok]):
            self.assertEqual(list(ChatStream("x/model", [])), ["ok"])

    def test_permanent_error_is_not_retried(self):
        dead = fake_response(status=404, body={"error": {"message": "No endpoints found"}})
        with patch("apps.llms.services.openrouter.requests.post", return_value=dead) as post:
            with self.assertRaises(OpenRouterError) as ctx:
                list(ChatStream("x/gone", []))
        self.assertEqual(post.call_count, 1)
        self.assertIn("No endpoints found", str(ctx.exception))

    def test_empty_reply_gives_up(self):
        with patch("apps.llms.services.openrouter.requests.post", side_effect=lambda *a, **k: fake_response(lines=sse_lines())):
            with self.assertRaises(OpenRouterError) as ctx:
                list(ChatStream("x/model", []))
        self.assertIn("gave up after 3 attempts", str(ctx.exception))

    def test_error_after_text_is_not_retried(self):
        lines = sse_lines("Part")[:4] + ["data: " + json.dumps({"error": {"code": 502, "message": "upstream died"}}), ""]
        with patch("apps.llms.services.openrouter.requests.post", return_value=fake_response(lines=lines)) as post:
            got = []
            with self.assertRaises(OpenRouterError) as ctx:
                for piece in ChatStream("x/model", []):
                    got.append(piece)
        self.assertEqual(got, ["Part"])
        self.assertEqual(post.call_count, 1)
        self.assertIn("cut off", str(ctx.exception))

    @override_settings(OPENROUTER_API_KEY="")
    def test_missing_key(self):
        with self.assertRaises(OpenRouterError):
            list(ChatStream("x/model", []))


class HistoryTests(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(title="t")

    def add(self, role, content, error=""):
        return ChatMessage.objects.create(conversation=self.conv, role=role, content=content, error=error)

    def test_system_prompt_and_order(self):
        self.add("user", "I feel dizzy")
        self.add("assistant", "Since when?")
        self.add("user", "Two days")
        history = build_history(self.conv)
        self.assertEqual(history[0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertEqual([m["content"] for m in history[1:]], ["I feel dizzy", "Since when?", "Two days"])

    def test_failed_and_empty_replies_are_left_out(self):
        self.add("user", "Hi")
        self.add("assistant", "half an ans", error="HTTP 502")
        self.add("assistant", "")
        self.add("user", "Hello?")
        self.assertEqual([m["content"] for m in build_history(self.conv)[1:]], ["Hi", "Hello?"])

    @override_settings(CHAT_HISTORY_MESSAGES=3)
    def test_trims_to_recent_and_starts_with_patient(self):
        for i in range(3):
            self.add("user", f"u{i}")
            self.add("assistant", f"a{i}")
        self.add("user", "u3")
        # Only the last 3 messages are sent.
        self.assertEqual([m["content"] for m in build_history(self.conv)[1:]], ["u2", "a2", "u3"])

    @override_settings(CHAT_HISTORY_MESSAGES=2)
    def test_trim_drops_leading_reply(self):
        self.add("user", "u0")
        self.add("assistant", "a0")
        self.add("user", "u1")
        self.assertEqual([m["content"] for m in build_history(self.conv)[1:]], ["u1"])

    def test_title(self):
        self.assertEqual(title_from("  short   one "), "short one")
        self.assertEqual(len(title_from("x" * 200)), 60)


@override_settings(LLM_MOCK_MODE=False, OPENROUTER_API_KEY="test")
class StreamReplyTests(TestCase):
    def setUp(self):
        FakeStream.calls = []
        self.llm = LLMModel.objects.create(display_name="Model A", provider="OpenAI", openrouter_id="openai/a")
        self.conv = Conversation.objects.create(title="t")
        ChatMessage.objects.create(conversation=self.conv, role="user", content="Hello")

    def test_saves_reply_and_metadata(self):
        with patch.object(chat_service, "ChatStream", FakeStream):
            events = list(stream_reply(self.conv, self.llm))
        self.assertEqual([e for e, _ in events], ["delta", "delta", "done"])
        reply = self.conv.messages.last()
        self.assertEqual(reply.content, "Hello there.")
        self.assertEqual(reply.model_name, "Model A")
        self.assertEqual(reply.latency_ms, 42)
        self.assertEqual(reply.completion_tokens, 3)
        self.assertEqual(events[-1][1]["id"], reply.pk)

    def test_error_is_saved(self):
        class Broken(FakeStream):
            def __iter__(self):
                yield "Par"
                raise OpenRouterError("HTTP 502: provider error")

        with patch.object(chat_service, "ChatStream", Broken):
            events = list(stream_reply(self.conv, self.llm))
        self.assertEqual(events[-1][0], "error")
        reply = self.conv.messages.last()
        self.assertEqual(reply.error, "HTTP 502: provider error")
        self.assertEqual(reply.content, "Par")
        self.assertEqual(len(build_history(self.conv)), 2)  # system + "Hello": the broken reply is left out

    def test_disconnect_saves_partial_reply(self):
        with patch.object(chat_service, "ChatStream", FakeStream):
            gen = stream_reply(self.conv, self.llm)
            next(gen)  # first piece arrives, then the browser goes away
            gen.close()
        reply = self.conv.messages.last()
        self.assertTrue(reply.stopped)
        self.assertEqual(reply.content, "Hello")
        self.assertEqual(reply.error, "")

    def test_reply_deleted_mid_stream_stays_deleted(self):
        with patch.object(chat_service, "ChatStream", FakeStream):
            gen = stream_reply(self.conv, self.llm)
            next(gen)
            self.conv.messages.filter(role="assistant").delete()  # Regenerate clicked right after Stop
            list(gen)
        self.assertEqual(list(self.conv.messages.values_list("role", flat=True)), ["user"])

    def test_chat_deleted_mid_stream(self):
        with patch.object(chat_service, "ChatStream", FakeStream):
            gen = stream_reply(self.conv, self.llm)
            next(gen)
            Conversation.objects.all().delete()
            list(gen)
        self.assertFalse(ChatMessage.objects.exists())

    def test_unexpected_exception_does_not_crash(self):
        class Buggy(FakeStream):
            def __iter__(self):
                raise ValueError("boom")
                yield  # pragma: no cover

        with patch.object(chat_service, "ChatStream", Buggy):
            events = list(stream_reply(self.conv, self.llm))
        self.assertEqual(events, [("error", {"id": self.conv.messages.last().pk, "error": "Unexpected error: boom"})])


def read_events(response) -> list[tuple[str, dict]]:
    body = b"".join(response.streaming_content).decode()
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@override_settings(LLM_MOCK_MODE=False, OPENROUTER_API_KEY="test")
class ChatViewTests(TestCase):
    def setUp(self):
        FakeStream.calls = []
        self.a = LLMModel.objects.create(display_name="Model A", provider="OpenAI", openrouter_id="openai/a",
                                         selected_by_default=True)
        self.b = LLMModel.objects.create(display_name="Model B", provider="Anthropic", openrouter_id="anthropic/b")
        patcher = patch.object(chat_service, "ChatStream", FakeStream)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, text, model=None, conversation=None):
        data = {"message": text, "model": (model or self.a).pk}
        if conversation:
            data["conversation"] = conversation.pk
        return self.client.post(reverse("chat:send"), data)

    def test_empty_chat_page(self):
        resp = self.client.get(reverse("chat:new"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "How can I help today?")
        self.assertContains(resp, "Model B")  # in the model menu
        self.assertEqual(resp.context["current_llm"], self.a)

    def test_first_message_creates_conversation_and_streams(self):
        resp = self.send("I feel dizzy since my new pill")
        self.assertEqual(resp["Content-Type"], "text/event-stream")
        events = read_events(resp)
        names = [e for e, _ in events]
        self.assertEqual(names[0], "start")
        self.assertEqual(names[-1], "done")
        self.assertEqual("".join(d["text"] for e, d in events if e == "delta"), "Hello there.")
        conv = Conversation.objects.get()
        self.assertTrue(events[0][1]["created"])
        self.assertEqual(events[0][1]["conversation"], conv.pk)
        self.assertEqual(conv.title, "I feel dizzy since my new pill")
        self.assertEqual([m.role for m in conv.messages.all()], ["user", "assistant"])

    def test_second_message_sends_the_whole_history(self):
        read_events(self.send("I feel dizzy"))
        conv = Conversation.objects.get()
        read_events(self.send("Since Monday", model=self.b, conversation=conv))
        model_id, messages = FakeStream.calls[-1]
        self.assertEqual(model_id, "anthropic/b")  # switched model mid-chat
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], "Since Monday")
        conv.refresh_from_db()
        self.assertEqual(conv.llm, self.b)
        self.assertEqual(conv.messages.count(), 4)

    def test_conversation_page_shows_history(self):
        read_events(self.send("I feel dizzy"))
        conv = Conversation.objects.get()
        resp = self.client.get(reverse("chat:conversation", args=[conv.pk]))
        self.assertContains(resp, "I feel dizzy")
        self.assertContains(resp, "Hello there.")
        self.assertContains(resp, "Regenerate")

    def test_regenerate_replaces_last_reply(self):
        read_events(self.send("I feel dizzy"))
        conv = Conversation.objects.get()
        old = conv.messages.last()
        events = read_events(self.client.post(reverse("chat:regenerate", args=[conv.pk]), {"model": self.b.pk}))
        self.assertEqual(events[-1][0], "done")
        self.assertFalse(ChatMessage.objects.filter(pk=old.pk).exists())
        self.assertEqual(conv.messages.count(), 2)
        self.assertEqual(conv.messages.last().model_name, "Model B")

    def test_validation(self):
        self.assertEqual(self.send("   ").status_code, 400)
        self.b.is_active = False
        self.b.save()
        self.assertEqual(self.send("hi", model=self.b).status_code, 400)
        self.assertEqual(self.send("x" * 9000).status_code, 400)
        self.assertEqual(self.client.get(reverse("chat:send")).status_code, 405)
        self.assertFalse(Conversation.objects.exists())

    def test_delete_and_export(self):
        read_events(self.send("I feel dizzy"))
        conv = Conversation.objects.get()
        data = json.loads(self.client.get(reverse("chat:export", args=[conv.pk])).content)
        self.assertEqual([m["role"] for m in data["messages"]], ["user", "assistant"])
        resp = self.client.post(reverse("chat:delete", args=[conv.pk]))
        self.assertRedirects(resp, reverse("chat:new"))
        self.assertFalse(ChatMessage.objects.exists())

    def test_nav_has_chat_link(self):
        self.assertContains(self.client.get(reverse("comparisons:compare")), 'href="/chat/"')


@override_settings(LLM_MOCK_MODE=True, OPENROUTER_API_KEY="")
class MockModeTests(TestCase):
    def test_mock_mode_makes_no_api_call(self):
        llm = LLMModel.objects.create(display_name="Model A", provider="OpenAI", openrouter_id="openai/a")
        with patch.object(chat_service, "MOCK_WORD_DELAY", 0), \
             patch("apps.llms.services.openrouter.requests.post") as post:
            events = read_events(self.client.post(reverse("chat:send"), {"message": "Hi", "model": llm.pk}))
        post.assert_not_called()
        self.assertEqual(events[-1][0], "done")
        self.assertIn("Mock reply", Conversation.objects.get().messages.last().content)
