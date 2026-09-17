# -*- coding: utf-8 -*-
u"""Переписка команды с людьми через бота и дожим «заходи на марафон».

AleX 14.09.2026: писал людям со своих аккаунтов — оба в спам-блоке; «надо
отправить от имени бота». Теперь всё, что человек пишет боту, приходит
команде, а реплай команды — на его сообщение, на уведомление о запуске или
на заявку — уходит человеку от имени бота.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, nudge, texts                                  # noqa: E402
from bot.handlers import purchase, start, support                         # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser          # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'bridge.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    start._launching.clear()
    yield


def _в(bot, chat, kind=None):
    return [(k, p) for k, c, p in bot.sent if c == chat and (kind is None or k == kind)]


def _последнее_в(bot, chat):
    u"""FakeMessage последнего сообщения бота в этот чат — на него отвечают реплаем."""
    return [m for _, m in sorted(bot.by_id.items()) if m.chat.id == chat][-1]


# ------------------------------------------------------------ туда

async def test_написанное_боту_приходит_команде_и_человеку_говорим_ответим_здесь():
    bot = FakeBot()
    вопрос = FakeMessage(text=u'А сколько стоит зал?', user=FakeUser(1, 'olga', u'Ольга'), bot=bot)
    await support.to_support(вопрос)
    for chat in (SHARP, PAVEL, ALEX):
        виды = [k for k, _ in _в(bot, chat)]
        assert виды == ['text', 'copy'], chat
    assert вопрос.answers[-1] == texts.CARE_SENT_HERE


async def test_некому_переслать_как_раньше_кнопка_заботы(monkeypatch):
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    вопрос = FakeMessage(text=u'Когда второй день?', user=FakeUser(2))
    await support.to_support(вопрос)
    assert вопрос.answers[-1] == texts.CARE_SENT


async def test_сообщение_команды_людям_не_дублируется():
    bot = FakeBot()
    своё = FakeMessage(text=u'проверка', user=FakeUser(PAVEL), bot=bot)
    await support.to_support(своё)
    assert _в(bot, SHARP) == [] and _в(bot, ALEX) == []
    assert своё.answers[-1] == texts.TEAM_HINT


# ------------------------------------------------------------ обратно

async def test_реплай_команды_на_сообщение_человека_уходит_ему():
    bot = FakeBot()
    await support.to_support(FakeMessage(text=u'вопрос', user=FakeUser(1), bot=bot))
    ответ = FakeMessage(text=u'Здравствуйте! Отвечаю', user=FakeUser(ALEX), bot=bot,
                        reply_to=_последнее_в(bot, ALEX))
    assert support._team_reply(ответ)
    await support.from_team(ответ)
    assert _в(bot, 1, 'copy'), u'ответ должен уйти человеку'
    assert ответ.replies[-1] == u'Отправлено ✅'


async def test_реплай_на_уведомление_о_запуске_уходит_этому_человеку():
    bot = FakeBot()
    await start.on_start(FakeMessage(text='/start ls', user=FakeUser(5, None, u'Feruza'), bot=bot))
    уведомление = _последнее_в(bot, ALEX)
    assert u'Новый запуск марафона' in уведомление.text
    ответ = FakeMessage(text=u'Посмотрели разборы?', user=FakeUser(ALEX), bot=bot,
                        reply_to=уведомление)
    await support.from_team(ответ)
    assert _в(bot, 5, 'copy')


async def test_реплай_на_заявку_на_покупку_уходит_покупателю():
    bot = FakeBot()
    db.remember_user(6, 'buyer', u'Покупатель')
    await purchase.on_buy(FakeCall('buy:gym', user=FakeUser(6, 'buyer', u'Покупатель'), bot=bot))
    заявка = next(m for _, m in sorted(bot.by_id.items())
                  if m.chat.id == PAVEL and u'Заявка №' in (m.text or u''))
    ответ = FakeMessage(text=u'Созвонимся?', user=FakeUser(PAVEL), bot=bot, reply_to=заявка)
    await support.from_team(ответ)
    assert _в(bot, 6, 'copy')


async def test_реплай_непонятно_на_что_подсказка():
    bot = FakeBot()
    чужое = FakeMessage(text=u'что-то', bot=bot, message_id=999999)
    ответ = FakeMessage(text=u'кому это?', user=FakeUser(ALEX), bot=bot, reply_to=чужое)
    await support.from_team(ответ)
    assert u'Не понял, кому это' in ответ.replies[-1]


def test_реплай_человека_не_принимается_за_ответ_команды():
    человек = FakeMessage(text=u'ок', user=FakeUser(1), reply_to=FakeMessage(message_id=5))
    assert not support._team_reply(человек)


# ------------------------------------------------------------ дожим

NOW = 2_000_000_000.0


def _запустил(uid, hours_ago, day1=True, answered=False):
    db.remember_user(uid, 'u%d' % uid, u'Человек')
    db.mark_launched(uid)
    db._run('UPDATE users SET launched_at=? WHERE user_id=?', (NOW - hours_ago * 3600, uid))
    if day1:
        db.log_event(uid, 'day', 1)
    if answered:
        db.save_answer(uid, 'day1', 'yes')


def _дожимы(bot):
    return [c for k, c, p in bot.sent if p == texts.NUDGE_DAY1]


async def test_через_5_часов_без_ответа_дожим_один_раз():
    nudge.since(NOW - 10 * 3600)                               # дожим действует давно
    _запустил(1, hours_ago=6)
    bot = FakeBot()
    assert await nudge.run_once(bot, now=NOW) == 1
    assert await nudge.run_once(bot, now=NOW + 60) == 0
    assert _дожимы(bot) == [1]


async def test_кто_ответил_рано_или_без_первого_дня_дожим_не_получает():
    nudge.since(NOW - 10 * 3600)
    _запустил(1, hours_ago=6, answered=True)                   # ответил
    _запустил(2, hours_ago=4)                                  # 5 часов не прошло
    _запустил(3, hours_ago=6, day1=False)                      # первый день не ушёл
    bot = FakeBot()
    await nudge.run_once(bot, now=NOW)
    assert _дожимы(bot) == []


async def test_задним_числом_прежним_молчунам_не_пишем():
    _запустил(1, hours_ago=30)                                 # запускал давно
    _запустил(2, hours_ago=4)                                  # незадолго до выкладки
    bot = FakeBot()
    await nudge.run_once(bot, now=NOW)                         # первый запуск ставит отметку
    assert _дожимы(bot) == []                                  # его 5 часов ещё не прошли
    await nudge.run_once(bot, now=NOW + 1.5 * 3600)
    assert _дожимы(bot) == [2]                                 # давний так и не получит


async def test_закрывший_бота_отмечен_и_больше_не_трогаем():
    nudge.since(NOW - 10 * 3600)
    _запустил(1, hours_ago=6)
    bot = FakeBot(forbidden=True)
    assert await nudge.run_once(bot, now=NOW) == 0
    assert db.get_user(1)['blocked_at'] is not None
    assert db.nudge_candidates(NOW - 10 * 3600, NOW) == []


async def test_пройти_заново_снова_разрешает_дожим():
    nudge.since(NOW - 10 * 3600)
    _запустил(1, hours_ago=6)
    await nudge.run_once(FakeBot(), now=NOW)
    db.reset_funnel(1)
    assert db.get_user(1)['nudged_at'] is None


# --------------------------------------------- дожим после 2-го и 3-го дня

def _спросили(uid, poll, hours_ago, answered=False):
    u"""Человек в марафоне, вопрос после дня ему ушёл hours_ago часов назад."""
    db.remember_user(uid, 'u%d' % uid, u'Человек')
    db.mark_launched(uid)
    db.log_event(uid, 'poll', poll)
    db._run("UPDATE events SET at=? WHERE user_id=? AND kind='poll' AND ref=?",
            (NOW - hours_ago * 3600, uid, poll))
    db.set_poll(uid, poll)
    if answered:
        db.save_answer(uid, poll, 'yes')
        db.set_poll(uid, None)


def _дожимы_по(bot, poll):
    return [c for k, c, p in bot.sent if p == texts.NUDGE_POLL[poll]]


async def test_молчуна_после_второго_дня_тоже_дожимаем_один_раз():
    nudge.since_poll('day2', NOW - 10 * 3600)
    _спросили(1, 'day2', hours_ago=6)
    bot = FakeBot()
    assert await nudge.run_once(bot, now=NOW) == 1
    assert await nudge.run_once(bot, now=NOW + 60) == 0
    assert _дожимы_по(bot, 'day2') == [1]


async def test_после_третьего_дня_свой_текст():
    nudge.since_poll('day3', NOW - 10 * 3600)
    _спросили(1, 'day3', hours_ago=6)
    bot = FakeBot()
    await nudge.run_once(bot, now=NOW)
    assert _дожимы_по(bot, 'day3') == [1]


async def test_кто_ответил_или_ждёт_меньше_пяти_часов_дожим_не_получает():
    for poll in ('day2', 'day3'):
        nudge.since_poll(poll, NOW - 10 * 3600)
    _спросили(1, 'day2', hours_ago=6, answered=True)            # ответил
    _спросили(2, 'day2', hours_ago=4)                           # 5 часов не прошло
    _спросили(3, 'day3', hours_ago=6, answered=True)
    bot = FakeBot()
    assert await nudge.run_once(bot, now=NOW) == 0


async def test_задним_числом_по_второму_дню_прежним_молчунам_не_пишем():
    _спросили(1, 'day2', hours_ago=30)                          # вопрос ушёл давно
    _спросили(2, 'day2', hours_ago=4)                           # незадолго до выкладки
    bot = FakeBot()
    await nudge.run_once(bot, now=NOW)                          # первый запуск ставит отметку
    assert _дожимы_по(bot, 'day2') == []
    await nudge.run_once(bot, now=NOW + 1.5 * 3600)
    assert _дожимы_по(bot, 'day2') == [2]                       # давний так и не получит


async def test_закрывший_бота_на_втором_дне_отмечен_и_больше_не_трогаем():
    nudge.since_poll('day2', NOW - 10 * 3600)
    _спросили(1, 'day2', hours_ago=6)
    bot = FakeBot(forbidden=True)
    assert await nudge.run_once(bot, now=NOW) == 0
    assert db.get_user(1)['blocked_at'] is not None
    assert db.poll_nudge_candidates('day2', NOW - 10 * 3600, NOW) == []


async def test_кого_воронка_увела_дальше_сама_дожим_не_получает():
    u"""Автопереход сейчас выключен; включат обратно — дожим не должен догонять."""
    nudge.since_poll('day2', NOW - 10 * 3600)
    _спросили(1, 'day2', hours_ago=6)
    db.set_poll(1, None)                                        # ветка «нет» ушла сама
    bot = FakeBot()
    assert await nudge.run_once(bot, now=NOW) == 0


async def test_пройти_заново_снова_разрешает_дожим_после_второго_дня():
    nudge.since_poll('day2', NOW - 10 * 3600)
    _спросили(1, 'day2', hours_ago=6)
    await nudge.run_once(FakeBot(), now=NOW)
    assert db.poll_nudge_candidates('day2', NOW - 10 * 3600, NOW) == []
    db.reset_funnel(1)
    _спросили(1, 'day2', hours_ago=6)
    assert db.poll_nudge_candidates('day2', NOW - 10 * 3600, NOW) == [1]


# ------------------------------------------------------------ /написать по ID или нику

from aiogram.exceptions import TelegramForbiddenError                     # noqa: E402
from aiogram.methods import SendMessage                                   # noqa: E402
from aiogram.types import MessageEntity                                   # noqa: E402


class ПочтаБот(FakeBot):
    u"""Запоминает, с какими параметрами ушло сообщение; closed — закрыли бота."""

    def __init__(self, closed=()):
        super().__init__()
        self.closed = set(closed)
        self.kw = {}

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.closed:
            raise TelegramForbiddenError(method=SendMessage(chat_id=chat_id, text=text),
                                         message='Forbidden: bot was blocked by the user')
        self.kw[chat_id] = kw
        return await self._record('text', chat_id, text)


def _команда(text, bot, user=ALEX, entities=None):
    m = FakeMessage(text=text, user=FakeUser(user), bot=bot)
    m.entities = entities
    return m


async def test_написать_по_id_уходит_человеку_и_дальше_можно_реплаем():
    db.remember_user(5, 'Aidyn2381', u'Айдын')
    bot = ПочтаБот()
    cmd = _команда(u'/написать 5 Посмотрели разборы?', bot)
    assert support._team_private(cmd)
    await support.on_write(cmd)
    assert _в(bot, 5) == [('text', u'Посмотрели разборы?')]
    assert bot.kw[5]['parse_mode'] is None                     # «<» в тексте не ломает отправку
    подтверждение = _последнее_в(bot, ALEX)
    assert u'Отправлено ✅' in подтверждение.text and 'Aidyn2381' in подтверждение.text
    ещё = FakeMessage(text=u'И вот ещё', user=FakeUser(ALEX), bot=bot, reply_to=подтверждение)
    await support.from_team(ещё)
    assert _в(bot, 5, 'copy')


async def test_написать_по_нику_без_учёта_регистра_и_по_ссылке():
    db.remember_user(6, 'Elena_Reiki28', u'Елена')
    bot = ПочтаБот()
    await support.on_write(_команда(u'/написать @elena_reiki28 Здравствуйте', bot))
    await support.on_write(_команда(u'/написать t.me/Elena_Reiki28\nВторое\nв две строки', bot))
    assert _в(bot, 6) == [('text', u'Здравствуйте'), ('text', u'Второе\nв две строки')]


async def test_оформление_текста_сохраняется_со_сдвигом_в_utf16():
    db.remember_user(7, 'olga', u'Ольга')
    bot = ПочтаБот()
    head = u'/написать 7 '
    body = u'👋 Привет, это важно'
    важно = body.index(u'важно')
    utf16 = lambda s: len(s.encode('utf-16-le')) // 2          # noqa: E731
    entities = [
        MessageEntity(type='bot_command', offset=0, length=utf16(u'/написать')),
        MessageEntity(type='bold', offset=utf16(head + body[:важно]), length=5),
    ]
    await support.on_write(_команда(head + body, bot, entities=entities))
    [bold] = bot.kw[7]['entities']
    assert (bold.type, bold.offset, bold.length) == ('bold', utf16(body[:важно]), 5)


async def test_без_текста_бот_спрашивает_и_реплаем_уходит_фото():
    db.remember_user(8, None, u'Oksana')
    bot = ПочтаБот()
    await support.on_write(_команда(u'/написать 8', bot))
    вопрос = _последнее_в(bot, ALEX)
    assert u'Кому:' in вопрос.text and u'без ника' in вопрос.text
    assert _в(bot, 8) == []
    фото = FakeMessage(user=FakeUser(ALEX), bot=bot, reply_to=вопрос, photo=['file'])
    await support.from_team(фото)
    assert _в(bot, 8, 'copy')


async def test_кого_нет_в_боте_не_пишем_и_объясняем():
    bot = ПочтаБот()
    for ref in (u'999', u'@nobody_here', u'кто-то'):
        cmd = _команда(u'/написать %s привет' % ref, bot)
        await support.on_write(cmd)
        assert u'Не нашёл в боте' in cmd.answers[-1]
    assert [c for k, c, p in bot.sent if c != ALEX] == []


async def test_без_адресата_подсказка_как_писать():
    cmd = _команда(u'/написать', ПочтаБот())
    await support.on_write(cmd)
    assert cmd.answers[-1] == texts.WRITE_USAGE


async def test_закрывший_бота_отмечен_и_команде_сказано():
    db.remember_user(9, 'gone', u'Aliya')
    bot = ПочтаБот(closed=[9])
    cmd = _команда(u'/написать 9 Здравствуйте', bot)
    await support.on_write(cmd)
    assert u'закрыл(а) бота' in cmd.answers[-1]
    assert db.get_user(9)['blocked_at'] is not None


def test_написать_могут_только_свои():
    assert not support._team_private(FakeMessage(text=u'/написать 5 привет', user=FakeUser(12345)))
    assert support._team_private(FakeMessage(text=u'/написать 5 привет', user=FakeUser(PAVEL)))


# ------------------------------------------------------------ «@ник текст» без команды

async def test_ник_в_начале_сообщения_команды_уходит_человеку():
    u"""AleX 17.09.2026 написал боту «@nataliya_famme : Добрый день!…»."""
    db.remember_user(11, 'nataliya_famme', u'Наталья')
    bot = ПочтаБот()
    msg = _команда(u'@nataliya_famme : Добрый день! Как удобнее оплатить?', bot)
    assert support._nick_message(msg)
    await support.on_nick_message(msg)
    assert _в(bot, 11) == [('text', u'Добрый день! Как удобнее оплатить?')]
    assert u'Отправлено ✅' in msg.answers[-1]


async def test_ник_без_текста_и_чужой_ник_не_отправляют():
    bot = ПочтаБот()
    assert not support._nick_message(_команда(u'@nataliya_famme', bot))
    assert not support._nick_message(FakeMessage(text=u'@nataliya_famme привет', user=FakeUser(12345)))
    незнакомый = _команда(u'@nobody_here привет', bot)
    await support.on_nick_message(незнакомый)
    assert u'Не нашёл в боте' in незнакомый.answers[-1]
