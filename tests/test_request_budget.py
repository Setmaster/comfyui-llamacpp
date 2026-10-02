"""Pure budget policy and strict complete-request token-count contracts."""

from __future__ import annotations

import copy
import json

import pytest
import requests

from generation.budget import (
    CONTEXT_LIMIT_SOURCE,
    INPUT_TOKEN_SOURCE,
    RequestBudget,
    RequestBudgetError,
    RequestBudgetStatus,
    effective_context_limit,
    enforce_request_budget,
)
from runtime.client import (
    AuthConfig,
    ConnectionConfig,
    InputTokenSupport,
    LlamaClientError,
    LlamaServerClient,
    OperationCancelled,
    ResponseBodyLimitError,
    ResponseProtocolError,
    TLSConfig,
)


class Response:
    def __init__(self, body, status=200):
        self.body = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.status_code = status
        self.headers = {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def client_for(response):
    session = Session(response)
    config = ConnectionConfig(
        "https://example.invalid",
        auth=AuthConfig("private-token"),
        tls=TLSConfig(verify="ca.pem", cert=("cert.pem", "key.pem")),
    )
    return LlamaServerClient(config, session=session), session


@pytest.mark.parametrize(
    "input_tokens,max_tokens,status,remaining",
    [
        (90, 10, "fit", 0),
        (90, 11, "overflow", -1),
        (None, 10, "unknown", None),
        (0, 10, "fit", 90),
    ],
)
def test_budget_reserves_requested_output_without_modifying_request(
    input_tokens,
    max_tokens,
    status,
    remaining,
):
    budget = RequestBudget(
        input_tokens,
        100,
        max_tokens,
        "exact/model",
        INPUT_TOKEN_SOURCE,
        CONTEXT_LIMIT_SOURCE,
    )
    assert budget.status.value == status
    assert budget.remaining == remaining
    assert RequestBudget.from_json(budget.to_json()) == budget
    assert enforce_request_budget(budget) is budget
    if status == "fit":
        assert enforce_request_budget(budget, "enforce") is budget
    else:
        with pytest.raises(RequestBudgetError):
            enforce_request_budget(budget, "enforce")


def test_unknown_context_is_never_zero_or_total_context():
    props = {"n_ctx": 8192, "n_ctx_train": 32768, "total_context": 65536}
    assert effective_context_limit(props) is None
    for unknown in (None, 0, -1, True, 3.0, "4096", 2**63):
        assert (
            effective_context_limit({**props, "default_generation_settings": {"n_ctx": unknown}})
            is None
        )
    props["default_generation_settings"] = {"n_ctx": 2048}
    assert effective_context_limit(props) == 2048
    assert RequestBudget(1, None, 1).status == RequestBudgetStatus.UNKNOWN


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", True),
        ("input_tokens", -1),
        ("context_limit", 0),
        ("max_tokens", True),
        ("max_tokens", 0),
        ("max_tokens", 2**63),
        ("warnings", "not a sequence"),
        ("warnings", ["x"] * 33),
    ],
)
def test_budget_rejects_invalid_contract_values(field, value):
    kwargs = dict(input_tokens=20, context_limit=100, max_tokens=10)
    kwargs[field] = value
    with pytest.raises((ValueError, TypeError)):
        RequestBudget(**kwargs)


def test_versioned_budget_rejects_tampered_derived_values_and_unbounded_json():
    value = RequestBudget(90, 100, 10).as_dict()
    for field, bad in (
        ("remaining", False),
        ("remaining", 999),
        ("status", "overflow"),
        ("schema_version", True),
    ):
        with pytest.raises(ValueError):
            RequestBudget.from_dict({**value, field: bad})
    with pytest.raises(ValueError, match="duplicate"):
        RequestBudget.from_json('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="invalid JSON"):
        RequestBudget.from_json('{"input_tokens": NaN}')
    with pytest.raises(ValueError, match="exceeds"):
        RequestBudget.from_json(" " * 65_537)


def test_count_forwards_complete_payload_exact_model_no_autoload_auth_tls():
    payload = {
        "model": "router/exact-file.gguf",
        "stream": True,
        "max_tokens": 12,
        "messages": [
            {"role": "system", "content": "Be concise"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    before = copy.deepcopy(payload)
    response = Response({"input_tokens": 123})
    client, session = client_for(response)
    result = client.count_chat_input_tokens(payload)
    assert result.input_tokens == 123
    assert result.support == InputTokenSupport.SUPPORTED
    args, kwargs = session.calls[0]
    assert args == ("POST", "https://example.invalid/v1/chat/completions/input_tokens")
    assert kwargs["json"] == before == payload
    assert kwargs["params"] == {"autoload": "false"}
    assert kwargs["headers"]["Authorization"] == "Bearer private-token"
    assert kwargs["verify"] == "ca.pem"
    assert kwargs["cert"] == ("cert.pem", "key.pem")
    assert kwargs["stream"] is True and kwargs["allow_redirects"] is False
    assert response.closed


def test_direct_count_preserves_absence_of_model():
    client, session = client_for(Response({"input_tokens": 0}))
    assert client.count_chat_input_tokens({"messages": []}).input_tokens == 0
    assert "model" not in session.calls[0][1]["json"]


@pytest.mark.parametrize(
    "body",
    [
        {"input_tokens": True},
        {"input_tokens": -1},
        {"input_tokens": 1.0},
        {"input_tokens": "1"},
        {"input_tokens": 2**63},
        {},
        [],
        "",
        "not JSON",
        '{"input_tokens":1,"input_tokens":2}',
        '{"input_tokens":NaN}',
    ],
)
def test_count_rejects_malformed_counts_instead_of_reporting_zero(body):
    response = Response(body)
    client, _ = client_for(response)
    with pytest.raises(ResponseProtocolError):
        client.count_chat_input_tokens({"messages": []})
    assert response.closed


def test_count_response_has_a_small_bound():
    response = Response('{"input_tokens":1,"padding":"' + "x" * 65_536 + '"}')
    client, _ = client_for(response)
    with pytest.raises(ResponseBodyLimitError):
        client.count_chat_input_tokens({"messages": []})
    assert response.closed


@pytest.mark.parametrize("status", [404, 405])
def test_unavailable_endpoint_is_unknown_count_not_a_fabricated_zero(status):
    response = Response("unavailable HTML or plain text", status=status)
    client, _ = client_for(response)
    result = client.count_chat_input_tokens({"messages": []})
    assert result.input_tokens is None
    assert result.support == InputTokenSupport.UNSUPPORTED
    assert response.closed


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_count_http_failures_keep_classification(status):
    response = Response({"error": "private server detail"}, status=status)
    client, _ = client_for(response)
    with pytest.raises(LlamaClientError) as exc:
        client.count_chat_input_tokens({"messages": []})
    assert exc.value.status_code == status
    assert "private" not in str(exc.value)
    assert response.closed


def test_tls_failure_remains_distinct_and_redacted():
    client, _ = client_for(requests.exceptions.SSLError("certificate failed private-token"))
    with pytest.raises(LlamaClientError, match="TLS") as exc:
        client.count_chat_input_tokens({"messages": []})
    assert "private-token" not in str(exc.value)


def test_precancelled_count_never_dispatches():
    client, session = client_for(Response({"input_tokens": 1}))
    with pytest.raises(OperationCancelled):
        client.count_chat_input_tokens({"messages": []}, cancel=lambda: True)
    assert session.calls == []
