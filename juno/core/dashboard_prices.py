from typing import Any

PRICES: dict[str, Any] = {
    "source": "platform.claude.com/docs/en/about-claude/pricing, checked 2026-10-25",
    "unit": "USD per 1M tokens",
    "defaults": {"cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1},
    "models": {
        "claude-fable-5-1": {"in": 10, "out": 50, "cache_read": 0.25},
        "claude-fable-5": {"in": 10, "out": 50},
        "claude-mythos-5-1": {"in": 10, "out": 50, "cache_read": 0.25},
        "claude-opus-5-5": {"in": 4, "out": 20, "cache_read": 0.20},
        "claude-opus-5": {"in": 5, "out": 25},
        "claude-opus-4-8": {"in": 5, "out": 25},
        "claude-opus-4-7": {"in": 5, "out": 25},
        "claude-opus-4-6": {"in": 5, "out": 25},
        "claude-sonnet-5-5": {"in": 2, "out": 10, "cache_read": 0.10},
        "claude-sonnet-5": {"in": 2, "out": 10},
        "claude-sonnet-4-6": {"in": 3, "out": 15},
        "claude-haiku-5-5": {
            "in": 0.10,
            "out": 0.50,
            "above": {"prompt_tokens": 100000, "in": 0.50, "out": 2.50},
        },
        "claude-haiku-4-5": {"in": 1, "out": 5},
    },
}
