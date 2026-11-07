# -*- coding: utf-8 -*-
u"""Евгений через Claude (07.11.2026, ключ SLIST): запрос к Messages API и сбои.

Настоящий API здесь не зовём: клиент Anthropic подменён — проверяем, что бот
ему передаёт и как понимает ответ.
"""
import os
import sys
from types import SimpleNamespace

import httpx2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic                                                         # noqa: E402

from bot import ai, config, db                                           # noqa: E402


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'ai.db'))
    monkeypatch.setattr(config, 'ANTHROPIC_API_KEY', 'sk-ant-test')
    monkeypatch.setattr(config, 'ANTHROPIC_MODEL', 'claude-opus-5-5')
    monkeypatch.setattr(config, 'ANTHROPIC_EFFORT', 'low')
    yield


class Клиент:
    u"""Подделка AsyncAnthropic: запоминает запрос, отдаёт заготовленный ответ."""

    def __init__(self, ответ=None, сбой=None):
        self.запросы = []
        self.ответ, self.сбой = ответ, сбой
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.запросы.append(kw)
        if self.сбой:
            raise self.сбой
        return self.ответ


def _ответ(*blocks, stop='end_turn'):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


ПЕРЕПИСКА = [
    {'role': 'system', 'content': u'ПРАВИЛА'},
    {'role': 'system', 'content': u'ЧТО ИЗВЕСТНО О ЧЕЛОВЕКЕ\nИмя: Анна'},
    {'role': 'assistant', 'content': u'Приветствую тебя, мой друг'},
    {'role': 'assistant', 'content': u'Напиши ощущения'},
    {'role': 'user', 'content': u'Было тепло'},
    {'role': 'user', 'content': u'В руках'},
    {'role': 'system', 'content': u'Ответь на последнее сообщение'},
]


def test_переписка_для_messages_api():
    req = ai.claude_request(ПЕРЕПИСКА, 'claude-opus-5-5')
    assert req['system'][0] == {'type': 'text', 'text': u'ПРАВИЛА', 'cache_control': {'type': 'ephemeral'}}
    assert req['system'][1] == {'type': 'text', 'text': u'ЧТО ИЗВЕСТНО О ЧЕЛОВЕКЕ\nИмя: Анна'}
    assert [m['role'] for m in req['messages']] == ['user', 'assistant', 'user', 'system'], \
        'начинается с человека, одна сторона подряд склеена, задание — в конце'
    assert req['messages'][1]['content'] == u'Приветствую тебя, мой друг\n\nНапиши ощущения'
    assert req['messages'][2]['content'] == u'Было тепло\n\nВ руках'


def test_модель_без_системных_посреди_переписки_получает_задание_в_system():
    req = ai.claude_request(ПЕРЕПИСКА, 'claude-sonnet-5')
    assert req['messages'][-1]['role'] == 'user'
    assert req['system'][-1]['text'] == u'Ответь на последнее сообщение'


async def test_ответ_claude_текстом_без_размышлений(monkeypatch):
    клиент = Клиент(_ответ(SimpleNamespace(type='thinking', thinking=''),
                           SimpleNamespace(type='text', text=u'Это очищение тонкого плана 🙏')))
    monkeypatch.setattr(ai, '_client', lambda: клиент)
    assert await ai.complete(ПЕРЕПИСКА) == u'Это очищение тонкого плана 🙏'
    (kw,) = клиент.запросы
    assert kw['model'] == 'claude-opus-5-5' and kw['output_config'] == {'effort': 'low'}
    assert kw['fallbacks'] == 'default' and kw['betas'] == [ai.FALLBACK_BETA]
    assert 'temperature' not in kw, 'нынешняя версия API temperature не принимает'
    assert ai.health()[1] == ''


async def test_у_haiku_нет_подстраховки_другой_моделью(monkeypatch):
    monkeypatch.setattr(config, 'ANTHROPIC_MODEL', 'claude-haiku-5-5')
    клиент = Клиент(_ответ(SimpleNamespace(type='text', text=u'Ок')))
    monkeypatch.setattr(ai, '_client', lambda: клиент)
    await ai.complete(ПЕРЕПИСКА)
    assert 'fallbacks' not in клиент.запросы[0] and 'betas' not in клиент.запросы[0]


async def test_отказ_claude_команде_словами(monkeypatch):
    monkeypatch.setattr(ai, '_client', lambda: Клиент(_ответ(stop='refusal')))
    with pytest.raises(ai.AIError, match=u'отказался'):
        await ai.complete(ПЕРЕПИСКА)
    assert u'отказался' in ai.health()[1]


def _ошибка(cls, status, text):
    request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
    return cls(text, response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize('сбой, слова', [
    (lambda: _ошибка(anthropic.AuthenticationError, 401, 'invalid x-api-key'), u'ключ Claude неверный'),
    (lambda: _ошибка(anthropic.RateLimitError, 429, 'rate limited'), u'подождать'),
    (lambda: _ошибка(anthropic.BadRequestError, 400, 'Your credit balance is too low'), u'закончились деньги'),
    (lambda: anthropic.APIConnectionError(request=httpx2.Request('POST', 'https://api.anthropic.com')),
     u'нет связи'),
])
async def test_сбои_claude_понятными_словами(monkeypatch, сбой, слова):
    monkeypatch.setattr(ai, '_client', lambda: Клиент(сбой=сбой()))
    with pytest.raises(ai.AIError, match=слова):
        await ai.complete(ПЕРЕПИСКА)


def test_без_ключа_claude_прежний_шлюз(monkeypatch):
    monkeypatch.setattr(config, 'ANTHROPIC_API_KEY', '')
    monkeypatch.setattr(config, 'AI_API_KEY', 'apinet-key')
    monkeypatch.setattr(config, 'AI_MODEL', 'gpt-4.1-mini')
    assert ai.ready() and ai.describe() == u'шлюз apinet, модель gpt-4.1-mini'
    monkeypatch.setattr(config, 'ANTHROPIC_API_KEY', 'sk-ant-test')
    assert ai.describe() == u'Claude, модель claude-opus-5-5'
