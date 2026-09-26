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


# ------------------------------------------------ рассылка видна в переписке пульта
#
# AleX 24.09.2026: рассылки ушли (отчёт «Получили: 321»), а в пульте у всех
# последним висело сообщение, отправленное из самого пульта, — рассылка в
# историю переписки не писалась, и казалось, что до людей она не дошла.

def _переписка(uid):
    return db.chat_history(uid)


async def test_рассылка_ложится_в_переписку_каждого_получателя():
    _люди(1, 2, 3)
    db.mark_blocked(3)
    bot = FakeBot()
    await _подготовить(bot, text=u'Друзья, подключайтесь к эфиру')
    await broadcast.run(bot, 1)
    for uid in (1, 2):
        [m] = _переписка(uid)
        assert (m['side'], m['kind'], m['text'], m['mass']) == ('out', 'text', u'Друзья, подключайтесь к эфиру', 1)
        assert m['tg_id'] and m['author'] == АДМИН          # можно поправить и удалить у человека
    assert _переписка(3) == []                               # закрывшему не ушло — и не пишем


async def test_закрывшему_во_время_рассылки_в_переписку_не_пишем():
    _люди(1)
    await _подготовить(FakeBot())
    await broadcast.run(FakeBot(forbidden=True), 1)
    assert _переписка(1) == []


async def test_рассылка_не_убирает_человека_из_ждущих_ответа():
    u"""Вопрос человека остаётся «ждёт ответа», пока ему не ответили лично."""
    _люди(1, 2)
    db.save_message(1, 'in', 'text', u'А когда эфир?')
    bot = FakeBot()
    await _подготовить(bot)
    await broadcast.run(bot, 1)
    люди = {p['user_id']: p for p in db.people('', 60, only_chats=True)}
    assert люди[1]['waiting'] == 1 and люди[1]['last_mass'] == 1
    assert list(люди)[0] == 1                                 # ждущий — сверху списка
    db.save_message(1, 'out', 'text', u'В 20:00', author=АДМИН)
    assert {p['user_id']: p for p in db.people('', 60, only_chats=True)}[1]['waiting'] == 0


# ------------------------------------------------ прошлые рассылки — в переписку

def _старая_рассылка(bot_message_id, cursor, sent, gone=0, at=1_000.0, targets=None):
    u"""Рассылка, какой она лежит в базе до 24.09.2026: без текста и не записана."""
    db._run('INSERT INTO broadcasts (chat_id, message_id, author, at, cursor, sent, gone, '
            "status, targets, logged) VALUES (?, ?, ?, ?, ?, ?, ?, 'done', ?, 0)",
            (АДМИН, bot_message_id, АДМИН, at, cursor, sent, gone, targets))
    return db._conn.execute('SELECT MAX(id) FROM broadcasts').fetchone()[0]


def _пришли(*ids, at=500.0):
    _люди(*ids)
    db._run('UPDATE users SET started_at=?', (at,))


async def test_прошлая_рассылка_дописывается_с_текстом_и_служебная_пересылка_удаляется():
    _пришли(1, 2, 3)
    bot = FakeBot()
    origin = await bot.send_message(АДМИН, u'Запись эфира уже доступна')
    task_id = _старая_рассылка(origin.message_id, cursor=3, sent=3)
    assert await broadcast.backfill(bot) == 1
    for uid in (1, 2, 3):
        [m] = _переписка(uid)
        assert m['text'] == u'Запись эфира уже доступна' and m['mass'] == 1
        assert m['tg_id'] is None                              # номера у людей не знаем — не правим
    forwarded = [p for k, c, p in bot.sent if k == 'forward']
    deleted = [p for k, c, p in bot.sent if k == 'delete']
    assert len(forwarded) == 1 and len(deleted) == 1           # пересылку автору убрали
    assert db.broadcast(task_id)['logged'] == 1
    assert await broadcast.backfill(bot) == 0                  # второй раз не дописывает


