# -*- coding: utf-8 -*-
u"""Переписка команды с людьми через бота и дожим «заходи на марафон».

AleX 14.09.2026: писал людям со своих аккаунтов — оба в спам-блоке; «надо
отправить от имени бота». Теперь всё, что человек пишет боту, приходит
команде, а реплай команды — на его сообщение, на уведомление о запуске или
на заявку — уходит человеку от имени бота.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, nudge, texts                                  # noqa: E402
from bot.handlers import purchase, start, support                         # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser          # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'bridge.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    start._launching.clear()
    yield


def _в(bot, chat, kind=None):
    return [(k, p) for k, c, p in bot.sent if c == chat and (kind is None or k == kind)]


def _последнее_в(bot, chat):
    u"""FakeMessage последнего сообщения бота в этот чат — на него отвечают реплаем."""
    return [m for _, m in sorted(bot.by_id.items()) if m.chat.id == chat][-1]


# ------------------------------------------------------------ туда

async def test_написанное_боту_приходит_команде_и_человеку_говорим_ответим_здесь():
    bot = FakeBot()
    вопрос = FakeMessage(text=u'А сколько стоит зал?', user=FakeUser(1, 'olga', u'Ольга'), bot=bot)
    await support.to_support(вопрос)
    for chat in (SHARP, PAVEL, ALEX):
        виды = [k for k, _ in _в(bot, chat)]
        assert виды == ['text', 'copy'], chat
    assert вопрос.answers[-1] == texts.CARE_SENT_HERE


async def test_некому_переслать_как_раньше_кнопка_заботы(monkeypatch):
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    вопрос = FakeMessage(text=u'Когда второй день?', user=FakeUser(2))
    await support.to_support(вопрос)
    assert вопрос.answers[-1] == texts.CARE_SENT


async def test_сообщение_команды_людям_не_дублируется():
    bot = FakeBot()
    своё = FakeMessage(text=u'проверка', user=FakeUser(PAVEL), bot=bot)
    await support.to_support(своё)
    assert _в(bot, SHARP) == [] and _в(bot, ALEX) == []
    assert своё.answers[-1] == texts.TEAM_HINT


# ------------------------------------------------------------ обратно

async def test_реплай_команды_на_сообщение_человека_уходит_ему():
    bot = FakeBot()
    await support.to_support(FakeMessage(text=u'вопрос', user=FakeUser(1), bot=bot))
    ответ = FakeMessage(text=u'Здравствуйте! Отвечаю', user=FakeUser(ALEX), bot=bot,
                        reply_to=_последнее_в(bot, ALEX))
    assert support._team_reply(ответ)
    await support.from_team(ответ)
    assert _в(bot, 1, 'copy'), u'ответ должен уйти человеку'
    assert ответ.replies[-1] == u'Отправлено ✅'


async def test_реплай_на_уведомление_о_запуске_уходит_этому_человеку():
    bot = FakeBot()
    await start.on_start(FakeMessage(text='/start ls', user=FakeUser(5, None, u'Feruza'), bot=bot))
    уведомление = _последнее_в(bot, ALEX)
    assert u'Новый запуск марафона' in уведомление.text
    ответ = FakeMessage(text=u'Посмотрели разборы?', user=FakeUser(ALEX), bot=bot,
                        reply_to=уведомление)
    await support.from_team(ответ)
    assert _в(bot, 5, 'copy')


async def test_реплай_на_заявку_на_покупку_уходит_покупателю():
    bot = FakeBot()
    db.remember_user(6, 'buyer', u'Покупатель')
    await purchase.on_buy(FakeCall('buy:gym', user=FakeUser(6, 'buyer', u'Покупатель'), bot=bot))
    заявка = next(m for _, m in sorted(bot.by_id.items())
                  if m.chat.id == PAVEL and u'Заявка №' in (m.text or u''))
    ответ = FakeMessage(text=u'Созвонимся?', user=FakeUser(PAVEL), bot=bot, reply_to=заявка)
    await support.from_team(ответ)
    assert _в(bot, 6, 'copy')


async def test_реплай_непонятно_на_что_подсказка():
    bot = FakeBot()
    чужое = FakeMessage(text=u'что-то', bot=bot, message_id=999999)
    ответ = FakeMessage(text=u'кому это?', user=FakeUser(ALEX), bot=bot, reply_to=чужое)
    await support.from_team(ответ)
    assert u'Не понял, кому это' in ответ.replies[-1]


def test_реплай_человека_не_принимается_за_ответ_команды():
    человек = FakeMessage(text=u'ок', user=FakeUser(1), reply_to=FakeMessage(message_id=5))
    assert not support._team_reply(человек)


# ------------------------------------------------------------ дожим

NOW = 2_000_000_000.0


def _запустил(uid, hours_ago, day1=True, answered=False):
    db.remember_user(uid, 'u%d' % uid, u'Человек')
    db.mark_launched(uid)
    db._run('UPDATE users SET launched_at=? WHERE user_id=?', (NOW - hours_ago * 3600, uid))
    if day1:
        db.log_event(uid, 'day', 1)
    if answered:
        db.save_answer(uid, 'day1', 'yes')


def _дожимы(bot):
    return [c for k, c, p in bot.sent if p == texts.NUDGE_DAY1]


async def test_через_5_часов_без_ответа_дожим_один_раз():
    nudge.since(NOW - 10 * 3600)                               # дожим действует давно
    _запустил(1, hours_ago=6)
    bot = FakeBot()
    assert await nudge.run_once(bot, now=NOW) == 1
    assert await nudge.run_once(bot, now=NOW + 60) == 0
    assert _дожимы(bot) == [1]


async def test_кто_ответил_рано_или_без_первого_дня_дожим_не_получает():
    nudge.since(NOW - 10 * 3600)
    _запустил(1, hours_ago=6, answered=True)                   # ответил
    _запустил(2, hours_ago=4)                                  # 5 часов не прошло
    _запустил(3, hours_ago=6, day1=False)                      # первый день не ушёл
    bot = FakeBot()
    await nudge.run_once(bot, now=NOW)
    assert _дожимы(bot) == []


async def test_задним_числом_прежним_молчунам_не_пишем():
    _запустил(1, hours_ago=30)                                 # запускал давно
    _запустил(2, hours_ago=4)                                  # незадолго до выкладки
    bot = FakeBot()
    await nudge.run_once(bot, now=NOW)                         # первый запуск ставит отметку
    assert _дожимы(bot) == []                                  # его 5 часов ещё не прошли
    await nudge.run_once(bot, now=NOW + 1.5 * 3600)
    assert _дожимы(bot) == [2]                                 # давний так и не получит


async def test_закрывший_бота_отмечен_и_больше_не_трогаем():
    nudge.since(NOW - 10 * 3600)
    _запустил(1, hours_ago=6)
    bot = FakeBot(forbidden=True)
    assert await nudge.run_once(bot, now=NOW) == 0
    assert db.get_user(1)['blocked_at'] is not None
    assert db.nudge_candidates(NOW - 10 * 3600, NOW) == []


async def test_пройти_заново_снова_разрешает_дожим():
    nudge.since(NOW - 10 * 3600)
    _запустил(1, hours_ago=6)
    await nudge.run_once(FakeBot(), now=NOW)
    db.reset_funnel(1)
    assert db.get_user(1)['nudged_at'] is None
