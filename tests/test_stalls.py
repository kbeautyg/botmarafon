# -*- coding: utf-8 -*-
u"""Где человек мог застрять без блокировки — и что /кто пишет о каждом.

Ревью 11.09.2026 по просьбе AleX («напротив каждого — до какого шага
дошёл, где остановился, заблокировал ли бота»). Кроме блокировки, человек
застревал от запоздалого нажатия на вопрос, сбоя приветствия, запрета
кружков в настройках Telegram и сбоя связи дольше двух минут. Здесь каждая
из этих дыр и подписи /кто, которые не выдают догадку за факт.
"""
import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest
from aiogram.exceptions import (ClientDecodeError, TelegramBadRequest, TelegramEntityTooLarge,
                                TelegramNetworkError)
from aiogram.types import FSInputFile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, delivery, funnel, insights, scheduler, stats, texts   # noqa: E402
from bot.handlers import poll, start                                               # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser                   # noqa: E402

NOW = time.time()


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'stalls.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', ())
    monkeypatch.setattr(config, 'STATS_IDS', ())
    delivery._NO_VIDEO_NOTES.clear()
    start._launching.clear()
    yield
    delivery._NO_VIDEO_NOTES.clear()
    start._launching.clear()


def _человек(uid, launched=True, ago=3600):
    db.remember_user(uid, 'nick%d' % uid, u'Человек', '')
    db._run('UPDATE users SET started_at=? WHERE user_id=?', (NOW - ago, uid))
    if launched:
        db.mark_launched(uid)


def _созрело(uid):
    for job in db.user_jobs(uid):
        db._run('UPDATE jobs SET run_at=? WHERE id=?', (time.time() - 1, job['id']))


class НетКружков(FakeBot):
    u"""Человек закрыл себе кружки: Telegram отвечает VOICE_MESSAGES_FORBIDDEN."""

    async def send_video_note(self, chat_id, video, **kw):
        raise TelegramBadRequest(method=None, message=u'Bad Request: VOICE_MESSAGES_FORBIDDEN')


class СвязиНет(FakeBot):
    async def _record(self, kind, chat_id, payload=None):
        raise TelegramNetworkError(method=None, message=u'нет связи с Telegram')


# ------------------------------------------------------------ застревания

async def test_запоздалое_нажатие_на_вопрос_не_оставляет_без_следующего_дня():
    _человек(1)
    db.set_poll(1, 'day1')
    db.add_job(1, 'day1_no', 0, NOW + 43200)
    call = FakeCall('poll:day1:yes', user=FakeUser(1))

    async def устарело(*args, **kw):
        raise TelegramBadRequest(method=None, message=u'Bad Request: query is too old')
    call.answer = устарело

    await poll.on_answer(call)
    assert {j['chain'] for j in db.user_jobs(1)} == {'day1_yes'}
    assert call.edited                                       # вопрос переписан, как раньше


async def test_сбой_приветствия_не_оставляет_без_марафона():
    сообщение = FakeMessage(text='/start', user=FakeUser(2))

    async def падает(*args, **kw):
        raise TelegramNetworkError(method=None, message=u'нет связи')
    сообщение.answer = падает

    with pytest.raises(TelegramNetworkError):
        await start.on_start(сообщение)
    assert 'launch' in db.pending_chains(2)


async def test_повторный_старт_без_очереди_не_обещает_расписание():
    await start.on_start(FakeMessage(text='/start', user=FakeUser(3)))
    идёт = FakeMessage(text='/start', user=FakeUser(3))
    await start.on_start(идёт)
    assert идёт.answers[-1] == texts.ALREADY_RUNNING

    db.drop_chains(3, tuple(funnel.CHAINS))                  # прошёл или прервался
    вернулся = FakeMessage(text='/start', user=FakeUser(3))
    await start.on_start(вернулся)
    assert вернулся.answers[-1] == texts.NOTHING_SCHEDULED


