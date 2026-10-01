# -*- coding: utf-8 -*-
u"""Пульт: кнопки под сообщением (AleX 01.10.2026).

«Выбираем сообщение, где будет видео и текст и кнопка… кого — с помощью
функционала в пульте: оповещать всех, кто купил, кто на четвёртом шаге, на
третьем». Получателей пульт выбирать уже умел; здесь — кнопки: готовая пара
«Вступить…» и «Сайт», как под баннером после марафона, и своя кнопка-ссылка
(«кнопки обе сделай»).
"""
import json
import os
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import broadcast, config, db, keyboards, texts, web             # noqa: E402
from bot.handlers import purchase                                        # noqa: E402
from tests.fakes import FakeBot, FakeCall                                # noqa: E402
from tests.test_audience import (Почтальон, _дождаться_рассылки, _дошёл,  # noqa: E402
                                 отправить_файл)
from tests.test_panel import TOKEN, СВОЙ, _человек, подпись              # noqa: E402

ССЫЛКА = 'https://energy-sport-gum.ru/podcast'
ОБЕ = {'join': True, 'text': u'Слушать подкаст', 'url': ССЫЛКА}


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'buttons.db'))
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(broadcast, 'PAUSE', 0)
    web._hits.clear()
    web._window_from[0] = 0.0
    yield


