# -*- coding: utf-8 -*-
u"""Проверка Евгения командой, пока участникам он не пишет (Sharp 07.11.2026).

«Не запускай пока. Нужно протестить Евгения администрации… там всегда будет
кнопка — заставить Евгения написать, и Евгений начинает сначала… А сейчас
убери, чтобы клиенты с ним не общались. Только Павел, Алекс и я».

Участникам координатор пишет, только когда команда включила его (/евгений).
Своим — всегда: кнопка «🤖 Проверить Евгения» стирает прошлую проверку, и
Евгений знакомится заново; что получила бы команда, видит только проверяющий.
ИИ здесь поддельный.
"""
import asyncio
import os
import sys
import time

import pytest
from aiogram import Bot

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, keyboards, texts                 # noqa: E402
from bot.handlers import admin, support                                   # noqa: E402
from tests.fakes import FakeBot, FakeCall, FakeMessage, FakeUser, ЗаписьСессия  # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
КЛИЕНТ = 5


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'try.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'WEBAPP_URL', 'https://bot.example.com')
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'AI_API_KEY', 'test-key')
    monkeypatch.setattr(evgeny, 'DEBOUNCE', 0.05)
    monkeypatch.setattr(evgeny, '_typing_seconds', lambda text: 0)
    evgeny._waiting.clear()
    evgeny._locks.clear()
    evgeny._voice_told.clear()
    for day in (1, 2, 3, 4):
        db.put_content('day%d' % day, 'link', 'https://example.com/day%d' % day)
    yield


class ИИ:
    u"""Поддельный ИИ: отвечает заготовками по очереди и запоминает запросы."""

    def __init__(self, *ответы):
        self.ответы = list(ответы)
        self.запросы = []

    async def __call__(self, messages, **kw):
        self.запросы.append(messages)
        return self.ответы.pop(0) if self.ответы else u'Хорошо.'


ПОЯСНЕНИЕ = texts.EVGENY_TRY_NOTE.split(u'\n', 1)[0]     # «🧪 Проверка Евгения» с кнопками дней


def _кому(bot, uid):
    u"""Что пришло в личку — без пояснения о проверке: с 11.11.2026 оно идёт после
    знакомства Евгения (кнопки дней ниже его), а проверяют здесь слова Евгения."""
    return [p for k, c, p in bot.sent if c == uid and k == 'text' and not p.startswith(ПОЯСНЕНИЕ)]


async def _начать(bot, кто=PAVEL):
    сообщение = FakeMessage(text=texts.EVGENY_TRY_BUTTON, user=FakeUser(кто), bot=bot)
    await admin.on_evgeny_try(сообщение)
    return сообщение


async def _написать(bot, кто, текст):
    сообщение = FakeMessage(text=текст, user=FakeUser(кто), bot=bot)
    await support.to_support(сообщение)
    await asyncio.sleep(0.2)
    return сообщение


def _карточка(запрос):
    return next(m['content'] for m in запрос if m['content'].startswith(u'ЧТО ИЗВЕСТНО'))


# ------------------------------------------------------------------ участникам — нет

async def test_участникам_координатор_не_пишет_пока_команда_не_включила(monkeypatch):
    from tests.test_coach import _день, _шаг
    db.remember_user(КЛИЕНТ, 'anna', u'Анна')
    db.mark_launched(КЛИЕНТ)
    bot = FakeBot()
    assert not evgeny.coach_enabled(), 'новый бот: координатор выключен, пока не включат'
    await _шаг(bot, КЛИЕНТ, 'launch', _день('launch'))
    assert 'evgeny_intro' not in db.pending_chains(КЛИЕНТ)
    await _шаг(bot, КЛИЕНТ, 'evgeny_intro')                  # и старый шаг из очереди молчит
    assert texts.EVGENY_COACH_INTRO not in _кому(bot, КЛИЕНТ)

    фальшивый = ИИ(u'Не должен ответить')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    вопрос = await _написать(bot, КЛИЕНТ, u'Было тепло в руках')
    assert вопрос.answers and вопрос.answers[0].startswith(u'Получили'), 'человеку отвечает команда'
    assert фальшивый.запросы == []