async def test_запретил_кружки_получает_их_видео_и_идёт_дальше():
    _человек(4)
    bot = НетКружков()
    job = {'id': 0, 'user_id': 4, 'chain': 'launch', 'pos': 0, 'run_at': NOW, 'tries': 0}
    await scheduler.run_job(bot, job)
    assert [kind for kind, _, _ in bot.sent] == ['video']
    assert [(j['chain'], j['pos']) for j in db.user_jobs(4)] == [('launch', 1)]


async def test_отзыв_кружок_тому_же_человеку_уходит_видео():
    кружки = [n for n in funnel.REVIEW_SEQUENCE
              if (delivery.review_file(n) or ('',))[0] == 'circle']
    if not кружки:
        pytest.skip(u'в media/reviews нет отзыва-кружка')
    bot = НетКружков()
    await delivery.send_review(bot, 5, кружки[0])
    assert bot.sent and bot.sent[-1][0] == 'video'


async def test_сбой_связи_не_роняет_шаг_за_две_минуты():
    _человек(6)
    scheduler.start_chain(6, 'launch')
    bot = СвязиНет()
    for _ in range(5):
        _созрело(6)
        await scheduler.tick(bot)
    очередь = db.user_jobs(6)
    assert len(очередь) == 1 and очередь[0]['tries'] == 5     # раньше — снят после 3-й
    assert очередь[0]['run_at'] - time.time() > 10 * 60       # пауза растёт


async def test_обычная_ошибка_как_раньше_снимается_после_трёх_попыток():
    _человек(7)
    scheduler.start_chain(7, 'launch')
    bot = FakeBot(fail_times=10)
    for _ in range(3):
        _созрело(7)
        await scheduler.tick(bot)
    assert db.user_jobs(7) == []


# ------------------------------------------------------------------ /кто

def _строка(текст, uid):
    строки = текст.split(u'\n')
    i = next(i for i, s in enumerate(строки) if u'@nick%d ·' % uid in s)
    return строки[i + 1]


def test_кто_честно_про_блок_молчание_и_начавших_до_учёта():
    _человек(21)
    db.log_event(21, 'day', 2)
    db.mark_blocked(21)
    _человек(22)
    db.log_event(22, 'day', 1)
    db.set_poll(22, 'day1')
    db.add_job(22, 'day1_no', 0, NOW + 3600)
    _человек(23, ago=3 * 86400)
    db._run('UPDATE users SET launched_at=? WHERE user_id=?', (insights.TRACK_TS - 3600, 23))
    _человек(24)
    for day in (1, 2, 3, 4):
        db.log_event(24, 'day', day)
    db.log_event(24, 'offer', 'offer')

    текст = u'\n'.join(stats.who_messages(30))
    assert u'сейчас: заблокировал бота (бот заметил' in _строка(текст, 21)
    assert u'2-й день придёт сам' in _строка(текст, 22)
    assert u'пройдено: запуск или дальше' in _строка(текст, 23)
    assert stats.IDLE_BEFORE_TRACKING in _строка(текст, 23)
    assert u'«купить» не нажал' in _строка(текст, 24)
    assert текст.count(stats.WHO_FOOTER) == 1


def test_начавший_после_учёта_и_вставший_прервался_без_оговорок():
    _человек(25)
    db.log_event(25, 'day', 1)                               # очередь пуста, не блок
    строка = _строка(u'\n'.join(stats.who_messages(30)), 25)
    assert u'пройдено: день 1 ·' in строка and u'или дальше' not in строка
    assert stats.PLACE['idle'] in строка


# ------------------------------------------------- второе ревью 11.09.2026

class ШлюзЛежит(FakeBot):
    u"""Шлюз Telegram отдаёт HTML-страницу 502 вместо ответа."""

    async def _record(self, kind, chat_id, payload=None):
        raise ClientDecodeError('Failed to decode object', ValueError('not json'),
                                u'<html><title>502 Bad Gateway</title></html>')


