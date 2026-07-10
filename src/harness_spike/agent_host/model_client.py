from __future__ import annotations

from anthropic import Anthropic

from harness_spike.config import Settings


def build_model_client(settings: Settings) -> Anthropic:
    return Anthropic(
        api_key=settings.require_anthropic_api_key(),
        base_url=settings.require_anthropic_base_url(),
        default_headers=settings.anthropic_custom_headers,
    )
