# -*- coding: utf-8 -*-
u"""Рассылка всем: «уведомить всех» одним сообщением (AleX 18.09.2026).

Рассылку нельзя отозвать: ушло — значит ушло всем. Поэтому проверок тут
две группы. Первая — что без подтверждения не уходит вообще ничего.
Вторая — что при повторе и после перезапуска человек не получит одно и то
же дважды.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import broadcast, config, db, texts                            # noqa: E402
from bot.handlers import admin                                          # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser        # noqa: E402

АДМИН = 111


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'broadcast.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (АДМИН,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(broadcast, 'PAUSE', 0)      # в проверках не ждём
    yield


def _люди(*ids):
    for uid in ids:
        db.remember_user(uid, 'u%d' % uid, u'Человек %d' % uid)


def _получатели(bot):
    return [chat for kind, chat, _ in bot.sent if kind == 'copy']


async def _подготовить(bot, text=u'Новый подкаст: https://example.com/podcast'):
    u"""Пройти путь команды: /рассылка → сообщение → получить подтверждение."""
    await admin.on_broadcast(FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot))
    сообщение = FakeMessage(text=text, user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast_message(сообщение)
    return сообщение


# --------------------------------------------------------- подтверждение

async def test_без_подтверждения_никому_ничего_не_уходит():
    _люди(1, 2, 3)
    bot = FakeBot()
    сообщение = await _подготовить(bot)
    assert _получатели(bot) == []                    # только предпросмотр
    assert u'Разослать это всем?' in сообщение.replies[-1]


async def test_отмена_не_шлёт_ничего():
    _люди(1, 2)
    bot = FakeBot()
    await _подготовить(bot)
    task_id = db.broadcasts_going() or [db.broadcast(1)]
    await admin.on_broadcast_button(FakeCall('bc:no:1', user=FakeUser(АДМИН), bot=bot))
    assert _получатели(bot) == []
    assert db.broadcast(1)['status'] == 'cancelled'


async def test_по_кнопке_уходит_всем_кроме_ушедших_и_чёрного_списка():
    _люди(1, 2, 3, 4)
    db.mark_blocked(3)                               # закрыл бота
    db.ban_add(4, 'u4', u'Тролль', u'AleX')          # в чёрном списке
    bot = FakeBot()
    await _подготовить(bot)

    await broadcast.run(bot, 1)

    assert sorted(_получатели(bot)) == [1, 2]
    итог = db.broadcast(1)
    assert итог['sent'] == 2 and итог['status'] == 'done'


# ------------------------------------------------------------- дважды не шлём

async def test_прерванная_рассылка_продолжается_а_не_начинается_заново():
    u"""Деплой посреди рассылки: те, кто получил, второй раз не получают."""
    _люди(1, 2, 3, 4)
    bot = FakeBot()
    await _подготовить(bot)
    db.broadcast_status(1, 'going')
    db.broadcast_step(1, 2, 'ok')                    # первым двоим уже ушло
    db.broadcast_step(1, 2, 'ok')

    await broadcast.run(bot, 1)

    assert sorted(_получатели(bot)) == [3, 4]


async def test_законченную_рассылку_кнопкой_не_повторить():
    _люди(1, 2)
    bot = FakeBot()
    await _подготовить(bot)
    await broadcast.run(bot, 1)
    bot.sent.clear()

    await admin.on_broadcast_button(FakeCall('bc:go:1', user=FakeUser(АДМИН), bot=bot))
    assert _получатели(bot) == []


async def test_закрывшего_бота_отмечаем_и_считаем_отдельно():
    _люди(1)
    await _подготовить(FakeBot())
    await broadcast.run(FakeBot(forbidden=True), 1)   # человек закрыл бота
    итог = db.broadcast(1)
    assert итог['gone'] == 1 and итог['sent'] == 0
    assert db.get_user(1)['blocked_at'] is not None


# ------------------------------------------------------------------ доступ

async def test_чужому_рассылка_недоступна():
    _люди(1, 2)
    bot = FakeBot()
    чужой = FakeMessage(text='/рассылка', user=FakeUser(999), bot=bot)
    await admin.on_broadcast(чужой)
    assert чужой.answers == []
    assert not admin.waiting_broadcast(FakeMessage(text=u'что угодно', user=FakeUser(999)))


async def test_вторую_рассылку_поверх_идущей_не_начинаем():
    _люди(1, 2)
    bot = FakeBot()
    await _подготовить(bot)
    db.broadcast_status(1, 'going')
    вторая = FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast(вторая)
    assert вторая.answers[-1] == texts.BROADCAST_BUSY


# ------------------------------- выбранным (AleX 19.09.2026: «пачкой кому-то»)

def _команда(args):
    return type('Cmd', (), {'args': args})()


async def test_рассылка_по_списку_ников_уходит_только_им():
    _люди(1, 2, 3)
    db.remember_user(4, 'nick_four', u'Четвёртый')
    bot = FakeBot()
    старт = FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast(старт, _команда(u'@nick_four 2'))
    assert u'Нашли в базе: 2 из 2' in старт.answers[-1]

    сообщение = FakeMessage(text=u'Новый подкаст', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast_message(сообщение)
    await broadcast.run(bot, 1)

    assert sorted(_получатели(bot)) == [2, 4]


async def test_кого_нет_в_базе_называем_поимённо():
    db.remember_user(1, 'nick_one', u'Первый')
    bot = FakeBot()
    старт = FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast(старт, _команда(u'@nick_one @kogo_net'))
    assert u'Нашли в базе: 1 из 2' in старт.answers[-1]
    assert u'Не нашли: @kogo_net' in старт.answers[-1]


async def test_если_никого_не_нашли_рассылку_не_заводим():
    _люди(1)
    bot = FakeBot()
    старт = FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast(старт, _команда(u'@nikogo @vovse_net'))
    assert старт.answers[-1] == texts.BROADCAST_PICKED_NONE
    assert not admin.waiting_broadcast(FakeMessage(text=u'что-то', user=FakeUser(АДМИН)))


async def test_выбранная_рассылка_не_трогает_чёрный_список():
    _люди(1, 2)
    db.ban_add(2, 'u2', u'Тролль', u'AleX')
    bot = FakeBot()
    старт = FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast(старт, _команда(u'1 2'))
    await admin.on_broadcast_message(FakeMessage(text=u'привет', user=FakeUser(АДМИН), bot=bot))
    await broadcast.run(bot, 1)
    assert _получатели(bot) == [1]


async def test_команда_проекта_а_не_только_владелец_может_рассылать(monkeypatch):
    u"""AleX 19.09.2026: «работает команда только пульт и статс»."""
    from bot import config as конфиг

    monkeypatch.setattr(конфиг, 'ADMIN_IDS', (999,))          # владелец — не он
    monkeypatch.setattr(конфиг, 'TEAM_STATS_IDS', (АДМИН,))   # AleX в команде
    monkeypatch.setattr(конфиг, 'STATS_IDS', (АДМИН,))
    _люди(1)
    bot = FakeBot()
    старт = FakeMessage(text='/рассылка', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast(старт, _команда(u''))
    assert старт.answers, u'команда не ответила тому, кто в команде проекта'
    assert admin.waiting_broadcast(FakeMessage(text=u'текст', user=FakeUser(АДМИН)))
    assert 'рассылка' in admin.STATS_COMMANDS and 'отчёт' in admin.STATS_COMMANDS


async def test_проба_рассылки_уходит_только_нажавшему():
    u"""Sharp 20.09.2026: проверить, ничего не рассылая людям."""
    _люди(1, 2, 3)
    bot = FakeBot()
    await _подготовить(bot, u'Новость дня')
    bot.sent.clear()

    await admin.on_broadcast_button(FakeCall('bc:me:1', user=FakeUser(АДМИН), bot=bot))

    копии = [chat for kind, chat, _ in bot.sent if kind == 'copy']
    assert копии == [АДМИН]                       # только себе
    assert db.broadcast(1)['status'] == 'ready'   # рассылка не запущена
