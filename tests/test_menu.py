# -*- coding: utf-8 -*-
u"""Меню кнопками для команды (AleX 19.09.2026).

«А нельзя это кнопками всё прилепить, чтобы команду не называть». Команды
остались, но держать их в голове больше не нужно — и проверяем именно то,
что кнопка делает ровно то же, что команда, а посторонний её не нажмёт.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, keyboards, texts                             # noqa: E402
from bot.handlers import admin, start                                    # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser         # noqa: E402

СВОЙ = 350631550
ЧУЖОЙ = 424242


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'menu.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'TEAM_STATS_IDS', ())
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.setattr(config, 'WEBAPP_URL', 'https://bot.example.com')
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    start._launching.clear()
    yield


def _кнопки(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_свой_нажал_запустить_и_получил_меню_а_не_марафон():
    bot = FakeBot()
    сообщение = FakeMessage(text='/start', user=FakeUser(СВОЙ), bot=bot)
    await start.on_start(сообщение)

    assert texts.MENU_TITLE in сообщение.answers[0]
    assert db.pending_chains(СВОЙ) == set()          # воронка себе не ушла


async def test_человеку_старт_по_прежнему_запускает_марафон():
    bot = FakeBot()
    await start.on_start(FakeMessage(text='/start', user=FakeUser(ЧУЖОЙ), bot=bot))
    assert 'launch' in db.pending_chains(ЧУЖОЙ)


async def test_в_меню_есть_всё_что_просили_кнопками():
    названия = _кнопки(keyboards.menu())
    assert texts.PANEL_BUTTON.split()[-1] in ' '.join(названия) or texts.PANEL_BUTTON in названия
    for кнопка in (texts.MENU_BROADCAST, texts.MENU_DAILY, texts.MENU_STATS,
                   texts.MENU_WHO, texts.MENU_BAN, texts.MENU_LINKS, texts.MENU_CHATS):
        assert кнопка in названия, кнопка


async def test_кнопка_написать_всем_запускает_рассылку_как_команда():
    bot = FakeBot()
    call = FakeCall('mn:bc', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_menu_button_pressed(call)

    assert texts.BROADCAST_ASK_MESSAGE in call.message.answers[-1]
    assert admin.waiting_broadcast(FakeMessage(text=u'что разослать', user=FakeUser(СВОЙ)))


async def test_кнопка_отчёта_даёт_тот_же_отчёт():
    bot = FakeBot()
    call = FakeCall('mn:day', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_menu_button_pressed(call)
    assert u'Отчёт за' in call.message.answers[-1]


async def test_кнопка_чёрного_списка_подсказывает_как_вносить():
    bot = FakeBot()
    call = FakeCall('mn:ban', user=FakeUser(СВОЙ), bot=bot)
    await admin.on_menu_button_pressed(call)
    assert u'/чс' in call.message.answers[-1]


async def test_посторонний_кнопки_меню_не_нажмёт():
    bot = FakeBot()
    call = FakeCall('mn:bc', user=FakeUser(ЧУЖОЙ), bot=bot)
    await admin.on_menu_button_pressed(call)
    assert call.message.answers == []
    assert not admin.waiting_broadcast(FakeMessage(text=u'текст', user=FakeUser(ЧУЖОЙ)))


async def test_кнопка_меню_под_полем_открывает_меню():
    bot = FakeBot()
    сообщение = FakeMessage(text=texts.MENU_BUTTON, user=FakeUser(СВОЙ), bot=bot)
    await admin.on_menu_button(сообщение)
    assert texts.MENU_TITLE in сообщение.answers[-1]


async def test_без_адреса_пульта_меню_всё_равно_работает(monkeypatch):
    monkeypatch.setattr(config, 'WEBAPP_URL', '')
    названия = _кнопки(keyboards.menu())
    assert texts.PANEL_BUTTON not in названия
    assert texts.MENU_BROADCAST in названия


# ------------------------------------------ пульт в рабочем чате
#
# Мини-приложение телеграм открывает только из личной переписки. Кнопку в
# группе он не просто не показывает — он отвергает ВСЮ клавиатуру, а с ней
# и сообщение: до 20.09.2026 /меню и /пульт из рабочего чата не приходили
# вовсе, молча.

def test_в_рабочем_чате_кнопки_пульта_в_меню_нет():
    assert texts.PANEL_BUTTON in _кнопки(keyboards.menu(СВОЙ))        # личка
    assert texts.PANEL_BUTTON not in _кнопки(keyboards.menu(-1001234567890))
    # остальное на месте — меню в группе работать обязано
    assert texts.MENU_BROADCAST in _кнопки(keyboards.menu(-1001234567890))


async def test_пульт_из_рабочего_чата_говорит_куда_идти():
    группа = FakeMessage(text=u'/пульт', user=FakeUser(СВОЙ), chat_id=-1001234567890)
    await admin.on_panel(группа)
    assert u'только в личной переписке' in группа.answers[-1]
    assert группа.markups[-1] is None

    личка = FakeMessage(text=u'/пульт', user=FakeUser(СВОЙ))
    await admin.on_panel(личка)
    assert _кнопки(личка.markups[-1]) == [texts.PANEL_BUTTON]


async def test_меню_из_рабочего_чата_приходит_без_пульта():
    группа = FakeMessage(text=u'/меню', user=FakeUser(СВОЙ), chat_id=-1001234567890)
    await admin.on_menu(группа)
    assert texts.PANEL_BUTTON not in _кнопки(группа.markups[0])
    assert texts.MENU_STATS in _кнопки(группа.markups[0])
