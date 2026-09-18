# -*- coding: utf-8 -*-
u"""Мини-отчёт за сутки и календарь под ним (AleX 18.09.2026).

Числа в отчёте команда читает как есть и по ним принимает решения, поэтому
проверяем не «отчёт построился», а что в нём ровно те люди, что надо: за
те сутки, о которых отчёт, и ни одним больше.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, daily, db, keyboards, stats                     # noqa: E402
from bot.handlers import admin                                          # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser        # noqa: E402

АДМИН = 111


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'daily.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (АДМИН,))
    monkeypatch.setattr(config, 'STATS_IDS', (АДМИН,))
    monkeypatch.setattr(config, 'TEAM_STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    yield


ВЧЕРА = daily.day_number() - 1
СЕГОДНЯ = daily.day_number()


def _в_день(номер, час=12):
    u"""Время внутри московских суток с этим номером."""
    начало, _ = daily.bounds(номер)
    return начало + час * 3600


def _человек(uid, день, источник='', запустил=True):
    db.remember_user(uid, 'u%d' % uid, u'Человек', источник)
    db._run('UPDATE users SET started_at=? WHERE user_id=?', (_в_день(день), uid))
    if запустил:
        db.mark_launched(uid)
        db._run('UPDATE users SET launched_at=? WHERE user_id=?', (_в_день(день) + 60, uid))


def _ответ(uid, poll, answer, день):
    db.save_answer(uid, poll, answer)
    db._run('UPDATE answers SET answered=? WHERE user_id=? AND poll=?',
            (_в_день(день) + 3600, uid, poll))


def _день_получен(uid, номер_дня, день):
    db.log_event(uid, 'day', номер_дня)
    db._run("UPDATE events SET at=? WHERE user_id=? AND kind='day' AND ref=?",
            (_в_день(день) + 1800, uid, str(номер_дня)))


# ------------------------------------------------------------------ числа

def test_отчёт_считает_только_свои_сутки():
    _человек(1, ВЧЕРА, 'chat_7')
    _человек(2, ВЧЕРА, 'ig')
    _человек(3, СЕГОДНЯ, 'chat_7')          # сегодняшний во вчерашний не попадёт

    текст = daily.report([ВЧЕРА])
    assert u'Пришли в бота: <b>2</b>' in текст
    assert u'Запустили марафон: <b>2</b>' in текст
    assert u'Рабочий чат ← 7 — 1' in текст and u'Instagram — 1' in текст


def test_в_отчёте_видно_да_нет_и_молчунов_по_каждому_дню():
    for uid in (1, 2, 3):
        _человек(uid, ВЧЕРА)
        _день_получен(uid, 1, ВЧЕРА)
    _ответ(1, 'day1', 'yes', ВЧЕРА)
    _ответ(2, 'day1', 'no', ВЧЕРА)
    # третий промолчал
    _день_получен(1, 2, ВЧЕРА)
    _ответ(1, 'day2', 'yes', ВЧЕРА)

    текст = daily.report([ВЧЕРА])
    assert u'День 1: получили 3 · Да 1 · Нет 1 · молчат 1' in текст
    assert u'День 2: получили 1 · Да 1 · Нет 0 · молчат 0' in текст
    assert u'День 4: получили 0' in текст


def test_несколько_дней_складываются():
    _человек(1, ВЧЕРА)
    _человек(2, СЕГОДНЯ)
    assert u'Пришли в бота: <b>2</b>' in daily.report([ВЧЕРА, СЕГОДНЯ])
    assert u'Пришли в бота: <b>1</b>' in daily.report([СЕГОДНЯ])


def test_покупки_и_ушедшие_попадают_в_отчёт():
    _человек(1, ВЧЕРА)
    db.add_purchase(1, 'gym')
    db._run('UPDATE purchases SET at=? WHERE user_id=?', (_в_день(ВЧЕРА) + 7200, 1))
    _человек(2, ВЧЕРА)
    db.mark_blocked(2)
    db._run('UPDATE users SET blocked_at=? WHERE user_id=?', (_в_день(ВЧЕРА) + 100, 2))

    текст = daily.report([ВЧЕРА])
    assert u'Нажали «купить»: <b>1</b>' in текст and u'Закрыли бота: 1' in текст


# -------------------------------------------------------------- календарь

def test_выбранные_дни_переживают_кнопку():
    данные = daily.pack(СЕГОДНЯ, [СЕГОДНЯ, ВЧЕРА])
    assert len(данные) <= 64                      # предел Telegram на кнопку
    база, дни = daily.unpack(данные)
    assert база == СЕГОДНЯ and sorted(дни) == sorted([ВЧЕРА, СЕГОДНЯ])


def test_нажатие_на_выбранный_день_снимает_его_а_последний_остаётся():
    клавиатура = keyboards.daily(СЕГОДНЯ, [СЕГОДНЯ])
    первая = клавиатура.inline_keyboard[0][0]
    assert первая.text.startswith(u'• ')           # сегодня выбран
    _, дни = daily.unpack(первая.callback_data)
    assert дни == [СЕГОДНЯ]                        # снять последний нельзя


async def test_кнопка_пересчитывает_отчёт_в_том_же_сообщении():
    _человек(1, ВЧЕРА)
    bot = FakeBot()
    сообщение = FakeMessage(text=u'/отчёт', user=FakeUser(АДМИН), bot=bot)
    await admin.on_daily(сообщение)

    call = FakeCall(daily.pack(СЕГОДНЯ, [ВЧЕРА]), user=FakeUser(АДМИН), bot=bot)
    await admin.on_daily_button(call)
    assert call.edited and u'Пришли в бота: <b>1</b>' in call.edited[-1]
    assert u'вчера' in call.edited[-1]


async def test_чужой_отчёт_не_получает():
    bot = FakeBot()
    чужой = FakeMessage(text=u'/отчёт', user=FakeUser(999), bot=bot)
    await admin.on_daily(чужой)
    assert чужой.answers == []


# ----------------------------------------------------------- когда шлём

def test_время_отчёта_берётся_из_настройки(monkeypatch):
    monkeypatch.setattr(config, 'DAILY_REPORT_AT', '09:30')
    сейчас = datetime(2026, 9, 18, 9, 0, tzinfo=stats.MSK)
    assert daily.when_next(сейчас) == 30 * 60

    monkeypatch.setattr(config, 'DAILY_REPORT_AT', '00:00')
    сейчас = datetime(2026, 9, 18, 23, 0, tzinfo=stats.MSK)
    assert daily.when_next(сейчас) == 3600


def test_негодное_время_не_роняет_бота(monkeypatch):
    monkeypatch.setattr(config, 'DAILY_REPORT_AT', 'полночь')
    assert config.daily_at() == (0, 0)


async def test_ночной_отчёт_уходит_команде_за_прошедшие_сутки():
    _человек(1, ВЧЕРА)
    bot = FakeBot()
    assert await daily.send(bot) == 1
    kind, chat, текст = bot.sent[0]
    assert chat == АДМИН and u'вчера' in текст
