# -*- coding: utf-8 -*-
u"""Запрос к ИИ для Евгения (bot/evgeny.py).

С 07.11.2026 Евгений говорит через Claude (Anthropic), ключом SLIST: переменная
Railway ANTHROPIC_API_KEY, модель — ANTHROPIC_MODEL. Запрос идёт официальной
библиотекой anthropic в Messages API: правила и факты Евгения — в system и в
кэше (одинаковые для всех людей), задание — системным сообщением в конце.

Без ключа Claude — прежний путь: OpenAI-совместимый шлюз apinet.cloud, как у
Евгения с Авито (finish-bot), ключ AI_API_KEY. Клиент — aiohttp. Модели
семейства GLM «думают» перед ответом: это медленно, дорого и съедает лимит
ответа. Рассуждения выключаем полями шлюза; если шлюз какое-то поле не знает —
убираем названное в ошибке и повторяем.

Ключей в коде и в базе нет — только в переменных Railway.
"""
import asyncio
import logging
import re
import time

import aiohttp
import anthropic

from . import config, db

log = logging.getLogger(__name__)

TIMEOUT = 90                     # секунд на ответ
HEALTH_KEY = 'ai:health'         # последняя ошибка ИИ — видна в /евгений
THINKING_HINTS = ('glm', 'qwen', 'qwq', 'deepseek', 'kimi', 'moonshot', 'minimax',
                  'doubao', 'grok', 'hunyuan', 'ernie', 'think')
_UNKNOWN_ARGS = re.compile(r'unrecognized request arguments?\s+supplied:\s*([^(\n]+)', re.I)


CLAUDE_MAX_TOKENS = 8000       # размышления Claude Opus 5.5 не выключаются и тратят тот же лимит
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
# Модели, где задание можно оставить системным сообщением в конце переписки,
# и где Anthropic сам переспросит другую модель, если эта откажется отвечать
# (fallbacks: "default"; у Haiku 5.5 такой подстраховки нет).
_MID_SYSTEM = ('claude-opus-5', 'claude-opus-4-8', 'claude-fable-5', 'claude-mythos-5',
               'claude-sonnet-5-5', 'claude-haiku-5-5')
_FALLBACKS = ('claude-opus-5', 'claude-fable-5', 'claude-sonnet-5-5')
_claude: anthropic.AsyncAnthropic | None = None


class AIError(Exception):
    u"""ИИ не ответил. Текст — для людей: его видно команде."""


def ready() -> bool:
    return bool(config.ANTHROPIC_API_KEY or config.AI_API_KEY)


def describe() -> str:
    u"""Кто отвечает за Евгения — для /евгений."""
    if config.ANTHROPIC_API_KEY:
        return u'Claude, модель %s' % config.ANTHROPIC_MODEL
    return u'шлюз apinet, модель %s' % config.AI_MODEL


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
    u"""Ответ модели текстом. Сбой — AIError с понятным текстом.

    messages — в виде OpenAI (system/user/assistant); для Claude их переводит
    claude_request. max_tokens и temperature — только для шлюза: у Claude свой
    лимит (CLAUDE_MAX_TOKENS), а temperature нынешняя версия API не принимает.
    """
    if not ready():
        raise AIError(u'нет ключа ИИ — добавьте ANTHROPIC_API_KEY (Claude) в Railway')
    if config.ANTHROPIC_API_KEY:
        return await _complete_claude(messages)
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


# ------------------------------------------------------------------ Claude

def claude_request(messages: list, model: str) -> dict:
    u"""Сообщения в виде OpenAI → system и messages для Messages API.

    Системные сообщения до переписки идут в system; первое — правила и факты
    Евгения, одинаковые для всех людей, — кэшируется: повторные запросы читают
    его из кэша за десятую часть цены. Задание в конце остаётся системным
    сообщением после реплики человека; модель, которая так не умеет, получает
    его в system. Реплики одной стороны подряд склеиваются, а переписка
    начинается с человека: первым мог написать Евгений.
    """
    system, chat, tail = [], [], []
    for m in messages:
        role, text = m['role'], (m.get('content') or '').strip()
        if not text:
            continue
        if role == 'system':
            (tail if chat else system).append(text)
        elif chat and chat[-1]['role'] == role:
            chat[-1]['content'] += '\n\n' + text
        else:
            chat.append({'role': role, 'content': text})
    if not chat or chat[0]['role'] != 'user':
        chat.insert(0, {'role': 'user', 'content': u'(начало переписки)'})
    blocks = [{'type': 'text', 'text': text} for text in system]
    if blocks:
        blocks[0]['cache_control'] = {'type': 'ephemeral'}
    if tail and chat[-1]['role'] == 'user' and model.startswith(_MID_SYSTEM):
        chat.append({'role': 'system', 'content': '\n\n'.join(tail)})
    elif tail:
        blocks.append({'type': 'text', 'text': '\n\n'.join(tail)})
    return {'system': blocks, 'messages': chat}


def claude_friendly(err: Exception) -> str:
    u"""Сбой Claude — словами для команды: его видно в /евгений и в тревоге."""
    message = str(getattr(err, 'message', '') or err)
    if isinstance(err, anthropic.AuthenticationError):
        return u'ключ Claude неверный или отозван — проверьте ANTHROPIC_API_KEY в Railway'
    if isinstance(err, anthropic.PermissionDeniedError):
        return u'ключу Claude не хватает прав'
    if isinstance(err, anthropic.NotFoundError):
        return u'модель %s не найдена — проверьте ANTHROPIC_MODEL' % config.ANTHROPIC_MODEL
    if isinstance(err, anthropic.RateLimitError):
        return u'Claude просит подождать (лимит запросов)'
    if isinstance(err, anthropic.APITimeoutError):
        return u'Claude не ответил вовремя'
    if isinstance(err, anthropic.APIConnectionError):
        return u'нет связи с Claude'
    if 'credit balance' in message.lower():
        return u'на счёте Anthropic закончились деньги — пополните баланс'
    if isinstance(err, anthropic.APIStatusError):
        return u'Claude не ответил (%s): %s' % (err.status_code, message[:150])
    return message[:200] or u'Claude не ответил'


def _client() -> anthropic.AsyncAnthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=TIMEOUT)
    return _claude


async def _complete_claude(messages: list) -> str:
    model = config.ANTHROPIC_MODEL
    extra = {'betas': [FALLBACK_BETA], 'fallbacks': 'default'} if model.startswith(_FALLBACKS) else {}
    try:
        response = await _client().beta.messages.create(
            model=model, max_tokens=CLAUDE_MAX_TOKENS,
            output_config={'effort': config.ANTHROPIC_EFFORT},
            **claude_request(messages, model), **extra)
    except anthropic.APIError as err:
        error = claude_friendly(err)
        log.warning(u'Claude: %s', error)
        _health(error)
        raise AIError(error) from err
    if response.stop_reason == 'refusal':
        error = u'Claude отказался отвечать на это сообщение — ответьте человеку сами'
        _health(error)
        raise AIError(error)
    text = ''.join(block.text for block in response.content if block.type == 'text').strip()
    if not text:
        _health(u'Claude вернул пустой ответ')
        raise AIError(u'Claude вернул пустой ответ')
    _health()
    return text
