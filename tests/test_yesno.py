# -*- coding: utf-8 -*-
u"""Рассылка с кнопками «Да» и «Нет» (30.09.2026).

«Здравствуйте, сегодня крайний день, когда мы составляем списки
энергоспортзала по акции: вы можете купить один месяц, а получить два.
Вы с нами? — две кнопки, Да или Нет. Прям всем, на каких бы шагах они ни были».

«Да» — то же, что кнопка покупки спортзала: заявка менеджерам и оплата
человеку. «Нет» — спасибо. Обычная /рассылка остаётся без кнопок.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import broadcast, config, db, texts                              # noqa: E402
from bot.handlers import admin, purchase                                   # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser           # noqa: E402

АДМИН = 111
ЧАТ_ПОКУПОК = -100777
ОПЛАТА = 'https://pay.example/gym'


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'yesno.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (АДМИН,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', ЧАТ_ПОКУПОК)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'PAY_URLS', {'gym': ОПЛАТА, 'course': ''})
    monkeypatch.setattr(broadcast, 'PAUSE', 0)
    yield


def _люди(*ids):
    for uid in ids:
        db.remember_user(uid, 'u%d' % uid, u'Человек %d' % uid)


async def _завести(bot, команда=u'/рассылка_да_нет', текст=u'Вы с нами?'):
    if команда == u'/рассылка_да_нет':
        await admin.on_broadcast_yesno(FakeMessage(text=команда, user=FakeUser(АДМИН), bot=bot))
    else:
        await admin.on_broadcast(FakeMessage(text=команда, user=FakeUser(АДМИН), bot=bot))
    сообщение = FakeMessage(text=текст, user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast_message(сообщение)
    return сообщение


def _кнопки(keys):
    return [(b.text, b.callback_data) for row in keys.inline_keyboard for b in row]


async def test_под_рассылкой_у_каждого_кнопки_да_и_нет():
    _люди(1, 2, 3)
    db.mark_blocked(3)
    bot = FakeBot()
    сообщение = await _завести(bot)
    assert texts.BROADCAST_YESNO_NOTE.strip() in сообщение.replies[-1]

    await broadcast.run(bot, 1)

    копии = [(chat, keys) for (kind, chat, _), keys in zip(bot.sent, bot.keys_sent) if kind == 'copy']
    assert [chat for chat, _ in копии] == [1, 2]              # всем, кроме закрывших бота
    for _, keys in копии:
        assert _кнопки(keys) == [(u'ДА', 'ask:yes:1'), (u'НЕТ', 'ask:no:1')]


async def test_обычная_рассылка_по_прежнему_без_кнопок():
    _люди(1, 2)
    bot = FakeBot()
    await _завести(bot, u'/рассылка', u'Новый подкаст')
    await broadcast.run(bot, 1)
    assert [keys for (kind, _, _), keys in zip(bot.sent, bot.keys_sent) if kind == 'copy'] == [None, None]


async def test_проба_на_себе_тоже_с_кнопками():
    _люди(1)
    bot = FakeBot()
    await _завести(bot)
    bot.sent.clear(), bot.keys_sent.clear()
    await admin.on_broadcast_button(FakeCall('bc:me:1', user=FakeUser(АДМИН), bot=bot))
    копии = [(chat, keys) for (kind, chat, _), keys in zip(bot.sent, bot.keys_sent) if kind == 'copy']
    assert копии and копии[0][0] == АДМИН and _кнопки(копии[0][1])[0][1] == 'ask:yes:1'
    assert db.broadcast(1)['status'] == 'ready'


async def test_да_это_заявка_менеджерам_и_кнопка_оплаты():
    _люди(5)
    bot = FakeBot()
    call = FakeCall('ask:yes:1', user=FakeUser(5, 'u5', u'Человек 5'), bot=bot)
    await purchase.on_ask(call)

    заявки = [(chat, text) for kind, chat, text in bot.sent
              if kind == 'text' and u'Заявка №' in (text or u'')]
    assert ЧАТ_ПОКУПОК in {chat for chat, _ in заявки}
    assert all(texts.ASK_YES_CHOICE in text for _, text in заявки)
    assert call.message.answers[-1] == texts.ASK_YES_PAY
    assert call.message.markups[-1].inline_keyboard[0][0].url == ОПЛАТА
    assert db.purchase_presses(5, 'gym')


async def test_да_дважды_менеджерам_одна_заявка():
    _люди(5)
    bot = FakeBot()
    for _ in range(2):
        await purchase.on_ask(FakeCall('ask:yes:1', user=FakeUser(5, 'u5', u'Человек 5'), bot=bot))
    заявки_в_чат = [chat for kind, chat, text in bot.sent
                    if kind == 'text' and chat == ЧАТ_ПОКУПОК and u'Заявка №' in (text or u'')]
    assert len(заявки_в_чат) == 1


async def test_нет_это_спасибо_без_заявки():
    _люди(6)
    bot = FakeBot()
    call = FakeCall('ask:no:1', user=FakeUser(6, 'u6', u'Человек 6'), bot=bot)
    await purchase.on_ask(call)
    assert call.message.answers[-1] == texts.ASK_NO_DONE
    assert not [1 for kind, _, text in bot.sent if u'Заявка №' in (text or u'')]
    assert not db.purchase_presses(6, 'gym')


def test_команда_открыта_команде_проекта():
    assert 'рассылка_да_нет' in admin.STATS_COMMANDS and 'ответы' in admin.STATS_COMMANDS


# ------------------------------------------ «кто что нажмёт — как мы увидим?»

async def test_отчёт_считает_да_нет_и_молчащих_поимённо():
    _люди(1, 2, 3, 4)
    bot = FakeBot()
    await _завести(bot)
    await broadcast.run(bot, 1)
    await purchase.on_ask(FakeCall('ask:yes:1', user=FakeUser(1, 'u1', u'Человек 1'), bot=bot))
    await purchase.on_ask(FakeCall('ask:no:1', user=FakeUser(2, 'u2', u'Человек 2'), bot=bot))
    # передумал: сначала «Нет», потом «Да» — считается последнее
    await purchase.on_ask(FakeCall('ask:no:1', user=FakeUser(3, 'u3', u'Человек 3'), bot=bot))
    await purchase.on_ask(FakeCall('ask:yes:1', user=FakeUser(3, 'u3', u'Человек 3'), bot=bot))

    запрос = FakeMessage(text=u'/ответы', user=FakeUser(АДМИН), bot=bot)
    await admin.on_answers(запрос)
    отчёт = запрос.answers[-1]
    assert u'Получили: 4' in отчёт and u'Да: 2' in отчёт and u'Нет: 1' in отчёт
    assert u'Пока не ответили: 1' in отчёт
    да, нет = отчёт.split(texts.ANSWERS_NO)
    assert u'@u1' in да and u'@u3' in да and u'@u2' in нет


async def test_длинный_отчёт_приходит_ещё_и_файлом():
    люди = list(range(1, 121))
    _люди(*люди)
    bot = FakeBot()
    await _завести(bot)
    await broadcast.run(bot, 1)
    for uid in люди:
        await purchase.on_ask(FakeCall('ask:no:1', user=FakeUser(uid, 'u%d' % uid, u'Человек %d' % uid),
                                       bot=bot))
    запрос = FakeMessage(text=u'/ответы', user=FakeUser(АДМИН), bot=bot)
    await admin.on_answers(запрос)
    assert texts.ANSWERS_FILE.strip() in запрос.answers[-1]
    assert [payload for kind, _, payload in bot.sent if kind == 'document'] == ['otvety_1.csv']


async def test_итог_рассылки_подсказывает_про_отчёт():
    _люди(1)
    bot = FakeBot()
    await _завести(bot)
    await broadcast.run(bot, 1)
    итоги = [text for kind, chat, text in bot.sent if kind == 'text' and u'Рассылка закончена' in (text or u'')]
    assert итоги and u'/ответы' in итоги[-1]


# ------------------ «пока только тем, кто пришёл из рекламы Мариуса» (30.09.2026)

async def test_по_каналу_мариус_уходит_только_людям_мариуса():
    db.remember_user(1, 'u1', u'Инста Мариуса', 'site_ig')           # Мариус — Instagram
    db.remember_user(2, 'u2', u'Окно Мариуса', 'site_popup_fb')      # Мариус — Facebook, окно
    db.remember_user(3, 'u3', u'Мессенджер', 'site_msg')             # Мариус — Messenger
    db.remember_user(4, 'u4', u'Инста без рекламы', 'site_igorg')    # не Мариус
    db.remember_user(5, 'u5', u'Телеграм', 'tg_1')                   # Telegram
    db.remember_user(6, 'u6', u'Яндекс', 'site_yandex')              # Яндекс Директ
    db.remember_user(7, 'u7', u'Напрямую', '')                       # напрямую
    db.remember_user(8, 'u8', u'Мариус, но закрыл бота', 'site_an')
    db.mark_blocked(8)
    bot = FakeBot()
    команда = FakeMessage(text=u'/рассылка_да_нет мариус', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast_yesno(команда, SimpleNamespace(args=u'мариус'))
    assert u'Таких в базе: 4 чел.' in команда.answers[-1]          # вместе с закрывшим бота
    assert texts.BROADCAST_YESNO_NOTE.strip() in команда.answers[-1]

    сообщение = FakeMessage(text=u'Вы с нами?', user=FakeUser(АДМИН), bot=bot)
    await admin.on_broadcast_message(сообщение)
    assert u'Получат: 3 чел.' in сообщение.replies[-1]              # закрывший бота не получит
    await broadcast.run(bot, 1)

    копии = [(chat, keys) for (kind, chat, _), keys in zip(bot.sent, bot.keys_sent) if kind == 'copy']
    assert sorted(chat for chat, _ in копии) == [1, 2, 3]
    assert all(_кнопки(keys)[0] == (u'ДА', 'ask:yes:1') for _, keys in копии)


def test_имя_канала_узнаётся_в_любом_написании():
    from bot import stats
    assert stats.channel_named(u'мариус') == stats.MARIUS
    assert stats.channel_named(u'Мариуса') == stats.MARIUS
    assert stats.channel_named(u'telegram ads') == stats.TG_ADS
    assert stats.channel_named(u'@ник') is None and stats.channel_named(u'123') is None


async def test_без_рассылки_с_кнопками_отчёт_честно_пуст():
    bot = FakeBot()
    запрос = FakeMessage(text=u'/ответы', user=FakeUser(АДМИН), bot=bot)
    await admin.on_answers(запрос)
    assert запрос.answers[-1] == texts.ANSWERS_NONE
