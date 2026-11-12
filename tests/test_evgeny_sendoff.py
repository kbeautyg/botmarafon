# -*- coding: utf-8 -*-
u"""Молчит человек на вопрос Евгения о дне — через 10 минут «смотрите следующий день» (Павел 12.11.2026).

Проверка Павла: Евгений спросил «Тяжесть в животе больше ощущалась слева или справа?», Павел ответил
через час — и весь этот час Евгений молчал. «А надо, если человек не ответил 10 минут, то он пишет
пожелание смотреть второй день и ощущения по телу». ИИ здесь поддельный.
"""
import asyncio
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, texts                             # noqa: E402
from bot.handlers import admin, support                                   # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
ЧАС = 3600
# слово в слово из проверки Павла 12.11.2026
ОЩУЩЕНИЯ = (u'В первый день были головные боли  , тяжесть в животе и часто сбивалась дыхание , как '
            u'будто были страхи')
ВОПРОС = (u'Так бывает, и это работа, а не сбой. Голова отозвалась, потому что поднимались старые '
          u'страхи и ограничения. Тяжесть в животе больше ощущалась слева или справа?')
ПОЖЕЛАНИЕ = texts.EVGENY_SENDOFF.format(day=u'второго')


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'sendoff.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'AI_API_KEY', 'test-key')
    monkeypatch.setattr(evgeny, 'DEBOUNCE', 0.05)
    monkeypatch.setattr(evgeny, '_typing_seconds', lambda text: 0)
    evgeny._waiting.clear()
    evgeny._locks.clear()
    evgeny._voice_told.clear()
    for day in (1, 2, 3, 4):
        db.put_content('day%d' % day, 'link', 'https://example.com/day%d' % day)
    evgeny.set_coach(True)
    yield


class ИИ:
    u"""Поддельный ИИ: отвечает заготовками по очереди и запоминает запросы."""

    def __init__(self, *ответы, пока_думает=None):
        self.ответы = list(ответы)
        self.запросы = []
        self.пока_думает = пока_думает

    async def __call__(self, messages, **kw):
        self.запросы.append(messages)
        if self.пока_думает and len(self.запросы) == 2:
            self.пока_думает()
        return self.ответы.pop(0) if self.ответы else u'Благодарю 🙏'


def _участник(uid, дней=2):
    u"""Участник, которому пришли записи дней 1…дней; координатор с ним уже знаком."""
    db.remember_user(uid, 'user%d' % uid, u'Виолетта')
    db.mark_launched(uid)
    for day in range(1, дней + 1):
        db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (?, 'day', ?, ?)",
                (uid, str(day), time.time() - (дней - day + 1) * ЧАС))
    db.evgeny_coach_on(uid)
    db.save_message(uid, 'out', 'text', texts.EVGENY_COACH_INTRO, None, author=evgeny.EVGENY)


def _человеку(bot, uid):
    return [p for k, c, p in bot.sent if c == uid and k == 'text']


async def _пишет(bot, uid, text, ждать=0.2):
    await support.to_support(FakeMessage(text=text, user=FakeUser(uid), bot=bot))
    await asyncio.sleep(ждать)


def _через(секунд):
    return time.time() + секунд


async def _спросил(monkeypatch, *дальше, uid=1, дней=2, **kw):
    u"""Человек рассказал об ощущениях первого дня — Евгений уточнил «слева или справа?»."""
    bot = FakeBot()
    _участник(uid, дней)
    фальшивый = ИИ(ВОПРОС + u'\n[FEEDBACK 1]', *дальше, **kw)
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, uid, ОЩУЩЕНИЯ)
    assert _человеку(bot, uid)[-1] == ВОПРОС
    return bot, фальшивый


# ------------------------------------------------------------------ как просил Павел

def test_пожелание__смотреть_следующий_день_и_слушать_тело():
    assert ПОЖЕЛАНИЕ == (u'Хорошего вам просмотра второго дня 🙏 Во время просмотра прислушивайтесь к '
                         u'ощущениям в теле, а потом обязательно напишите мне о них.')
    assert u'прислушивайся' in texts.EVGENY_SENDOFF_TY and u'напиши мне' in texts.EVGENY_SENDOFF_TY
    assert evgeny.SENDOFF_AFTER == 10 * 60


async def test_молчит_10_минут_на_вопрос__пожелание_смотреть_второй_день(monkeypatch):
    bot, _ = await _спросил(monkeypatch)
    assert await evgeny.sendoff_tick(bot, _через(9 * 60)) == 0, 'раньше 10 минут не пишет'
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 1
    assert _человеку(bot, 1)[-1] == ПОЖЕЛАНИЕ
    last = db.chat_history(1, 1)[-1]
    assert last['author'] == evgeny.EVGENY and last['text'] == ПОЖЕЛАНИЕ   # в переписке — от Евгения
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 60)) == 0, 'один раз'


