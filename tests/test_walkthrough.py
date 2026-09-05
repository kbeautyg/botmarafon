# -*- coding: utf-8 -*-
u"""Сквозной прогон воронки через планировщик.

Отдельные части проверены рядом; здесь важно другое — что человек,
прошедший весь путь, получит ровно то и в том порядке, как описано в ТЗ.
Время не ждём: перед каждым тиком двигаем очередь в прошлое.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, delivery, funnel, scheduler, texts         # noqa: E402
from tests.fakes import FakeBot                                        # noqa: E402

ME = 1


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'walk.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', ())
    db.remember_user(ME, 'tester', u'Тестер')
    for day in (1, 2, 3, 4):
        db.put_content('day%d' % day, 'video', 'DAY%d' % day)
    yield


async def прогнать(bot, ответы):
    u"""Крутить очередь, отвечая на опросники, пока шаги не кончатся."""
    ответы = list(ответы)
    for _ in range(200):
        for job in db.user_jobs(ME):
            db._run('UPDATE jobs SET run_at=? WHERE id=?', (time.time() - 1, job['id']))
        if await scheduler.tick(bot) == 0:
            break

        ждёт = db.get_user(ME)['poll']
        if ждёт and ответы:
            ответ = ответы.pop(0)
            ветки = funnel.POLL_BRANCHES[ждёт]
            db.save_answer(ME, ждёт, ответ)
            db.set_poll(ME, None)
            db.drop_chains(ME, tuple(ветки.values()))
            scheduler.start_chain(ME, ветки[ответ])


@pytest.mark.asyncio
async def test_полный_путь_отвечающего_да():
    bot = FakeBot()
    scheduler.start_chain(ME, 'launch')
    await прогнать(bot, ('yes', 'yes', 'yes'))

    виды = [kind for kind, _, _ in bot.sent]
    кружки = [body for kind, _, body in bot.sent if kind == 'circle']
    # Записи дней уходят по file_id (строкой); видеоотзывы — файлом.
    видео = [body for kind, _, body in bot.sent if kind == 'video' and isinstance(body, str)]

    # Приветствие обеими половинами, потом лента отзывов — те её шаги, чьи
    # файлы лежат на диске (видеоотзывы приезжают позже), — потом первый день.
    отзывы = ['video' if delivery.review_path(name).endswith('.mp4') else 'photo'
              for name in funnel.REVIEW_SEQUENCE if delivery.review_path(name)]
    assert виды[:2] == ['circle', 'circle']
    assert отзывы and виды[2:2 + len(отзывы)] == отзывы
    assert видео == ['DAY1', 'DAY2', 'DAY3', 'DAY4']

    # Приветствие, кружки веток «да» и ни одного из веток «нет».
    assert кружки[:2] == ['welcome_1', 'welcome_2']
    assert 'day2_yes' in кружки and 'day3_yes' in кружки and 'day4_yes' in кружки
    assert not any(c.endswith('_no') for c in кружки)

    # Финал: кружок перед кнопками, сами кнопки, кружок про службу заботы.
    тексты = [body for kind, _, body in bot.sent if kind == 'text']
    assert texts.OFFER_PROMPT in тексты
    assert кружки[-2:] == ['offer', 'care']


@pytest.mark.asyncio
async def test_полный_путь_отвечающего_нет():
    u"""Отказ не выкидывает из воронки: дни всё равно приходят."""
    bot = FakeBot()
    scheduler.start_chain(ME, 'launch')
    await прогнать(bot, ('no', 'no', 'no'))

    видео = [body for kind, _, body in bot.sent if kind == 'video' and isinstance(body, str)]
    кружки = [body for kind, _, body in bot.sent if kind == 'circle']
    assert видео == ['DAY1', 'DAY2', 'DAY3', 'DAY4']
    assert 'day2_no' in кружки and 'day3_no' in кружки and 'day4_no' in кружки
    assert not any(c.endswith('_yes') for c in кружки)


@pytest.mark.asyncio
async def test_молчун_доезжает_до_конца_по_ветке_нет():
    u"""Никто не нажал ни одной кнопки — добивание доводит до продажи."""
    bot = FakeBot()
    scheduler.start_chain(ME, 'launch')
    await прогнать(bot, ())

    видео = [body for kind, _, body in bot.sent if kind == 'video' and isinstance(body, str)]
    assert видео == ['DAY1', 'DAY2', 'DAY3', 'DAY4'], (
        u'без добивания воронка встала бы на первом опроснике')


@pytest.mark.asyncio
async def test_молчун_не_получает_обе_ветки_сразу():
    bot = FakeBot()
    scheduler.start_chain(ME, 'launch')
    await прогнать(bot, ())
    кружки = [body for kind, _, body in bot.sent if kind == 'circle']
    assert not any(c.endswith('_yes') for c in кружки), (
        u'добивание обязано вести по мягкой ветке «нет»')