async def test_закрывшие_бота_во_время_прошлой_рассылки_не_попадают():
    _пришли(1, 2, 3, 4)
    db._run('UPDATE users SET blocked_at=? WHERE user_id=?', (1_010.0, 2))   # в ходе рассылки
    db._run('UPDATE users SET blocked_at=? WHERE user_id=?', (900.0, 3))     # до неё — не слали
    bot = FakeBot()
    origin = await bot.send_message(АДМИН, u'Эфир')
    _старая_рассылка(origin.message_id, cursor=4, sent=2, gone=1)
    await broadcast.backfill(bot)
    assert [uid for uid in (1, 2, 3, 4) if _переписка(uid)] == [1, 4]


async def test_пришедшие_после_рассылки_её_не_получали():
    _пришли(1, 2)
    db._run('UPDATE users SET started_at=? WHERE user_id=?', (2_000.0, 2))
    bot = FakeBot()
    origin = await bot.send_message(АДМИН, u'Эфир')
    _старая_рассылка(origin.message_id, cursor=2, sent=1)
    await broadcast.backfill(bot)
    assert _переписка(1) and not _переписка(2)


async def test_не_сходится_со_счётом_рассылки_не_дописываем():
    u"""Лучше не показать, чем показать сообщение, которого человек мог не получить."""
    _пришли(*range(1, 11))
    bot = FakeBot()
    origin = await bot.send_message(АДМИН, u'Эфир')
    task_id = _старая_рассылка(origin.message_id, cursor=10, sent=4)
    assert await broadcast.backfill(bot) == 0
    assert all(not _переписка(uid) for uid in range(1, 11))
    assert db.broadcast(task_id)['logged'] == 1               # и не пробует на каждом запуске


async def test_текст_не_прочитался_в_переписку_честная_заглушка():
    class БезПересылки(FakeBot):
        async def forward_message(self, *a, **kw):
            raise RuntimeError('message to forward not found')

    _пришли(1)
    _старая_рассылка(99999, cursor=1, sent=1)
    await broadcast.backfill(БезПересылки())
    assert _переписка(1)[0]['text'] == texts.BROADCAST_LOST


def test_пульт_помечает_рассылку_в_списке_и_в_диалоге():
    from bot import web
    _люди(1)
    db.save_message(1, 'out', 'text', u'Эфир в 20:00', author=АДМИН, tg_id=5, mass=True)
    [человек] = db.people('', 60, only_chats=True)
    assert web._person(человек)['last_mass'] is True
    assert web._messages(1)[0]['mass'] is True
    assert web._messages(1)[0]['can_edit'] is False      # правка сняла бы кнопки


async def test_промежуточный_доклад_говорит_из_скольких(monkeypatch):
    u"""AleX 24.09.2026: «ушло 200» читали как итог при 321 получателе."""
    monkeypatch.setattr(broadcast, 'REPORT_EVERY', 2)
    _люди(1, 2, 3)
    bot = FakeBot()
    await _подготовить(bot)
    await broadcast.run(bot, 1)
    доклады = [p for k, c, p in bot.sent if k == 'text' and u'Рассылка идёт' in (p or u'')]
    assert доклады and u'ушло 2 из 3' in доклады[0]


async def test_итог_раскладывает_всех_кто_в_боте():
    u"""AleX 26.09.2026: «в боте ~450, а ушло 370 с чем-то» — итог обязан
    сходиться с числом людей в боте."""
    _люди(1, 2, 3, 4, 5, 6)
    db.mark_blocked(3)                                # закрыл бота давно
    db._run('UPDATE users SET blocked_at=? WHERE user_id=3', (1.0,))
    db.ban_add(4, 'u4', u'Тролль', u'AleX')          # в чёрном списке

    class ЗакрылВоВремя(FakeBot):
        async def copy_message(self, chat_id, *a, **kw):
            if chat_id == 6:
                from bot import delivery
                raise delivery.Gone()
            return await super().copy_message(chat_id, *a, **kw)

    bot = ЗакрылВоВремя()
    await _подготовить(bot)
    await broadcast.run(bot, 1)
    итог = [p for k, c, p in bot.sent if k == 'text' and u'Рассылка закончена' in (p or u'')][-1]
    assert u'Получили: 3' in итог and u'Закрыли бота (не доставлено): 1' in итог
    assert u'Всего в боте: 6' in итог
    assert u'закрыли бота раньше — 1' in итог and u'в чёрном списке — 1' in итог
