# -*- coding: utf-8 -*-
u"""Пульт админа: кого пускаем, что показываем и что уходит человеку.

Главное здесь — дверь. Пульт умеет писать людям от имени бота и банить, и
живёт он на публичном адресе: без проверки подписи Telegram туда зашёл бы
кто угодно, зная только адрес. Поэтому проверок на доступ больше, чем на
всё остальное вместе.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import chatlog, config, db, web                                  # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

TOKEN = '123456:TESTTOKEN'
СВОЙ = 350631550          # AleX, команда проекта
ЧУЖОЙ = 777000            # посторонний


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'panel.db'))
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    web._hits.clear()                      # счёт запросов — с чистого листа
    web._window_from[0] = 0.0
    yield


def подпись(user_id=СВОЙ, token=TOKEN, auth_date=None, **поля):
    u"""Собрать initData так же, как это делает Telegram."""
    data = {
        'user': json.dumps({'id': user_id, 'first_name': u'Кто-то', 'username': 'kent'}),
        'auth_date': str(int(auth_date if auth_date is not None else time.time())),
        'query_id': 'AAA',
    }
    data.update(поля)
    check = u'\n'.join(u'%s=%s' % (k, data[k]) for k in sorted(data))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    data['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


@pytest.fixture
async def пульт(база):
    bot = FakeBot()
    client = TestClient(TestServer(web.build(bot)))
    await client.start_server()
    client.bot = bot
    yield client
    await client.close()


def _человек(uid, name=u'Человек', nick=None, launched=True):
    db.remember_user(uid, nick, name)
    if launched:
        db.mark_launched(uid)


# ------------------------------------------------------------------ дверь

def test_подпись_телеграма_сходится():
    who = web.check_data(подпись(), TOKEN)
    assert who['id'] == СВОЙ


def test_поддельная_подпись_не_проходит():
    with pytest.raises(web.Denied):
        web.check_data(подпись(token='999:ЧУЖОЙТОКЕН'), TOKEN)


def test_подделанные_данные_при_чужой_подписи_не_проходят():
    u"""Подменили, кто открыл пульт, а подпись оставили старую."""
    данные = подпись()
    порченые = данные.replace(urllib.parse.quote(str(СВОЙ)), urllib.parse.quote(str(ЧУЖОЙ)))
    with pytest.raises(web.Denied):
        web.check_data(порченые, TOKEN)


def test_старая_подпись_не_вечна():
    with pytest.raises(web.Denied):
        web.check_data(подпись(auth_date=time.time() - 2 * web.MAX_AGE), TOKEN)


async def test_без_подписи_пульт_молчит(пульт):
    ответ = await пульт.post('/api/people', json={})
    assert ответ.status == 403


async def test_посторонний_с_правильной_подписью_дальше_двери_не_идёт(пульт):
    u"""Подпись настоящая — человек открыл бота, — но он не из команды."""
    ответ = await пульт.post('/api/people', json={'initData': подпись(ЧУЖОЙ)})
    assert ответ.status == 403
    assert u'команды проекта' in (await ответ.json())['error']


@pytest.mark.parametrize('тело', [None, [], 'строка', 42, True])
async def test_мусор_вместо_запроса_не_роняет_пульт(пульт, тело):
    u"""Сканер шлёт «null» вместо запроса. Бот и пульт — один процесс:
    такое должно упираться в отказ, а не в исключение."""
    ответ = await пульт.post('/api/people', json=тело)
    assert ответ.status == 403


async def test_подпись_не_строкой_тоже_отказ(пульт):
    ответ = await пульт.post('/api/people', json={'initData': 12345})
    assert ответ.status == 403


async def test_слишком_частые_запросы_придерживаем(пульт, monkeypatch):
    monkeypatch.setattr(web, 'RATE_LIMIT', 3)
    web._window_from[0] = 0.0
    коды = [(await пульт.post('/api/people', json={'initData': подпись()})).status
            for _ in range(5)]
    assert коды[-1] == 429 and коды[0] == 200


async def test_слишком_длинное_сообщение_не_уходит(пульт):
    _человек(12)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 12, 'text': u'а' * (web.MAX_TEXT + 1)})
    assert ответ.status == 400
    assert пульт.bot.sent == []


async def test_в_ответе_нет_внутренних_id(пульт):
    u"""Пульту не нужны ни id отправителя из команды, ни file_id вложений."""
    _человек(13)
    db.save_message(13, 'out', 'text', u'привет', author=СВОЙ)
    db.save_message(13, 'in', 'photo', None, file_id='SECRET')
    ответ = await пульт.post('/api/chat', json={'initData': подпись(), 'id': 13})
    сообщения = (await ответ.json())['messages']
    assert all('author' not in m and 'file_id' not in m for m in сообщения)
    assert [m['kind'] for m in сообщения] == ['text', 'photo']


# --------------------------------------------------------------- страница

async def test_стили_и_скрипт_отдаются_с_правильным_типом(пульт):
    u"""Без явного типа браузер (из-за nosniff) не применит стили, и пульт
    откроется голой разметкой — так это и выглядело 18.09.2026."""
    for имя, тип in (('app.css', 'text/css'), ('app.js', 'application/javascript')):
        ответ = await пульт.get('/static/' + имя)
        assert ответ.status == 200, имя
        assert тип in ответ.headers['Content-Type'], имя

    страница = await пульт.get('/')
    assert страница.status == 200 and 'text/html' in страница.headers['Content-Type']


async def test_наружу_отдаём_только_файлы_пульта(пульт):
    u"""Ни чужих папок, ни файлов не того вида — пульт стоит в интернете."""
    for путь in ('/static/../bot/config.py', '/static/.env', '/static/config.py',
                 '/static/нет-такого.css'):
        assert (await пульт.get(путь)).status in (400, 403, 404), путь


# ------------------------------------------------------------------ люди

async def test_список_показывает_свежую_переписку_сверху(пульт):
    _человек(1, u'Первый', 'first')
    _человек(2, u'Второй', 'second')
    db.save_message(1, 'in', 'text', u'вопрос')
    db.save_message(2, 'in', 'text', u'а я позже')
    ответ = await пульт.post('/api/people', json={'initData': подпись()})
    люди = (await ответ.json())['people']
    assert [p['id'] for p in люди] == [2, 1]
    assert люди[0]['waiting'] == 1 and люди[0]['last_text'] == u'а я позже'


async def test_поиск_находит_по_нику_имени_и_id(пульт):
    _человек(4242, u'Наталья', 'nataliya_famme')
    for запрос in ('@nataliya', u'ната', '4242'):
        ответ = await пульт.post('/api/people', json={'initData': подпись(), 'query': запрос})
        люди = (await ответ.json())['people']
        assert [p['id'] for p in люди] == [4242], запрос


async def test_без_поиска_вкладка_переписок_показывает_только_тех_кто_писал(пульт):
    _человек(1, u'Молчун', 'quiet')
    _человек(2, u'Писал', 'wrote')
    db.save_message(2, 'in', 'text', u'привет')
    ответ = await пульт.post('/api/people', json={'initData': подпись(), 'onlyChats': True})
    assert [p['id'] for p in (await ответ.json())['people']] == [2]

    ответ = await пульт.post('/api/people', json={'initData': подпись(), 'onlyChats': False})
    assert {p['id'] for p in (await ответ.json())['people']} == {1, 2}


# ---------------------------------------------------------------- диалог

async def test_диалог_отдаёт_переписку_по_порядку(пульт):
    _человек(5)
    db.save_message(5, 'in', 'text', u'здравствуйте')
    db.save_message(5, 'out', 'text', u'добрый день', author=СВОЙ)
    ответ = await пульт.post('/api/chat', json={'initData': подпись(), 'id': 5})
    данные = await ответ.json()
    assert [(m['text'], m['mine']) for m in данные['messages']] == [
        (u'здравствуйте', False), (u'добрый день', True)]


async def test_сообщение_из_пульта_уходит_человеку_и_остаётся_в_переписке(пульт):
    _человек(6)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 6, 'text': u'Добрый день! Я координатор'})
    assert ответ.status == 200
    assert пульт.bot.sent == [('text', 6, u'Добрый день! Я координатор')]
    записано = db.chat_history(6)
    assert [(m['side'], m['text'], m['author']) for m in записано] == [
        ('out', u'Добрый день! Я координатор', СВОЙ)]


async def test_пустое_сообщение_не_уходит(пульт):
    _человек(7)
    ответ = await пульт.post('/api/send', json={'initData': подпись(), 'id': 7, 'text': u'   '})
    assert ответ.status == 400
    assert пульт.bot.sent == []


async def test_закрывшему_бота_пульт_честно_говорит_что_не_доставили(пульт):
    _человек(8)
    пульт.app['bot'] = FakeBot(forbidden=True)
    ответ = await пульт.post('/api/send', json={'initData': подпись(), 'id': 8, 'text': u'ау'})
    assert ответ.status == 409
    assert db.get_user(8)['blocked_at'] is not None
    assert db.chat_history(8) == []            # не записали как отправленное


async def test_незнакомому_id_пульт_не_пишет(пульт):
    ответ = await пульт.post('/api/send', json={'initData': подпись(), 'id': 999, 'text': u'эй'})
    assert ответ.status == 404
    assert пульт.bot.sent == []


# --------------------------------------------------------- чёрный список

async def test_чёрный_список_из_пульта_и_обратно(пульт):
    _человек(9, u'Тролль', 'troll')
    ответ = await пульт.post('/api/ban', json={'initData': подпись(), 'id': 9})
    assert (await ответ.json())['banned'] is True
    assert db.is_banned(9)

    ответ = await пульт.post('/api/ban', json={'initData': подпись(), 'id': 9, 'undo': True})
    assert (await ответ.json())['banned'] is False
    assert not db.is_banned(9)


async def test_в_истории_списка_видно_кто_нажал(пульт):
    _человек(10, u'Тролль', 'troll')
    await пульт.post('/api/ban', json={'initData': подпись(), 'id': 10})
    assert u'kent' in (db.ban_entry(10)['added_by'] or '')


# -------------------------------------------------------- запись переписки

def test_из_сообщения_запоминаем_вид_вложения():
    фото = FakeMessage(text=None, user=FakeUser(11))
    фото.photo = [type('P', (), {'file_id': 'SMALL'})(), type('P', (), {'file_id': 'BIG'})()]
    фото.caption = u'вот так'
    assert chatlog.parts(фото) == ('photo', u'вот так', 'BIG')

    текст = FakeMessage(text=u'просто текст', user=FakeUser(11))
    assert chatlog.parts(текст) == ('text', u'просто текст', None)
