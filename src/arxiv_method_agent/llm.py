from __future__ import annotations

import logging
import os
import time
from typing import Type, TypeVar

from anthropic import AuthenticationError, BadRequestError, PermissionDeniedError
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential, before_sleep_log

from .config import LLMConfig

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Thin wrapper around ChatAnthropic with structured output."""

    def __init__(self, cfg: LLMConfig):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Export it before running: export ANTHROPIC_API_KEY='sk-ant-...'"
            )
        self._model = ChatAnthropic(
            model=cfg.model_name,
            temperature=cfg.temperature,
            api_key=api_key,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_not_exception_type((AuthenticationError, BadRequestError, PermissionDeniedError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def structured(self, prompt: str, schema: Type[T]) -> T:
        runnable = self._model.with_structured_output(schema)
        result = runnable.invoke(prompt)
        time.sleep(0.5)
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_not_exception_type((AuthenticationError, BadRequestError, PermissionDeniedError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def text(self, prompt: str) -> str:
        result = self._model.invoke(prompt)
        time.sleep(0.5)
        content = getattr(result, "content", "")
        if isinstance(content, str):
            return content
        return str(content)
