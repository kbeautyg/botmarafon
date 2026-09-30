# -*- coding: utf-8 -*-
u"""После марафона: записи закрываются, «пройти заново» — нет.

AleX 29.09.2026: «всем, кто посмотрел четвёртый день и им пришла кнопка
купить, но они не сделали свой выбор, — чтобы через 3 дня видео четырёх
дней марафона удалялись и приходило как баннер» (с кнопкой «купить» и
ссылкой на сайт энергозала), и «тем, кто четвёртый шаг уже запускал,
перезапустить бота чтобы нельзя было». Для всех, кроме админов и
разработчиков.
"""
import os
import sys
import time

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import FSInputFile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import backup, closing, config, db, delivery, texts                  # noqa: E402
from bot.handlers import admin, purchase, start                               # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser, FakeVideo   # noqa: E402

ADMIN = 777
PAVEL = 312701042
ДЕНЬ = 24 * 3600


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'closing.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'PAY_URLS', {'gym': '', 'course': ''})
    monkeypatch.setattr(closing, 'PAUSE', 0)
    monkeypatch.setattr(closing, '_broken_told', 0.0)
    for day in (1, 2, 3, 4):
        db.put_content('day%d' % day, 'video', 'fileday%d' % day)
    yield


async def _прошёл(uid, давно=4 * ДЕНЬ):
    u"""Человек прошёл марафон: четвёртый день пришёл ему `давно` назад, кнопки
    покупки — через час после него. Записи уходят настоящей отправкой — так
    бот запоминает их сообщения."""
    db.remember_user(uid, 'u%d' % uid, u'Человек')
    db.mark_launched(uid)
    marathon = FakeBot()
    for day in (1, 2, 3, 4):
        await delivery.send_day(marathon, uid, day)
    db.save_answer(uid, 'day3', 'yes')
    db.log_event(uid, 'day', 4)
    db.log_event(uid, 'offer')
    day4 = time.time() - давно
    db._run("UPDATE events SET at=? WHERE user_id=? AND kind='day'", (day4, uid))
    db._run("UPDATE events SET at=? WHERE user_id=? AND kind='offer'", (day4 + 3600, uid))


def _включить(kind='video', file_id='banner-file'):
    db.put_content(closing.BANNER, kind, file_id)
    closing.switch(True)


def _кнопки(markup):
    return [b.callback_data or b.url for row in markup.inline_keyboard for b in row]


# ------------------------------------------------------------ закрытие

async def test_через_три_дня_приходит_баннер_и_записи_закрываются():
    await _прошёл(1)
    _включить()
    bot = FakeBot()

    assert await closing.run_once(bot) == 1

    # баннер — новым сообщением: кнопка «купить» и сайт энергозала
    assert bot.sent[0] == ('video', 1, 'banner-file')
    assert _кнопки(bot.keys_sent[0]) == ['buy:gym:banner', config.GYM_SITE_URL]
    # все четыре записи — картинкой «закрыта», кнопки «Да»/«Нет» сняты
    assert [(kind, chat) for kind, chat, *_ in bot.edits] == [('media', 1)] * 4
    assert all(keys is None for *_, keys in bot.edits)
    assert [media.caption for *_, media, _ in bot.edits] == [
        texts.DAY_CLOSED.format(day=d) for d in (1, 2, 3, 4)]
    assert db.open_day_messages(1) == []
    assert db.get_user(1)['closed_at'] is not None


async def test_72_часа_считаются_от_четвёртого_дня_а_не_от_кнопок():
    u"""Заказчик 30.09.2026: «после момента, как 4-й день пришёл, через 72 часа».
    Кнопки покупки — через час после дня, но ждать этот час не нужно."""
    await _прошёл(1, давно=72 * 3600 + 600)          # день 4 — 72 ч 10 мин назад
    _включить()
    assert await closing.run_once(FakeBot()) == 1


async def test_без_шага_четвёртого_дня_считаем_от_кнопок():
    u"""Шаг «день 4» пишется с 11.09.2026 — у кого его нет, есть кнопки покупки."""
    await _прошёл(1)
    db._run("DELETE FROM events WHERE user_id=1 AND kind='day'")
    _включить()
    assert await closing.run_once(FakeBot()) == 1


async def test_баннер_картинкой_уходит_картинкой():
    await _прошёл(1)
    _включить(kind='photo', file_id='banner-photo')
    bot = FakeBot()
    await closing.run_once(bot)
    assert bot.sent[0] == ('photo', 1, 'banner-photo')


async def test_раньше_трёх_дней_записи_на_месте():
    await _прошёл(1, давно=2 * ДЕНЬ)
    _включить()
    bot = FakeBot()
    assert await closing.run_once(bot) == 0
    assert bot.sent == [] and bot.edits == []


