# -*- coding: utf-8 -*-
u"""/написать проходит настоящий разбор aiogram, а не только прямой вызов.

Проверяется то, чего не видно в тестах обработчика: кириллическая команда
находится фильтром Command, админский роутер (он стоит раньше заботы) её
не перехватывает, а человек не из команды получает «команда не для вас».
Сеть не нужна — поддельная сессия бота записывает вызовы API.
"""
import os
import sys
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import CopyMessage, SendMessage
from aiogram.types import Chat, Message, MessageId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, handlers, texts                               # noqa: E402

SHARP, PAVEL, ALEX, PERSON, STRANGER = 7874595355, 312701042, 350631550, 5, 777


class ЗаписьСессия(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.next_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        self.next_id += 1
        if isinstance(method, CopyMessage):
            return MessageId(message_id=self.next_id)
        if isinstance(method, SendMessage):
            return Message(message_id=self.next_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type='private'), text=method.text)
        return True

    async def stream_content(self, *a, **kw):                  # pragma: no cover
        yield b''

    async def close(self):
        pass


@pytest.fixture(scope='module')
def dispatcher():
    dp = Dispatcher()
    for router in handlers.ROUTERS:
        dp.include_router(router)
    return dp


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'routing.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    db.remember_user(PERSON, 'Aidyn2381', u'Айдын')
    yield


def _update(sender, text, n=1):
    return {'update_id': n, 'message': {
        'message_id': n, 'date': 1_760_000_000, 'text': text,
        'chat': {'id': sender, 'type': 'private'},
        'from': {'id': sender, 'is_bot': False, 'first_name': 'X'},
        'entities': [{'type': 'bot_command', 'offset': 0, 'length': len(text.split()[0])}],
    }}


async def _send(dispatcher, sender, text):
    session = ЗаписьСессия()
    bot = Bot('42:TEST', session=session)
    await dispatcher.feed_raw_update(bot, _update(sender, text))
    return [(type(c).__name__, c.chat_id, getattr(c, 'text', None)) for c in session.calls]


@pytest.mark.parametrize('sender', [ALEX, SHARP])
async def test_команда_доходит_до_написать_у_алекса_и_у_админа(dispatcher, sender):
    calls = await _send(dispatcher, sender, u'/написать @aidyn2381 Посмотрели разборы?')
    assert ('SendMessage', PERSON, u'Посмотрели разборы?') in calls
    assert any(c[1] == sender and u'Отправлено ✅' in (c[2] or u'') for c in calls)


async def test_латинский_вариант_write(dispatcher):
    calls = await _send(dispatcher, PAVEL, u'/write 5 Hello')
    assert ('SendMessage', PERSON, u'Hello') in calls


async def test_чужому_команда_не_открыта_и_людям_ничего_не_уходит(dispatcher):
    calls = await _send(dispatcher, STRANGER, u'/написать 5 спам')
    assert [c for c in calls if c[1] == PERSON] == []
    assert calls[-1][1] == STRANGER
    assert calls[-1][2] == texts.NOT_ALLOWED_COMMAND.format(id=STRANGER)
