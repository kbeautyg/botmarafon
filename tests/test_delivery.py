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
async def test_отзыв_уходит_файлом_а_без_файла_шаг_пропускается(tmp_path, monkeypatch):
    u"""Видеоотзывы заказчик присылает позже картинок — лента не должна ломаться."""
    monkeypatch.setattr(config, 'REVIEWS_DIR', str(tmp_path))
    bot = FakeBot()
    assert await delivery.send_review(bot, 1, 'vid5') is None
    assert bot.sent == []

    (tmp_path / 'img1.jpg').write_bytes(b'jpg')
    (tmp_path / 'vid1.mp4').write_bytes(b'mp4')
    (tmp_path / 'vid2.note.mp4').write_bytes(b'mp4')      # кружок заказчика
    await delivery.send_review(bot, 1, 'img1')
    await delivery.send_review(bot, 1, 'vid1')
    await delivery.send_review(bot, 1, 'vid2')
    assert [kind for kind, _, _ in bot.sent] == ['photo', 'video', 'circle']


@pytest.mark.asyncio
async def test_загруженный_админом_отзыв_важнее_файла(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REVIEWS_DIR', str(tmp_path))
    (tmp_path / 'img1.jpg').write_bytes(b'jpg')
    db.put_content('review1', 'photo', 'PHOTOID')
    bot = FakeBot()
    await delivery.send_review(bot, 1, 'img1')
    assert bot.sent == [('photo', 1, 'PHOTOID')]


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


def test_лента_отзывов_четыре_картинки_и_пять_видео():
    u"""Заказчик: картинка, видеоотзыв, картинка, видеоотзыв… 4 картинки, 5 видео."""
    seq = funnel.REVIEW_SEQUENCE
    assert seq[:8] == ('img1', 'vid1', 'img2', 'vid2', 'img3', 'vid3', 'img4', 'vid4')
    assert seq[8] == 'vid5' and funnel.REVIEW_COUNT == 9


def test_картинки_отзывов_лежат_на_диске():
    u"""Видеоотзывы заказчик ещё не прислал — их отсутствие допустимо, картинок нет."""
    нет = [name for name in funnel.REVIEW_SEQUENCE
           if name.startswith('img') and not delivery.review_path(name)]
    assert нет == []


@pytest.mark.asyncio
async def test_день_из_переменной_окружения_уходит_видео(monkeypatch):
    u"""База пуста (контейнер переехал), но DAY2 задан в окружении."""
    monkeypatch.setattr(config, 'DAY_ENV', {1: '', 2: 'ENVFILE2', 3: '', 4: ''})
    bot = FakeBot()
    await delivery.send_day(bot, 1, 2)
    assert bot.sent == [('video', 1, 'ENVFILE2')]


@pytest.mark.asyncio
async def test_день_из_переменной_окружения_уходит_ссылкой(monkeypatch):
    monkeypatch.setattr(config, 'DAY_ENV',
                        {1: '', 2: '', 3: 'https://energy-sport-gum.ru/marathon/3?k=abc', 4: ''})
    bot = FakeBot()
    await delivery.send_day(bot, 1, 3)
    kind, chat, body = bot.sent[0]
    assert kind == 'text' and chat == 1 and 'marathon/3?k=abc' in body
    assert 777 not in [c for _, c, _ in bot.sent], u'тревоги админам быть не должно'


@pytest.mark.asyncio
async def test_запись_из_базы_важнее_переменной(monkeypatch):
    monkeypatch.setattr(config, 'DAY_ENV', {1: 'ENVFILE1', 2: '', 3: '', 4: ''})
    db.put_content('day1', 'video', 'FILEID1')
    bot = FakeBot()
    await delivery.send_day(bot, 1, 1)
    assert bot.sent == [('video', 1, 'FILEID1')]


def test_база_в_папке_проекта_не_считается_диском(tmp_path, monkeypatch):
    u"""Папка контейнера — не примонтированный том: бот должен это замечать."""
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'marathon.db'))
    assert config.db_persistent() is False


@pytest.mark.asyncio
async def test_подменённая_картинка_отзыва_уходит_новой_а_не_из_кэша(tmp_path, monkeypatch):
    u"""file_id запоминается вместе с размером файла: заменили файл — кэш мимо."""
    monkeypatch.setattr(config, 'REVIEWS_DIR', str(tmp_path))
    (tmp_path / 'img1.jpg').write_bytes(b'old')
    db.put_content('review:img1:3', 'photo', 'OLD_ID')
    bot = FakeBot()
    await delivery.send_review(bot, 1, 'img1')
    assert bot.sent[0][2] == 'OLD_ID'

    (tmp_path / 'img1.jpg').write_bytes(b'new-image')
    await delivery.send_review(bot, 1, 'img1')
    assert bot.sent[1][2] != 'OLD_ID'