async def test_нажавшему_купить_записи_не_закрываем():
    u"""«Не сделали свой выбор» — не нажали «купить» после кнопок."""
    await _прошёл(1)
    db.add_purchase(1, 'gym')
    _включить()
    assert await closing.run_once(FakeBot()) == 0
    assert len(db.open_day_messages(1)) == 4


async def test_второй_круг_баннер_не_повторяет():
    await _прошёл(1)
    _включить()
    bot = FakeBot()
    await closing.run_once(bot)
    await closing.run_once(bot)
    assert [s for s in bot.sent if s[0] == 'video'] == [('video', 1, 'banner-file')]


async def test_пока_закрытие_не_включили_никому_ничего():
    u"""Включают кнопкой в /баннер: сразу после включения баннер уходит
    сотням людей, и без явного «да» писать им нельзя."""
    await _прошёл(1)
    db.put_content(closing.BANNER, 'video', 'banner-file')
    bot = FakeBot()
    assert await closing.run_once(bot) == 0
    closing.switch(True)
    db._run("DELETE FROM content WHERE key='banner'")
    assert await closing.run_once(bot) == 0
    assert bot.sent == [] and bot.edits == []


async def test_команду_проекта_не_трогаем():
    u"""«Кроме админов и разработчиков»."""
    await _прошёл(PAVEL)
    await _прошёл(ADMIN)
    _включить()
    bot = FakeBot()
    assert await closing.run_once(bot) == 0
    assert bot.sent == [] and bot.edits == []


async def test_закрывший_бота_получит_баннер_когда_вернётся():
    await _прошёл(1)
    _включить()
    assert await closing.run_once(FakeBot(forbidden=True)) == 0
    assert db.get_user(1)['blocked_at'] is not None
    assert db.get_user(1)['closed_at'] is None

    db.remember_user(1, 'u1', u'Человек')           # вернулся и нажал «Старт»
    bot = FakeBot()
    assert await closing.run_once(bot) == 1
    assert len(bot.edits) == 4


async def test_удалённое_человеком_сообщение_не_мешает():
    await _прошёл(1)
    first = db.open_day_messages(1)[0]['tg_id']

    class Удалил(FakeBot):
        async def edit_message_media(self, media, chat_id=None, message_id=None, **kw):
            if message_id == first:
                raise TelegramBadRequest(method=None, message=u'Bad Request: message to edit not found')
            return await super().edit_message_media(media, chat_id, message_id, **kw)

    _включить()
    bot = Удалил()
    assert await closing.run_once(bot) == 1
    assert len(bot.edits) == 3
    assert db.open_day_messages(1) == []


async def test_сбой_связи_дозакрывает_записи_без_второго_баннера():
    await _прошёл(1)

    class Сбоит(FakeBot):
        fails = 2

        async def edit_message_media(self, media, chat_id=None, message_id=None, **kw):
            if Сбоит.fails:
                Сбоит.fails -= 1
                raise TelegramNetworkError(method=None, message=u'нет связи')
            return await super().edit_message_media(media, chat_id, message_id, **kw)

    _включить()
    bot = Сбоит()
    await closing.run_once(bot)
    assert db.get_user(1)['closed_at'] is not None      # баннер ушёл
    assert len(db.open_day_messages(1)) == 4            # записи — ещё нет

    await closing.run_once(bot)
    assert db.open_day_messages(1) == []
    assert [s for s in bot.sent if s[0] == 'video'] == [('video', 1, 'banner-file')]


async def test_просьба_притормозить_переносит_остальных_на_следующий_круг():
    from aiogram.exceptions import TelegramRetryAfter
    await _прошёл(1)
    await _прошёл(2)

    class Притормози(FakeBot):
        async def send_video(self, chat_id, video, **kw):
            if chat_id == 1:
                raise TelegramRetryAfter(method=None, message=u'Too Many Requests', retry_after=3)
            return await super().send_video(chat_id, video, **kw)

    _включить()
    bot = Притормози()
    assert await closing.run_once(bot) == 0             # второму — следующим кругом
    assert db.get_user(1)['closed_at'] is None and db.get_user(2)['closed_at'] is None


async def test_запись_ссылкой_закрывается_текстом():
    db.put_content('day2', 'link', 'https://energy-sport-gum.ru/marathon/2?k=x')
    await _прошёл(1)
    _включить()
    bot = FakeBot()
    await closing.run_once(bot)
    texts_edited = [(kind, what) for kind, _, _, what, _ in bot.edits if kind == 'text']
    assert texts_edited == [('text', texts.DAY_CLOSED.format(day=2))]


async def test_в_тестовом_прогоне_три_дня_сжаты_как_весь_марафон():
    await _прошёл(1, давно=80 * 60)                     # 80 минут назад
    db.set_speed(1, 1.0 / 60)                           # как /test: три дня — 72 минуты
    _включить()
    assert await closing.run_once(FakeBot()) == 1


