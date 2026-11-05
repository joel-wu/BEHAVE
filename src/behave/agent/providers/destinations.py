"""Fixed hosted destinations shared by profiles and transport retry policy.

This data-only module must not import brokers, credentials or process launchers.
Local Chat uses its separately validated loopback URL, not this registry.
"""

DESTINATIONS = {
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "fireworks": "https://api.fireworks.ai/inference/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
