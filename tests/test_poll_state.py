# -*- coding: utf-8 -*-
u"""Вопрос дня как состояние человека — находки аудита 20.09.2026.

Пока вопрос открыт, кнопки под записью живые и ведут дальше по воронке.
Стоит открыть его не вовремя или не тому — и человек обездвижен: старые
кнопки отвечают «этот вопрос уже закрыт», новые тоже, а продолжить нечем.
Ни одна из этих поломок не была видна глазами: люди просто переставали
доходить до оффера.

Отсюда четыре проверки: вопрос не открывается раньше отправки, не
откатывается назад, не выдаётся на день без записи и не остаётся в списке
застрявших у того, кто только что получил день и ещё смотрит.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, delivery, funnel                            # noqa: E402
from bot.handlers import poll as poll_handler                           # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeUser                     # noqa: E402


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'poll.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (777,))
    db.remember_user(1, 'tester', u'Тестер')
    db.mark_launched(1)
    yield


def _запись(day=1):
    db.put_content('day%d' % day, 'video', 'file-%d' % day)


# ------------------------------------------- вопрос открывается после отправки

@pytest.mark.asyncio
async def test_запись_не_ушла_вопрос_не_открыт():
    u"""Иначе человек числится ждущим ответа на день, которого не видел:
    попадает в досылку и получает вопрос про запись, до него не дошедшую."""
    _запись(1)
    with pytest.raises(RuntimeError):
        await delivery.send_day(FakeBot(fail_times=1), 1, 1)

    assert db.get_user(1)['poll'] is None


@pytest.mark.asyncio
async def test_запись_ушла_вопрос_открыт_и_момент_записан():
    u"""Момент нужен дожиму и статистике: до 20.09 он брался с отдельного
    сообщения-вопроса, то есть на два часа позже, чем кнопки у человека."""
    _запись(2)
    await delivery.send_day(FakeBot(), 1, 2)

    assert db.get_user(1)['poll'] == 'day2'
    asked = [e for e in db.timeline(1) if e['kind'] == 'poll']
    assert [e['ref'] for e in asked] == ['day2']


@pytest.mark.asyncio
async def test_четвёртый_день_вопроса_не_открывает():
    _запись(4)
    await delivery.send_day(FakeBot(), 1, 4)
    assert db.get_user(1)['poll'] is None


# ------------------------------------------------------- день без записи

@pytest.mark.asyncio
async def test_день_без_записи_уходит_без_кнопок():
    u"""С кнопками человек нажал бы «Да», ничего не посмотрев, и уехал на
    следующий день — весь марафон прошёл бы мимо записей."""
    bot = FakeBot()
    await delivery.send_day(bot, 1, 1, admins_alert=False)

    кому = [(chat, keys) for (_, chat, _), keys in zip(bot.sent, bot.keys_sent)]
    assert [keys for chat, keys in кому if chat == 1] == [None]
    assert db.get_user(1)['poll'] is None


@pytest.mark.asyncio
async def test_досылка_записи_открывает_вопрос():
    u"""Для тех, кому день ушёл без записи, это единственный путь дальше."""
    await delivery.send_day(FakeBot(), 1, 1, admins_alert=False)
    _запись(1)
    assert await delivery.resend_day(FakeBot(), 1, 1) is True
    assert db.get_user(1)['poll'] == 'day1'


@pytest.mark.asyncio
async def test_досылка_старого_дня_не_возвращает_к_прошлому_вопросу():
    u"""Админ дослал запись первого дня всем — тот, кто уже на третьем,
    не должен откатиться на первый."""
    _запись(1)
    db.save_answer(1, 'day1', 'yes')
    db.save_answer(1, 'day2', 'yes')
    db.set_poll(1, 'day3')

    await delivery.resend_day(FakeBot(), 1, 1)
    assert db.get_user(1)['poll'] == 'day3'


# ----------------------------------------------------- вопрос не едет назад

@pytest.mark.asyncio
async def test_вопрос_не_уходит_тому_кто_уже_ответил():
    db.save_answer(1, 'day1', 'yes')
    db.set_poll(1, 'day2')
    bot = FakeBot()

    assert await delivery.send_poll(bot, 1, 'day1') is None
    assert bot.sent == []
    assert db.get_user(1)['poll'] == 'day2'


@pytest.mark.asyncio
async def test_вопрос_своего_дня_уходит_как_обычно():
    db.set_poll(1, 'day2')
    bot = FakeBot()

    assert await delivery.send_poll(bot, 1, 'day2') is not None
    assert db.get_user(1)['poll'] == 'day2'


# --------------------------------------------- список застрявших ждёт время

def test_только_что_получивший_день_не_застрял():
    u"""Он смотрит видео. Досылка прислала бы ему дубль вопроса — и, пока
    он его читает, ответ по старой кнопке откатил бы его назад."""
    db.set_poll(1, 'day1')
    db.log_event(1, 'poll', 'day1')

    assert db.stuck_on_poll() == []


def test_молчащий_с_утра_застрял():
    db.set_poll(1, 'day1')
    db.log_event(1, 'poll', 'day1')
    db._run('UPDATE events SET at=? WHERE user_id=1', (time.time() - 6 * 3600,))

    assert [p['user_id'] for p in db.stuck_on_poll()] == [1]


def test_без_записанного_момента_считаем_застрявшим():
    u"""Те, кому день ушёл до того, как бот начал записывать момент, —
    это и есть исходные застрявшие, ради которых досылку и делали."""
    db.set_poll(1, 'day1')
    assert [p['user_id'] for p in db.stuck_on_poll()] == [1]


# ------------------------------------------- ответ снимает цепочку вопроса

@pytest.mark.asyncio
async def test_ответ_снимает_кружок_успел_посмотреть():
    u"""Кнопки теперь под записью, а цепочка вопроса продолжает тикать: без
    снятия человеку, уже получившему оффер, прилетает кружок «успел
    посмотреть второй день?» — на вопрос, отвеченный тремя часами раньше."""
    from bot import scheduler
    db.set_poll(1, 'day1')
    scheduler.start_chain(1, 'after_day1')
    assert 'after_day1' in {j['chain'] for j in db.user_jobs(1)}

    await poll_handler.on_answer(
        FakeCall('poll:day1:yes', user=FakeUser(1), bot=FakeBot()))

    chains = {j['chain'] for j in db.user_jobs(1)}
    assert 'after_day1' not in chains
    assert 'day1_yes' in chains


@pytest.mark.asyncio
async def test_после_ответа_воронка_считает_дни_доставленными():
    u"""funnel.day_delivered смотрит на очередь. Застрявшая цепочка вопроса
    убеждала его, что дни 2-4 ещё не ушли, — и «дослать запись всем»
    пропускало как раз тех, кто отвечает быстро."""
    from bot import scheduler
    db.set_poll(1, 'day1')
    scheduler.start_chain(1, 'after_day1')
    await poll_handler.on_answer(
        FakeCall('poll:day1:yes', user=FakeUser(1), bot=FakeBot()))

    pending = {j['chain'] for j in db.user_jobs(1)}
    assert funnel.day_delivered(pending, 1) is True
