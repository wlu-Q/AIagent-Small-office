"""OpenAI-compatible chat models that replay reasoning_content across tool calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr

from deerflow.models.assistant_payload_replay import restore_assistant_payloads, restore_reasoning_content

_MISSING = object()


def _extract_reasoning_content(value: Any) -> str | object:
    """Return reasoning_content from a dict/Pydantic object, preserving empty strings."""
    if isinstance(value, Mapping):
        if "reasoning_content" in value and value["reasoning_content"] is not None:
            return value["reasoning_content"]
        return _MISSING

    reasoning = getattr(value, "reasoning_content", _MISSING)
    if reasoning is not _MISSING and reasoning is not None:
        return reasoning

    model_extra = getattr(value, "model_extra", None)
    if isinstance(model_extra, Mapping) and "reasoning_content" in model_extra and model_extra["reasoning_content"] is not None:
        return model_extra["reasoning_content"]

    return _MISSING


def _with_reasoning_content(message: AIMessage | AIMessageChunk, reasoning: str) -> AIMessage | AIMessageChunk:
    additional_kwargs = dict(message.additional_kwargs)
    if additional_kwargs.get("reasoning_content") != reasoning:
        additional_kwargs["reasoning_content"] = reasoning
    return message.model_copy(update={"additional_kwargs": additional_kwargs})


def _get_typed_choice_message(response: Any, index: int) -> Any:
    choices = getattr(response, "choices", None)
    if choices is None:
        return None
    try:
        return choices[index].message
    except (AttributeError, IndexError, TypeError):
        return None


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI with ``reasoning_content`` preservation for multi-turn tool calls."""

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        original_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        restore_assistant_payloads(
            payload.get("messages", []),
            original_messages,
            restore_reasoning_content,
        )

        return payload

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return None

        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning = _extract_reasoning_content(delta)
            if reasoning is not _MISSING and isinstance(generation_chunk.message, AIMessageChunk):
                generation_chunk = ChatGenerationChunk(
                    message=_with_reasoning_content(generation_chunk.message, reasoning),
                    generation_info=generation_chunk.generation_info,
                )

        return generation_chunk

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices", [])

        patched_generations: list[ChatGeneration] | None = None
        for index, generation in enumerate(result.generations):
            choice = choices[index] if index < len(choices) else {}
            choice_message = choice.get("message", {}) if isinstance(choice, Mapping) else {}
            reasoning = _extract_reasoning_content(choice_message)
            if reasoning is _MISSING and not isinstance(response, dict):
                reasoning = _extract_reasoning_content(_get_typed_choice_message(response, index))

            message = generation.message
            if reasoning is not _MISSING and isinstance(message, AIMessage):
                if patched_generations is None:
                    patched_generations = list(result.generations)
                patched_generations[index] = ChatGeneration(
                    message=_with_reasoning_content(message, reasoning),
                    generation_info=generation.generation_info,
                )

        return ChatResult(generations=patched_generations or result.generations, llm_output=result.llm_output)


class ChatQwen(ReasoningChatOpenAI):
    """Qwen chat completions through Alibaba Cloud Model Studio."""

    openai_api_key: SecretStr = Field(alias="api_key")
    openai_api_base: str = Field(alias="base_url")

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "DASHSCOPE_API_KEY", "openai_api_key": "DASHSCOPE_API_KEY"}
