# -*- coding: utf-8 -*-
u"""Чёрный список (AleX 16.09.2026).

«В одно касание в чёрный список, с авто удалением из бота и из всех
каналов и чатов Павла; в статистику — ник, дата добавления и удаления, с
возможностью убрать». Проверяем то, что увидит команда и чего не увидит
человек из списка.
"""
import csv
import io
import os
import sys
import time

import pytest
from aiogram import Bot

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import blacklist, config, db, insights, keyboards, nudge, scheduler, texts  # noqa: E402
from bot.handlers import blacklist as ban_handlers                        # noqa: E402
from bot.handlers import purchase, start, support                         # noqa: E402
from tests.fakes import (FakeBot, FakeCall, FakeMessage, FakeUser,        # noqa: E402
                         ЗаписьСессия)

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
TROLL, OTHER = 424242, 515151
CHANNEL, GROUP = -1001111111111, -1002222222222


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'blacklist.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'STATS_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    start._launching.clear()
    db.remember_user(TROLL, 'troll_boy', u'Тролль')
    yield


def _чаты():
    db.chat_seen(CHANNEL, u'Канал Павла', 'channel', True)
    db.chat_seen(GROUP, u'Чат марафона', 'supergroup', True)


def _кнопки(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


class Уведомление(FakeCall):
    u"""Нажатие под уведомлением: запоминает, какие кнопки стали под ним."""

    def __init__(self, data, user_id=ALEX, bot=None):
        super().__init__(data, user=FakeUser(user_id, 'alex', u'AleX KENT'), bot=bot or FakeBot())
        self.markups = []
        call = self

        async def edit_reply_markup(reply_markup=None, **kw):
            call.markups.append(reply_markup)

        self.message.edit_reply_markup = edit_reply_markup


# ------------------------------------------------------------ кнопка под уведомлением

async def test_кнопка_спрашивает_подтверждение_и_не_банит_сразу():
    call = Уведомление('bl:ask:%d' % TROLL)
    await ban_handlers.on_ban_button(call)
    assert _кнопки(call.markups[-1]) == ['bl:yes:%d' % TROLL, 'bl:no:%d' % TROLL]
    assert not db.is_banned(TROLL)


async def test_отмена_возвращает_кнопку():
    call = Уведомление('bl:no:%d' % TROLL)
    await ban_handlers.on_ban_button(call)
    assert _кнопки(call.markups[-1]) == ['bl:ask:%d' % TROLL]
    assert not db.is_banned(TROLL)


async def test_да_банит_в_боте_и_во_всех_чатах_павла():
    _чаты()
    db.add_job(TROLL, 'launch', 3, time.time() + 3600)
    db.set_poll(TROLL, 'day1')
    bot = FakeBot()
    call = Уведомление('bl:yes:%d' % TROLL, bot=bot)
    await ban_handlers.on_ban_button(call)

    assert db.is_banned(TROLL)
    assert db.pending_chains(TROLL) == set()                   # марафон снят
    assert db.get_user(TROLL)['poll'] is None
    assert ('ban', CHANNEL, TROLL) in bot.sent and ('ban', GROUP, TROLL) in bot.sent
    отчёт = call.message.replies[-1]
    assert u'В чёрном списке' in отчёт and u'troll_boy' in отчёт and u'AleX KENT' in отчёт
    assert u'2 из 2' in отчёт
    assert _кнопки(call.markups[-1]) == ['bl:undo:%d' % TROLL]
    запись = db.ban_entry(TROLL)
    assert запись['added_by'] == u'AleX KENT (@alex)' and запись['removed_at'] is None


async def test_убрать_снимает_баны_и_история_остаётся():
    _чаты()
    bot = FakeBot()
    await ban_handlers.on_ban_button(Уведомление('bl:yes:%d' % TROLL, bot=bot))
    call = Уведомление('bl:undo:%d' % TROLL, user_id=PAVEL, bot=bot)
    await ban_handlers.on_ban_button(call)

    assert not db.is_banned(TROLL)
    assert ('unban', CHANNEL, (TROLL, True)) in bot.sent
    assert u'Убран из чёрного списка' in call.message.replies[-1]
    запись = db.ban_entry(TROLL)
    assert запись['removed_at'] is not None and запись['added_at'] is not None
    assert _кнопки(call.markups[-1]) == ['bl:ask:%d' % TROLL]


async def test_без_чатов_честно_говорит_что_банит_только_в_боте():
    call = Уведомление('bl:yes:%d' % TROLL)
    await ban_handlers.on_ban_button(call)
    assert db.is_banned(TROLL)
    assert texts.BAN_NO_CHATS in call.message.replies[-1]


async def test_сбой_в_одном_чате_виден_в_отчёте_остальные_забанены():
    _чаты()

    class Упрямый(FakeBot):
        async def ban_chat_member(self, chat_id, user_id, **kw):
            if chat_id == GROUP:
                raise RuntimeError('not enough rights to restrict/unrestrict chat member')
            return await super().ban_chat_member(chat_id, user_id)

    bot = Упрямый()
    call = Уведомление('bl:yes:%d' % TROLL, bot=bot)
    await ban_handlers.on_ban_button(call)
    отчёт = call.message.replies[-1]
    assert u'1 из 2' in отчёт and u'Не вышло' in отчёт and u'not enough rights' in отчёт
    assert db.is_banned(TROLL)


async def test_повторное_нажатие_не_дублирует_и_показывает_кто_добавил():
    await ban_handlers.on_ban_button(Уведомление('bl:yes:%d' % TROLL))
    ещё = Уведомление('bl:ask:%d' % TROLL, user_id=PAVEL)
    await ban_handlers.on_ban_button(ещё)
    assert _кнопки(ещё.markups[-1]) == ['bl:undo:%d' % TROLL]
    new, report = await blacklist.add(FakeBot(), TROLL, FakeUser(PAVEL))
    assert not new and u'Уже в чёрном списке' in report and u'AleX' in report


async def test_команду_проекта_в_список_не_внести():
    call = Уведомление('bl:yes:%d' % PAVEL)
    await ban_handlers.on_ban_button(call)
    assert not db.is_banned(PAVEL)
    assert call.message.replies[-1] == texts.BAN_TEAM


async def test_чужой_нажать_кнопку_не_может():
    call = Уведомление('bl:yes:%d' % TROLL, user_id=OTHER)
    await ban_handlers.on_ban_button(call)
    assert call.answers[-1] == u'Нет доступа'
    assert not db.is_banned(TROLL)


# ------------------------------------------------------------ кнопка стоит под уведомлениями

class Почта(FakeBot):
    def __init__(self):
        super().__init__()
        self.markups = {}

    async def send_message(self, chat_id, text, **kw):
        sent = await super().send_message(chat_id, text)
        self.markups.setdefault(chat_id, []).append(kw.get('reply_markup'))
        return sent


async def test_кнопка_есть_под_запуском_сообщением_и_заявкой():
    bot = Почта()
    await start.on_start(FakeMessage(text='/start ig', user=FakeUser(OTHER, 'x', u'Икс'), bot=bot))
    await support.to_support(FakeMessage(text=u'вопрос', user=FakeUser(OTHER, 'x', u'Икс'), bot=bot))
    await purchase.on_buy(FakeCall('buy:gym', user=FakeUser(OTHER, 'x', u'Икс'), bot=bot))
    у_алекса = [_кнопки(m) for m in bot.markups[ALEX]]
    assert у_алекса.count(['bl:ask:%d' % OTHER]) == 3


# ------------------------------------------------------------ команды

async def test_команда_по_нику_и_по_id_незнакомого_боту():
    _чаты()
    bot = FakeBot()
    по_нику = FakeMessage(text=u'/чс @Troll_Boy', user=FakeUser(ALEX), bot=bot)
    await ban_handlers.on_ban_command(по_нику, _args(u'@Troll_Boy'))
    assert db.is_banned(TROLL)
    # человек из канала, в бота не заходил — по ID банится в чатах Павла
    по_id = FakeMessage(text=u'/чс 999000111', user=FakeUser(ALEX), bot=bot)
    await ban_handlers.on_ban_command(по_id, _args(u'999000111'))
    assert db.is_banned(999000111) and ('ban', CHANNEL, 999000111) in bot.sent


async def test_команда_без_аргумента_показывает_список_с_историей():
    await blacklist.add(FakeBot(), TROLL, FakeUser(ALEX, 'alex', u'AleX'))
    await blacklist.remove(FakeBot(), TROLL, FakeUser(PAVEL, 'pavel', u'Павел'))
    await blacklist.add(FakeBot(), OTHER, FakeUser(ALEX, 'alex', u'AleX'))
    msg = FakeMessage(text=u'/чс', user=FakeUser(ALEX))
    await ban_handlers.on_ban_command(msg, _args(u''))
    text = msg.answers[-1]
    assert u'Сейчас в списке: <b>1</b>' in text
    assert u'Убраны из списка' in text and u'troll_boy' in text and u'Павел' in text
    assert u'/разбан' in text


async def test_разбан_и_незнакомый_ник():
    await blacklist.add(FakeBot(), TROLL, FakeUser(ALEX))
    msg = FakeMessage(text=u'/разбан 424242', user=FakeUser(PAVEL))
    await ban_handlers.on_unban_command(msg, _args(u'424242'))
    assert not db.is_banned(TROLL)
    нет = FakeMessage(text=u'/чс @nobody_here', user=FakeUser(PAVEL))
    await ban_handlers.on_ban_command(нет, _args(u'@nobody_here'))
    assert u'Не нашёл в боте' in нет.answers[-1]


async def test_разбан_возвращает_человека_на_его_вопрос():
    u"""Кнопка «в чёрный список» стоит в одно касание под каждым
    уведомлением о входе — промахнуться мимо «Ответить» легко. Бан снимает
    всю очередь и закрывает вопрос; до 20.09.2026 разбан возвращал только
    чаты, и в боте у человека оставались мёртвые кнопки под записью и один
    путь дальше — начать марафон заново с первого дня."""
    db.log_event(TROLL, 'day', 2)
    db.set_poll(TROLL, 'day2')
    await blacklist.add(FakeBot(), TROLL, FakeUser(ALEX))
    assert db.get_user(TROLL)['poll'] is None

    ok, отчёт = await blacklist.remove(FakeBot(), TROLL, FakeUser(PAVEL))

    assert ok and u'Вернул на вопрос 2-го дня' in отчёт
    assert db.get_user(TROLL)['poll'] == 'day2'
    assert 'after_day2' in {j['chain'] for j in db.user_jobs(TROLL)}


async def test_разбан_прошедшего_марафон_на_вопрос_не_возвращает():
    u"""Он всё посмотрел и на всё ответил — возвращать некуда."""
    db.log_event(TROLL, 'day', 4)
    db.save_answer(TROLL, 'day3', 'yes')
    await blacklist.add(FakeBot(), TROLL, FakeUser(ALEX))

    ok, отчёт = await blacklist.remove(FakeBot(), TROLL, FakeUser(PAVEL))

    assert ok and u'Вернул на вопрос' not in отчёт
    assert db.get_user(TROLL)['poll'] is None


def _args(text):
    return type('Cmd', (), {'args': text})()


# ------------------------------------------------------------ человек из списка

async def test_планировщик_не_шлёт_шаг_тому_кто_в_списке():
    db.mark_launched(TROLL)
    db.add_job(TROLL, 'launch', 0, time.time() - 1)
    job = db.due_jobs()[0]
    db.ban_add(TROLL, 'troll_boy', u'Тролль', u'AleX')
    bot = FakeBot()
    await scheduler.run_job(bot, job)
    assert bot.sent == [] and db.due_jobs() == []


async def test_дожим_и_заявки_обходят_список():
    db.remember_user(OTHER, 'lead', u'Заявка', 'zayavka')
    db.ban_add(OTHER, 'lead', u'Заявка', u'AleX')
    assert all(l['user_id'] != OTHER for l in db.waiting_leads())
    nudge.since(1.0)
    db.mark_launched(TROLL)
    db._run('UPDATE users SET launched_at=? WHERE user_id=?', (time.time() - 6 * 3600, TROLL))
    db.log_event(TROLL, 'day', 1)
    assert TROLL in db.nudge_candidates(1.0, time.time())
    db.ban_add(TROLL, 'troll_boy', u'Тролль', u'AleX')
    assert TROLL not in db.nudge_candidates(1.0, time.time())


def test_статистика_где_сейчас_и_таблица():
    db.ban_add(TROLL, 'troll_boy', u'Тролль', u'AleX (@alex)')
    m = insights.model()
    assert m['people'][TROLL]['position'] == 'banned'
    assert u'Чёрный список' in insights.render('bl', '7')
    строки = list(csv.reader(io.StringIO(insights.csv_bytes().decode('utf-8-sig')), delimiter=';'))
    assert строки[0][-1] == u'чёрный список'
    [тролль] = [r for r in строки[1:] if r[0] == str(TROLL)]
    assert тролль[-1].startswith(u'с ') and u'AleX (@alex)' in тролль[-1]


# ------------------------------------------------------------ настоящий разбор aiogram

def _сообщение(sender, text, n=1):
    upd = {'update_id': n, 'message': {
        'message_id': n, 'date': 1_760_000_000, 'text': text,
        'chat': {'id': sender, 'type': 'private'},
        'from': {'id': sender, 'is_bot': False, 'first_name': 'X'}}}
    if text.startswith('/'):
        upd['message']['entities'] = [{'type': 'bot_command', 'offset': 0,
                                       'length': len(text.split()[0])}]
    return upd


async def _через_диспетчер(dispatcher, update):
    session = ЗаписьСессия()
    await dispatcher.feed_raw_update(Bot('42:TEST', session=session), update)
    return session.calls


async def test_человек_из_списка_не_доходит_ни_до_старта_ни_до_заботы(dispatcher):
    db.ban_add(TROLL, 'troll_boy', u'Тролль', u'AleX')
    assert await _через_диспетчер(dispatcher, _сообщение(TROLL, '/start')) == []
    assert await _через_диспетчер(dispatcher, _сообщение(TROLL, u'эй, ответьте', 2)) == []
    assert db.get_user(TROLL)['launched_at'] is None
    # нажатие старой кнопки: молча гасим, никуда не ведём
    calls = await _через_диспетчер(dispatcher, {'update_id': 3, 'callback_query': {
        'id': 'q1', 'chat_instance': 'c', 'data': 'buy:gym',
        'from': {'id': TROLL, 'is_bot': False, 'first_name': 'X'}}})
    assert [type(c).__name__ for c in calls] == ['AnswerCallbackQuery']


async def test_убранный_из_списка_снова_доходит(dispatcher):
    await blacklist.add(FakeBot(), TROLL, FakeUser(ALEX))
    await blacklist.remove(FakeBot(), TROLL, FakeUser(ALEX))
    calls = await _через_диспетчер(dispatcher, _сообщение(TROLL, '/start'))
    assert any(getattr(c, 'chat_id', None) == TROLL for c in calls)


def _бот_в_чате(status, can_restrict=True, chat=GROUP, kind='supergroup', by=PAVEL):
    member = {'user': {'id': 42, 'is_bot': True, 'first_name': 'bot'}, 'status': status}
    if status == 'administrator':
        member.update({'can_be_edited': False, 'is_anonymous': False, 'can_manage_chat': True,
                       'can_delete_messages': True, 'can_manage_video_chats': True,
                       'can_restrict_members': can_restrict, 'can_promote_members': False,
                       'can_change_info': False, 'can_invite_users': True,
                       'can_post_stories': False, 'can_edit_stories': False,
                       'can_delete_stories': False})
    return {'update_id': 9, 'my_chat_member': {
        'chat': {'id': chat, 'type': kind, 'title': u'Чат марафона'},
        'from': {'id': by, 'is_bot': False, 'first_name': u'Павел'},
        'date': 1_760_000_000,
        'old_chat_member': {'user': {'id': 42, 'is_bot': True, 'first_name': 'bot'}, 'status': 'left'},
        'new_chat_member': member}}


async def test_бота_сделали_админом_запомнил_чат_и_забанил_уже_внесённых(dispatcher):
    db.ban_add(TROLL, 'troll_boy', u'Тролль', u'AleX')
    calls = await _через_диспетчер(dispatcher, _бот_в_чате('administrator'))
    assert [c['chat_id'] for c in db.ban_chats()] == [GROUP]
    bans = [c for c in calls if type(c).__name__ == 'BanChatMember']
    assert [(c.chat_id, c.user_id) for c in bans] == [(GROUP, TROLL)]
    сказал = [c for c in calls if type(c).__name__ == 'SendMessage']
    assert сказал and сказал[0].chat_id == PAVEL and u'забанил сразу: 1' in сказал[0].text


async def test_без_права_блокировать_чат_не_банит_и_команде_сказано(dispatcher):
    calls = await _через_диспетчер(dispatcher, _бот_в_чате('administrator', can_restrict=False))
    assert db.ban_chats() == [] and len(db.known_chats()) == 1
    assert any(u'без права' in (getattr(c, 'text', '') or '') for c in calls)


async def test_бота_убрали_из_чата(dispatcher):
    await _через_диспетчер(dispatcher, _бот_в_чате('administrator'))
    await _через_диспетчер(dispatcher, _бот_в_чате('left'))
    assert db.ban_chats() == [] and db.known_chats() == []


# ------------------------------------------------- пачкой (AleX 18.09.2026)

async def test_список_одним_сообщением_вносит_всех_сразу():
    u"""AleX прислал семь строк и попросил «сразу, чтобы удобно и быстро»."""
    _чаты()
    db.remember_user(515152, 'olegkostiuc', u'Олег')
    bot = FakeBot()
    список = u'@Troll_Boy\n@olegkostiuc\n+7 925 585 4559\n@nikogo_takogo\n999000111'
    сообщение = FakeMessage(text=u'/чс ' + список, user=FakeUser(ALEX), bot=bot)

    await ban_handlers.on_ban_command(сообщение, _args(список))

    assert db.is_banned(TROLL) and db.is_banned(515152) and db.is_banned(999000111)
    отчёт = сообщение.answers[-1]
    assert u'Внесены (3)' in отчёт
    assert u'@nikogo_takogo' in отчёт and u'Не нашли в базе бота (1)' in отчёт
    assert u'+7 925 585 4559' in отчёт and u'телефона внести нельзя' in отчёт


async def test_номер_телефона_и_числовой_id_не_путаются():
    from bot.handlers.blacklist import PHONE, _refs

    разобрано = _refs(u'@nick\n+7 925 585 4559\n391182739')
    assert разобрано == ['@nick', '+7 925 585 4559', '391182739']
    assert [bool(PHONE.match(r)) for r in разобрано] == [False, True, False]


async def test_повторный_список_не_задваивает_и_говорит_что_уже_были():
    _чаты()
    bot = FakeBot()
    сообщение = FakeMessage(text=u'/чс', user=FakeUser(ALEX), bot=bot)
    await ban_handlers.on_ban_command(сообщение, _args(u'@Troll_Boy 999000111'))
    повтор = FakeMessage(text=u'/чс', user=FakeUser(ALEX), bot=bot)
    await ban_handlers.on_ban_command(повтор, _args(u'@Troll_Boy 999000111'))
    assert u'Уже были в списке (2)' in повтор.answers[-1]
