import json
import logging
import os
import traceback
from typing import AsyncGenerator

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

logger = logging.getLogger("utils")


def new_async_client(_app):
    if "claude-3" in _app.ctx.model.lower():
        return AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    else:
        return AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            http_client=_app.ctx.http_session,
        )


async def _raw_stream_response(
    _app, contexts, llm_response, related_questions_future
) -> AsyncGenerator[str, None]:
    """
    A generator that yields the raw stream response. You do not need to call
    this directly. Instead, use the stream_and_upload_to_kv which will also
    upload the response to KV.
    """
    # First, yield the contexts.
    yield json.dumps(contexts)
    yield "\n\n__LLM_RESPONSE__\n\n"
    # Second, yield the llm response.
    if not contexts:
        # Prepend a warning to the user
        yield (
            "(The search engine returned nothing for this query. Please take the"
            " answer with a grain of salt.)\n\n"
        )

    if "claude-3" in _app.ctx.model.lower():
        # Process Claude's stream response
        async for text in llm_response:
            yield text
    else:
        # Process OpenAI's stream response
        async for chunk in llm_response:
            if chunk.choices:
                yield chunk.choices[0].delta.content or ""
    # Third, yield the related questions. If any error happens, we will just
    # return an empty list.
    if related_questions_future is not None:
        related_questions = await related_questions_future
        try:
            result = json.dumps(related_questions)
        except Exception as e:
            logger.error(f"encountered error: {e}\n{traceback.format_exc()}")
            result = "[]"
        yield "\n\n__RELATED_QUESTIONS__\n\n"
        yield result
