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


# ------------------------------------------- адрес пульта и уведомления
#
# 18.09.2026: в переменную WEBAPP_URL лёг адрес без «https://», как его
# показывает Railway. Telegram отверг кнопку — и вместе с /пульт пропали
# уведомления команде о каждом новом человеке. Дважды такому не бывать.

def test_адрес_пульта_приводим_к_https():
    приведём = config._webapp_url
    assert приведём('botmarafon-production.up.railway.app') == \
        'https://botmarafon-production.up.railway.app'
    assert приведём('http://bot.example.com/') == 'https://bot.example.com'
    assert приведём(' https://bot.example.com/ ') == 'https://bot.example.com'


def test_негодный_адрес_пульта_лучше_никакого():
    u"""Без точки в имени Telegram кнопку не примет — не показываем её вовсе."""
    for мусор in ('', None, 'localhost:8080', 'адрес не задан'):
        assert config._webapp_url(мусор) == ''


async def test_уведомление_доходит_даже_если_телеграм_отверг_кнопки():
    from aiogram.exceptions import TelegramBadRequest

    from bot import delivery, keyboards

    class Привереда(FakeBot):
        u"""Принимает сообщение только без клавиатуры — как Telegram с битой кнопкой."""

        async def send_message(self, chat_id, text, **kw):
            if kw.get('reply_markup') is not None:
                raise TelegramBadRequest(method=None, message=u'Bad Request: Web App URL is invalid')
            return await self._record('text', chat_id, text)

    bot = Привереда()
    ушло = await delivery.note(bot, 999, u'🚀 Новый запуск марафона',
                               keyboards.ban_ask(1, 999), None)
    assert ушло is not None and bot.sent == [('text', 999, u'🚀 Новый запуск марафона')]


# ------------------------------------- вложения ссылкой (AleX 18.09.2026)

def test_вид_вложения_узнаём_по_ссылке():
    assert web.media_kind('https://site.ru/pic.JPG') == 'photo'
    assert web.media_kind('https://site.ru/video.mp4?x=1') == 'video'
    assert web.media_kind('https://site.ru/podcast.mp3') == 'audio'
    assert web.media_kind('https://youtube.com/watch?v=abc') == ''   # уйдёт ссылкой


def test_негодная_ссылка_на_вложение_не_проходит(пульт):
    assert web.check_media('javascript:alert(1)') == ''
    assert web.check_media('не ссылка') == ''
    assert web.check_media('https://site.ru/a.jpg') == 'https://site.ru/a.jpg'


async def test_фото_ссылкой_уходит_фотографией_и_ложится_в_переписку(пульт):
    _человек(31)
    отправлено = []

    class СМедиа(FakeBot):
        async def send_photo(self, chat_id, photo, **kw):
            отправлено.append(('photo', chat_id, photo, kw.get('caption')))
            return await self._record('photo', chat_id, photo)

    пульт.app['bot'] = СМедиа()
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 31, 'text': u'Смотрите',
        'media': 'https://site.ru/pic.jpg'})

    assert ответ.status == 200
    assert отправлено == [('photo', 31, 'https://site.ru/pic.jpg', u'Смотрите')]
    записано = db.chat_history(31)[-1]
    assert записано['kind'] == 'photo' and записано['file_id'] == 'https://site.ru/pic.jpg'


async def test_подкаст_без_текста_тоже_уходит(пульт):
    _человек(32)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 32, 'media': 'https://site.ru/podcast.mp3'})
    assert ответ.status == 200


async def test_кривая_ссылка_на_вложение_объясняет_что_не_так(пульт):
    _человек(33)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'id': 33, 'text': u'вот', 'media': 'site.ru/pic.jpg'})
    assert ответ.status == 400
    assert u'http' in (await ответ.json())['error']


# --------------------------------- «писали в бота» (AleX 18.09.2026)

