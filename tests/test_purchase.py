# -*- coding: utf-8 -*-
u"""Заявки на покупку: кому уходят и что считается одной заявкой.

Sharp 13.09.2026: «куда заявки падают — прикрепи Павла и Алекса».
AleX 13.09.2026: «Наталья четыре раза ткнула одно и то же, а у нас в отчёте
выходит четыре отдельных человека. Пусть это считается как одно обращение, а
в скобках — количество». Мария с «спортзалом» и «обучением» — две заявки.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, insights, texts                               # noqa: E402
from bot.handlers import purchase                                         # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeUser                       # noqa: E402

ADMIN = 777
BUY_CHAT = -100777
PAVEL = 312701042
ALEX = 350631550


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'purchase.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', BUY_CHAT)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    yield


def _кнопка(bot, product='gym', uid=1, nick='nataliya_famme', name=u'Наталия'):
    db.remember_user(uid, nick, name)
    return FakeCall('buy:' + product, user=FakeUser(uid, nick, name), bot=bot)


def _заявки(bot):
    return [(chat, text) for kind, chat, text in bot.sent
            if kind == 'text' and u'Заявка №' in (text or u'')]


# ---------------------------------------------------------------- кому

async def test_заявка_уходит_в_чат_покупок_и_лично_павлу_и_алексу():
    bot = FakeBot()
    call = _кнопка(bot)
    await purchase.on_buy(call)
    assert {chat for chat, _ in _заявки(bot)} == {BUY_CHAT, PAVEL, ALEX}
    assert call.message.answers[-1] == texts.OFFER_DONE


async def test_дополнительные_получатели_из_переменной(monkeypatch):
    monkeypatch.setenv('PURCHASE_TO', '555, 556')
    bot = FakeBot()
    await purchase.on_buy(_кнопка(bot))
    assert {chat for chat, _ in _заявки(bot)} == {BUY_CHAT, PAVEL, ALEX, 555, 556}


async def test_не_дошла_до_одного_получателя_админа_не_будим():
    class ПавелЗакрылБота(FakeBot):
        async def send_message(self, chat_id, text, **kw):
            if chat_id == PAVEL:
                raise RuntimeError(u'bot was blocked by the user')
            return await super().send_message(chat_id, text, **kw)

    bot = ПавелЗакрылБота()
    await purchase.on_buy(_кнопка(bot))
    assert {chat for chat, _ in _заявки(bot)} == {BUY_CHAT, ALEX}
    assert not [c for _, c, t in bot.sent if c == ADMIN]


async def test_не_дошла_ни_до_кого_админ_узнаёт():
    class НиктоНеПолучил(FakeBot):
        async def send_message(self, chat_id, text, **kw):
            if chat_id != ADMIN:
                raise RuntimeError(u'chat not found')
            return await super().send_message(chat_id, text, **kw)

    bot = НиктоНеПолучил()
    await purchase.on_buy(_кнопка(bot))
    админу = [t for _, c, t in bot.sent if c == ADMIN]
    assert админу and u'не дошла ни до кого' in админу[0]


async def test_запоздалое_нажатие_заявку_не_теряет():
    u"""Нажатие после перезапуска бота Telegram не даёт подтвердить — раньше
    обработчик на этом падал, и заявка оставалась только в базе."""
    bot = FakeBot()
    call = _кнопка(bot)

    async def устарело(*args, **kw):
        raise RuntimeError(u'query is too old')
    call.answer = устарело

    await purchase.on_buy(call)
    assert {chat for chat, _ in _заявки(bot)} == {BUY_CHAT, PAVEL, ALEX}


# ---------------------------------------------------- повторные нажатия

async def test_четыре_нажатия_подряд_одна_заявка():
    bot = FakeBot()
    calls = [_кнопка(bot) for _ in range(4)]
    for call in calls:
        await purchase.on_buy(call)
    assert len(_заявки(bot)) == 3                   # один раз каждому из троих
    assert calls[-1].message.answers[-1] == texts.OFFER_ALREADY
    assert db.stats()['purchases'] == 1 and db.stats()['presses'] == 4


async def test_другой_продукт_тем_же_человеком_отдельная_заявка():
    bot = FakeBot()
    await purchase.on_buy(_кнопка(bot, 'gym', uid=2, nick='poteychuk7', name=u'Мария'))
    await purchase.on_buy(_кнопка(bot, 'course', uid=2, nick='poteychuk7', name=u'Мария'))
    assert len(_заявки(bot)) == 6
    assert db.stats()['purchases'] == 2


async def test_вернулся_к_кнопке_через_сутки_снова_заявка_с_пометкой():
    bot = FakeBot()
    await purchase.on_buy(_кнопка(bot))
    db._run('UPDATE purchases SET at=?', (time.time() - 2 * 86400,))
    await purchase.on_buy(_кнопка(bot))
    вторые = [t for _, t in _заявки(bot)][3:]
    assert len(вторые) == 3 and all(u'Повторная заявка' in t for t in вторые)


# ------------------------------------------------------------ в отчёте

def _люди_как_на_скриншоте():
    for uid, nick, name in ((1, 'nataliya_famme', u'Наталия'), (2, 'poteychuk7', u'Мария'),
                            (3, 'irynasumy', u'Ирина')):
        db.remember_user(uid, nick, name)
        db.mark_launched(uid)
    for _ in range(4):
        db.add_purchase(1, 'gym')
    db.add_purchase(2, 'gym')
    db.add_purchase(2, 'course')
    db.add_purchase(3, 'gym')


def test_в_покупках_повторы_одной_строкой_с_числом():
    _люди_как_на_скриншоте()
    текст = insights.render('buy', 'all')
    assert u'3</b> человека · заявок 4 · всего нажатий 7' in текст
    assert u'энерго спортзал: 3' in текст and u'обучение: 1' in текст
    assert текст.count(u'@nataliya_famme') == 1
    строка = next(s for s in текст.split(u'\n') if u'@nataliya_famme' in s)
    assert строка.endswith(u'(4 нажатия)')
    мария = [s for s in текст.split(u'\n') if u'@poteychuk7' in s]
    assert len(мария) == 2 and not any(u'нажат' in s for s in мария)


def test_в_таблице_повторы_одной_записью():
    _люди_как_на_скриншоте()
    таблица = insights.csv_bytes().decode('utf-8-sig')
    наталия = next(s for s in таблица.splitlines() if 'nataliya_famme' in s)
    assert наталия.count(u'энерго спортзал') == 1 and u'(4 нажатия)' in наталия


def test_склонение_нажатий():
    assert insights._times(1) == u''
    assert insights._times(2) == u' (2 нажатия)'
    assert insights._times(5) == u' (5 нажатий)'
    assert insights._times(11) == u' (11 нажатий)'
    assert insights._times(21) == u' (21 нажатие)'


async def test_в_заявке_имя_ссылкой_на_профиль_даже_без_ника():
    bot = FakeBot()
    await purchase.on_buy(_кнопка(bot, uid=21, nick=None, name=u'Ivan'))
    текст = _заявки(bot)[0][1]
    assert u'tg://user?id=21' in текст and u'без ника' in текст
