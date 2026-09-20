# -*- coding: utf-8 -*-
u"""Досылка вопроса тем, кто застрял без кнопок (Sharp 20.09.2026: «дошли»).

Людям уходит сообщение, поэтому проверяем две вещи: что список ровно те,
кто правда ждёт, и что без подтверждения не уходит ничего.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, texts                                        # noqa: E402
from bot.handlers import admin                                           # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser         # noqa: E402

СВОЙ = 111
ЧУЖОЙ = 222


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'stuck.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    yield


def _человек(uid, poll=None, answered=False, blocked=False, banned=False):
    db.remember_user(uid, 'u%d' % uid, u'Человек')
    db.mark_launched(uid)
    if poll:
        db.set_poll(uid, poll)
    if answered:
        db.save_answer(uid, poll, 'yes')
    if blocked:
        db.mark_blocked(uid)
    if banned:
        db.ban_add(uid, 'u%d' % uid, u'Человек', u'AleX')


def _кому(bot):
    u"""Кому из людей ушло — ответы самой команде не считаем."""
    return sorted(chat for kind, chat, _ in bot.sent if kind == 'text' and chat != СВОЙ)


class ЛюдиУшли(FakeBot):
    u"""Люди закрыли бота, а команде сообщения доходят."""

    async def send_message(self, chat_id, text, **kw):
        if chat_id != СВОЙ:
            from bot import delivery
            raise delivery.Gone()
        return await self._record('text', chat_id, text)


# ----------------------------------------------------------------- список

def test_ждут_только_те_кто_правда_застрял():
    _человек(1, 'day1')                       # ждёт — ему и дошлём
    _человек(2, 'day2')                       # ждёт
    _человек(3, 'day1', answered=True)        # уже ответил
    _человек(4)                               # вопрос не открыт
    _человек(5, 'day1', blocked=True)         # закрыл бота
    _человек(6, 'day1', banned=True)          # в чёрном списке

    assert [p['user_id'] for p in db.stuck_on_poll()] == [1, 2]


# ---------------------------------------------------------- подтверждение

async def test_без_нажатия_никому_ничего_не_уходит():
    _человек(1, 'day1')
    bot = FakeBot()
    сообщение = FakeMessage(text=u'/дошли', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_stuck(сообщение)

    assert _кому(bot) == []
    assert u'Дослать вопрос с кнопками?' in сообщение.answers[-1]
    assert u'день 1 — 1' in сообщение.answers[-1]


async def test_по_кнопке_вопрос_уходит_каждому_со_своим_днём():
    _человек(1, 'day1')
    _человек(2, 'day3')
    bot = FakeBot()
    call = FakeCall('stuck:go', user=FakeUser(СВОЙ), bot=bot)

    await admin.on_stuck_go(call)

    ушло = [(chat, payload) for kind, chat, payload in bot.sent if kind == 'text']
    тексты = dict(ушло)
    assert тексты[1] == texts.POLL_QUESTIONS['day1']
    assert тексты[2] == texts.POLL_QUESTIONS['day3']


async def test_закрывшего_бота_отмечаем_и_не_считаем_доставленным():
    _человек(1, 'day1')
    call = FakeCall('stuck:go', user=FakeUser(СВОЙ), bot=ЛюдиУшли())
    await admin.on_stuck_go(call)
    assert db.get_user(1)['blocked_at'] is not None


async def test_когда_застрявших_нет_так_и_говорим():
    _человек(1)
    bot = FakeBot()
    сообщение = FakeMessage(text=u'/дошли', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_stuck(сообщение)
    assert сообщение.answers[-1] == texts.STUCK_NONE


async def test_посторонний_досылку_не_запустит():
    _человек(1, 'day1')
    bot = FakeBot()
    await admin.on_stuck(FakeMessage(text=u'/дошли', user=FakeUser(ЧУЖОЙ), bot=bot))
    await admin.on_stuck_go(FakeCall('stuck:go', user=FakeUser(ЧУЖОЙ), bot=bot))
    assert _кому(bot) == []
