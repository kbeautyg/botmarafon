# -*- coding: utf-8 -*-
u"""Очередь и планировщик: то, из-за чего воронка может встать или задвоиться."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, funnel, scheduler                          # noqa: E402
from tests.fakes import FakeBot                                        # noqa: E402


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'test.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', ())
    db.remember_user(1, 'tester', u'Тестер')
    yield


def сдвинуть_время(user_id):
    u"""Сделать все шаги человека созревшими — вместо ожидания часами."""
    for job in db.user_jobs(user_id):
        db._run('UPDATE jobs SET run_at=? WHERE id=?', (time.time() - 1, job['id']))


@pytest.mark.asyncio
async def test_шаг_ставит_следующий():
    scheduler.start_chain(1, 'launch')
    сдвинуть_время(1)

    bot = FakeBot()
    await scheduler.tick(bot)

    assert bot.sent and bot.sent[0][0] == 'circle'
    очередь = db.user_jobs(1)
    assert len(очередь) == 1 and очередь[0]['pos'] == 1


@pytest.mark.asyncio
async def test_опросник_ставит_отложенное_добивание():
    u"""Не ответил — через POLL_FALLBACK_HOURS уходит ветка «нет»."""
    scheduler.start_chain(1, 'after_day1')
    bot = FakeBot()
    for _ in range(3):
        сдвинуть_время(1)
        await scheduler.tick(bot)

    ветки = {job['chain'] for job in db.user_jobs(1)}
    assert 'day1_no' in ветки
    assert db.get_user(1)['poll'] == 'day1'


@pytest.mark.asyncio
async def test_ответ_снимает_добивание():
    u"""Иначе человек получил бы обе ветки: и «да», и отложенную «нет»."""
    scheduler.start_chain(1, 'after_day1')
    bot = FakeBot()
    for _ in range(3):
        сдвинуть_время(1)
        await scheduler.tick(bot)

    db.drop_chains(1, tuple(funnel.POLL_BRANCHES['day1'].values()))
    scheduler.start_chain(1, 'day1_yes')

    ветки = {job['chain'] for job in db.user_jobs(1)}
    assert 'day1_no' not in ветки and 'day1_yes' in ветки


@pytest.mark.asyncio
async def test_закрывший_бота_убирается_из_очереди():
    scheduler.start_chain(1, 'launch')
    сдвинуть_время(1)

    await scheduler.tick(FakeBot(forbidden=True))
    assert db.user_jobs(1) == []


@pytest.mark.asyncio
async def test_сбой_отправки_повторяется_а_не_теряется():
    scheduler.start_chain(1, 'launch')
    сдвинуть_время(1)

    bot = FakeBot(fail_times=1)
    await scheduler.tick(bot)

    очередь = db.user_jobs(1)
    assert len(очередь) == 1 and очередь[0]['tries'] == 1
    assert очередь[0]['run_at'] > time.time(), u'повтор должен быть отложен'

    сдвинуть_время(1)
    await scheduler.tick(bot)
    assert bot.sent, u'со второй попытки шаг должен уйти'


@pytest.mark.asyncio
async def test_безнадёжный_шаг_снимается_после_трёх_попыток():
    scheduler.start_chain(1, 'launch')
    bot = FakeBot(fail_times=99)
    for _ in range(scheduler.MAX_TRIES):
        сдвинуть_время(1)
        await scheduler.tick(bot)
    assert db.user_jobs(1) == [], u'вечный повтор забил бы очередь'


def test_один_и_тот_же_шаг_не_ставится_дважды():
    u"""Двойное нажатие кнопки не должно удваивать воронку."""
    scheduler.start_chain(1, 'launch')
    scheduler.start_chain(1, 'launch')
    assert len(db.user_jobs(1)) == 1


def test_тестовый_режим_сжимает_паузы():
    db.set_speed(1, 1.0 / 60)
    scheduler.start_chain(1, 'after_day1')
    ждать = db.user_jobs(1)[0]['run_at'] - time.time()
    assert 100 < ждать < 200, u'150 минут должны стать 2,5 минутами, а стали %.0f сек' % ждать


@pytest.mark.asyncio
async def test_темп_не_плывёт_от_времени_отправки():
    u"""Пауза считается от намеченного времени, а не от «сейчас».

    Иначе к каждому шагу приклеивается отправка и ожидание тика: восемь
    отзывов «каждые две секунды» расползались на минуту вместо шестнадцати
    секунд, и это было видно в живом прогоне.
    """
    scheduler.start_chain(1, 'launch')
    job = db.user_jobs(1)[0]
    опоздание = 2
    db._run('UPDATE jobs SET run_at=? WHERE id=?',
            (time.time() - опоздание, job['id']))
    намечено = db.user_jobs(1)[0]['run_at']

    await scheduler.tick(FakeBot())

    следующий = db.user_jobs(1)[0]
    пауза = funnel.CHAINS['launch'].steps[1].delay
    assert abs(следующий['run_at'] - (намечено + пауза)) < 0.5, (
        u'следующий шаг сдвинулся на время отправки')


@pytest.mark.asyncio
async def test_после_простоя_шаги_не_сыплются_лавиной():
    u"""Бот полежал полчаса — накопленный хвост не должен прийти разом."""
    scheduler.start_chain(1, 'launch')
    job = db.user_jobs(1)[0]
    db._run('UPDATE jobs SET run_at=? WHERE id=?', (time.time() - 1800, job['id']))

    await scheduler.tick(FakeBot())

    следующий = db.user_jobs(1)[0]
    пауза = funnel.CHAINS['launch'].steps[1].delay
    ждать = следующий['run_at'] - time.time()
    assert пауза - 0.5 < ждать <= пауза + 0.5, (
        u'после простоя пауза между шагами должна сохраняться, а не схлопываться')
