# -*- coding: utf-8 -*-
u"""Хранилище записей в закрепе у админа.

База без диска на Railway пропадает при деплое — 2 сентября так исчезли все
четыре записи дней. Закреп в телеграме переживает что угодно: после загрузки
бот пишет туда file_id, на старте читает обратно.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import backup, config, db                                     # noqa: E402
from tests.fakes import FakeBot                                        # noqa: E402

ADMIN = 777


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'backup.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    yield


async def test_после_сохранения_закреп_у_админа_содержит_записи():
    db.put_content('day1', 'video', 'FILE1')
    db.put_content('review2', 'photo', 'PIC2')
    bot = FakeBot()
    await backup.save(bot)
    text = bot.pinned[ADMIN].text
    assert text.startswith(backup.TAG)
    assert 'day1 video FILE1' in text and 'review2 photo PIC2' in text


async def test_повторное_сохранение_правит_закреп_а_не_плодит_сообщения():
    bot = FakeBot()
    db.put_content('day1', 'video', 'A')
    await backup.save(bot)
    db.put_content('day2', 'link', 'https://x/2')
    await backup.save(bot)
    assert sum(1 for _, chat, _ in bot.sent if chat == ADMIN) == 1
    assert 'day2 link https://x/2' in bot.pinned[ADMIN].text


async def test_на_старте_записи_возвращаются_из_закрепа(tmp_path):
    bot = FakeBot()
    db.put_content('day3', 'video', 'F3')
    await backup.save(bot)

    db.connect(str(tmp_path / 'after-deploy.db'))     # контейнер переехал
    assert db.get_content('day3') is None
    assert await backup.restore(bot) == 1
    assert db.get_content('day3') == ('video', 'F3')


async def test_восстановление_не_перетирает_то_что_уже_в_базе(tmp_path):
    bot = FakeBot()
    db.put_content('day1', 'video', 'OLD')
    await backup.save(bot)
    db.connect(str(tmp_path / 'fresh.db'))
    db.put_content('day1', 'video', 'NEW')
    assert await backup.restore(bot) == 0
    assert db.get_content('day1') == ('video', 'NEW')


async def test_без_закрепа_старт_не_падает():
    assert await backup.restore(FakeBot()) == 0


async def test_чужой_закреп_не_считается_хранилищем():
    bot = FakeBot()
    note = await bot.send_message(ADMIN, u'просто заметка')
    await bot.pin_chat_message(ADMIN, note.message_id)
    assert await backup.restore(bot) == 0


async def test_беда_телеграма_не_ломает_сохранение():
    bot = FakeBot(fail_times=5)
    db.put_content('day1', 'video', 'A')
    await backup.save(bot)                             # не должно бросить


def test_разбор_терпит_мусор():
    text = backup.TAG + u' — заметка\nчто-то не то\nday1 video X\nday9 video Y\nreview3 photo P'
    assert backup.parse(text) == [('day1', 'video', 'X'), ('review3', 'photo', 'P')]
