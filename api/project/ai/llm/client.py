"""LLM client — provider-agnostic calls via any-llm SDK.

Model definitions live in config/models/*.yml. Each defines provider,
connection URL, timeout, and model name. The client resolves a model
name to a definition and makes the call via any-llm.

Model resolution priority (selection, not retry):
  1. Explicit model_name argument
  2. Global default from config

One model is selected, one call is made. If it fails, it fails.
"""

import logging

from any_llm import completion, acompletion
from pydantic import BaseModel

from ..config.models import ModelCatalog

logger = logging.getLogger(__name__)


class LLMClient:
    """Wraps any-llm with Malcolm's model catalog."""

    def __init__(self, model_catalog: ModelCatalog):
        self._catalog = model_catalog

    def _build_kwargs(self, model_name, messages, response_model=None, **kwargs):
        """Build the kwargs dict for an any-llm completion call."""
        model_def = self._catalog.get(model_name)
        if not model_def:
            logger.error("Model '%s' not found in catalog", model_name)
            raise ValueError(f"Unknown model: {model_name}")

        avail = model_def.availability()
        if not avail['available']:
            logger.error("Model '%s' unavailable: %s", model_name, avail['hint'])
            raise ValueError(avail['hint'])

        call_kwargs = {
            "model": model_def.model,
            "provider": model_def.provider,
            "messages": messages,
            "max_tokens": model_def.max_tokens,
            "timeout": model_def.timeout,
        }

        if model_def.url:
            call_kwargs["api_base"] = model_def.url
        if model_def.api_key:
            call_kwargs["api_key"] = model_def.api_key
        if response_model and issubclass(response_model, BaseModel):
            call_kwargs["response_format"] = response_model

        call_kwargs.update(kwargs)
        return call_kwargs

    def call(self, model_name, messages, response_model=None, **kwargs):
        """Make a synchronous LLM call."""
        call_kwargs = self._build_kwargs(model_name, messages, response_model, **kwargs)
        logger.debug("LLM call: model=%s provider=%s", call_kwargs["model"], call_kwargs["provider"])
        return completion(**call_kwargs)

    async def acall(self, model_name, messages, response_model=None, **kwargs):
        """Make an async LLM call. Same interface as call()."""
        call_kwargs = self._build_kwargs(model_name, messages, response_model, **kwargs)
        logger.debug("LLM acall: model=%s provider=%s", call_kwargs["model"], call_kwargs["provider"])
        return await acompletion(**call_kwargs)

    @property
    def default_model(self):
        return self._catalog.default_model

    def available_models(self):
        return self._catalog.available_models()