async def test_команда_включила_координатора_участникам_пишет():
    from tests.test_coach import _день, _шаг
    evgeny.set_coach(True)
    db.remember_user(КЛИЕНТ, 'anna', u'Анна')
    db.mark_launched(КЛИЕНТ)
    await _шаг(FakeBot(), КЛИЕНТ, 'launch', _день('launch'))
    assert 'evgeny_intro' in db.pending_chains(КЛИЕНТ)


# ------------------------------------------------------------------ кнопка проверки

async def test_кнопка_евгений_начинает_заново_и_знакомится(monkeypatch):
    bot = FakeBot()
    нажал = await _начать(bot)
    assert texts.EVGENY_TRY_STATE_OFF in нажал.answers[0], 'сказано, что участникам он не пишет'
    assert нажал.markups[0] is not None, 'под пояснением — кнопки дней'
    assert _кому(bot, PAVEL)[-1] == texts.EVGENY_COACH_INTRO
    assert evgeny.listening(PAVEL)

    фальшивый = ИИ(u'Тепло в руках — энергия пошла 🙏 Что ещё почувствовал?')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    ответ = await _написать(bot, PAVEL, u'Посмотрел первый день, было тепло в руках')
    assert ответ.answers == [], 'не «это сообщение от команды»'
    assert _кому(bot, PAVEL)[-1].startswith(u'Тепло в руках')
    assert not _кому(bot, SHARP) and not _кому(bot, ALEX), 'остальной команде не дублируем'
    карточка = _карточка(фальшивый.запросы[0])
    assert u'получил записи дней 1.' in карточка, 'как участник после первого дня'