async def test_ответил_после_пожелания__разговор_дальше_и_второго_нет(monkeypatch):
    u"""Павел ответил «В середине» через час — Евгений продолжает разговор, а на новый вопрос о том
    же дне второе «смотрите второй день» не шлёт."""
    bot, фальшивый = await _спросил(
        monkeypatch, u'По центру живота — старое, что долго стояло. А в груди что-то было?\n[FEEDBACK 1]')
    await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1))
    await _пишет(bot, 1, u'В середине')
    assert _человеку(bot, 1)[-1].startswith(u'По центру живота')
    assert {'role': 'assistant', 'content': ПОЖЕЛАНИЕ} in фальшивый.запросы[-1], 'ИИ видит пожелание'
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_на_второй_день__пожелание_смотреть_третий(monkeypatch):
    bot = FakeBot()
    _участник(1, дней=3)
    db.log_event(1, 'feedback', '1')
    monkeypatch.setattr(ai, 'complete', ИИ(u'Уснули — и это работа. По какой стороне было '
                                           u'тяжелее?\n[FEEDBACK 2]'))
    await _пишет(bot, 1, u'Было всё спокойно, только уснул в конце, тяжесть была')
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 1
    assert _человеку(bot, 1)[-1] == texts.EVGENY_SENDOFF.format(day=u'третьего')


async def test_на_ты__пожелание_на_ты(monkeypatch):
    db.put_content(evgeny.TY_KEY % 1, 'evgeny', '1')
    bot, _ = await _спросил(monkeypatch)
    await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1))
    assert _человеку(bot, 1)[-1] == texts.EVGENY_SENDOFF_TY.format(day=u'второго')


# ------------------------------------------------------------------ когда не пишет

async def test_ответил_за_10_минут__пожелания_нет(monkeypatch):
    bot, _ = await _спросил(monkeypatch, u'По центру — старое, что стояло. Впереди день отношений 🙏')
    await _пишет(bot, 1, u'В середине')
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_написал_а_евгений_ещё_не_ответил__пожелания_нет(monkeypatch):
    bot, _ = await _спросил(monkeypatch)
    db.save_message(1, 'in', 'text', u'В середине')
    db.evgeny_touch(1, 'in')
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_ответ_без_вопроса__ждать_нечего(monkeypatch):
    bot = FakeBot()
    _участник(1)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Так бывает, это работа, а не сбой 🙏 Впереди день '
                                           u'отношений — интересно, как он отзовётся.\n[FEEDBACK 1]'))
    await _пишет(bot, 1, ОЩУЩЕНИЯ)
    assert db.evgeny_get(1)['ask_at'] is None
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


@pytest.mark.parametrize('ответ_ии', [
    u'Уснуть — частая реакция. Пришлю запись первого дня ещё раз 👇 Досмотрите?\n[FEEDBACK 1]\n[DAY 1]',
    u'Тепло в груди — энергия пошла. Что ещё почувствовали?\n[FEEDBACK 1]\n[PAY] как оплатить',
])
async def test_прислал_запись_ещё_раз_или_про_оплату__не_провожает(monkeypatch, ответ_ии):
    bot = FakeBot()
    _участник(1)
    monkeypatch.setattr(ai, 'complete', ИИ(ответ_ии))
    await _пишет(bot, 1, u'Уснула на середине первого дня, было тепло в груди. А как оплатить?')
    assert db.evgeny_get(1)['ask_at'] is None
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_после_4_го_дня__следующего_дня_нет(monkeypatch):
    bot = FakeBot()
    _участник(1, дней=4)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Страхи выходят дрожью 🙏 Самое время продолжить в '
                                           u'Энергоспортзале. Есть вопросы?\n[FEEDBACK 4]'))
    await _пишет(bot, 1, u'Поднимались страхи, от них дрожало тело')
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_вопрос_про_день_по_сценарию__не_провожает(monkeypatch):
    u"""После уточнения пришёл вопрос про следующий день по сценарию — разговор ушёл дальше."""
    bot, _ = await _спросил(monkeypatch, дней=3)
    await evgeny.coach(bot, 1, 'day2')
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0
    assert db.evgeny_get(1)['ask_at'] is None


async def test_после_вопроса_пришла_рассылка__не_провожает(monkeypatch):
    bot, _ = await _спросил(monkeypatch)
    db.save_message(1, 'out', 'text', u'Сегодня в 19:00 эфир с Павлом', None, None, mass=True)
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_об_ощущениях_рассказывал_давно__не_провожает(monkeypatch):
    u"""Вопрос не в разговоре о дне: об ощущениях человек писал 4 часа назад."""
    bot = FakeBot()
    _участник(1)
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (1, 'feedback', '1', ?)",
            (time.time() - 4 * ЧАС,))
    monkeypatch.setattr(ai, 'complete', ИИ(u'Эфиры анонсируем здесь, в чате. Второй день уже '
                                           u'посмотрели?'))
    await _пишет(bot, 1, u'А когда будет эфир?')
    assert db.evgeny_get(1)['ask_at'] is None
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_евгения_остановили__не_пишет(monkeypatch):
    bot, _ = await _спросил(monkeypatch)
    evgeny.set_coach(False)
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_дожим__не_провожает(monkeypatch):
    bot = FakeBot()
    _участник(1)
    db.evgeny_on(1, 1, mode='sales')
    monkeypatch.setattr(ai, 'complete', ИИ(ВОПРОС + u'\n[FEEDBACK 1]'))
    await _пишет(bot, 1, ОЩУЩЕНИЯ)
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0


