"""Optional official OpenAI SDK adapter. No tools, action callbacks or local data."""

import os

from brain.ai_client import AIClient, AIUnavailable


class OpenAIClient(AIClient):
    def __init__(self, model: str, timeout: float = 15.0) -> None:
        self.model = model
        self.timeout = timeout

    def complete(self, *, system_prompt, user_text, schema) -> str:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key or not self.model:
            raise AIUnavailable("OpenAI is not configured.")
        try:
            # Lazy import: CLI/STT/TTS work without the SDK installed.
            from openai import OpenAI
            with OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                        timeout=self.timeout, max_retries=0) as client:
                response = client.responses.create(
                    model=self.model,
                    input=[{"role": "system", "content": system_prompt},
                           {"role": "user", "content": user_text}],
                    text={"format": {"type": "json_schema", "name": "jarvis_intent",
                                     "strict": True, "schema": schema}},
                    tools=[], store=False, max_output_tokens=1500,
                )
                if response.status != "completed" or not response.output_text:
                    raise AIUnavailable("OpenAI returned no complete intent.")
                return response.output_text
        except Exception:
            # Never expose SDK exception messages, request bodies, or credentials.
            raise AIUnavailable("AI provider unavailable.") from None