class СВидео(Почтальон):
    u"""Помнит, с какими кнопками ушло видео."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.video_keys = []

    async def send_video(self, chat_id, video, **kw):
        self.video_keys.append((chat_id, kw.get('reply_markup')))
        return await super().send_video(chat_id, video, **kw)


@pytest.fixture
async def пульт(база):
    bot = СВидео()
    client = TestClient(TestServer(web.build(bot)))
    await client.start_server()
    client.bot = bot
    yield client
    await client.close()


def _кнопки(markup):
    return [(b.text, b.callback_data or b.url) for row in markup.inline_keyboard for b in row]


def _ушло(bot, вид):
    u"""(кому, кнопки) по каждой отправке этого вида."""
    return [(chat, keys) for (kind, chat, _), keys in zip(bot.sent, bot.keys_sent) if kind == вид]


async def test_одному_человеку_с_обеими_кнопками(пульт):
    _человек(1)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 1, 'text': u'Новый подкаст', 'buttons': ОБЕ})
    assert ответ.status == 200
    [(кому, keys)] = _ушло(пульт.bot, 'text')
    assert кому == 1
    assert _кнопки(keys) == [(texts.FINISH_BUY, 'buy:gym:cast'),
                             (texts.FINISH_SITE, config.GYM_SITE_URL),
                             (u'Слушать подкаст', ССЫЛКА)]


async def test_только_своя_кнопка(пульт):
    _человек(1)
    await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 1, 'text': u'Эфир',
        'buttons': {'text': u'  Смотреть   эфир ', 'url': ССЫЛКА}})
    [(_, keys)] = _ушло(пульт.bot, 'text')
    assert _кнопки(keys) == [(u'Смотреть эфир', ССЫЛКА)]


async def test_без_кнопок_всё_как_раньше(пульт):
    _человек(1)
    await пульт.post('/api/send', json={'initData': подпись(), 'id': 1, 'text': u'Привет',
                                        'buttons': None})
    assert _ушло(пульт.bot, 'text') == [(1, None)]


@pytest.mark.parametrize('кнопки,слово', [
    ({'text': u'Смотреть'}, u'и текст, и ссылка'),
    ({'url': ССЫЛКА}, u'и текст, и ссылка'),
    ({'text': u'Смотреть', 'url': 'energy-sport-gum.ru'}, u'http'),
    ({'text': u'ы' * 41, 'url': ССЫЛКА}, u'длиннее'),
])
async def test_кривая_кнопка_не_уходит(пульт, кнопки, слово):
    _человек(1)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 1, 'text': u'Эфир', 'buttons': кнопки})
    assert ответ.status == 400 and слово in (await ответ.json())['error']
    assert пульт.bot.sent == []


async def test_выбранным_каждому_с_кнопками(пульт):
    _человек(1)
    _человек(2)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'ids': [1, 2], 'text': u'Эфир', 'buttons': {'join': True}})
    assert await ответ.json() == {'sent': 2, 'gone': 0, 'failed': 0}
    ушло = _ушло(пульт.bot, 'text')
    assert [кому for кому, _ in ушло] == [1, 2]
    assert all(_кнопки(keys)[0] == (texts.FINISH_BUY, 'buy:gym:cast') for _, keys in ушло)


async def test_группе_кнопки_едут_с_рассылкой(пульт):
    u"""Группа уходит рассылкой: кнопки лежат в ней и переживают перезапуск.
    У автора — образец: «Вступить» на нём заявку не создаёт."""
    _дошёл(1, 4)
    _дошёл(2, 4)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'day4', 'text': u'Старт спортзала', 'buttons': ОБЕ})
    assert (await ответ.json())['started'] is True

    [(автор, образец)] = _ушло(пульт.bot, 'text')[:1]
    assert автор == СВОЙ and _кнопки(образец)[0] == (texts.FINISH_BUY, 'buy:gym:preview')
    задача = await _дождаться_рассылки()
    assert json.loads(задача['buttons']) == {'join': True, 'link': [u'Слушать подкаст', ССЫЛКА]}
    копии = _ушло(пульт.bot, 'copy')
    assert [кому for кому, _ in копии] == [1, 2]
    for _, keys in копии:
        assert _кнопки(keys) == [(texts.FINISH_BUY, 'buy:gym:cast'),
                                 (texts.FINISH_SITE, config.GYM_SITE_URL),
                                 (u'Слушать подкаст', ССЫЛКА)]


async def test_видео_с_устройства_с_кнопками(пульт):
    u"""«Видео и текст и кнопка»: файл с телефона, подпись и кнопки под ним."""
    _человек(1)
    ответ = await отправить_файл(пульт, name='clip.mp4', mime='video/mp4', text=u'Смотрите',
                                 buttons=json.dumps({'join': True}))
    assert ответ.status == 200
    [(кому, keys)] = пульт.bot.video_keys
    assert кому == 1 and _кнопки(keys)[0] == (texts.FINISH_BUY, 'buy:gym:cast')


async def test_видео_группе_с_кнопками(пульт):
    _дошёл(1, 4)
    ответ = await отправить_файл(пульт, name='clip.mp4', mime='video/mp4', text=u'Смотрите',
                                 scope='day4', buttons=json.dumps(ОБЕ))
    assert (await ответ.json())['started'] is True
    [(автор, образец)] = пульт.bot.video_keys
    assert автор == СВОЙ and _кнопки(образец)[0][1] == 'buy:gym:preview'
    await _дождаться_рассылки()
    [(кому, keys)] = _ушло(пульт.bot, 'copy')
    assert кому == 1 and _кнопки(keys)[-1] == (u'Слушать подкаст', ССЫЛКА)


async def test_кривые_кнопки_у_файла_тоже_отказ(пульт):
    _человек(1)
    ответ = await отправить_файл(пульт, name='clip.mp4', mime='video/mp4',
                                 buttons=json.dumps({'text': u'Смотреть'}))
    assert ответ.status == 400
    assert пульт.bot.sent == []


def test_рассылка_без_выбранных_кнопок_и_с_битыми_идёт_без_них():
    assert broadcast.markup({'id': 1, 'buttons': None}) is None
    assert broadcast.markup({'id': 1, 'buttons': '{не json'}) is None
    assert _кнопки(broadcast.markup({'id': 1, 'buttons': 'yesno'}))[0] == (u'ДА', 'ask:yes:1')
    assert keyboards.extra(None) is None and keyboards.extra({}) is None


async def test_вступить_под_сообщением_из_пульта_это_заявка_с_пометкой():
    db.remember_user(1, 'u1', u'Человек')
    bot = FakeBot()
    await purchase.on_buy(FakeCall('buy:gym:cast', bot=bot))
    notes = [text for kind, chat, text in bot.sent if u'Заявка №' in (text or u'')]
    assert notes and all(purchase.PLACES['cast'] in note and texts.FINISH_BUY in note
                         for note in notes)
    assert db.purchase_presses(1, 'gym')


async def test_вступить_на_образце_у_автора_не_заявка():
    bot = FakeBot()
    call = FakeCall('buy:gym:preview', bot=bot)
    await purchase.on_buy(call)
    assert db.purchase_presses(1, 'gym') == []
    assert call.answers == [u'Это образец для команды — заявка не создаётся']
