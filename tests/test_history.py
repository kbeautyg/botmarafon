# -*- coding: utf-8 -*-
u"""Записи дней в старых переписках (bot/history.py).

До 29.09.2026 бот не запоминал сообщения с записями, а закрыть запись без
номера сообщения нельзя. Бот читает свою историю по MTProto и находит их
по подписи — здесь проверяется, что находит именно их и ничего лишнего.
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, history, texts                                # noqa: E402
from tests.fakes import FakeBot                                           # noqa: E402

ADMIN = 777
КОГДА = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'history.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    monkeypatch.setattr(config, 'TG_API_ID', 2040)
    monkeypatch.setattr(config, 'TG_API_HASH', 'hash')
    monkeypatch.setattr(history, 'PAUSE', 0)
    yield


def _msg(mid, text, user=1, out=True, video=True):
    return SimpleNamespace(id=mid, out=out, message=text, date=КОГДА,
                           peer_id=SimpleNamespace(user_id=user),
                           video=object() if video else None, document=None)


class Клиент(object):
    u"""Подделка Telethon: история бота — словарь «номер → сообщение»."""

    def __init__(self, messages):
        self.messages = {m.id: m for m in messages}
        self.asked = []

    async def get_messages(self, entity, ids):
        assert entity is None                       # общая нумерация личных переписок
        self.asked.append((ids[0], ids[-1]))
        return [self.messages.get(i) for i in ids]

    async def disconnect(self):
        pass


def test_подписи_дней_начинаются_так_как_ищет_поиск():
    u"""Поменяют начало текста дня — поиск старых записей перестанет их узнавать."""
    for day, mark in history.MARKS.items():
        assert texts.DAY_TEXTS[day].startswith(mark), day
        assert history.day_of(texts.DAY_TEXTS[day]) == day
    assert history.day_of(texts.DAY_RESEND.format(day=3)) == 3


def test_узнаёт_только_свои_записи_дней():
    day2 = texts.DAY_TEXTS[2]
    assert history.classify(_msg(1, day2)) == (1, 2, 'video')
    link = u'https://energy-sport-gum.ru/marathon/1?k=x\n\n' + texts.DAY_TEXTS[1]
    assert history.classify(_msg(2, link, video=False)) == (1, 1, 'link')
    # текст дня без записи: запись ещё не залили — закрывать нечего
    promise = texts.DAY_TEXTS[3] + u'\n\n' + texts.DAY_MISSING_USER
    assert history.classify(_msg(3, promise, video=False)) is None
    # человек процитировал текст дня боту — это не наша запись
    assert history.classify(_msg(4, day2, out=False)) is None
    # вопрос после дня — не запись
    assert history.classify(_msg(5, texts.POLL_QUESTIONS['day1'], video=False)) is None
    assert history.classify(None) is None


async def test_проход_идёт_через_пустые_номера_до_известного_и_дальше():
    u"""Удалённые переписки оставляют полосы пустых номеров: до последнего
    известного номера идём сквозь них, за ним — пока не пойдут одни пустые."""
    client = Клиент([_msg(5, texts.DAY_TEXTS[1], user=7),
                     _msg(4500, texts.DAY_TEXTS[4], user=8)])
    found = await history.scan(client, upper=4000)
    assert [(u, d, m, k) for u, d, m, k, _ in found] == [(7, 1, 5, 'video'), (8, 4, 4500, 'video')]
    assert client.asked[-1][0] > 4500 + (history.EMPTY_RUNS - 1) * history.BATCH


async def test_найденное_запоминается_один_раз_и_админы_знают(monkeypatch):
    client = Клиент([_msg(10, texts.DAY_TEXTS[1], user=7),
                     _msg(11, texts.DAY_TEXTS[2], user=7),
                     _msg(12, texts.DAY_TEXTS[1], user=9)])

    async def войти():
        return client
    monkeypatch.setattr(history, '_client', войти)
    bot = FakeBot()

    assert await history.backfill(bot) == 3
    rows = db.open_day_messages(7)
    assert [(r['day'], r['tg_id'], r['kind']) for r in rows] == [(1, 10, 'video'), (2, 11, 'video')]
    assert rows[0]['at'] == КОГДА.timestamp()           # когда запись на самом деле ушла
    [(_, chat, text)] = bot.sent
    assert chat == ADMIN and text == texts.HISTORY_FOUND.format(messages=3, people=2)

    assert await history.backfill(bot) is None           # второй раз не ищет


async def test_без_пары_клиента_не_ищет(monkeypatch):
    monkeypatch.setattr(config, 'TG_API_ID', 0)
    assert await history.backfill(FakeBot()) is None
    assert db.get_content(history.DONE) is None


async def test_сбой_поиска_не_ставит_отметку_и_будит_админа(monkeypatch):
    async def не_пускают():
        raise ConnectionError(u'нет связи с Telegram')
    monkeypatch.setattr(history, '_client', не_пускают)
    bot = FakeBot()
    await history.run(bot)
    assert db.get_content(history.DONE) is None          # следующий запуск попробует снова
    assert [chat for _, chat, _ in bot.sent] == [ADMIN]