async def test_повторное_нажатие_стирает_прошлую_проверку(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Слева — женская линия рода. [FEEDBACK 1]'))
    await _написать(bot, PAVEL, u'Слева тянуло')
    assert db.feedback_days(PAVEL) == {1}

    await _начать(bot)
    assert db.feedback_days(PAVEL) == set()
    assert [m['text'] for m in db.chat_history(PAVEL)] == [texts.EVGENY_COACH_INTRO]
    assert _кому(bot, PAVEL)[-1] == texts.EVGENY_COACH_INTRO, 'знакомство снова'


async def test_кнопка_проверки_и_меню_работают_у_павла_и_алекса_по_настоящему(dispatcher):
    u"""07.11.2026: «🧰 Меню» у Павла и AleX уходило в заботу — фильтр админки
    пропускал текст только от админов."""
    def _нажать(кто, текст, n):
        return {'update_id': n, 'message': {
            'message_id': n, 'date': 1_760_000_000, 'text': текст,
            'chat': {'id': кто, 'type': 'private'},
            'from': {'id': кто, 'is_bot': False, 'first_name': 'X'}}}

    for n, кто in enumerate((PAVEL, ALEX), 1):
        сессия = ЗаписьСессия()
        await dispatcher.feed_raw_update(Bot('42:TEST', session=сессия), _нажать(кто, texts.MENU_BUTTON, n))
        тексты = [getattr(c, 'text', None) or '' for c in сессия.calls]
        assert any(texts.MENU_TITLE in t for t in тексты), кто

        сессия = ЗаписьСессия()
        await dispatcher.feed_raw_update(Bot('42:TEST', session=сессия),
                                         _нажать(кто, texts.EVGENY_TRY_BUTTON, n + 10))
        тексты = [getattr(c, 'text', None) or '' for c in сессия.calls]
        assert texts.EVGENY_COACH_INTRO in тексты, кто
        assert not any(texts.TEAM_HINT in t for t in тексты)


def test_кнопка_под_полем_ввода_и_в_меню():
    названия = [b.text for row in keyboards.team_keys().keyboard for b in row]
    assert названия == [texts.MENU_BUTTON, texts.EVGENY_TRY_BUTTON]
    в_меню = [b.callback_data for row in keyboards.menu().inline_keyboard for b in row]
    assert 'mn:evgeny' in в_меню


async def test_кнопка_в_меню_и_в_евгении_тоже_начинают_проверку():
    bot = FakeBot()
    call = FakeCall('mn:evgeny', user=FakeUser(ALEX), bot=bot)
    await admin.on_menu_button_pressed(call)
    assert _кому(bot, ALEX)[-1] == texts.EVGENY_COACH_INTRO

    call = FakeCall('ev:try', user=FakeUser(SHARP), bot=bot)
    await admin.on_evgeny_try_button(call)
    assert _кому(bot, SHARP)[-1] == texts.EVGENY_COACH_INTRO


async def test_участнику_проверка_не_открывается(monkeypatch):
    db.remember_user(КЛИЕНТ, 'anna', u'Анна')
    assert await evgeny.try_start(КЛИЕНТ, 'anna', u'Анна') == texts.EVGENY_TRY_TEAM_ONLY
    bot = FakeBot()
    фальшивый = ИИ(u'Не должен ответить')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    вопрос = await _написать(bot, КЛИЕНТ, texts.EVGENY_TRY_BUTTON)
    assert вопрос.answers[0].startswith(u'Получили'), 'для участника это просто сообщение команде'
    assert фальшивый.запросы == []


async def test_без_ключа_проверка_не_начинается(monkeypatch):
    monkeypatch.setattr(config, 'AI_API_KEY', '')
    bot = FakeBot()
    нажал = await _начать(bot)
    assert нажал.answers == [texts.EVGENY_NO_KEY]
    assert not evgeny.is_on(PAVEL)


async def test_в_рабочем_чате_зовёт_в_личку():
    bot = FakeBot()
    call = FakeCall('mn:evgeny', user=FakeUser(ALEX), bot=bot,
                    message=FakeMessage(bot=bot, user=FakeUser(ALEX), chat_id=-100500))
    await admin.on_menu_button_pressed(call)
    assert call.message.answers == [texts.EVGENY_TRY_PRIVATE]
    assert not evgeny.is_on(ALEX)


# ------------------------------------------------------------------ что видит команда

async def test_про_оплату_в_проверке_только_проверяющему(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] спрашивает, сколько стоит спортзал'))
    await _написать(bot, PAVEL, u'Сколько стоит спортзал?')
    уведомления = [p for p in _кому(bot, PAVEL) if u'Пишет про оплату' in p]
    assert len(уведомления) == 1 and уведомления[0].startswith(texts.EVGENY_TRY_TEAM)
    assert not _кому(bot, SHARP) and not _кому(bot, ALEX)
    i = next(n for n, (k, c, p) in enumerate(bot.sent) if u'Пишет про оплату' in str(p))
    кнопки = [b.text for row in bot.keys_sent[i].inline_keyboard for b in row]
    assert texts.BAN_BUTTON not in кнопки, 'себя в чёрный список не внести'

    await evgeny.pay_tick(bot, time.time() + evgeny.PAY_WAIT + 1)
    assert texts.EVGENY_PAY_COLLEAGUE in _кому(bot, PAVEL)
    assert any(u'Срочно ответьте' in p for p in _кому(bot, PAVEL))
    assert not _кому(bot, SHARP) and not _кому(bot, ALEX)


async def test_реплай_на_сообщение_евгения_ответ_ему(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Понимаю тебя 🙏'))
    сообщение_евгения = FakeMessage(text=texts.EVGENY_COACH_INTRO, message_id=999, bot=bot)
    ответ = FakeMessage(text=u'Спасибо, начну сегодня', user=FakeUser(PAVEL), bot=bot,
                        reply_to=сообщение_евгения)
    await support.from_team(ответ)
    await asyncio.sleep(0.2)
    assert ответ.replies == [], 'не «Не понял, кому это»'
    assert _кому(bot, PAVEL)[-1] == u'Понимаю тебя 🙏'


async def test_запись_дня_в_проверке_пометкой(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Держи первый день ещё раз 🙏 [DAY 1]'))
    await _написать(bot, PAVEL, u'Пришли первый день ещё раз')
    assert texts.EVGENY_TRY_DAY_NOTE.format(day=1) in _кому(bot, PAVEL)
    assert not [k for k, c, p in bot.sent if k == 'video']


# ------------------------------------------------------------------ дни марафона в проверке

async def test_кнопки_дней_спрашивают_как_участника(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    assert await evgeny.try_day(bot, PAVEL, 1) == ''
    assert _кому(bot, PAVEL)[-1] == texts.EVGENY_COACH_ASK.format(day=texts.EVGENY_COACH_DAY[1])

    фальшивый = ИИ(u'Слева — женская линия рода 🙏 [FEEDBACK 1]')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _написать(bot, PAVEL, u'Слева тянуло в плече')
    карточка = _карточка(фальшивый.запросы[0])
    assert u'получил записи дней 1, 2.' in карточка and u'после 1-го дня — «Да»' in карточка

    assert await evgeny.try_day(bot, PAVEL, 1) == texts.EVGENY_TRY_TOLD.format(day=1)
    assert await evgeny.try_day(bot, PAVEL, 2) == ''
    assert _кому(bot, PAVEL)[-1] == texts.EVGENY_COACH_AGAIN.format(
        after=texts.EVGENY_COACH_AFTER[1], sphere=texts.EVGENY_COACH_SPHERE[2])


async def test_день_через_кнопку_вызова_и_всплывашка():
    bot = FakeBot()
    await _начать(bot)
    call = FakeCall('ev:t:3', user=FakeUser(PAVEL), bot=bot)
    await admin.on_evgeny_try_button(call)
    assert call.answers == ['']
    assert _кому(bot, PAVEL)[-1] == texts.EVGENY_COACH_ASK.format(day=texts.EVGENY_COACH_DAY[3])


async def test_попросил_не_писать_евгений_молчит_как_с_участником(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Хорошо, больше не пишу 🙏 [STOP]'))
    await _написать(bot, PAVEL, u'Не пиши мне больше')
    assert not evgeny.listening(PAVEL)
    assert await evgeny.try_day(bot, PAVEL, 2) == texts.EVGENY_TRY_HELD


# ------------------------------------------------------------------ конец проверки

async def test_закончить_проверку_и_снова_команда():
    bot = FakeBot()
    await _начать(bot)
    call = FakeCall('ev:t:end', user=FakeUser(PAVEL), bot=bot)
    await admin.on_evgeny_try_button(call)
    assert call.message.answers == [texts.EVGENY_TRY_ENDED]
    ответ = await _написать(bot, PAVEL, u'Ау')
    assert ответ.answers == [texts.TEAM_HINT]
    assert await evgeny.try_day(bot, PAVEL, 1) == texts.EVGENY_TRY_NOT_STARTED


async def test_брошенная_на_сутки_проверка_заканчивается_сама(monkeypatch):
    bot = FakeBot()
    await _начать(bot)
    сутки_спустя = time.time() + evgeny.TRY_IDLE + 60
    monkeypatch.setattr(evgeny.time, 'time', lambda: сутки_спустя)
    assert not evgeny.listening(PAVEL)
    ответ = await _написать(bot, PAVEL, u'Ау')
    assert ответ.answers == [texts.TEAM_HINT]


async def test_прогон_test_начинает_и_евгения_заново():
    from tests.test_coach import _шаг
    bot = FakeBot()
    db.remember_user(SHARP, 'sharp', u'Sharp')
    await _шаг(bot, SHARP, 'evgeny_intro')
    assert _кому(bot, SHARP) == [texts.EVGENY_COACH_INTRO]
    await admin.on_test(FakeMessage(text='/test', user=FakeUser(SHARP), bot=bot))
    db._run('DELETE FROM jobs WHERE user_id=?', (SHARP,))
    await _шаг(bot, SHARP, 'evgeny_intro')
    assert _кому(bot, SHARP).count(texts.EVGENY_COACH_INTRO) == 2, 'знакомство снова, а не тишина'


def test_стирается_только_переписка_проверяющего():
    db.remember_user(КЛИЕНТ, 'anna', u'Анна')
    db.save_message(КЛИЕНТ, 'in', 'text', u'Вопрос участника')
    db.save_message(PAVEL, 'in', 'text', u'Проверка')
    db.log_event(КЛИЕНТ, 'feedback', '1')
    db.evgeny_forget(PAVEL)
    assert [m['text'] for m in db.chat_history(КЛИЕНТ)] == [u'Вопрос участника']
    assert db.feedback_days(КЛИЕНТ) == {1}
    assert db.chat_history(PAVEL) == []


async def test_в_пульте_евгения_видно_проверку_и_что_участникам_выключен():
    bot = FakeBot()
    await _начать(bot)
    text, keys = admin._evgeny_status()
    assert texts.EVGENY_COACH_STATE_OFF in text
    assert u'Проверяют сейчас: 1' in text and u'Ведёт марафон сейчас: 0' in text
    assert keys.inline_keyboard[0][0].callback_data == 'ev:try'


async def test_начали_заново_пока_ии_думал_старый_ответ_до_знакомства(monkeypatch):
    u"""Ревью 07.11.2026: ответ прошлой проверки приходил после нового знакомства
    и попадал в только что стёртую переписку."""
    bot = FakeBot()
    await _начать(bot)
    думает = asyncio.Event()
    заново = []

    async def медленный(messages, **kw):
        думает.set()
        await asyncio.sleep(0.1)                # нажал «Начать заново», пока ИИ думал
        return u'Ответ на прошлую проверку'

    monkeypatch.setattr(ai, 'complete', медленный)
    await support.to_support(FakeMessage(text=u'Было тепло', user=FakeUser(PAVEL), bot=bot))
    await думает.wait()
    заново.append(asyncio.create_task(_начать(bot)))
    await asyncio.gather(*заново)
    await asyncio.sleep(0.2)
    пришло = _кому(bot, PAVEL)
    assert пришло.index(u'Ответ на прошлую проверку') < len(пришло) - 1
    assert пришло[-1] == texts.EVGENY_COACH_INTRO
    assert [m['text'] for m in db.chat_history(PAVEL)] == [texts.EVGENY_COACH_INTRO]


async def test_реплай_на_пометку_о_себе_ответ_евгению_а_не_конец_проверки(monkeypatch):
    u"""Ревью 07.11.2026: реплай на «💳 Пишет про оплату» в проверке уходил копией
    самому себе как «команда ответила», и Евгений замолкал."""
    bot = FakeBot()
    await _начать(bot)
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] сколько стоит спортзал', u'Хорошо 🙏'))
    await _написать(bot, PAVEL, u'Сколько стоит спортзал?')
    пометка = next(m for m in bot.by_id.values() if u'Пишет про оплату' in (m.text or ''))
    ответ = FakeMessage(text=u'А ещё вопрос', user=FakeUser(PAVEL), bot=bot, reply_to=пометка)
    await support.from_team(ответ)
    await asyncio.sleep(0.2)
    assert ответ.replies == []
    assert evgeny.listening(PAVEL)
    assert _кому(bot, PAVEL)[-1] == u'Хорошо 🙏'


# ------------------------------------------------------------------ до запуска участникам — никак

async def test_кнопка_в_пульте_и_массовый_дожим_до_запуска_не_включают(monkeypatch):
    db.remember_user(КЛИЕНТ, 'anna', u'Анна')
    bot = FakeBot()
    assert evgeny.enable(bot, КЛИЕНТ, ALEX) == texts.EVGENY_NOT_LAUNCHED
    assert not evgeny.is_on(КЛИЕНТ)
    call = FakeCall('ev:go', user=FakeUser(ALEX), bot=bot)
    await admin.on_evgeny_button(call)
    assert call.answers == [texts.EVGENY_NOT_LAUNCHED]
    _, keys = admin._evgeny_status()
    assert 'ev:go' not in [b.callback_data for row in keys.inline_keyboard for b in row]


async def test_дожим_включённый_раньше_до_запуска_молчит(monkeypatch):
    u"""Строки дожима, включённые до 07.11, — ни ответов, ни напоминаний, ни
    «позову коллегу»: участникам Евгений не пишет, пока его не запустят."""
    db.remember_user(КЛИЕНТ, 'anna', u'Анна')
    db.evgeny_on(КЛИЕНТ, ALEX)                                   # дожим из пульта
    db.evgeny_on(КЛИЕНТ + 1, ALEX, pending=True)                 # массовый, в очереди
    db.remember_user(КЛИЕНТ + 1, 'oleg', u'Олег')
    db.evgeny_pay(КЛИЕНТ, time.time() - 1, u'спрашивает цену')
    bot = FakeBot()
    фальшивый = ИИ(u'Не должен ответить')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    вопрос = await _написать(bot, КЛИЕНТ, u'Сколько стоит?')
    assert вопрос.answers[0].startswith(u'Получили')
    monkeypatch.setattr(evgeny, 'daytime', lambda now=None: True)
    await evgeny.tick(bot)
    await evgeny.pay_tick(bot, time.time() + 1)
    assert _кому(bot, КЛИЕНТ) == [вопрос.answers[0]] and _кому(bot, КЛИЕНТ + 1) == []
    assert фальшивый.запросы == []
    assert any(u'Срочно ответьте' in p for p in _кому(bot, SHARP)), 'команде про оплату — всё равно'