def test_картинка_закрытой_записи_есть_на_каждый_день():
    for day in (1, 2, 3, 4):
        assert os.path.exists(closing.picture(day)), day


async def test_картинка_уходит_файлом_один_раз():
    await _прошёл(1)
    await _прошёл(2)
    _включить()
    bot = FakeBot()
    await closing.run_once(bot)
    medias = [media.media for kind, *_, media, _ in bot.edits]
    assert isinstance(medias[0], FSInputFile)            # первый раз — файлом
    assert medias[4] == 'closed-photo-id'                 # дальше — по file_id


async def test_баннер_не_принят_записи_не_трогаем_админа_будим():
    await _прошёл(1)
    await _прошёл(2)

    class Битый(FakeBot):
        async def send_video(self, chat_id, video, **kw):
            raise TelegramBadRequest(method=None, message=u'Bad Request: wrong file identifier')

    _включить()
    bot = Битый()
    assert await closing.run_once(bot) == 0
    assert bot.edits == []
    assert db.get_user(1)['closed_at'] is None
    to_admin = [text for kind, chat, text in bot.sent if chat == ADMIN]
    assert len(to_admin) == 1 and u'wrong file identifier' in to_admin[0]


async def test_баннер_гифкой_уходит_гифкой():
    await _прошёл(1)
    _включить(kind='animation', file_id='gif-banner')
    bot = FakeBot()
    await closing.run_once(bot)
    assert bot.sent[0] == ('animation', 1, 'gif-banner')


async def test_баннер_виден_в_переписке_пульта():
    await _прошёл(1)
    _включить()
    await closing.run_once(FakeBot())
    [line] = db.chat_history(1)
    assert (line['side'], line['text'], line['mass']) == ('out', texts.BANNER_CAPTION, 1)


# ------------------------------------------- какие сообщения бот помнит

async def test_запись_дня_запоминает_своё_сообщение():
    bot = FakeBot()
    sent = await delivery.send_day(bot, 5, 2)
    assert [(r['day'], r['tg_id'], r['kind']) for r in db.open_day_messages(5)] == [
        (2, sent.message_id, 'video')]


async def test_досланная_запись_тоже_запоминается():
    await delivery.resend_day(FakeBot(), 5, 3)
    assert [r['day'] for r in db.open_day_messages(5)] == [3]


async def test_день_без_записи_запоминать_нечего():
    db._run("DELETE FROM content WHERE key='day1'")
    await delivery.send_day(FakeBot(), 5, 1)
    assert db.open_day_messages(5) == []


async def test_resend_всем_не_возвращает_закрытые_записи():
    await _прошёл(1)
    await _прошёл(2)
    db.mark_closed(1)
    message = FakeMessage(text='/resend 2 всем', user=FakeUser(ADMIN))
    await admin.on_resend(message)
    got = {chat for kind, chat, _ in message.bot.sent if kind == 'video'}
    assert got == {2}


# --------------------------------------------------- «пройти заново»

async def test_после_четвёртого_дня_заново_не_начать():
    await _прошёл(1)
    jobs = db.user_jobs(1)
    message = FakeMessage(text='/start')
    await start.on_start(message)

    assert message.answers == [texts.MARATHON_PASSED]
    assert _кнопки(message.markups[0]) == ['buy:gym:again', config.GYM_SITE_URL]
    assert db.user_jobs(1) == jobs


async def test_старая_кнопка_заново_ничего_не_сбрасывает():
    await _прошёл(1)
    call = FakeCall('restart')
    await start.on_restart(call)

    assert db.answered(1, 'day3')                       # путь цел
    assert 'launch' not in db.pending_chains(1)
    assert call.answers == [texts.RESTART_CLOSED]
    assert call.message.answers == [texts.MARATHON_PASSED]
    assert call.markup_cleared


async def test_до_четвёртого_дня_заново_можно():
    await start.on_start(FakeMessage(text='/start'))
    db.save_answer(1, 'day1', 'yes')
    db.save_answer(1, 'day2', 'no')
    call = FakeCall('restart')
    await start.on_restart(call)
    assert call.message.answers == [texts.RESTART_DONE]
    assert 'launch' in db.pending_chains(1)


async def test_четвёртый_день_в_очереди_уже_считается():
    u"""Ответ на вопрос после третьего дня ставит четвёртый через секунды;
    до 17.09.2026 его ставил и таймер без ответа."""
    db.remember_user(1, 'u1', u'Человек')
    db.mark_launched(1)
    db.add_job(1, 'day3_no', 0, time.time() + 60)
    assert db.passed_marathon(1)
    assert not db.passed_marathon(2)


