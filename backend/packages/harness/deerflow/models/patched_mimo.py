"""MiMo chat completions with reasoning replay."""
from deerflow.models.reasoning_chat_openai import ReasoningChatOpenAI


class PatchedChatMiMo(ReasoningChatOpenAI):
    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "MIMO_API_KEY", "openai_api_key": "MIMO_API_KEY"}
