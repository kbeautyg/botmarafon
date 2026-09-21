# -*- coding: utf-8 -*-
u"""Эфир: бот собирает людей на трансляцию (Павел 20.09.2026).

Анонс уходит всем сразу, поэтому проверок две группы: что без
подтверждения не уходит ничего и что напоминания приходят по одному разу
и вовремя — второе «эфир начался» через час людей бы разозлило.
"""
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, live, stats, texts                           # noqa: E402
from bot.handlers import admin                                           # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser         # noqa: E402

СВОЙ = 111


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'live.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(live, 'PER_SECOND', 10000)      # в проверках не ждём
    yield


def _люди(*ids):
    for uid in ids:
        db.remember_user(uid, 'u%d' % uid, u'Человек')


def _кому(bot):
    return sorted(chat for kind, chat, _ in bot.sent if kind == 'text' and chat != СВОЙ)


def _через(seconds):
    return time.time() + seconds


# ------------------------------------------------------------- разбор

def test_дату_время_и_ссылку_узнаём_в_любом_порядке():
    at, url, own = live.parse(u'20.09 19:00 https://t.me/x?livestream Приходите с вопросами')
    assert url == 'https://t.me/x?livestream'
    assert own == u'Приходите с вопросами'
    когда = datetime.fromtimestamp(at, stats.MSK)
    assert (когда.day, когда.month, когда.hour, когда.minute) == (20, 9, 19, 0)


def test_без_ссылки_или_без_времени_не_берём():
    assert live.parse(u'19:00 приходите')[0] is None
    assert live.parse(u'https://t.me/x')[0] is None
    assert live.parse(u'31.02 19:00 https://t.me/x')[0] is None   # такой даты нет


def test_дата_без_года_не_уходит_в_прошлое():
    u"""«02.01 20:00», присланное в декабре, — это январь следующего года."""
    декабрь = datetime(2026, 12, 25, 12, 0, tzinfo=stats.MSK).timestamp()
    at, _, _ = live.parse(u'02.01 20:00 https://t.me/x', now=декабрь)
    assert datetime.fromtimestamp(at, stats.MSK).year == 2027


# ------------------------------------------------------- подтверждение

