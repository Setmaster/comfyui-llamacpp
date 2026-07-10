from __future__ import annotations

import json
import unittest

from runtime.client import AuthConfig, ConnectionConfig, Deadline, ModelState, TLSConfig
from runtime.streaming import iter_model_events, iter_sse_events, stream_chat


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class FakeResponse:
    def __init__(
        self, lines=(), *, status_code=200, payload=None, text="", before_line=None
    ) -> None:
        self.lines = list(lines)
        self.status_code = status_code
        self.payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = {}
        self.before_line = before_line
        self.closed = False

    def json(self):
        if self.payload is None:
            raise ValueError("no JSON")
        return self.payload

    def iter_lines(self, decode_unicode=False):
        for line in self.lines:
            if self.before_line:
                self.before_line()
            yield line

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def close(self) -> None:
        self.closed = True


def data_line(payload) -> str:
    return f"data: {json.dumps(payload)}"


class SSEParserTests(unittest.TestCase):
    def test_comments_multiline_data_and_metadata(self) -> None:
        events = list(
            iter_sse_events(
                [
                    ": ping",
                    "event: update",
                    "id: 42",
                    "retry: 1000",
                    "data: first",
                    "data: second",
                    "",
                ]
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data, "first\nsecond")
        self.assertEqual(events[0].event, "update")
        self.assertEqual(events[0].id, "42")
        self.assertEqual(events[0].retry, 1000)

    def test_pending_event_is_dispatched_at_eof(self) -> None:
        events = list(iter_sse_events([b"data: final"]))
        self.assertEqual(events[0].data, "final")


class ChatStreamingTests(unittest.TestCase):
    def connection(self) -> ConnectionConfig:
        return ConnectionConfig(
            "https://localhost:8080",
            auth=AuthConfig("top-secret"),
            tls=TLSConfig(verify=False),
            connect_timeout=2,
            read_timeout=5,
        )

    def test_complete_stream_collects_content_reasoning_usage_and_finish(self) -> None:
        chunks = []
        lines = [
            data_line(
                {
                    "id": "chat-1",
                    "model": "vlm",
                    "choices": [{"delta": {"reasoning_content": "think ", "content": "Hello "}}],
                }
            ),
            "",
            data_line({"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]}),
            "",
            data_line({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
            "",
            "data: [DONE]",
            "",
        ]
        response = FakeResponse(lines)
        session = FakeSession(response)
        result = stream_chat(
            self.connection(),
            {"messages": [], "stream": True},
            session=session,
            on_chunk=lambda content, reasoning: chunks.append((content, reasoning)),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response, "Hello world")
        self.assertEqual(result.thinking, "think ")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage["completion_tokens"], 2)
        self.assertEqual(result.response_id, "chat-1")
        self.assertTrue(result.done_received)
        self.assertFalse(result.partial)
        self.assertEqual(chunks, [("Hello ", "think "), ("world", "")])
        self.assertTrue(response.closed)
        self.assertFalse(session.closed)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://localhost:8080/v1/chat/completions"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer top-secret")
        self.assertFalse(kwargs["verify"])
        self.assertTrue(kwargs["stream"])

    def test_complete_stream_preserves_exact_content_and_reasoning_whitespace(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "  leading",
                                    "reasoning_content": "\nthink ",
                                }
                            }
                        ]
                    }
                ),
                "",
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "\ntrailing  ",
                                    "reasoning_content": "tail\n",
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ),
                "",
            ]
        )

        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))

        self.assertTrue(result.success)
        self.assertEqual(result.response, "  leading\ntrailing  ")
        self.assertEqual(result.thinking, "\nthink tail\n")
        self.assertFalse(result.partial)

    def test_finish_reason_is_terminal_without_done_marker(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {"choices": [{"delta": {"content": "complete"}, "finish_reason": "length"}]}
                ),
                "",
            ]
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertTrue(result.success)
        self.assertFalse(result.done_received)
        self.assertEqual(result.finish_reason, "length")

    def test_done_marker_allows_empty_success(self) -> None:
        response = FakeResponse(["data: [DONE]", ""])
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertTrue(result.success)
        self.assertEqual(result.response, "")

    def test_unterminated_eof_returns_partial_metadata(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "partial"}}]}),
                "",
            ]
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "partial")
        self.assertEqual(result.error_type, "incomplete")
        self.assertTrue(response.closed)

    def test_unterminated_partial_stream_preserves_exact_whitespace(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "\n  partial  \n",
                                    "reasoning_content": "  unfinished thought ",
                                }
                            }
                        ]
                    }
                ),
                "",
            ]
        )

        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))

        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "\n  partial  \n")
        self.assertEqual(result.thinking, "  unfinished thought ")
        self.assertEqual(result.error_type, "incomplete")

    def test_server_error_preserves_partial_content_and_redacts_secret(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "  partial error\n"}}]}),
                "",
                data_line({"error": {"message": "key top-secret rejected"}}),
                "",
            ]
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "  partial error\n")
        self.assertEqual(result.error_type, "server")
        self.assertNotIn("top-secret", result.error_message)

    def test_cancellation_closes_response_and_returns_partial(self) -> None:
        should_cancel = False

        def on_chunk(content, reasoning):
            nonlocal should_cancel
            should_cancel = True

        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "first"}}]}),
                "",
                data_line({"choices": [{"delta": {"content": "second"}}]}),
                "",
            ]
        )
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            on_chunk=on_chunk,
            cancel=lambda: should_cancel,
        )
        self.assertFalse(result.success)
        self.assertTrue(result.cancelled)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "first")
        self.assertTrue(response.closed)

    def test_monotonic_overall_deadline_is_checked_between_lines(self) -> None:
        clock = FakeClock()

        def advance() -> None:
            clock.now += 0.6

        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "first"}}]}),
                "",
                data_line({"choices": [{"delta": {"content": "late"}}]}),
                "",
            ],
            before_line=advance,
        )
        deadline = Deadline(1.0, clock.monotonic)
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            deadline=deadline,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "timeout")
        self.assertTrue(response.closed)

    def test_http_error_is_typed_and_closed(self) -> None:
        response = FakeResponse(
            status_code=401,
            payload={"error": {"message": "invalid top-secret"}},
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.error_type, "http")
        self.assertNotIn("top-secret", result.error_message)
        self.assertTrue(response.closed)


