from typing import Any

from juno import config, hookcore
from juno.adapters import cardjob
from juno.handlers.common import main_session


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    if cfg.get_str("handoff", "mode") != "end" or not main_session(payload):
        return None
    cardjob.on_session_end(cfg, payload, hookcore.started())
    return None
