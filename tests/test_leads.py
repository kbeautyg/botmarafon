# -*- coding: utf-8 -*-
u"""Марафон людям с заявки с сайта: новым — сразу, прежним — по кнопке админа.

AleX 14.09.2026: «бот видит тех, кто вбил заявку, но ничего не делает — пусть
сразу запускается марафон первым сообщением и первым кружком». Sharp
подтвердил для новых и для прежних; прежним — только после списка и кнопки.
"""
import os
import sys

import pytest
from aiogram.exceptions import TelegramForbiddenError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, insights, leads, texts                        # noqa: E402
from bot.handlers import admin, start                                     # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser          # noqa: E402

ADMIN = 777
PAVEL = 312701042


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'leads.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL,))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(leads, 'PAUSE', 0)
    start._launching.clear()
    yield


def _прежний(uid, name=u'Maija', nick=None, no=None, blocked=False):
    db.remember_user(uid, nick, name, 'zayavka')
    if no:
        db.set_lead_no(uid, no)
    if blocked:
        db.mark_blocked(uid)


def _лично(bot, chat):
    return [text for kind, c, text in bot.sent if c == chat and kind == 'text']


# ------------------------------------------------------------- новые

async def test_новый_по_заявке_сразу_получает_марафон():
    сообщение = FakeMessage(text='/start zayavka70', user=FakeUser(1, None, u'Inna'))
    await start.on_start(сообщение)
    assert db.get_user(1)['launched_at'] is not None
    assert [j['chain'] for j in db.user_jobs(1)] == ['launch']
    assert u'№70' in сообщение.answers[0] and u'подарок' in сообщение.answers[0]


async def test_повторный_вход_по_заявке_марафон_не_удваивает_и_не_врёт():
    await start.on_start(FakeMessage(text='/start zayavka70', user=FakeUser(1)))
    второй = FakeMessage(text='/start zayavka70', user=FakeUser(1))
    await start.on_start(второй)
    assert [(j['chain'], j['pos']) for j in db.user_jobs(1)] == [('launch', 0)]
    assert u'подарок' not in второй.answers[0]


async def test_сбой_приветствия_заявки_марафон_не_отменяет():
    сообщение = FakeMessage(text='/start zayavka71', user=FakeUser(2))

    async def падает(*args, **kw):
        raise RuntimeError(u'нет связи')
    сообщение.answer = падает

    with pytest.raises(RuntimeError):
        await start.on_start(сообщение)
    assert 'launch' in db.pending_chains(2)


# ------------------------------------------------------------ прежние

def test_в_списке_только_прежние_без_марафона_и_без_команды():
    _прежний(1)
    _прежний(2, blocked=True)                  # закрыл бота — писать некуда
    _прежний(3)
    db.mark_launched(3)                        # марафон уже идёт
    _прежний(PAVEL)                            # команда проекта
    assert [p['user_id'] for p in leads.pending()] == [1]


async def test_список_админу_уходит_один_раз():
    _прежний(1, name=u'Maija', no=70)
    bot = FakeBot()
    await leads.offer_backfill(bot)
    await leads.offer_backfill(bot)            # перезапуск бота
    (текст,) = _лично(bot, ADMIN)
    assert u'tg://user?id=1' in текст and u'заявка №70' in текст
    assert u'Люди с заявки с сайта без марафона: 1' in текст


async def test_нечего_слать_админ_ничего_не_получает():
    bot = FakeBot()
    await leads.offer_backfill(bot)
    assert _лично(bot, ADMIN) == []


async def test_список_не_дошёл_попробуем_при_следующем_запуске():
    _прежний(1)
    await leads.offer_backfill(FakeBot(fail_times=1))
    bot = FakeBot()
    await leads.offer_backfill(bot)
    assert len(_лично(bot, ADMIN)) == 1


async def test_по_кнопке_каждому_сообщение_и_марафон():
    _прежний(1)
    _прежний(2)
    bot = FakeBot()
    await admin.on_leads_launch(FakeCall('leads:launch', user=FakeUser(ADMIN), bot=bot))
    for uid in (1, 2):
        assert _лично(bot, uid) == [texts.LEAD_GIFT]
        assert [j['chain'] for j in db.user_jobs(uid)] == ['launch']
    assert u'Марафон запущен: 2' in _лично(bot, ADMIN)[-1]
    assert leads.pending() == []


async def test_закрывший_бота_пропускается():
    class ВторойЗакрыл(FakeBot):
        async def send_message(self, chat_id, text, **kw):
            if chat_id == 2:
                raise TelegramForbiddenError(method=None, message=u'bot was blocked by the user')
            return await super().send_message(chat_id, text, **kw)

    _прежний(1)
    _прежний(2)
    bot = ВторойЗакрыл()
    итог = await leads.launch_all(bot)
    assert итог == {'done': 1, 'closed': 1, 'failed': 0}
    assert db.get_user(2)['blocked_at'] is not None and db.user_jobs(2) == []


async def test_второе_нажатие_никому_не_шлёт_повторно():
    _прежний(1)
    bot = FakeBot()
    for _ in range(2):
        await admin.on_leads_launch(FakeCall('leads:launch', user=FakeUser(ADMIN), bot=bot))
    assert _лично(bot, 1) == [texts.LEAD_GIFT]


async def test_кнопку_нажал_не_админ_ничего_не_уходит():
    _прежний(1)
    bot = FakeBot()
    await admin.on_leads_launch(FakeCall('leads:launch', user=FakeUser(PAVEL), bot=bot))
    assert _лично(bot, 1) == [] and len(leads.pending()) == 1


async def test_команда_заявки_показывает_список():
    _прежний(1, name=u'Merunas')
    сообщение = FakeMessage(text='/zayavki', user=FakeUser(ADMIN))
    await admin.on_leads(сообщение)
    assert u'Merunas' in сообщение.answers[0]


# ---------------------------------------------------------- статистика

def test_запустившие_с_заявки_входят_в_воронку_ждущие_отдельной_строкой():
    _прежний(1)                                # марафон не получал
    _прежний(2)
    db.mark_launched(2)                        # получил марафон по заявке
    db.remember_user(3, 'x', u'Обычный', 'ig')
    db.mark_launched(3)

    m = insights.model()
    воронка = insights.cohort(m, 0, 10 ** 12)
    assert {p['u']['user_id'] for p in воронка} == {2, 3}
    assert [p['u']['user_id'] for p in insights.cohort(m, 0, 10 ** 12, leads=True)] == [1]
    сводка = insights.render('sum', 'all')
    assert u'Пришли по заявке с сайта, марафон не получали: 1' in сводка


def test_когда_ждущих_нет_строки_про_заявки_нет():
    _прежний(2)
    db.mark_launched(2)
    assert u'марафон не получали' not in insights.render('sum', 'all')