class ФайлВелик(FakeBot):
    async def _record(self, kind, chat_id, payload=None):
        raise TelegramEntityTooLarge(method=None, message=u'Request Entity Too Large')


class ВидеоВисит(FakeBot):
    u"""Telegram жив (админу пишется), а загрузка кружка всё время по таймауту."""

    async def send_video_note(self, chat_id, video, **kw):
        raise TelegramNetworkError(method=None, message=u'Request timeout error')


def _последняя_попытка(uid):
    job = db.user_jobs(uid)[0]
    db._run('UPDATE jobs SET tries=?, run_at=? WHERE id=?',
            (scheduler.TRANSIENT_TRIES - 1, time.time() - 1, job['id']))


async def test_страница_шлюза_вместо_ответа_считается_сбоем_связи():
    _человек(12)
    scheduler.start_chain(12, 'launch')
    bot = ШлюзЛежит()
    for _ in range(5):
        _созрело(12)
        await scheduler.tick(bot)
    очередь = db.user_jobs(12)
    assert len(очередь) == 1 and очередь[0]['tries'] == 5


def test_неразобранный_json_не_считается_сбоем_связи():
    u"""Там сообщение могло и дойти — повторять часами значит слать дубли."""
    ошибка = ClientDecodeError('Failed to deserialize object', ValueError('x'), {'ok': True})
    assert not delivery.is_transient(ошибка)


async def test_слишком_большой_файл_не_ждёт_часами():
    _человек(13)
    scheduler.start_chain(13, 'launch')
    bot = ФайлВелик()
    for _ in range(3):
        _созрело(13)
        await scheduler.tick(bot)
    assert db.user_jobs(13) == []


