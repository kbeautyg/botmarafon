# -*- coding: utf-8 -*-
u"""Евгений — ИИ-помощник, который дожимает до «Энергетического спортзала».

AleX 11.10.2026: кнопка (Ai) в пульте подключает Евгения к диалогу; массово —
пользователь 11.10.2026: «Евгений должен дожимать тех, кто ничего после
марафона не сделал». ИИ здесь поддельный: проверяем, что Евгений говорит,
когда молчит, кого берёт и как отдаёт диалог команде.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, texts                              # noqa: E402
from bot.handlers import support                                          # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
ДЕНЬ = 86400


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'evgeny.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'AI_API_KEY', 'test-key')
    monkeypatch.setattr(evgeny, 'DEBOUNCE', 0.05)
    monkeypatch.setattr(evgeny, 'OPEN_PER_MINUTE', 6000)      # без пауз между первыми сообщениями
    evgeny._waiting.clear()
    evgeny._locks.clear()
    yield


class ИИ:
    u"""Поддельный ИИ: отвечает заготовками по очереди и запоминает запросы."""

    def __init__(self, *ответы):
        self.ответы = list(ответы)
        self.запросы = []

    async def __call__(self, messages, **kw):
        self.запросы.append(messages)
        return self.ответы.pop(0) if self.ответы else u'Хорошо.'


def _человек(uid, name=u'Анна', прошёл=True, давно=True):
    db.remember_user(uid, None, name)
    db.mark_launched(uid)
    if прошёл:
        when = time.time() - (4 * ДЕНЬ if давно else 3600)
        db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (?, 'day', '4', ?)", (uid, when))


def _в(bot, chat):
    return [(k, p) for k, c, p in bot.sent if c == chat]


# ------------------------------------------------------------------ разбор

def test_теги_разбираются_и_убираются_из_текста():
    text, tags = evgeny.parse(u'Отлично, рад! Вот кнопка 👇\n[BUY]')
    assert text == u'Отлично, рад! Вот кнопка 👇' and 'buy' in tags
    text, tags = evgeny.parse(u'Уточню у команды.\n[TEAM] есть ли рассрочка')
    assert tags['team'] == u'есть ли рассрочка' and '[TEAM]' not in text
    text, tags = evgeny.parse(u'Поняла, не буду беспокоить. Всего доброго!\n[STOP] не_интересно')
    assert 'stop' in tags and text.endswith(u'Всего доброго!')


def test_первое_сообщение_с_именем_и_без():
    import random
    for seed in range(8):
        с_именем = evgeny.opener(u'Анна', random.Random(seed))
        без_имени = evgeny.opener(u'', random.Random(seed))
        assert u'Анна' in с_именем and u'{name}' not in с_именем
        assert u'{name}' not in без_имени and без_имени[0].isupper() and u', !' not in без_имени
        assert u'Евгений' in без_имени


# ------------------------------------------------------------------ кого берёт массовый дожим

def test_массовый_дожим_только_прошедшим_и_ничего_не_сделавшим():
    _человек(1)                                   # прошёл, 4 дня назад, ничего не делал — берём
    _человек(2, давно=False)                      # 4-й день час назад — ещё рано
    _человек(3, прошёл=False)                     # марафон не прошёл
    _человек(4); db.add_purchase(4, 'gym')        # нажимал «купить»
    _человек(5); db.mark_blocked(5)               # закрыл бота
    _человек(6); db._run("INSERT INTO jobs (user_id, chain, pos, run_at) VALUES (6, 'after_day4', 0, 0)")
    _человек(7); db.evgeny_on(7, SHARP)          # Евгений уже писал
    _человек(PAVEL)                               # команда
    _человек(8, прошёл=False)
    db._run('UPDATE users SET closed_at=? WHERE user_id=8', (time.time(),))   # записи закрыты — прошёл
    assert sorted(p['user_id'] for p in evgeny.candidates()) == [1, 8]

    assert evgeny.push(SHARP) == 2
    assert evgeny.candidates() == [], 'второй раз тех же не берём'
    assert db.evgeny_counts()['pending'] == 2


async def test_массовый_дожим_пишет_днём_готовым_текстом(monkeypatch):
    _человек(1, u'Ольга')
    evgeny.push(SHARP)
    bot = FakeBot()
    фальшивый = ИИ()
    monkeypatch.setattr(ai, 'complete', фальшивый)

    monkeypatch.setattr(evgeny, 'daytime', lambda now=None: False)
    await evgeny.tick(bot)
    assert _в(bot, 1) == [], 'ночью не пишем'

    monkeypatch.setattr(evgeny, 'daytime', lambda now=None: True)
    await evgeny.tick(bot)
    (kind, text), = _в(bot, 1)
    assert u'Ольга' in text and u'Евгений' in text
    assert фальшивый.запросы == [], 'первое сообщение — готовый текст, без ИИ'
    (m,) = db.chat_history(1)
    assert m['author'] == evgeny.EVGENY and m['mass'] == 1
    assert db.evgeny_counts()['pending'] == 0


async def test_молчащему_напоминание_через_сутки_не_больше_двух(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    db._run('UPDATE evgeny SET last_out=? WHERE user_id=1', (time.time() - 2 * ДЕНЬ,))
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Анна, как вы? Получилось подумать о спортзале?'))
    monkeypatch.setattr(evgeny, 'daytime', lambda now=None: True)

    await evgeny.tick(bot)
    assert len(_в(bot, 1)) == 1
    await evgeny.tick(bot)
    assert len(_в(bot, 1)) == 1, 'следующее — только через сутки'
    db._run('UPDATE evgeny SET last_out=? WHERE user_id=1', (time.time() - 2 * ДЕНЬ,))
    await evgeny.tick(bot)
    db._run('UPDATE evgeny SET last_out=? WHERE user_id=1', (time.time() - 2 * ДЕНЬ,))
    await evgeny.tick(bot)
    assert len(_в(bot, 1)) == 2, 'не больше двух напоминаний'


# ------------------------------------------------------------------ разговор

async def test_человек_пишет_отвечает_евгений_а_команда_видит(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    bot = FakeBot()
    фальшивый = ИИ(u'Здорово, что откликнулось! Хотите продолжить в «Энергетическом спортзале»?')
    monkeypatch.setattr(ai, 'complete', фальшивый)

    вопрос = FakeMessage(text=u'Марафон понравился', user=FakeUser(1, 'anna', u'Анна'), bot=bot)
    await support.to_support(вопрос)
    assert вопрос.answers == [], 'без «Получили 🤝» — ответит Евгений'
    головы = [p for k, p in _в(bot, SHARP) if k == 'text']
    assert any(texts.EVGENY_MIRROR_HINT in p for p in головы), 'команда видит, что ведёт Евгений'

    await asyncio.sleep(0.2)
    ответы = [p for k, p in _в(bot, 1) if k == 'text']
    assert ответы == [u'Здорово, что откликнулось! Хотите продолжить в «Энергетическом спортзале»?']
    system = фальшивый.запросы[0][0]['content']
    assert u'ФАКТЫ О СПОРТЗАЛЕ' in system and u'12 000' in system
    assert {'role': 'user', 'content': u'Марафон понравился'} in фальшивый.запросы[0]


async def test_готов_купить_кнопка_вступления(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Отлично! Вступить можно по кнопке ниже 👇\n[BUY]'))
    await support.to_support(FakeMessage(text=u'Как оплатить?', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    i = [n for n, (k, c, p) in enumerate(bot.sent) if c == 1][-1]
    кнопки = bot.keys_sent[i].inline_keyboard
    assert кнопки[0][0].callback_data == 'buy:gym:evgeny'
    assert evgeny.is_on(1)


async def test_отказ_евгений_прощается_и_замолкает(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Поняла, больше не побеспокою. Всего доброго!\n[STOP]'))
    await support.to_support(FakeMessage(text=u'Не пишите мне', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert [p for k, p in _в(bot, 1)][-1].startswith(u'Поняла')
    assert not evgeny.is_on(1)
    assert any(u'Евгений закончил диалог' in p for k, p in _в(bot, SHARP))


async def test_нет_ответа_в_фактах_зовёт_команду(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'[TEAM] есть ли рассрочка'))
    await support.to_support(FakeMessage(text=u'А рассрочка есть?', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert [p for k, p in _в(bot, 1)][-1] == texts.EVGENY_FALLBACK
    assert not evgeny.is_on(1)
    assert any(u'есть ли рассрочка' in p for k, p in _в(bot, SHARP))


async def test_голосовое_просим_текстом(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    bot = FakeBot()
    фальшивый = ИИ()
    monkeypatch.setattr(ai, 'complete', фальшивый)
    голос = FakeMessage(user=FakeUser(1), bot=bot)
    голос.voice = type('Voice', (), {'file_id': 'v1'})()
    await support.to_support(голос)
    await asyncio.sleep(0.1)
    assert [p for k, p in _в(bot, 1)] == [texts.EVGENY_VOICE] and фальшивый.запросы == []


async def test_сбой_ии_команде_сказано(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    bot = FakeBot()

    async def сбой(messages, **kw):
        raise ai.AIError(u'на ключе ИИ закончились деньги')

    monkeypatch.setattr(ai, 'complete', сбой)
    await support.to_support(FakeMessage(text=u'Привет', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert _в(bot, 1) == []
    assert any(u'закончились деньги' in p for k, p in _в(bot, SHARP))


async def test_команда_ответила_сама_евгений_выключается(monkeypatch):
    _человек(1)
    db.evgeny_on(1, SHARP)
    assert evgeny.team_wrote(1, u'Павел') is True
    assert not evgeny.is_on(1)
    assert db.evgeny_get(1)['reason'].startswith(u'ответил(а) сам(а)')


# ------------------------------------------------------------------ включение из пульта

async def test_включить_без_ключа_нельзя(monkeypatch):
    _человек(1)
    monkeypatch.setattr(config, 'AI_API_KEY', '')
    assert u'ключа' in evgeny.enable(FakeBot(), 1, SHARP)
    assert not evgeny.is_on(1)


async def test_включили_из_пульта_евгений_сразу_пишет(monkeypatch):
    _человек(1)
    bot = FakeBot()
    фальшивый = ИИ(u'Анна, здравствуйте! Это Евгений из команды Павла Андреева. Как вам марафон?')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    assert evgeny.enable(bot, 1, SHARP) == ''
    await asyncio.sleep(0.2)
    assert [p for k, p in _в(bot, 1)][0].startswith(u'Анна, здравствуйте')
    assert фальшивый.запросы[0][-1]['content'] == texts.EVGENY_OPEN_TASK


async def test_включили_когда_человек_ждёт_ответа_евгений_отвечает(monkeypatch):
    _человек(1)
    db.save_message(1, 'in', 'text', u'Сколько стоит спортзал?')
    bot = FakeBot()
    фальшивый = ИИ(u'12 000 ₽ за месяц — примерно 400 ₽ в день.')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    evgeny.enable(bot, 1, SHARP)
    await asyncio.sleep(0.2)
    assert фальшивый.запросы[0][-1]['content'] == texts.EVGENY_REPLY_TASK


async def test_команде_и_закрывшим_бота_евгения_не_включить():
    _человек(PAVEL)
    assert u'команды' in evgeny.enable(FakeBot(), PAVEL, SHARP)
    _человек(1)
    db.mark_blocked(1)
    assert u'закрыл бота' in evgeny.enable(FakeBot(), 1, SHARP)


def test_факты_правятся_и_возвращаются():
    assert evgeny.facts() == texts.EVGENY_FACTS
    evgeny.set_facts(u'Акция: месяц в подарок до 20.10')
    assert evgeny.facts() == u'Акция: месяц в подарок до 20.10'
    evgeny.set_facts('')
    assert evgeny.facts() == texts.EVGENY_FACTS


# ------------------------------------------------------------------ пульт

async def test_кнопка_в_пульте_включает_и_выключает_евгения(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from bot import web
    from tests.test_panel import TOKEN, подпись
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'ADMIN_IDS', (ALEX,))
    web._hits.clear()
    web._window_from[0] = 0.0
    _человек(1)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Анна, здравствуйте! Это Евгений. Как вам марафон?'))
    bot = FakeBot()
    client = TestClient(TestServer(web.build(bot)))
    await client.start_server()
    try:
        r = await client.post('/api/ai', json={'initData': подпись(ALEX), 'id': 1, 'on': True})
        assert (await r.json())['ai'] is True
        await asyncio.sleep(0.3)
        r = await client.post('/api/chat', json={'initData': подпись(ALEX), 'id': 1})
        data = await r.json()
        assert data['person']['ai'] is True
        assert [m['ai'] for m in data['messages'] if m.get('mine')] == [True]

        r = await client.post('/api/send', json={'initData': подпись(ALEX), 'id': 1, 'text': u'Это Павел'})
        data = await r.json()
        assert data['ai'] is False and data['ai_stopped'] is True, 'написали сами — Евгений выключился'

        monkeypatch.setattr(config, 'AI_API_KEY', '')
        r = await client.post('/api/ai', json={'initData': подпись(ALEX), 'id': 1, 'on': True})
        assert r.status == 409 and u'ключа' in (await r.json())['error']
    finally:
        await client.close()