async def test_команде_заново_можно():
    u"""«Кроме админов и разработчиков»: команде марафон нужен для проверок."""
    await _прошёл(ADMIN)
    call = FakeCall('restart', user=FakeUser(ADMIN))
    await start.on_restart(call)
    assert call.message.answers == [texts.RESTART_DONE]


# ----------------------------------------------------- покупка и админ

async def test_покупка_под_баннером_помечена_в_заявке():
    db.remember_user(1, 'u1', u'Человек')
    bot = FakeBot()
    await purchase.on_buy(FakeCall('buy:gym:banner', bot=bot))
    notes = [text for kind, chat, text in bot.sent if u'Заявка №' in (text or u'')]
    assert notes and all(purchase.PLACES['banner'] in note for note in notes)
    assert db.purchase_presses(1, 'gym')


async def test_кнопка_в_просмотре_баннера_не_заявка():
    bot = FakeBot()
    call = FakeCall('buy:gym:preview', bot=bot)
    await purchase.on_buy(call)
    assert db.purchase_presses(1, 'gym') == []
    assert not [s for s in bot.sent if u'Заявка' in (s[2] or u'')]


async def test_баннер_от_админа_записывается_и_показывается():
    bot = FakeBot()
    message = FakeMessage(caption='banner', user=FakeUser(ADMIN), video=FakeVideo('bn1'), bot=bot)
    await admin.on_banner(message)

    assert db.get_content(closing.BANNER) == ('video', 'bn1')
    assert 'banner video bn1' in backup.dump()
    kinds = [kind for kind, chat, _ in bot.sent if chat == ADMIN]
    assert kinds[-3:] == ['photo', 'video', 'text']      # закрытая запись, баннер, состояние
    assert _кнопки(bot.keys_sent[-1]) == ['cl:on']
    assert _кнопки(bot.keys_sent[-2]) == ['buy:gym:preview', config.GYM_SITE_URL]


async def test_баннер_от_не_админа_не_записывается():
    message = FakeMessage(caption='banner', user=FakeUser(555), video=FakeVideo('x'))
    await admin.on_banner(message)
    assert db.get_content(closing.BANNER) is None


async def test_включает_закрытие_только_админ_и_только_с_баннером():
    await admin.on_closing_switch(FakeCall('cl:on', user=FakeUser(ADMIN)))
    assert not closing.enabled()                         # баннера нет
    db.put_content(closing.BANNER, 'video', 'bn1')
    await admin.on_closing_switch(FakeCall('cl:on', user=FakeUser(555)))
    assert not closing.enabled()                         # не админ
    await admin.on_closing_switch(FakeCall('cl:on', user=FakeUser(ADMIN)))
    assert closing.enabled()
    await admin.on_closing_switch(FakeCall('cl:off', user=FakeUser(ADMIN)))
    assert not closing.enabled()


async def test_status_говорит_про_баннер():
    message = FakeMessage(text='/status', user=FakeUser(ADMIN))
    await admin.on_status(message)
    assert u'Баннер после марафона — <b>НЕ ЗАДАН</b>' in message.answers[0]


def _видео(sender, caption, n=1):
    return {'update_id': n, 'message': {
        'message_id': n, 'date': 1_760_000_000, 'caption': caption,
        'chat': {'id': sender, 'type': 'private'},
        'from': {'id': sender, 'is_bot': False, 'first_name': 'A'},
        'video': {'file_id': 'vid-%s' % caption, 'file_unique_id': 'u%d' % n,
                  'width': 720, 'height': 900, 'duration': 12}}}


async def test_маршрут_баннер_не_путается_с_записью_дня(dispatcher):
    u"""Обработчик записей дней ловит любое видео админа — баннер должен
    попасть к своему, а запись дня — по-прежнему к своему."""
    from aiogram import Bot
    from tests.fakes import ЗаписьСессия

    await dispatcher.feed_raw_update(Bot('42:TEST', session=ЗаписьСессия()),
                                     _видео(ADMIN, 'banner', 1))
    assert db.get_content(closing.BANNER) == ('video', 'vid-banner')
    assert db.get_content('day2') == ('video', 'fileday2')

    await dispatcher.feed_raw_update(Bot('42:TEST', session=ЗаписьСессия()),
                                     _видео(ADMIN, 'day2', 2))
    assert db.get_content('day2') == ('video', 'vid-day2')
    assert db.get_content(closing.BANNER) == ('video', 'vid-banner')


async def test_backup_присылает_базу_файлом():
    db.remember_user(5, 'nick', u'Человек')
    message = FakeMessage(text='/backup', user=FakeUser(ADMIN))
    await admin.on_backup(message)
    [(kind, chat, name)] = message.bot.sent
    assert (kind, chat) == ('document', ADMIN) and name.endswith('.db')