async def test_после_всех_попыток_шаг_ждёт_пока_админам_не_сказать(monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', (999,))
    _человек(10)
    scheduler.start_chain(10, 'launch')
    _последняя_попытка(10)
    await scheduler.tick(СвязиНет())
    очередь = db.user_jobs(10)
    assert len(очередь) == 1                                   # не снят молча
    assert очередь[0]['run_at'] - time.time() > scheduler.RETRY_CAP - 60


async def test_после_всех_попыток_шаг_снимается_когда_админам_сказали(monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', (999,))
    _человек(11)
    scheduler.start_chain(11, 'launch')
    _последняя_попытка(11)
    bot = ВидеоВисит()
    await scheduler.tick(bot)
    assert db.user_jobs(11) == []
    assert any(u'Не отправили шаг' in (payload or u'') for _, _, payload in bot.sent)


async def test_два_старта_подряд_второй_не_пугает_новичка():
    u"""Второй /start из той же пачки обновлений, пока уходит приветствие."""
    ворота = asyncio.Event()
    первое = FakeMessage(text='/start', user=FakeUser(8))
    настоящий = первое.answer

    async def медленно(text, **kw):
        await ворота.wait()
        return await настоящий(text, **kw)
    первое.answer = медленно

    задача = asyncio.create_task(start.on_start(первое))
    for _ in range(3):
        await asyncio.sleep(0)                                 # дошло до приветствия
    второе = FakeMessage(text='/start', user=FakeUser(8))
    await start.on_start(второе)
    ворота.set()
    await задача
    assert второе.answers[-1] == texts.ALREADY_RUNNING
    assert 'launch' in db.pending_chains(8)


async def test_вернулся_к_открытому_вопросу_получает_его_снова():
    u"""Закрывал бота на вопросе: ответ продолжит марафон, «заново» стёрло бы путь."""
    await start.on_start(FakeMessage(text='/start', user=FakeUser(9)))
    db.drop_chains(9, tuple(funnel.CHAINS))
    db.set_poll(9, 'day2')
    вернулся = FakeMessage(text='/start', user=FakeUser(9))
    await start.on_start(вернулся)
    assert вернулся.answers[-1] == texts.ALREADY_RUNNING
    assert [(j['chain'], j['pos']) for j in db.user_jobs(9)] == [('after_day2', 1)]


def test_ответ_без_следующего_дня_в_записях_не_засчитывает_день():
    u"""Вопрос записан, ответ есть, а 2-го дня в записях нет — ветка сорвалась."""
    _человек(26)
    db.log_event(26, 'day', 1)
    db.log_event(26, 'poll', 'day1')
    db.save_answer(26, 'day1', 'yes')
    p = insights.model()['people'][26]
    assert insights.FUNNEL[p['reached']][0] == 'day1'
    assert u'пройдено: день 1 ·' in _строка(u'\n'.join(stats.who_messages(30)), 26)


def test_день_4_без_кнопок_в_записях_не_считается_пройденным():
    _человек(27)
    for day in (1, 2, 3, 4):
        db.log_event(27, 'day', day)
    p = insights.model()['people'][27]
    assert insights.FUNNEL[p['reached']][0] == 'day4' and p['position'] == 'idle'


class КэшБот(FakeBot):
    u"""Отдаёт file_id, как настоящий Telegram; FORBIDDEN закрыли себе кружки."""
    FORBIDDEN = {52, 53}

    def __init__(self):
        super().__init__()
        self.calls = []
        self.n = 0

    def _media(self, media):
        return 'upload' if isinstance(media, FSInputFile) else media

    async def send_video_note(self, chat_id, video, **kw):
        self.calls.append(('note', chat_id, self._media(video)))
        if chat_id in self.FORBIDDEN:
            raise TelegramBadRequest(method=None, message=u'Bad Request: VOICE_MESSAGES_FORBIDDEN')
        self.n += 1
        return SimpleNamespace(video_note=SimpleNamespace(file_id='NOTE%d' % self.n), video=None)

    async def send_video(self, chat_id, video, **kw):
        self.calls.append(('video', chat_id, self._media(video)))
        self.n += 1
        return SimpleNamespace(video=SimpleNamespace(file_id='VID%d' % self.n), video_note=None)


async def test_кэш_кружка_и_его_видео_не_путаются():
    bot = КэшБот()
    for uid in (51, 52, 53, 51):
        await delivery.send_circle(bot, uid, 'welcome_1')
    assert bot.calls == [('note', 51, 'upload'),
                         ('note', 52, 'NOTE1'), ('video', 52, 'upload'),
                         ('note', 53, 'NOTE1'), ('video', 53, 'VID2'),
                         ('note', 51, 'NOTE1')]
    assert tuple(db.get_content('circle:welcome_1')) == ('circle', 'NOTE1')
    assert tuple(db.get_content('circlevideo:welcome_1')) == ('video', 'VID2')

    bot.calls.clear()                                          # отказавшемуся — сразу видео
    await delivery.send_circle(bot, 52, 'welcome_1')
    assert bot.calls == [('video', 52, 'VID2')]


async def test_кэш_отзыва_кружка_и_его_видео_не_путаются():
    кружки = [n for n in funnel.REVIEW_SEQUENCE
              if (delivery.review_file(n) or ('',))[0] == 'circle']
    if not кружки:
        pytest.skip(u'в media/reviews нет отзыва-кружка')
    name = кружки[0]
    size = os.path.getsize(delivery.review_file(name)[1])
    bot = КэшБот()
    for uid in (51, 52, 53):
        await delivery.send_review(bot, uid, name)
    assert tuple(db.get_content('review:%s:%d' % (name, size))) == ('circle', 'NOTE1')
    assert tuple(db.get_content('reviewvideo:%s:%d' % (name, size))) == ('video', 'VID2')
    assert bot.calls[-1] == ('video', 53, 'VID2')


# ------------------------------------------------ третье ревью 11.09.2026

class АдминБезРазметки(FakeBot):
    u"""Telegram отвергает разметку в тексте админу, простой текст принимает."""

    def __init__(self):
        super().__init__()
        self.admin = []

    async def send_message(self, chat_id, text, **kw):
        if kw.get('parse_mode', 'HTML') is not None and u'<html' in text:
            raise TelegramBadRequest(method=None, message=u"Bad Request: can't parse entities")
        self.admin.append((text, kw.get('parse_mode', 'HTML')))
        return await self._record('text', chat_id, text)


class ШлюзЛежитДляЧеловека(АдминБезРазметки):
    u"""Человеку шлюз отдаёт HTML-страницу 502, админу сообщения доходят."""

    async def send_video_note(self, chat_id, video, **kw):
        raise ClientDecodeError('Failed to decode object', ValueError('not json'),
                                u'<html><title>502 Bad Gateway</title></html>')


async def test_предупреждение_с_разметкой_уходит_админу_простым_текстом(monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', (999,))
    bot = АдминБезРазметки()
    assert await delivery.alert_admins(bot, u'сбой: <html>502</html>') is True
    assert bot.admin == [(u'сбой: <html>502</html>', None)]


async def test_шаг_снимается_только_когда_админ_правда_получил_предупреждение(monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', (999,))
    _человек(14)
    scheduler.start_chain(14, 'launch')
    _последняя_попытка(14)
    bot = ШлюзЛежитДляЧеловека()
    await scheduler.tick(bot)
    assert db.user_jobs(14) == []
    assert len(bot.admin) == 1 and u'&lt;html&gt;' in bot.admin[0][0]     # экранировано, дошло


async def test_старая_кнопка_пока_вопрос_ждёт_в_очереди_не_задваивает_марафон():
    await start.on_start(FakeMessage(text='/start', user=FakeUser(15)))
    db.drop_chains(15, tuple(funnel.CHAINS))
    db.set_poll(15, 'day2')                                   # закрывал бота на вопросе
    await start.on_start(FakeMessage(text='/start', user=FakeUser(15)))  # вопрос снова в очереди
    await poll.on_answer(FakeCall('poll:day2:yes', user=FakeUser(15)))  # ответ старой кнопкой
    _созрело(15)
    await scheduler.tick(FakeBot())
    цепочки = {j['chain'] for j in db.user_jobs(15)}
    assert 'after_day2' not in цепочки and 'day2_no' not in цепочки
    assert db.get_user(15)['poll'] is None


def test_ответ_во_время_отправки_вопроса_не_ставит_добивание():
    _человек(16)
    db.save_answer(16, 'day1', 'yes')                          # ответ пришёл, пока вопрос уходил
    scheduler._plan_next(16, 'after_day1', 1, funnel.step_at('after_day1', 1))
    assert db.user_jobs(16) == []


async def test_отвеченный_вопрос_не_запускает_вторую_ветку():
    _человек(18)
    db.save_answer(18, 'day1', 'yes')
    db.set_poll(18, 'day1')                                    # вопрос снова открыт гонкой
    call = FakeCall('poll:day1:no', user=FakeUser(18))
    await poll.on_answer(call)
    assert u'Этот вопрос уже закрыт' in call.answers
    assert db.user_jobs(18) == []


async def test_сбой_постановки_марафона_не_оставляет_ложного_обещания(monkeypatch):
    import sqlite3

    настоящий = scheduler.start_chain

    def занято(*args, **kw):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(scheduler, 'start_chain', занято)
    with pytest.raises(sqlite3.OperationalError):
        await start.on_start(FakeMessage(text='/start', user=FakeUser(17)))
    assert 17 not in start._launching

    monkeypatch.setattr(scheduler, 'start_chain', настоящий)
    второй = FakeMessage(text='/start', user=FakeUser(17))
    await start.on_start(второй)
    assert второй.answers[-1] == texts.NOTHING_SCHEDULED
