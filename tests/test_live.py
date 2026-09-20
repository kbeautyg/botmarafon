# -*- coding: utf-8 -*-
u"""Эфир: бот собирает людей на трансляцию (Павел 20.09.2026).

Анонс уходит всем сразу, поэтому проверок две группы: что без
подтверждения не уходит ничего и что напоминания приходят по одному разу
и вовремя — второе «эфир начался» через час людей бы разозлило.
"""
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, live, stats, texts                           # noqa: E402
from bot.handlers import admin                                           # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser         # noqa: E402

СВОЙ = 111


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'live.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(live, 'PER_SECOND', 10000)      # в проверках не ждём
    yield


def _люди(*ids):
    for uid in ids:
        db.remember_user(uid, 'u%d' % uid, u'Человек')


def _кому(bot):
    return sorted(chat for kind, chat, _ in bot.sent if kind == 'text' and chat != СВОЙ)


def _через(seconds):
    return time.time() + seconds


# ------------------------------------------------------------- разбор

def test_дату_время_и_ссылку_узнаём_в_любом_порядке():
    at, url, own = live.parse(u'20.09 19:00 https://t.me/x?livestream Приходите с вопросами')
    assert url == 'https://t.me/x?livestream'
    assert own == u'Приходите с вопросами'
    когда = datetime.fromtimestamp(at, stats.MSK)
    assert (когда.day, когда.month, когда.hour, когда.minute) == (20, 9, 19, 0)


def test_без_ссылки_или_без_времени_не_берём():
    assert live.parse(u'19:00 приходите')[0] is None
    assert live.parse(u'https://t.me/x')[0] is None
    assert live.parse(u'31.02 19:00 https://t.me/x')[0] is None   # такой даты нет


def test_дата_без_года_не_уходит_в_прошлое():
    u"""«02.01 20:00», присланное в декабре, — это январь следующего года."""
    декабрь = datetime(2026, 12, 25, 12, 0, tzinfo=stats.MSK).timestamp()
    at, _, _ = live.parse(u'02.01 20:00 https://t.me/x', now=декабрь)
    assert datetime.fromtimestamp(at, stats.MSK).year == 2027


# ------------------------------------------------------- подтверждение

async def test_без_подтверждения_анонс_никому_не_уходит():
    _люди(1, 2)
    bot = FakeBot()
    сообщение = FakeMessage(text=u'20.09 19:00 https://t.me/x', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_live_message(сообщение)
    assert _кому(bot) == []
    assert db.live(1)['status'] == 'ready'


async def test_по_кнопке_анонс_уходит_всем_кроме_ушедших_и_списка():
    _люди(1, 2, 3, 4)
    db.mark_blocked(3)
    db.ban_add(4, 'u4', u'Тролль', u'AleX')
    db.live_add('https://t.me/x', u'', _через(7200), СВОЙ)

    bot = FakeBot()
    await live.announce(bot, 1)

    assert _кому(bot) == [1, 2]
    assert db.live(1)['status'] == 'going'


async def test_отмена_анонса_не_шлёт_ничего():
    _люди(1)
    db.live_add('https://t.me/x', u'', _через(7200), СВОЙ)
    bot = FakeBot()
    await admin.on_live_button(FakeCall('live:no:1', user=FakeUser(СВОЙ), bot=bot))
    assert _кому(bot) == []
    assert db.live(1)['status'] == 'cancelled'


# --------------------------------------------------------- напоминания

async def test_напоминания_приходят_по_одному_разу_и_вовремя():
    _люди(1)
    at = _через(7200)                                  # эфир через два часа
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    db.live_status(1, 'going')
    bot = FakeBot()

    assert await live.tick(bot, now=at - 5400) == 0     # за полтора часа рано
    assert await live.tick(bot, now=at - 3500) == 1     # час — пора
    assert await live.tick(bot, now=at - 3400) == 0     # второй раз не шлём
    assert await live.tick(bot, now=at - 300) == 1      # десять минут
    assert await live.tick(bot, now=at + 10) == 1       # началось

    тексты = [p for k, c, p in bot.sent if c == 1]
    assert u'через час' in тексты[0]
    assert u'через десять минут' in тексты[1]
    assert u'начался' in тексты[2]


async def test_эфир_прошёл_и_бот_замолкает():
    _люди(1)
    at = _через(-7200)                                 # начался два часа назад
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    db.live_status(1, 'going')
    bot = FakeBot()

    await live.tick(bot)
    assert _кому(bot) == []                            # догонять поздно
    assert db.live(1)['status'] == 'done'


async def test_под_анонсом_кнопка_ведёт_на_эфир():
    from bot import keyboards

    кнопка = keyboards.live('https://t.me/x?livestream').inline_keyboard[0][0]
    assert кнопка.url == 'https://t.me/x?livestream'
    assert u'Смотреть' in кнопка.text


# ------------------------- проба на себе (Sharp 20.09.2026)

async def test_проба_эфира_уходит_только_нажавшему():
    u"""«Как протестить, чтоб не обосраться потом?» — вот так."""
    _люди(1, 2, 3)
    db.live_add('https://t.me/x', u'Приходите', _через(7200), СВОЙ)
    bot = FakeBot()

    await admin.on_live_button(FakeCall('live:me:1', user=FakeUser(СВОЙ), bot=bot))

    получатели = [c for k, c, _ in bot.sent if k == 'text']
    assert СВОЙ in получатели                     # себе пришло
    assert _кому(bot) == []                       # людям — нет
    assert db.live(1)['status'] == 'ready'        # эфир ещё не объявлен


async def test_после_пробы_кнопки_остаются_и_можно_объявить():
    _люди(1)
    db.live_add('https://t.me/x', u'', _через(7200), СВОЙ)
    bot = FakeBot()
    проба = FakeCall('live:me:1', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_live_button(проба)
    assert not проба.edited                       # разметку не сняли

    await live.announce(bot, 1)
    assert _кому(bot) == [1]