async def test_уведомления_команде_не_считаются_сообщениями_людей(пульт):
    u"""Раньше «писали в заботу» считалось по мосту для реплаев: туда падало
    каждое уведомление команде, да ещё по записи на каждого получателя, — и
    в отчёте выходили сотни вместо десятка."""
    from bot import insights

    _человек(41)
    for chat in (100, 200, 300):                # уведомление ушло троим
        db.link_care(chat, 1000 + chat, 41)
    db.save_message(41, 'in', 'text', u'единственный вопрос')

    люди = insights.model()['people']
    assert люди[41]['care'] == 1

    _человек(42)
    db.link_care(500, 777, 42)                  # только уведомление, сам не писал
    assert insights.model()['people'][42]['care'] == 0


# ------------------- правка и удаление сообщения (AleX 19.09.2026)

class Редактор(FakeBot):
    u"""Телеграм, который умеет править и удалять свои сообщения."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.edited = []
        self.deleted = []

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kw):
        self.edited.append((chat_id, message_id, text))
        return True

    async def delete_message(self, chat_id=None, message_id=None, **kw):
        self.deleted.append((chat_id, message_id))
        return True


async def test_своё_сообщение_правится_и_у_человека(пульт):
    _человек(51)
    номер = db.save_message(51, 'out', 'text', u'Добрый день', author=СВОЙ, tg_id=777)
    bot = Редактор()
    пульт.app['bot'] = bot

    ответ = await пульт.post('/api/edit', json={
        'initData': подпись(), 'messageId': номер, 'text': u'Добрый вечер'})

    assert ответ.status == 200
    assert bot.edited == [(51, 777, u'Добрый вечер')]
    assert db.chat_history(51)[-1]['text'] == u'Добрый вечер'


async def test_своё_сообщение_удаляется_и_пропадает_из_переписки(пульт):
    _человек(52)
    номер = db.save_message(52, 'out', 'text', u'Лишнее', author=СВОЙ, tg_id=778)
    bot = Редактор()
    пульт.app['bot'] = bot

    ответ = await пульт.post('/api/drop', json={'initData': подпись(), 'messageId': номер})

    assert ответ.status == 200
    assert bot.deleted == [(52, 778)]
    assert db.chat_history(52) == []


async def test_чужое_сообщение_править_нельзя(пульт):
    u"""Сообщение человека — не наше: Telegram его править и не даст."""
    _человек(53)
    номер = db.save_message(53, 'in', 'text', u'вопрос', tg_id=12)
    ответ = await пульт.post('/api/edit', json={
        'initData': подпись(), 'messageId': номер, 'text': u'подмена'})
    assert ответ.status == 400
    assert db.chat_history(53)[0]['text'] == u'вопрос'


async def test_старое_сообщение_телеграм_удалять_не_даёт_а_мы_объясняем(пульт):
    _человек(54)
    номер = db.save_message(54, 'out', 'text', u'Давнее', author=СВОЙ, tg_id=779)

    class Отказ(FakeBot):
        async def delete_message(self, **kw):
            raise RuntimeError("Bad Request: message can't be deleted for everyone")

    пульт.app['bot'] = Отказ()
    ответ = await пульт.post('/api/drop', json={'initData': подпись(), 'messageId': номер})
    assert ответ.status == 409
    assert u'двух суток' in (await ответ.json())['error']
    assert db.chat_history(54)                      # в переписке осталось


async def test_в_ленте_видно_шаги_воронки_а_не_только_слова(пульт):
    u"""AleX 19.09.2026: «просто одно слово, а на что это был ответ — непонятно»."""
    _человек(55)
    db.log_event(55, 'day', 1)
    db.log_event(55, 'poll', 'day1')
    db.save_answer(55, 'day1', 'yes')

    ответ = await пульт.post('/api/chat', json={'initData': подпись(), 'id': 55})
    строки = [m['text'] for m in (await ответ.json())['messages']]
    assert u'Бот прислал запись первого дня' in строки
    assert u'Бот спросил: посмотрел первый день?' in строки
    assert u'Ответ на вопрос первого дня: Да' in строки
