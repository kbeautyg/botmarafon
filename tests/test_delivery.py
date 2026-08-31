# -*- coding: utf-8 -*-
u"""Отправка: поведение на неполном контенте.

Половина материалов приезжает от заказчика позже кода — записи дней,
недостающий кружок. Бот обязан работать и на неполном комплекте, не роняя
воронку и не отдавая человеку пустоту молча.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, delivery, funnel, texts                    # noqa: E402
from tests.fakes import FakeBot                                        # noqa: E402


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'test.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (777,))
    db.remember_user(1, 'tester', u'Тестер')
    yield


@pytest.mark.asyncio
async def test_отсутствующий_кружок_не_ломает_шаг():
    u"""ask_day3 заказчик не прислал — вопрос должен уйти всё равно."""
    bot = FakeBot()
    assert await delivery.send_circle(bot, 1, 'ask_day3_нет_такого') is None
    assert bot.sent == []


@pytest.mark.asyncio
async def test_день_без_записи_уходит_текстом_и_тревогой_админам():
    bot = FakeBot()
    await delivery.send_day(bot, 1, 1)

    кому = [chat for _, chat, _ in bot.sent]
    assert 1 in кому, u'человек должен получить хотя бы текст дня'
    assert 777 in кому, u'админ должен узнать, что записи нет'

    человеку = [body for _, chat, body in bot.sent if chat == 1][0]
    assert texts.DAY_TEXTS[1] in человеку


@pytest.mark.asyncio
async def test_заданная_запись_уходит_видео():
    db.put_content('day2', 'video', 'FILEID42')
    bot = FakeBot()
    await delivery.send_day(bot, 1, 2)
    assert bot.sent == [('video', 1, 'FILEID42')]


@pytest.mark.asyncio
async def test_запись_ссылкой_уходит_сообщением():
    db.put_content('day3', 'link', 'https://kinescope.io/xyz')
    bot = FakeBot()
    await delivery.send_day(bot, 1, 3)
    kind, _, body = bot.sent[0]
    assert kind == 'text' and 'kinescope.io/xyz' in body


@pytest.mark.asyncio
async def test_отзыв_текстом_а_после_загрузки_картинкой():
    bot = FakeBot()
    await delivery.send_review(bot, 1, 1)
    assert bot.sent[0][0] == 'text' and u'Ольга' in bot.sent[0][2]

    db.put_content('review1', 'photo', 'PHOTOID')
    await delivery.send_review(bot, 1, 1)
    assert bot.sent[1] == ('photo', 1, 'PHOTOID')


def test_все_кружки_сценария_лежат_на_диске():
    u"""Кроме ask_day3 — его заказчик ещё не прислал."""
    нужны = {step.ref for chain in funnel.CHAINS.values()
             for step in chain.steps if step.kind == 'circle'}
    нет = {name for name in нужны
           if not os.path.exists(os.path.join(config.CIRCLES_DIR, name + '.mp4'))}
    assert нет == {'ask_day3'}, u'неожиданно не хватает: %s' % (нет,)


def test_у_каждого_дня_есть_текст():
    дни = {step.ref for chain in funnel.CHAINS.values()
           for step in chain.steps if step.kind == 'day'}
    assert дни == set(texts.DAY_TEXTS)


def test_отзывов_ровно_столько_сколько_шлём():
    assert len(texts.REVIEWS) == funnel.REVIEW_COUNT
