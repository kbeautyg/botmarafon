# -*- coding: utf-8 -*-
u"""Дожим «заходи на марафон» — и кнопки в нём.

AleX 14.09.2026: «кто зашёл в бота и не ответил да или нет, то через 5
часов прислать ему текст». Текст и присылался — но одним текстом: человека
звали вернуться, а нажать было нечего. Кнопки остались выше в переписке, у
кого-то она и вовсе очищена. Дожим — последняя страховка перед тем, как
человек выпадет из воронки навсегда, и приходить пустым он не должен
(аудит 20.09.2026).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, nudge, texts                                 # noqa: E402
from tests.fakes import FakeBot                                          # noqa: E402

ЧАС = 3600


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'nudge.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (777,))
    # Дожим намеренно не пишет задним числом: при первом запуске он
    # отмечает «с этого момента» и прежних молчунов не трогает. В тесте
    # молчун как раз прежний — отматываем отметку к началу времён.
    for key in (nudge.SINCE,) + tuple(nudge.SINCE_POLL % p for p in nudge.LATER_POLLS):
        db.put_content(key, 'nudge', repr(0.0))
    yield


def _кнопки(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


def _молчун(uid, day=1, давно=6 * ЧАС):
    u"""Человек получил день `day` и с тех пор молчит."""
    db.remember_user(uid, 'u%d' % uid, u'Человек')
    db.mark_launched(uid)
    db.log_event(uid, 'day', day)
    db.set_poll(uid, 'day%d' % day)
    db.log_event(uid, 'poll', 'day%d' % day)
    было = time.time() - давно
    db._run('UPDATE users SET launched_at=? WHERE user_id=?', (было, uid))
    db._run('UPDATE events SET at=? WHERE user_id=?', (было, uid))


async def test_дожим_первого_дня_приходит_с_кнопками():
    _молчун(1, day=1)
    bot = FakeBot()

    assert await nudge.run_once(bot, time.time()) == 1

    (kind, chat, text), keys = bot.sent[0], bot.keys_sent[0]
    assert (kind, chat, text) == ('text', 1, texts.NUDGE_DAY1)
    assert _кнопки(keys) == ['poll:day1:yes', 'poll:day1:no']


async def test_дожим_второго_дня_даёт_кнопки_своего_вопроса():
    _молчун(2, day=2)
    bot = FakeBot()

    assert await nudge.run_once(bot, time.time()) == 1
    assert _кнопки(bot.keys_sent[0]) == ['poll:day2:yes', 'poll:day2:no']


async def test_если_вопрос_уже_закрыт_кнопок_не_даём():
    u"""Ответ мог прийти между выборкой и отправкой: живая кнопка от
    закрытого вопроса ответила бы «этот вопрос уже закрыт»."""
    _молчун(1, day=1)
    db.set_poll(1, None)
    db.save_answer(1, 'day1', 'yes')
    bot = FakeBot()

    await nudge.run_once(bot, time.time())
    assert bot.keys_sent == [] or bot.keys_sent[0] is None


async def test_дожим_виден_в_переписке_пульта():
    _молчун(1, day=1)
    await nudge.run_once(FakeBot(), time.time())
    [m] = db.chat_history(1)
    assert (m['side'], m['text'], m['mass']) == ('out', texts.NUDGE_DAY1, 1)
