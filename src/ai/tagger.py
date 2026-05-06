from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger('RSStT.ai.tagger')

MAX_TAGS = 3
GENERATE_TIMEOUT = 15  # seconds
MAX_INPUT_CHARS = 1500

_client = None
_client_initialized = False
_client_lock = asyncio.Lock()


async def _get_client():
    global _client, _client_initialized
    if _client_initialized:
        return _client
    async with _client_lock:
        if _client_initialized:
            return _client
        _client_initialized = True
        from .. import env
        if not env.OPENAI_API_KEY:
            logger.warning('OPENAI_API_KEY not set, AI tagging unavailable')
            return None
        try:
            from openai import AsyncOpenAI
            _client = AsyncOpenAI(
                api_key=env.OPENAI_API_KEY,
                base_url=env.OPENAI_API_BASE,
            )
            logger.info(f'AI tagger initialized with model={env.DEFAULT_AI_MODEL}, base={env.OPENAI_API_BASE}')
        except Exception as e:
            logger.error(f'Failed to initialize AI tagger: {e}')
        return _client


async def generate_tags(title: str = '', content: str = '') -> list[str]:
    """Generate up to 3 hashtag words from article content using AI.

    Returns a list of clean tag strings (no # prefix, no spaces within tag).
    Falls back to empty list on any error.
    """
    client = await _get_client()
    if client is None:
        return []

    from .. import env

    text = (title.strip() + '\n' + content.strip()).strip()[:MAX_INPUT_CHARS]
    if not text:
        return []

    prompt = (
        'Based on the following article, generate at most 3 concise keyword tags '
        'that best represent its main topics.\n'
        'Rules:\n'
        '- Output ONLY the tags separated by spaces\n'
        '- No # symbols, no punctuation, no explanations\n'
        '- Each tag should be a single word or compound word without spaces\n'
        '- Use the same language as the article\n'
        '- Example output: 人工智能 开源 编程\n\n'
        f'Article:\n{text}'
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=env.DEFAULT_AI_MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=60,
                temperature=0.3,
            ),
            timeout=GENERATE_TIMEOUT,
        )
        raw = (response.choices[0].message.content or '').strip()
        tags = _parse_tags(raw)
        logger.debug(f'AI tags generated: {tags} (raw: {raw!r})')
        return tags[:MAX_TAGS]
    except asyncio.TimeoutError:
        logger.warning('AI tag generation timed out')
        return []
    except Exception as e:
        logger.warning(f'AI tag generation failed: {e}')
        return []


def _parse_tags(raw: str) -> list[str]:
    """Parse raw AI output into a list of clean tag strings."""
    raw = raw.replace('#', '').strip()
    parts = re.split(r'[\s,，、;；]+', raw)
    tags = []
    for part in parts:
        # Keep word chars + CJK/Japanese/Korean characters
        part = re.sub(
            r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7a3-]',
            '',
            part,
        ).strip()
        if part and 1 <= len(part) <= 30:
            tags.append(part)
    return tags
