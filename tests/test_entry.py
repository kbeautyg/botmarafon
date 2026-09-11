# -*- coding: utf-8 -*-
u"""Уведомление о каждом входе в бота и номер заявки с сайта.

AleX 11.09.2026: «как только кто-то зашёл в бот марафона — сообщение: ник,
номер, время, запустил марафон, и в скобках — в первый раз или в который».
И там же: людей из бота не нашли в отчёте сайта — теперь у пришедшего по
заявке виден её номер, по нему сверяются с сайтом.
"""
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, insights, stats, texts                        # noqa: E402
from bot.handlers import start                                            # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser          # noqa: E402

ENTRY = -100555
SUPPORT = -100777


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'entry.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', ())
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', ENTRY)
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', SUPPORT)
    start._launching.clear()
    yield


def _в_чат(bot, chat_id):
    return [payload for kind, chat, payload in bot.sent if chat == chat_id and kind == 'text']


async def test_первый_старт_уходит_в_чат_уведомлений():
    сообщение = FakeMessage(text='/start ig', user=FakeUser(1, 'anna', u'Анна'))
    await start.on_start(сообщение)
    (текст,) = _в_чат(сообщение.bot, ENTRY)
    assert u'Вход в бота марафона' in текст
    assert u'Анна' in текст and u'@anna' in текст and u'<code>1</code>' in текст
    assert u'откуда: Instagram' in текст
    assert u'Запустил марафон (в первый раз)' in текст
    assert 'launch' in db.pending_chains(1)


async def test_повторный_старт_не_уведомляет_а_перезапуск_считает_разы():
    первый = FakeMessage(text='/start', user=FakeUser(2))
    await start.on_start(первый)
    повтор = FakeMessage(text='/start', user=FakeUser(2), bot=первый.bot)
    await start.on_start(повтор)
    assert len(_в_чат(первый.bot, ENTRY)) == 1               # марафон не запускался заново

    call = FakeCall('restart', user=FakeUser(2), bot=первый.bot)
    await start.on_restart(call)
    тексты = _в_чат(первый.bot, ENTRY)
    assert len(тексты) == 2 and u'Запустил марафон (2-й раз)' in тексты[-1]
    assert db.get_user(2)['launches'] == 2


async def test_заявка_с_сайта_уведомляет_с_номером_и_без_дубля_в_заботе():
    сообщение = FakeMessage(text='/start zayavka65', user=FakeUser(3, 'irina', u'Ирина'))
    await start.on_start(сообщение)
    (текст,) = _в_чат(сообщение.bot, ENTRY)
    assert u'Заявка с сайта №65' in текст and u'ждёт менеджера' in текст
    assert db.get_user(3)['lead_no'] == 65
    assert len(_в_чат(сообщение.bot, SUPPORT)) == 1           # заявка менеджеру, как и была


async def test_чат_уведомлений_совпадает_с_заботой_второй_раз_не_шлём(monkeypatch):
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', SUPPORT)
    сообщение = FakeMessage(text='/start zayavka66', user=FakeUser(4))
    await start.on_start(сообщение)
    (единственное,) = _в_чат(сообщение.bot, SUPPORT)
    assert u'Заявка с сайта' in единственное and u'Вход в бота' not in единственное


async def test_без_чата_уведомлений_ничего_не_шлём(monkeypatch):
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    сообщение = FakeMessage(text='/start', user=FakeUser(5))
    await start.on_start(сообщение)
    assert {chat for _, chat, _ in сообщение.bot.sent} == {5}


async def test_сбой_чата_уведомлений_не_ломает_старт():
    class ЧатаНет(FakeBot):
        async def send_message(self, chat_id, text, **kw):
            if chat_id == ENTRY:
                raise RuntimeError(u'chat not found')
            return await super().send_message(chat_id, text, **kw)

    сообщение = FakeMessage(text='/start', user=FakeUser(6), bot=ЧатаНет())
    await start.on_start(сообщение)
    assert 'launch' in db.pending_chains(6)
    assert сообщение.answers == [texts.START_TEXT]


async def test_номер_заявки_виден_в_кто_и_в_таблице():
    await start.on_start(FakeMessage(text='/start zayavka58', user=FakeUser(7, 'maija', u'Maija')))
    assert u'Заявка с сайта №58' in u'\n'.join(stats.who_messages(30))
    таблица = insights.csv_bytes().decode('utf-8-sig')
    assert u'Заявка с сайта №58' in таблица


async def test_сбой_приветствия_не_отменяет_уведомление():
    u"""Вход был и марафон запущен — команда должна увидеть это и тогда."""
    сообщение = FakeMessage(text='/start', user=FakeUser(8))

    async def падает(*args, **kw):
        raise RuntimeError(u'нет связи')
    сообщение.answer = падает

    with pytest.raises(RuntimeError):
        await start.on_start(сообщение)
    (текст,) = _в_чат(сообщение.bot, ENTRY)
    assert u'Запустил марафон (в первый раз)' in текст
    assert 'launch' in db.pending_chains(8)


def test_старая_база_получает_колонки_и_счётчик_запусков():
    path = os.path.join(tempfile.mkdtemp(), 'old.db')
    old = sqlite3.connect(path)
    old.executescript(
        'CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,'
        ' started_at REAL NOT NULL, launched_at REAL, source TEXT, poll TEXT,'
        ' care_open INTEGER DEFAULT 0, speed REAL DEFAULT 1.0, blocked_at REAL);'
        'INSERT INTO users (user_id, started_at, launched_at) VALUES (1, 1, 2), (2, 1, NULL);')
    old.commit()
    old.close()
    db.connect(path)
    assert db.get_user(1)['launches'] == 1 and db.get_user(2)['launches'] == 0
    assert db.get_user(1)['lead_no'] is None
    assert db.count_launch(2) == 1
