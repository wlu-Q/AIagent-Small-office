"""Tests for deerflow.models.patched_mimo.PatchedChatMiMo."""

from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage


def _make_model(**kwargs):
    from deerflow.models.patched_mimo import PatchedChatMiMo

    return PatchedChatMiMo(
        model="mimo-v2.5-pro",
        api_key="test-key",
        base_url="https://api.xiaomimimo.com/v1",
        **kwargs,
    )


def test_is_lc_serializable_returns_true():
    from deerflow.models.patched_mimo import PatchedChatMiMo

    assert PatchedChatMiMo.is_lc_serializable() is True


def test_lc_secrets_contains_mimo_api_key_mapping():
    model = _make_model()

    assert model.lc_secrets["api_key"] == "MIMO_API_KEY"
    assert model.lc_secrets["openai_api_key"] == "MIMO_API_KEY"


def test_reasoning_content_injected_into_assistant_tool_call_message():
    model = _make_model()

    human = HumanMessage(content="Check Beijing weather.")
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "call_weather", "name": "get_weather", "args": {"location": "Beijing"}}],
        additional_kwargs={"reasoning_content": "I need to call the weather tool."},
    )
    payload = model._get_request_payload([human, ai])

    assert payload["messages"][1]["reasoning_content"] == "I need to call the weather tool."


def test_reasoning_content_is_noop_when_missing():
    model = _make_model()

    human = HumanMessage(content="hello")
    ai = AIMessage(content="hi", additional_kwargs={})
    payload = model._get_request_payload([human, ai])

    assert "reasoning_content" not in payload["messages"][1]


def test_create_chat_result_maps_message_reasoning_content():
    model = _make_model()
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The weather is sunny.",
                    "reasoning_content": "The tool returned sunny weather, so answer directly.",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }
        ],
        "model": "mimo-v2.5-pro",
    }

    result = model._create_chat_result(response)
    message = result.generations[0].message

    assert message.content == "The weather is sunny."
    assert message.additional_kwargs["reasoning_content"] == "The tool returned sunny weather, so answer directly."


def test_create_chat_result_reads_reasoning_content_from_message_attribute():
    model = _make_model()

    class FakeMessage:
        reasoning_content = "Reasoning stored on the SDK message object."

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

        def model_dump(self, **kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Answer.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": "mimo-v2.5-pro",
            }

    result = model._create_chat_result(FakeResponse())

    assert result.generations[0].message.additional_kwargs["reasoning_content"] == "Reasoning stored on the SDK message object."


def test_convert_chunk_to_generation_chunk_preserves_reasoning_deltas():
    model = _make_model()

    first = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "I need "}}]},
        AIMessageChunk,
        {},
    )
    second = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"reasoning_content": "a tool."}}]},
        AIMessageChunk,
        {},
    )
    answer = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"content": "Done."}, "finish_reason": "stop"}], "model": "mimo-v2.5-pro"},
        AIMessageChunk,
        {},
    )

    assert first is not None
    assert second is not None
    assert answer is not None

    combined = first.message + second.message + answer.message

    assert combined.additional_kwargs["reasoning_content"] == "I need a tool."
    assert combined.content == "Done."