async def test_пока_ии_писал__человек_ответил__пожелание_не_уходит(monkeypatch):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)

    def ответил():
        db.save_message(1, 'in', 'text', u'В середине')
        db.evgeny_touch(1, 'in')

    bot, _ = await _спросил(monkeypatch, u'Хорошего просмотра второго дня 🙏', пока_думает=ответил)
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 0
    assert _человеку(bot, 1)[-1] == ВОПРОС


# ------------------------------------------------------------------ словами ИИ

async def test_ии_пишет_своими_словами__день_и_его_тема_в_задаче(monkeypatch):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)
    своими = u'Пусть день отношений пройдёт спокойно 🙏 Прислушивайтесь к телу, а потом расскажите.'
    bot, фальшивый = await _спросил(monkeypatch, своими)
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 1
    assert _человеку(bot, 1)[-1] == своими
    задача = фальшивый.запросы[-1][-1]['content']
    assert u'2-го дня («Отношения»)' in задача and u'10 минут' in задача
    assert texts.EVGENY_SENDOFF_LATER not in задача, 'запись второго дня уже пришла'


async def test_запись_следующего_дня_ещё_не_пришла__ии_знает(monkeypatch):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)
    bot, фальшивый = await _спросил(monkeypatch, u'Хорошего просмотра второго дня 🙏', дней=1)
    await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1))
    assert texts.EVGENY_SENDOFF_LATER in фальшивый.запросы[-1][-1]['content']


@pytest.mark.parametrize('ответ_ии', [u'Хорошего просмотра 🙏 [DAY 2]', u'Хорошего просмотра. ' * 40])
async def test_ии_написал_не_то__запасной_текст(monkeypatch, ответ_ии):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)
    bot, _ = await _спросил(monkeypatch, ответ_ии)
    await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1))
    assert _человеку(bot, 1)[-1] == ПОЖЕЛАНИЕ


# ------------------------------------------------------------------ проверка командой

async def test_проверка_павла__через_10_минут_смотрите_второй_день(monkeypatch):
    u"""Как в проверке Павла 12.11.2026: участникам Евгений не запущен, Павел — свой."""
    evgeny.set_coach(False)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ВОПРОС + u'\n[FEEDBACK 1]'))
    await admin.on_evgeny_try(FakeMessage(text=texts.EVGENY_TRY_BUTTON,
                                          user=FakeUser(PAVEL, 'pavel', u'Павел'), bot=bot))
    assert any(u'Не ответите 10 мин на его вопрос о дне' in p for p in _человеку(bot, PAVEL))
    assert await evgeny.try_day(bot, PAVEL, 1) == ''
    await _пишет(bot, PAVEL, ОЩУЩЕНИЯ)
    assert _человеку(bot, PAVEL)[-1] == ВОПРОС
    assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 1
    assert _человеку(bot, PAVEL)[-1] == ПОЖЕЛАНИЕ


async def test_начать_заново__пожелание_снова(monkeypatch):
    evgeny.set_coach(False)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ВОПРОС + u'\n[FEEDBACK 1]', ВОПРОС + u'\n[FEEDBACK 1]'))
    for _ in range(2):
        assert await evgeny.try_start(PAVEL, 'pavel', u'Павел') == ''
        assert await evgeny.try_day(bot, PAVEL, 1) == ''
        await _пишет(bot, PAVEL, ОЩУЩЕНИЯ)
        assert await evgeny.sendoff_tick(bot, _через(evgeny.SENDOFF_AFTER + 1)) == 1


# ------------------------------------------------------------------ база

def test_старая_база__колонки_добавляются(tmp_path):
    путь = str(tmp_path / 'old.db')
    старая = sqlite3.connect(путь)
    старая.execute('CREATE TABLE evgeny (user_id INTEGER PRIMARY KEY, active INTEGER NOT NULL '
                   'DEFAULT 1, since REAL NOT NULL, by_id INTEGER, pending INTEGER NOT NULL '
                   'DEFAULT 0, last_out REAL, last_in REAL, nudges INTEGER NOT NULL DEFAULT 0, '
                   'stopped_at REAL, reason TEXT)')
    старая.commit()
    старая.close()
    db.connect(путь)
    колонки = [r[1] for r in db._conn.execute('PRAGMA table_info(evgeny)')]
    assert {'ask_at', 'ask_day', 'sendoff_day'} <= set(колонки)


def test_последний_рассказ__по_времени_а_не_по_номеру_дня():
    db.remember_user(1, 'u1', u'Виолетта')
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (1, 'feedback', '2', ?)",
            (time.time() - 60,))
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (1, 'feedback', '1', ?)",
            (time.time(),))
    assert db.last_feedback(1)[0] == 1
    assert db.last_feedback(2) is None