async def test_без_подтверждения_анонс_никому_не_уходит():
    _люди(1, 2)
    bot = FakeBot()
    сообщение = FakeMessage(text=u'20.09 19:00 https://t.me/x', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_live_message(сообщение)
    assert _кому(bot) == []
    assert db.live(1)['status'] == 'ready'


async def test_по_кнопке_анонс_уходит_всем_кроме_ушедших_и_списка():
    _люди(1, 2, 3, 4)
    db.mark_blocked(3)
    db.ban_add(4, 'u4', u'Тролль', u'AleX')
    db.live_add('https://t.me/x', u'', _через(7200), СВОЙ)

    bot = FakeBot()
    await live.announce(bot, 1)

    assert _кому(bot) == [1, 2]
    assert db.live(1)['status'] == 'going'


async def test_отмена_анонса_не_шлёт_ничего():
    _люди(1)
    db.live_add('https://t.me/x', u'', _через(7200), СВОЙ)
    bot = FakeBot()
    await admin.on_live_button(FakeCall('live:no:1', user=FakeUser(СВОЙ), bot=bot))
    assert _кому(bot) == []
    assert db.live(1)['status'] == 'cancelled'


# --------------------------------------------------------- напоминания

async def test_напоминания_приходят_по_одному_разу_и_вовремя():
    _люди(1)
    at = _через(7200)                                  # эфир через два часа
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    db.live_status(1, 'going')
    bot = FakeBot()

    assert await live.tick(bot, now=at - 5400) == 0     # за полтора часа рано
    assert await live.tick(bot, now=at - 3500) == 1     # час — пора
    assert await live.tick(bot, now=at - 3400) == 0     # второй раз не шлём
    assert await live.tick(bot, now=at - 300) == 1      # десять минут
    assert await live.tick(bot, now=at + 10) == 1       # началось

    тексты = [p for k, c, p in bot.sent if c == 1]
    assert u'через час' in тексты[0]
    assert u'через десять минут' in тексты[1]
    assert u'начался' in тексты[2]


async def test_эфир_прошёл_и_бот_замолкает():
    _люди(1)
    at = _через(-7200)                                 # начался два часа назад
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    db.live_status(1, 'going')
    bot = FakeBot()

    await live.tick(bot)
    assert _кому(bot) == []                            # догонять поздно
    assert db.live(1)['status'] == 'done'


async def test_под_анонсом_кнопка_ведёт_на_эфир():
    from bot import keyboards

    кнопка = keyboards.live('https://t.me/x?livestream').inline_keyboard[0][0]
    assert кнопка.url == 'https://t.me/x?livestream'
    assert u'Смотреть' in кнопка.text


# ------------------------- проба на себе (Sharp 20.09.2026)

async def test_проба_эфира_уходит_только_нажавшему():
    u"""«Как протестить, чтоб не обосраться потом?» — вот так."""
    _люди(1, 2, 3)
    db.live_add('https://t.me/x', u'Приходите', _через(7200), СВОЙ)
    bot = FakeBot()

    await admin.on_live_button(FakeCall('live:me:1', user=FakeUser(СВОЙ), bot=bot))

    получатели = [c for k, c, _ in bot.sent if k == 'text']
    assert СВОЙ in получатели                     # себе пришло
    assert _кому(bot) == []                       # людям — нет
    assert db.live(1)['status'] == 'ready'        # эфир ещё не объявлен


async def test_после_пробы_кнопки_остаются_и_можно_объявить():
    _люди(1)
    db.live_add('https://t.me/x', u'', _через(7200), СВОЙ)
    bot = FakeBot()
    проба = FakeCall('live:me:1', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_live_button(проба)
    assert not проба.edited                       # разметку не сняли

    await live.announce(bot, 1)
    assert _кому(bot) == [1]


# ------------------- прогон эфира на себе (Sharp 21.09.2026: «как протестировать?»)
#
# «Сначала мне» показывает только анонс, а напоминания уходят по часам —
# их так не проверить. Прогон проводит весь эфир целиком, но один человек.

async def _дать_поработать():
    import asyncio
    for _ in range(5):
        await asyncio.sleep(0)


async def test_прогон_проводит_весь_эфир_только_нажавшему():
    _люди(1, 2, СВОЙ)
    at = _через(900)                                   # эфир через 15 минут
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    bot = FakeBot()

    нажатие = FakeCall('live:test:1', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_live_button(нажатие)
    await _дать_поработать()

    assert _кому(bot) == []                            # людям — ничего
    assert db.live(1)['status'] == 'going'
    assert any(u'Прогон пошёл' in (a or u'') for a in нажатие.message.answers)
    assert any(c == СВОЙ and u'Эфир с Павлом' in (p or u'') for k, c, p in bot.sent)

    # напоминания по часам — тоже только проверяющему
    assert await live.tick(bot, now=at - 590) == 1
    assert await live.tick(bot, now=at + 5) == 1
    assert _кому(bot) == []
    мне = [p for k, c, p in bot.sent if c == СВОЙ and k == 'text']
    assert any(u'через десять минут' in p for p in мне)
    assert any(u'начался' in p for p in мне)


async def test_прогон_не_виден_в_меню_как_ближайший_эфир():
    _люди(СВОЙ)
    db.live_add('https://t.me/x', u'', _через(900), СВОЙ)
    await admin.on_live_button(FakeCall('live:test:1', user=FakeUser(СВОЙ), bot=FakeBot()))
    await _дать_поработать()
    assert db.live_next() is None


def test_план_прогона_говорит_что_и_когда_придёт():
    at = time.time() + 900
    план = admin._test_plan(at)
    assert u'через десять минут' in план and u'начался' in план
    assert u'через час' not in план                    # до эфира меньше часа


# ------------------------------------------ напоминания не врут о времени

async def test_эфир_объявленный_поздно_не_шлёт_через_час():
    u"""Анонс за двадцать минут: следом шло «эфир через час» — всем."""
    _люди(1)
    now = time.time()
    at = now + 1200
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    bot = FakeBot()

    await live.announce(bot, 1, now=now)
    assert await live.tick(bot, now=now + 30) == 0     # «через час» не уходит
    assert await live.tick(bot, now=at - 590) == 1     # «через десять минут» — да

    тексты = [p for k, c, p in bot.sent if c == 1]
    assert not any(u'через час' in p for p in тексты)


async def test_опоздавшее_напоминание_не_уходит():
    u"""Бот пролежал выкладку: «через час» за пятьдесят минут — уже враньё."""
    _люди(1)
    at = _через(7200)
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    db.live_status(1, 'going')
    bot = FakeBot()

    assert await live.tick(bot, now=at - 3600 + live.LATE + 60) == 0
    assert _кому(bot) == []
    assert db.live_reminded(1, 3600)                   # отмечено — не придёт и позже


# ------------------------- прогон на выбранных (Sharp 21.09.2026)
#
# «Чтобы тест показался одному человеку, которого я выбрал»: прогон уходит
# тем, кого назвали ником или ID. Остальным людям — ничего.

async def test_прогон_уходит_только_выбранному_по_нику():
    _люди(1, 2, СВОЙ)
    db.remember_user(5, 'alex_kent', u'AleX')
    at = _через(900)
    db.live_add('https://t.me/x', u'', at, СВОЙ)
    bot = FakeBot()

    await admin.on_live_button(FakeCall('live:pick:1', user=FakeUser(СВОЙ), bot=bot))
    выбор = FakeMessage(text=u'@alex_kent', user=FakeUser(СВОЙ), bot=bot)
    assert admin.waiting_live_pick(выбор)
    await admin.on_live_pick(выбор)
    await _дать_поработать()

    людям = sorted(c for k, c, _ in bot.sent if k == 'text' and c not in (СВОЙ,))
    assert людям == [5]                                # только AleX
    assert u'@alex_kent' in выбор.answers[-1]

    assert await live.tick(bot, now=at - 590) == 1
    людям = sorted(c for k, c, _ in bot.sent if k == 'text' and c not in (СВОЙ,))
    assert людям == [5, 5]                             # и напоминание — ему же


async def test_себя_можно_добавить_словом_мне():
    _люди(1, СВОЙ)
    db.remember_user(5, 'alex_kent', u'AleX')
    db.live_add('https://t.me/x', u'', _через(900), СВОЙ)
    bot = FakeBot()

    await admin.on_live_button(FakeCall('live:pick:1', user=FakeUser(СВОЙ), bot=bot))
    await admin.on_live_pick(FakeMessage(text=u'@alex_kent мне', user=FakeUser(СВОЙ), bot=bot))
    await _дать_поработать()

    assert sorted(int(x) for x in db.live(1)['targets'].split(',')) == sorted([5, СВОЙ])
    assert 1 not in [c for k, c, _ in bot.sent]        # человек 1 — ничего


async def test_незнакомый_ник_не_запускает_прогон():
    _люди(1, СВОЙ)
    db.live_add('https://t.me/x', u'', _через(900), СВОЙ)
    bot = FakeBot()

    await admin.on_live_button(FakeCall('live:pick:1', user=FakeUser(СВОЙ), bot=bot))
    выбор = FakeMessage(text=u'@nobody_here', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_live_pick(выбор)

    assert u'нет в боте' in выбор.answers[-1]
    assert db.live(1)['status'] == 'ready'             # ничего не ушло
    assert admin.waiting_live_pick(FakeMessage(text=u'@alex', user=FakeUser(СВОЙ)))


async def test_отмена_снимает_выбор_получателей():
    _люди(СВОЙ)
    db.live_add('https://t.me/x', u'', _через(900), СВОЙ)
    await admin.on_live_button(FakeCall('live:pick:1', user=FakeUser(СВОЙ), bot=FakeBot()))
    assert not admin.waiting_live_pick(FakeMessage(text=u'/отмена', user=FakeUser(СВОЙ)))
    await admin.on_cancel(FakeMessage(text=u'/отмена', user=FakeUser(СВОЙ)))
    assert not admin.waiting_live_pick(FakeMessage(text=u'@alex', user=FakeUser(СВОЙ)))


async def test_отмена_во_время_ожидания_даты_эфира_работает():
    u"""LIVE_ASK обещает «передумали — /отмена», а «/отмена» разбиралась как
    дата эфира и отвечала «не разобрал»."""
    await admin._live_ask(FakeMessage(text=u'/эфир', user=FakeUser(СВОЙ)))
    assert not admin.waiting_live(FakeMessage(text=u'/отмена', user=FakeUser(СВОЙ)))
    await admin.on_cancel(FakeMessage(text=u'/отмена', user=FakeUser(СВОЙ)))
    assert not admin.waiting_live(FakeMessage(text=u'21.09 19:00 https://t.me/x',
                                              user=FakeUser(СВОЙ)))


async def test_кнопки_эфира_стоят_под_читаемым_сообщением():
    u"""Ширину кнопок задаёт ширина сообщения: под одной «⬇️» они сжимались
    до «🧪 Прог…», и две кнопки прогона было не отличить (21.09.2026)."""
    bot = FakeBot()
    сообщение = FakeMessage(text=u'21.09 19:30 https://t.me/x', user=FakeUser(СВОЙ), bot=bot)
    await admin._live_ask(FakeMessage(text=u'/эфир', user=FakeUser(СВОЙ), bot=bot))
    await admin.on_live_message(сообщение)

    с_кнопками = сообщение.answers[-1]
    assert с_кнопками == texts.LIVE_CHOOSE
    assert len(с_кнопками) > 60                        # сообщение широкое
    надписи = [b.text for row in сообщение.markups[-1].inline_keyboard for b in row]
    assert len(set(надписи)) == len(надписи)           # все кнопки разные
    assert any(u'всем' in n for n in надписи)          # видно, какая — по-настоящему
