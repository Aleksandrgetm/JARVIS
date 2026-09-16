"""Single provider selection point; constructing clients never contacts a server."""

from brain.ai_client import DisabledAIClient
from brain.ollama_provider import OllamaClient
from brain.openai_provider import OpenAIClient


def create_ai_client(config):
    factories = {
        'disabled': lambda: DisabledAIClient(),
        'openai': lambda: OpenAIClient(config.ai_model, config.ai_timeout),
        'ollama': lambda: OllamaClient(config.ai_model, config.ai_timeout, config.ollama_url, debug=config.voice_debug),
    }
    return factories.get(config.ai_provider, factories['disabled'])()