class ModelEventStreamingTests(unittest.TestCase):
    def test_current_status_change_and_documented_model_status_are_both_typed(self) -> None:
        lines = [
            data_line({"model": "one", "event": "model_status", "data": {"status": "loading"}}),
            "",
            data_line({"model": "one", "event": "status_change", "data": {"status": "loaded"}}),
            "",
        ]
        response = FakeResponse(lines)
        session = FakeSession(response)
        connection = ConnectionConfig("http://localhost:8080", auth=AuthConfig("key"))
        events = list(iter_model_events(connection, session=session, timeout=5))

        self.assertEqual([event.event for event in events], ["model_status", "status_change"])
        self.assertEqual([event.state for event in events], [ModelState.LOADING, ModelState.LOADED])
        self.assertEqual(session.calls[0][2]["headers"]["Authorization"], "Bearer key")
        self.assertTrue(response.closed)

    def test_nonzero_unloaded_exit_is_normalized_as_failed(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "model": "one",
                        "event": "status_change",
                        "data": {"status": "unloaded", "exit_code": 9},
                    }
                ),
                "",
            ]
        )
        events = list(
            iter_model_events(
                ConnectionConfig("http://localhost:8080"),
                session=FakeSession(response),
                timeout=5,
            )
        )
        self.assertEqual(events[0].state, ModelState.FAILED)


if __name__ == "__main__":
    unittest.main()
