# -*- coding: utf-8 -*-
u"""Пульт: весь список, группы и файлы с устройства (AleX 23.09.2026).

«Больше 60 человек не отображается в списке, из которого нужно выбрать,
кому отправить»; «пусть сверху будет галочка выбрать всех… или оповестить
всех, кто на первом, втором, третьем, четвёртом дне или нажал купить»;
«нужно иметь возможность прикрепить и картинку с устройства, и видео с
устройства, и аудио если потребуется».

Главное, что здесь проверяется: пульт обещает ровно то число людей,
которое получит сообщение, и большие отправки уходят рассылкой, а не
висят в запросе из телефона.
"""
import asyncio
import os
import sys
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import broadcast, config, db, web                               # noqa: E402
from tests.fakes import FakeBot                                          # noqa: E402
from tests.test_panel import TOKEN, СВОЙ, ЧУЖОЙ, _человек, подпись       # noqa: E402


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'audience.db'))
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(broadcast, 'PAUSE', 0)     # в тестах ждать нечего
    web._hits.clear()
    web._window_from[0] = 0.0
    yield


class ФайлТГ(object):
    u"""То, чем Telegram отвечает об отправленном файле."""

    def __init__(self, file_id):
        self.file_id = file_id


class Почтальон(FakeBot):
    u"""Запоминает, чем и что именно ушло: байтами или уже залитым файлом."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.files = []                 # (вид, кому, чем, подпись)

    async def _file(self, kind, chat_id, body, caption):
        self.files.append((kind, chat_id,
                           body if isinstance(body, str) else 'БАЙТЫ', caption))
        message = await self._record(kind, chat_id, caption)
        piece = ФайлТГ('FILE-' + kind)
        setattr(message, kind, [piece] if kind == 'photo' else piece)
        return message

    async def send_photo(self, chat_id, photo, **kw):
        return await self._file('photo', chat_id, photo, kw.get('caption'))

    async def send_video(self, chat_id, video, **kw):
        return await self._file('video', chat_id, video, kw.get('caption'))

    async def send_audio(self, chat_id, audio, **kw):
        return await self._file('audio', chat_id, audio, kw.get('caption'))

    async def send_animation(self, chat_id, animation, **kw):
        return await self._file('animation', chat_id, animation, kw.get('caption'))

    async def send_document(self, chat_id, document, **kw):
        return await self._file('document', chat_id, document, kw.get('caption'))


@pytest.fixture
async def пульт(база):
    bot = Почтальон()
    client = TestClient(TestServer(web.build(bot)))
    await client.start_server()
    client.bot = bot
    yield client
    await client.close()


def _дошёл(uid, день, name=u'Человек'):
    u"""Человек, которому бот прислал записи по этот день включительно."""
    _человек(uid, name)
    for номер in range(1, день + 1):
        db.log_event(uid, 'day', str(номер))


async def _люди(пульт, **поля):
    тело = {'initData': подпись()}
    тело.update(поля)
    return await (await пульт.post('/api/people', json=тело)).json()


async def _дождаться_рассылки(номер=1, тайм_аут=3.0):
    u"""Рассылка идёт отдельной задачей — дождаться, пока закончит."""
    конец = time.time() + тайм_аут
    while time.time() < конец:
        task = db.broadcast(номер)
        if task and task['status'] == 'done':
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(u'рассылка так и не закончилась')


# ------------------------------------------------- список целиком, порциями

async def test_список_приходит_порциями_и_говорит_сколько_осталось(пульт):
    u"""AleX: «больше 60 человек не отображается в списке»."""
    for uid in range(1, 71):
        _человек(uid)

    первые = await _люди(пульт, onlyChats=False)
    assert len(первые['people']) == web.PAGE_SIZE
    assert первые['total'] == 70
    assert первые['more'] == 10

    ещё = await _люди(пульт, onlyChats=False, offset=web.PAGE_SIZE)
    assert len(ещё['people']) == 10
    assert ещё['more'] == 0
    # Никто не показан дважды: «показать ещё» продолжает список, а не
    # начинает его заново.
    было = {p['id'] for p in первые['people']}
    assert не_пересекаются(было, {p['id'] for p in ещё['people']})
    assert len(было | {p['id'] for p in ещё['people']}) == 70


def не_пересекаются(первое, второе) -> bool:
    return not (первое & второе)


async def test_в_списке_видно_и_чёрный_список(пульт):
    u"""AleX: «тут весь список должен быть, которые в ЧС в том числе»."""
    _человек(1, u'Обычный')
    _человек(2, u'Крикун')
    db.ban_add(2, None, u'Крикун', by='тест')

    люди = (await _люди(пульт, onlyChats=False))['people']
    assert {p['id'] for p in люди} == {1, 2}
    assert [p['banned'] for p in люди if p['id'] == 2] == [True]


async def test_поиск_тоже_считает_сколько_нашлось(пульт):
    for uid in range(1, 5):
        _человек(uid, u'Наталья' if uid < 3 else u'Пётр')
    найдено = await _люди(пульт, query=u'натал')
    assert найдено['total'] == 2 and найдено['more'] == 0


# ------------------------------------------------------------------ группы

async def test_группы_считают_людей_по_шагам_воронки(пульт):
    _дошёл(1, 1)
    _дошёл(2, 2)
    _дошёл(3, 2)
    _дошёл(4, 4)
    db.add_purchase(4, 'main')

    ответ = await (await пульт.post('/api/scopes', json={'initData': подпись()})).json()
    сколько = {s['key']: s['count'] for s in ответ['scopes']}
    assert сколько == {'all': 4, 'day1': 1, 'day2': 2, 'day3': 0,
                       'day4': 1, 'bought': 1}
    # У каждой группы есть понятное название — их видно на кнопках.
    assert [s['title'] for s in ответ['scopes']][0] == u'Все участники'


def test_шаг_воронки_это_где_человек_сейчас(база):
    u"""«На втором дне» — получил второй и не получил третий. Иначе один
    человек попадал бы сразу во все группы, и «оповестить всех, кто на
    втором» значило бы «почти всем»."""
    _дошёл(1, 2)
    assert db.audience('day1') == []
    assert db.audience('day2') == [1]

    db.log_event(1, 'day', '3')
    assert db.audience('day2') == []
    assert db.audience('day3') == [1]


def test_в_группу_не_попадают_закрывшие_бота_и_чёрный_список(база):
    u"""Пульт обещает ровно столько, сколько получит сообщение."""
    _дошёл(1, 1)
    _дошёл(2, 1)
    _дошёл(3, 1)
    db.mark_blocked(2)
    db.ban_add(3, None, u'Крикун', by='тест')

    assert db.audience('day1') == [1]
    assert db.audience('all') == [1]


def test_незнакомая_группа_никого_не_даёт(база):
    _человек(1)
    assert db.audience('всех-подряд') == []


async def test_посторонний_группы_не_увидит(пульт):
    ответ = await пульт.post('/api/scopes', json={'initData': подпись(ЧУЖОЙ)})
    assert ответ.status == 403


# ------------------------------------------------------- отправка группе

async def test_сообщение_группе_уходит_рассылкой(пульт):
    u"""Группа — это сотни человек и минуты отправки: запрос из телефона
    столько не ждёт, поэтому отправку ведёт рассылка."""
    _дошёл(1, 2)
    _дошёл(2, 2)
    _дошёл(3, 1)                                   # этот на другом шаге

    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'day2', 'text': u'Эфир сегодня в 19:00'})
    итог = await ответ.json()
    assert итог['started'] is True and итог['count'] == 2
    assert итог['title'] == u'Сейчас на 2 дне'

    # Сначала бот присылает сообщение автору: так видно, что ушло людям.
    assert пульт.bot.sent[0] == ('text', СВОЙ, u'Эфир сегодня в 19:00')
    задача = await _дождаться_рассылки()
    assert задача['sent'] == 2
    ушло = [чат for вид, чат, _ in пульт.bot.sent if вид == 'copy']
    assert ушло == [1, 2]


async def test_группе_всем_рассылка_идёт_без_перечня(пульт):
    u"""«Все» — это и есть обычная рассылка: километровый список id в базе
    ни к чему, его и так умеет собрать сама рассылка."""
    _человек(1)
    _человек(2)

    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'all', 'text': u'Привет'})
    assert (await ответ.json())['count'] == 2
    assert db.broadcast(1)['targets'] is None
    задача = await _дождаться_рассылки()
    assert задача['sent'] == 2


async def test_группе_из_никого_пульт_честно_отказывает(пульт):
    _дошёл(1, 1)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'day3', 'text': u'Эфир'})
    assert ответ.status == 400
    assert u'никого' in (await ответ.json())['error']


async def test_выбранная_группа_главнее_старых_галочек(пульт):
    u"""В пульте группа и галочки не складываются; сервер тоже не должен
    их путать, если в запросе пришло и то, и другое."""
    _дошёл(1, 1)
    _человек(7)

    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'day1', 'ids': [7], 'text': u'Эфир'})
    assert (await ответ.json())['count'] == 1
    await _дождаться_рассылки()
    assert [чат for вид, чат, _ in пульт.bot.sent if вид == 'copy'] == [1]


async def test_посторонний_группе_не_напишет(пульт):
    _человек(1)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(ЧУЖОЙ), 'scope': 'all', 'text': u'Эфир'})
    assert ответ.status == 403
    assert пульт.bot.sent == []


# ------------------------------------------------- файл с устройства
#
# AleX 23.09.2026: «нужно иметь возможность прикрепить и картинку с
# устройства, и видео с устройства, и аудио если потребуется. И возможность
# ссылку тоже прикрепить». Ссылка была и раньше (кнопка 📎), здесь — файл.


async def отправить_файл(пульт, data=b'DATA', name='photo.jpg', mime='image/jpeg',
                         кому=1, text=u'', **поля):
    import aiohttp

    writer = aiohttp.FormData()
    writer.add_field('initData', поля.pop('подпись_', None) or подпись())
    writer.add_field('id', str(кому))
    # kind и mime — раньше файла: по ним бот понимает, сколько принимать
    # и чем отправлять.
    writer.add_field('kind', 'file')
    writer.add_field('mime', mime)
    writer.add_field('text', text)
    for ключ, значение in поля.items():
        writer.add_field(ключ, значение)
    writer.add_field('file', data, filename=name,
                     content_type='application/octet-stream')
    return await пульт.post('/api/record', data=writer)


@pytest.mark.parametrize('name,mime,вид', [
    ('photo.jpg', 'image/jpeg', 'photo'),
    ('clip.mp4', 'video/mp4', 'video'),
    ('podcast.mp3', 'audio/mpeg', 'audio'),
    ('plan.pdf', 'application/pdf', 'document'),
    # Телефон промолчал о виде файла — узнаём по расширению.
    ('shot.png', '', 'photo'),
    ('unknown.xyz', '', 'document'),
])
async def test_файл_с_телефона_уходит_своим_видом(пульт, name, mime, вид):
    _человек(1)
    ответ = await отправить_файл(пульт, name=name, mime=mime, text=u'Смотрите')
    assert ответ.status == 200
    assert (await ответ.json())['kind'] == вид
    assert пульт.bot.files == [(вид, 1, 'БАЙТЫ', u'Смотрите')]
    # И осталось в переписке — иначе в пульте не видно, что отправляли.
    последнее = db.chat_history(1)[-1]
    assert последнее['kind'] == вид and последнее['text'] == u'Смотрите'
    assert последнее['file_id'] == 'FILE-' + вид


async def test_тяжёлая_картинка_уходит_файлом(пульт, monkeypatch):
    u"""Фотографией Телеграм берёт только до десяти мегабайт — что тяжелее,
    отправляем файлом: целиком и без пережатия."""
    monkeypatch.setattr(web, 'MAX_PHOTO', 10)
    _человек(1)
    ответ = await отправить_файл(пульт, data=b'D' * 50)
    assert (await ответ.json())['kind'] == 'document'


async def test_файл_выбранным_заливаем_один_раз(пульт):
    u"""Сорок мегабайт двадцать раз подряд не нужны ни нам, ни телефону:
    файл уходит автору, а остальным — тот же самый по номеру у Телеграма."""
    for uid in (1, 2, 3):
        _человек(uid)

    ответ = await отправить_файл(пульт, ids='1,2,3', text=u'Афиша')
    assert await ответ.json() == {'sent': 3, 'gone': 0, 'failed': 0}
    assert пульт.bot.files == [('photo', СВОЙ, 'БАЙТЫ', u'Афиша'),
                               ('photo', 1, 'FILE-photo', u'Афиша'),
                               ('photo', 2, 'FILE-photo', u'Афиша'),
                               ('photo', 3, 'FILE-photo', u'Афиша')]
    assert db.chat_history(2)[-1]['kind'] == 'photo'


async def test_файл_группе_уходит_рассылкой(пульт):
    _дошёл(1, 4)
    _дошёл(2, 4)

    ответ = await отправить_файл(пульт, scope='day4', text=u'Запись эфира')
    итог = await ответ.json()
    assert итог['started'] is True and итог['count'] == 2
    assert пульт.bot.files == [('photo', СВОЙ, 'БАЙТЫ', u'Запись эфира')]
    задача = await _дождаться_рассылки()
    assert задача['sent'] == 2


async def test_слишком_тяжёлый_файл_не_принимаем(пульт, monkeypatch):
    monkeypatch.setattr(web, 'MAX_FILE', 1024)
    _человек(1)
    ответ = await отправить_файл(пульт, data=b'x' * 4096)
    assert ответ.status == 413
    assert u'не принимает' in (await ответ.json())['error']
    assert пульт.bot.files == []


async def test_файл_незнакомому_id_не_уходит(пульт):
    ответ = await отправить_файл(пульт, кому=999999)
    assert ответ.status == 404
    assert пульт.bot.files == []


async def test_слишком_длинная_подпись_не_режется_молча(пульт):
    u"""Обрезать молча нельзя: человек бы даже не узнал, что конец его
    текста никуда не ушёл."""
    _человек(1)
    ответ = await отправить_файл(пульт, text=u'а' * (web.MAX_CAPTION + 1))
    assert ответ.status == 400
    assert u'отдельным сообщением' in (await ответ.json())['error']
    assert пульт.bot.files == []


# ------------------------------------------- снятые галочки внутри группы
#
# AleX 23.09.2026: «хочу выбрать всех, а потом рукой убрать галочку у тех,
# кому 60 сообщений отправилось перед этим, — чтобы дважды одно и то же не
# отправлять». Значит, список должен показывать саму группу, а снятые в
# ней — не получать.


async def test_выбрана_группа_список_показывает_её(пульт):
    _дошёл(1, 2)
    _дошёл(2, 2)
    _дошёл(3, 1)
    _человек(4)                                    # ни одного дня

    ответ = await _люди(пульт, onlyChats=False, scope='day2')
    assert {p['id'] for p in ответ['people']} == {1, 2}
    assert ответ['total'] == 2 and ответ['more'] == 0


async def test_в_группе_не_показываем_тех_кому_бот_не_напишет(пульт):
    u"""Иначе галочка стояла бы у человека, которому ничего не уйдёт."""
    _человек(1)
    _человек(2)
    _человек(3)
    db.mark_blocked(2)
    db.ban_add(3, None, u'Крикун', by='тест')

    ответ = await _люди(пульт, onlyChats=False, scope='all')
    assert [p['id'] for p in ответ['people']] == [1]
    assert ответ['total'] == 1


async def test_поиск_работает_и_внутри_группы(пульт):
    _дошёл(1, 1, u'Наталья')
    _дошёл(2, 1, u'Пётр')
    ответ = await _люди(пульт, scope='day1', query=u'натал')
    assert [p['id'] for p in ответ['people']] == [1]


async def test_снятым_галочкам_сообщение_не_уходит(пульт):
    for uid in (1, 2, 3):
        _дошёл(uid, 1)

    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'day1', 'except': [2],
        'text': u'Досылаю остальным'})
    итог = await ответ.json()
    assert итог['count'] == 2
    await _дождаться_рассылки()
    assert [чат for вид, чат, _ in пульт.bot.sent if вид == 'copy'] == [1, 3]


async def test_снятые_приходят_и_строкой_через_запятую(пульт):
    u"""Из формы с файлом список уходит строкой, а не списком."""
    for uid in (1, 2, 3):
        _дошёл(uid, 4)

    ответ = await отправить_файл(пульт, scope='day4', **{'except': '2,3'})
    assert (await ответ.json())['count'] == 1
    await _дождаться_рассылки()
    assert [чат for вид, чат, _ in пульт.bot.sent if вид == 'copy'] == [1]


async def test_если_сняли_всех_писать_некому(пульт):
    _дошёл(1, 1)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'day1', 'except': [1], 'text': u'Эфир'})
    assert ответ.status == 400
    assert u'сняты у всех' in (await ответ.json())['error']


async def test_во_всех_участниках_снятым_галочкам_тоже_не_уходит(пульт):
    u"""26.09.2026: «Все участники» со снятыми галочками уходили всем подряд —
    снятые получали сообщение, которое им не предназначалось."""
    for uid in (1, 2, 3):
        _человек(uid)
    ответ = await пульт.post('/api/send', json={
        'initData': подпись(), 'scope': 'all', 'except': [2], 'text': u'Эфир'})
    assert (await ответ.json())['count'] == 2
    await _дождаться_рассылки()
    получили = [чат for вид, чат, _ in пульт.bot.sent if вид == 'copy']
    assert 2 not in получили and 1 in получили and 3 in получили
