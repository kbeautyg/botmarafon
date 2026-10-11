# -*- coding: utf-8 -*-
u"""Запрос к ИИ для Евгения (bot/evgeny.py).

Провайдер — OpenAI-совместимый шлюз apinet.cloud, тот же, что у Евгения с
Авито (finish-bot). Ключ — только переменной Railway AI_API_KEY: в коде и в
базе его нет. Клиент — aiohttp, он уже есть у бота: отдельная библиотека
OpenAI ради одного запроса не нужна.

Модели семейства GLM «думают» перед ответом: это медленно, дорого и съедает
лимит ответа. Рассуждения выключаем полями шлюза; если шлюз какое-то поле не
знает — убираем названное в ошибке и повторяем.
"""
import asyncio
import logging
import re
import time

import aiohttp

from . import config, db

log = logging.getLogger(__name__)

TIMEOUT = 90                     # секунд на ответ
HEALTH_KEY = 'ai:health'         # последняя ошибка ИИ — видна в /евгений
THINKING_HINTS = ('glm', 'qwen', 'qwq', 'deepseek', 'kimi', 'moonshot', 'minimax',
                  'doubao', 'grok', 'hunyuan', 'ernie', 'think')
_UNKNOWN_ARGS = re.compile(r'unrecognized request arguments?\s+supplied:\s*([^(\n]+)', re.I)


class AIError(Exception):
    u"""ИИ не ответил. Текст — для людей: его видно команде."""


def ready() -> bool:
    return bool(config.AI_API_KEY)


def _thinking_off(model: str) -> bool:
    m = (model or '').lower()
    return any(hint in m for hint in THINKING_HINTS) and not ('thinking' in m and 'nothinking' not in m)


def _body(messages: list, max_tokens: int, temperature: float) -> dict:
    body = {'model': config.AI_MODEL, 'messages': messages,
            'max_tokens': max_tokens, 'temperature': temperature}
    if _thinking_off(config.AI_MODEL):
        body.update({'thinking': {'type': 'disabled'}, 'enable_thinking': False,
                     'reasoning_effort': 'none'})
    return body


def friendly(raw: str) -> str:
    u"""Частые сбои провайдера — понятными словами."""
    m = (raw or '').lower()
    if ('insufficient' in m and 'quota' in m) or u'余额' in raw or u'预扣费' in raw or 'arrears' in m:
        return u'на ключе ИИ закончились деньги (apinet.cloud) — пополните баланс'
    if '401' in m or 'invalid api key' in m or 'unauthorized' in m or 'incorrect api key' in m:
        return u'ключ ИИ неверный или отозван — проверьте AI_API_KEY в Railway'
    if 'model' in m and ('not found' in m or 'does not exist' in m):
        return u'модель %s не найдена у провайдера — проверьте AI_MODEL' % config.AI_MODEL
    if '429' in m or 'rate limit' in m or 'too many requests' in m:
        return u'провайдер ИИ просит подождать (лимит запросов)'
    return (raw or u'неизвестный сбой')[:200]


def _health(error: str = '') -> None:
    try:
        db.put_content(HEALTH_KEY, 'ai', u'%d|%s' % (int(time.time()), error))
    except Exception:                       # отметка здоровья не важнее ответа
        log.debug(u'не записали здоровье ИИ', exc_info=True)


def health() -> tuple[float, str]:
    u"""(когда, ошибка) — последняя попытка; ошибка пустая, если удалась."""
    stored = db.get_content(HEALTH_KEY)
    if not stored or '|' not in stored[1]:
        return 0.0, ''
    at, _, error = stored[1].partition('|')
    return float(at or 0), error


async def complete(messages: list, *, max_tokens: int = 700, temperature: float = 0.6) -> str:
    u"""Ответ модели текстом. Сбой — AIError с понятным текстом."""
    if not ready():
        raise AIError(u'нет ключа ИИ — добавьте переменную AI_API_KEY в Railway')
    body = _body(messages, max_tokens, temperature)
    url = config.AI_BASE_URL + '/chat/completions'
    headers = {'Authorization': 'Bearer ' + config.AI_API_KEY}
    last = u''
    for attempt in range(4):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    data = await resp.json(content_type=None)
                    status = resp.status
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as err:
            last = u'ИИ не ответил вовремя или нет связи (%s)' % type(err).__name__
            await asyncio.sleep(2 + attempt * 3)
            continue
        if status >= 400 or not isinstance(data, dict) or 'choices' not in data:
            raw = str((data or {}).get('error') if isinstance(data, dict) else data)
            unknown = _UNKNOWN_ARGS.search(raw)
            if unknown:                 # шлюз не знает поле — убираем и повторяем
                for name in (n.strip().strip('"\'') for n in unknown.group(1).split(',')):
                    body.pop(name, None)
                continue
            last = friendly(u'%s %s' % (status, raw))
            if status in (429, 500, 502, 503, 504) and attempt < 3:
                await asyncio.sleep(3 + attempt * 5)
                continue
            break
        choice = (data.get('choices') or [{}])[0]
        text = ((choice.get('message') or {}).get('content') or '').strip()
        if not text and choice.get('finish_reason') == 'length' and body['max_tokens'] < 4000:
            body['max_tokens'] *= 2      # рассуждения съели весь лимит — даём больше
            continue
        if not text:
            last = u'ИИ вернул пустой ответ'
            continue
        _health()
        return text
    _health(last)
    raise AIError(last or u'ИИ не ответил')
