# -*- coding: utf-8 -*-
u"""Евгений — координатор марафона (заказчик 06.11.2026).

Через минуту после записи первого дня Евгений знакомится; нажавшему «Да» или
«Нет» под днём через минуту после следующей записи пишет — какие были ощущения
по телу; рассказал сам — не спрашивает, рассказывал после прошлого дня — «так же
жду обратную связь». Кто пишет про оплату — команде, не ответит она за пять
минут — Евгений зовёт коллегу. ИИ здесь поддельный.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, funnel, scheduler, texts          # noqa: E402
from bot.handlers import support                                          # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
МИНУТА = 60


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'coach.db'))
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


def _участник(uid, name=u'Анна'):
    db.remember_user(uid, 'user%d' % uid, name)
    db.mark_launched(uid)


def _человеку(bot, uid):
    return [p for k, c, p in bot.sent if c == uid and k == 'text']


def _команде(bot):
    return [p for k, c, p in bot.sent if c in (SHARP, PAVEL, ALEX) and k == 'text']


async def _шаг(bot, uid, chain, pos=0):
    u"""Выполнить шаг воронки, как планировщик: поставить созревшим и разобрать."""
    db._run('DELETE FROM jobs WHERE user_id=? AND chain=? AND pos=?', (uid, chain, pos))
    db.add_job(uid, chain, pos, time.time() - 1)
    job = next(j for j in db.user_jobs(uid) if j['chain'] == chain and j['pos'] == pos)
    await scheduler.run_job(bot, job)


def _день(chain):
    return next(i for i, s in enumerate(funnel.CHAINS[chain].steps) if s.kind == 'day')


def _когда(uid, chain):
    return next(j['run_at'] for j in db.user_jobs(uid) if j['chain'] == chain)


# ------------------------------------------------------------------ сценарий

def test_евгений_не_сдвигает_сроки_воронки():
    u"""Цепочки Евгения рядом с воронкой: «ровно через 120 минут» остаётся ровно."""
    assert funnel.CHAINS['after_day2'].steps[0].delay == 120 * МИНУТА
    assert funnel.CHAINS['after_day1'].steps[0].delay == 150 * МИНУТА
    assert all(not chain.startswith('evgeny') for chain in funnel.ORDER)
    assert funnel.CHAINS['launch'].steps[-1].kind == 'day'
    # шаги Евгения не путают «докуда дошёл человек»
    assert not funnel.day_delivered({'after_day2', 'evgeny_day1'}, 3)
    assert funnel.day_delivered({'evgeny_day4'}, 4)


async def test_через_минуту_после_первого_дня_знакомится():
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'launch', _день('launch'))
    assert 'evgeny_intro' in db.pending_chains(1)
    assert _когда(1, 'evgeny_intro') - time.time() == pytest.approx(МИНУТА, abs=5)

    await _шаг(bot, 1, 'evgeny_intro')
    assert _человеку(bot, 1)[-1] == texts.EVGENY_COACH_INTRO
    assert evgeny.is_on(1) and db.evgeny_get(1)['mode'] == 'coach'
    (m,) = [m for m in db.chat_history(1) if m['author'] == evgeny.EVGENY]
    assert m['text'] == texts.EVGENY_COACH_INTRO, 'в пульте видно, что написал Евгений'

    # второй раз (пройти марафон заново) не знакомится
    await _шаг(bot, 1, 'evgeny_intro')
    assert _человеку(bot, 1).count(texts.EVGENY_COACH_INTRO) == 1


async def test_после_нажатия_спрашивает_про_ощущения_прошлого_дня():
    _участник(1)
    bot = FakeBot()
    db.save_answer(1, 'day1', 'yes')
    await _шаг(bot, 1, 'day1_yes', _день('day1_yes'))            # пришёл второй день
    assert 'evgeny_day1' in db.pending_chains(1)
    assert _когда(1, 'evgeny_day1') - time.time() == pytest.approx(МИНУТА, abs=5)
    await _шаг(bot, 1, 'evgeny_day1')
    ask = _человеку(bot, 1)[-1]
    assert texts.EVGENY_COACH_ASK.format(day=u'первого') in ask
    assert ask.startswith(texts.EVGENY_COACH_HELLO), 'раньше не знакомились — представляется'


async def test_нет_нажатия_нет_вопроса():
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'day1_no', _день('day1_no'))               # день пришёл, а ответа нет
    assert 'evgeny_day1' not in db.pending_chains(1)


async def test_после_четвёртого_дня_про_третий_сразу_а_про_четвёртый_через_два_часа():
    _участник(1)
    bot = FakeBot()
    db.save_answer(1, 'day3', 'no')
    await _шаг(bot, 1, 'day3_no', _день('day3_no'))
    assert _когда(1, 'evgeny_day3') - time.time() == pytest.approx(МИНУТА, abs=5)
    assert _когда(1, 'evgeny_day4') - time.time() == pytest.approx(120 * МИНУТА, abs=5)


async def test_рассказал_сам_про_этот_день_не_спрашивает_а_про_следующий_так_же_жду(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    monkeypatch.setattr(ai, 'complete', ИИ(
        u'По какой стороне ощущения были больше — слева или справа?\n[FEEDBACK 1]'))
    await support.to_support(FakeMessage(text=u'Было тепло в руках', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert db.feedback_days(1) == {1}
    assert _человеку(bot, 1)[-1] == u'По какой стороне ощущения были больше — слева или справа?'

    сколько = len(_человеку(bot, 1))
    await _шаг(bot, 1, 'evgeny_day1')
    assert len(_человеку(bot, 1)) == сколько, 'про первый день уже рассказал — не спрашиваем'

    await _шаг(bot, 1, 'evgeny_day2')
    assert _человеку(bot, 1)[-1] == (
        u'Дорогой мой друг, так же как ты давал после первого и важного дня «Предназначение» '
        u'свою обратную связь по ощущениям, день отношений не менее важен, поэтому так же жду '
        u'обратную связь.')


def test_так_же_жду_ссылается_на_последний_день_с_обратной_связью():
    _участник(1)
    db.save_message(1, 'out', 'text', u'Привет', author=evgeny.EVGENY)
    db.log_event(1, 'feedback', '1')
    db.log_event(1, 'feedback', '2')
    assert evgeny.coach_text(1, 'day3') == (
        u'Дорогой мой друг, так же как ты давал после второго дня «Отношения» свою обратную связь '
        u'по ощущениям, день здоровья не менее важен, поэтому так же жду обратную связь.')
    assert u'день финансов' in evgeny.coach_text(1, 'day4')
    assert evgeny.coach_text(1, 'day2') == '', 'про второй день уже рассказал'


async def test_ии_координатора_знает_мозги_павла_и_лицензии(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    фальшивый = ИИ(u'Это очищение тонкого плана 🙏')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await support.to_support(FakeMessage(text=u'Шла энергия по телу', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    system = фальшивый.запросы[0][0]['content']
    assert u'ТЫ КООРДИНАТОР МАРАФОНА' in system and u'ДОЖИМ ДО СПОРТЗАЛА' not in system
    assert u'женская линия рода' in system and u'магические воздействия' in system
    assert u'Роспотребнадзоре' in system and u'15 000' in system
    человек = фальшивый.запросы[0][1]
    assert человек['role'] == 'system', 'о человеке — отдельно: правила одни на всех и в кэше'
    assert u'Обратной связи по ощущениям пока не давал' in человек['content']
    assert u'Обратной связи' not in system


async def test_координатор_кнопку_покупки_не_даёт(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    monkeypatch.setattr(ai, 'complete', ИИ(u'Здорово!\n[BUY]'))
    await support.to_support(FakeMessage(text=u'Хочу в спортзал', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    i = [n for n, (k, c, p) in enumerate(bot.sent) if c == 1][-1]
    assert bot.keys_sent[i] is None


# ------------------------------------------------------------------ оплата

async def test_про_оплату_команде_сразу_а_через_пять_минут_евгений_зовёт_коллегу(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] спрашивает про рассрочку'))
    await support.to_support(FakeMessage(text=u'А рассрочка есть?', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)

    assert _человеку(bot, 1) == [texts.EVGENY_COACH_INTRO], 'про оплату Евгений молчит'
    оплата = [p for p in _команде(bot) if u'Пишет про оплату' in p]
    assert len(оплата) == 3, 'Sharp, Павел и AleX'
    assert u'спрашивает про рассрочку' in оплата[0] and u'А рассрочка есть?' in оплата[0]
    i = next(n for n, (k, c, p) in enumerate(bot.sent) if c == SHARP and u'Пишет про оплату' in str(p))
    кнопки = [b for row in bot.keys_sent[i].inline_keyboard for b in row]
    assert any(b.web_app and b.web_app.url.endswith('/?id=1') for b in кнопки), 'диалог в пульте одной кнопкой'

    # повторил про оплату — команда уже знает, второй раз не шлём
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] спрашивает про оплату'))
    await support.to_support(FakeMessage(text=u'Ну так как оплатить?', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert len([p for p in _команде(bot) if u'Пишет про оплату' in p]) == 3

    await evgeny.pay_tick(bot, time.time() + 60)
    assert texts.EVGENY_PAY_COLLEAGUE not in _человеку(bot, 1), 'пять минут ещё не прошли'
    await evgeny.pay_tick(bot, time.time() + evgeny.PAY_WAIT + 1)
    assert _человеку(bot, 1)[-1] == texts.EVGENY_PAY_COLLEAGUE
    assert not evgeny.is_on(1)
    срочно = [p for p in _команде(bot) if u'Срочно ответьте' in p]
    assert len(срочно) == 3 and u'спрашивает про рассрочку' in срочно[0]

    await evgeny.pay_tick(bot, time.time() + 2 * evgeny.PAY_WAIT)
    assert _человеку(bot, 1).count(texts.EVGENY_PAY_COLLEAGUE) == 1


async def test_команда_ответила_про_оплату_сама_коллегу_не_зовём(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] хочет купить'))
    await support.to_support(FakeMessage(text=u'Хочу купить', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    db.save_message(1, 'out', 'text', u'Здравствуйте! Это AleX', author=ALEX)
    evgeny.team_wrote(1, u'AleX')
    await evgeny.pay_tick(bot, time.time() + evgeny.PAY_WAIT + 1)
    assert texts.EVGENY_PAY_COLLEAGUE not in _человеку(bot, 1)
    assert not [p for p in _команде(bot) if u'Срочно ответьте' in p]


async def test_в_дожиме_кнопка_и_команде_сказано_без_ожидания(monkeypatch):
    db.remember_user(1, 'anna', u'Анна')
    db.mark_launched(1)
    db.evgeny_on(1, SHARP)                                     # дожим (sales)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Отлично! Вступить можно по кнопке ниже 👇\n[BUY]\n[PAY] готов'))
    await support.to_support(FakeMessage(text=u'Как оплатить?', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    i = [n for n, (k, c, p) in enumerate(bot.sent) if c == 1][-1]
    assert bot.keys_sent[i].inline_keyboard[0][0].callback_data == 'buy:gym:evgeny'
    оплата = [p for p in _команде(bot) if u'Пишет про оплату' in p]
    assert оплата and texts.EVGENY_PAY_BUY_NOTE in оплата[0]
    assert db.evgeny_get(1)['pay_due'] is None, 'кнопка уже есть — коллегу не зовём'


# ------------------------------------------------------------------ кому координатор пишет

async def test_просил_не_писать_или_команда_выключила_координатор_молчит():
    bot = FakeBot()
    _участник(1)
    db.evgeny_coach_on(1)
    evgeny.disable(1, u'человек отказался', 'refused')
    _участник(2)
    db.evgeny_coach_on(2)
    evgeny.disable(2, u'выключил(а) в пульте AleX', 'manual')
    _участник(3)
    db.mark_blocked(3)
    for uid in (1, 2, 3):
        await _шаг(bot, uid, 'evgeny_intro')
        assert _человеку(bot, uid) == []
    assert db.get_user(3)['blocked_at'], 'пропущенный шаг Евгения не «открывает» бота'


async def test_команда_недавно_писала_вопрос_уходит_а_отвечает_команда():
    _участник(1)
    bot = FakeBot()
    db.evgeny_coach_on(1)
    db.save_message(1, 'out', 'text', u'Это Павел', author=PAVEL)
    evgeny.team_wrote(1, u'Павел')
    await _шаг(bot, 1, 'evgeny_day1')
    assert _человеку(bot, 1), 'вопрос про ощущения — часть марафона, уходит'
    assert not evgeny.is_on(1), 'но в разговор команды Евгений не встревает'


async def test_команде_по_сценарию_пишет_но_не_отвечает():
    u"""Прогон /test: команда видит сообщения Евгения, а её ответы — команды боту."""
    bot = FakeBot()
    db.remember_user(ALEX, 'alex', u'AleX')
    await _шаг(bot, ALEX, 'evgeny_intro')
    assert _человеку(bot, ALEX) == [texts.EVGENY_COACH_INTRO]
    assert not evgeny.is_on(ALEX)


async def test_закрыл_бота_снимаем_как_любой_шаг():
    _участник(1)
    scheduler.start_chain(1, 'after_day1')
    await _шаг(FakeBot(forbidden=True), 1, 'evgeny_intro')
    assert db.get_user(1)['blocked_at']
    assert db.pending_chains(1) == set()


async def test_без_ключа_или_выключен_координатор_ничего_не_ставит(monkeypatch):
    _участник(1)
    bot = FakeBot()
    evgeny.set_coach(False)
    await _шаг(bot, 1, 'launch', _день('launch'))
    assert 'evgeny_intro' not in db.pending_chains(1)
    await _шаг(bot, 1, 'evgeny_intro')
    assert texts.EVGENY_COACH_INTRO not in _человеку(bot, 1)

    evgeny.set_coach(True)
    monkeypatch.setattr(config, 'AI_API_KEY', '')
    _участник(2)
    await _шаг(bot, 2, 'launch', _день('launch'))
    assert 'evgeny_intro' not in db.pending_chains(2)


def test_выключить_всех_выключает_и_координатора():
    _участник(1)
    db.evgeny_coach_on(1)
    assert evgeny.coach_on()
    assert evgeny.stop_all(u'выключили всех: AleX') == 1
    assert not evgeny.coach_on() and not evgeny.is_on(1)
    assert evgeny.coach_text(1, 'intro') == texts.EVGENY_COACH_INTRO
    assert db.evgeny_get(1)['off_kind'] == 'auto', 'координатор молчит, пока его не включат снова'


# ------------------------------------------------------------------ дожим после марафона

async def test_координатору_не_напоминают_по_суткам(monkeypatch):
    _участник(1)
    db.evgeny_coach_on(1)
    db._run('UPDATE evgeny SET last_out=? WHERE user_id=1', (time.time() - 3 * 86400,))
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Как ты?'))
    monkeypatch.setattr(evgeny, 'daytime', lambda now=None: True)
    await evgeny.tick(bot)
    assert _человеку(bot, 1) == []


async def test_массовый_дожим_берёт_прошедших_с_координатором_и_продолжает_разговор(monkeypatch):
    for uid in (1, 2):
        _участник(uid)
        db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (?, 'day', '4', ?)",
                (uid, time.time() - 4 * 86400))
        db.save_message(uid, 'out', 'text', texts.EVGENY_COACH_INTRO, author=evgeny.EVGENY)
        db.evgeny_coach_on(uid)
    evgeny.disable(2, u'человек отказался', 'refused')
    assert [p['user_id'] for p in evgeny.candidates()] == [1]

    evgeny.push(SHARP)
    assert db.evgeny_get(1)['mode'] == 'sales'
    фальшивый = ИИ(u'Анна, как ты сейчас? Что из марафона откликнулось больше всего?')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    monkeypatch.setattr(evgeny, 'daytime', lambda now=None: True)
    monkeypatch.setattr(evgeny, 'OPEN_PER_MINUTE', 6000)
    bot = FakeBot()
    await evgeny.tick(bot)
    assert _человеку(bot, 1) == [u'Анна, как ты сейчас? Что из марафона откликнулось больше всего?']
    assert фальшивый.запросы[0][-1]['content'] == texts.EVGENY_PUSH_TASK
    assert u'ДОЖИМ ДО СПОРТЗАЛА' in фальшивый.запросы[0][0]['content']
    assert evgeny.candidates() == [], 'второй раз в дожим не берём'


async def test_включили_из_пульта_на_марафоне_координатор_после_дожим(monkeypatch):
    monkeypatch.setattr(ai, 'complete', ИИ())
    _участник(1)
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (1, 'day', '1', ?)", (time.time(),))
    _участник(2)
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (2, 'day', '4', ?)", (time.time(),))
    bot = FakeBot()
    assert evgeny.enable(bot, 1, ALEX) == '' and evgeny.enable(bot, 2, ALEX) == ''
    await asyncio.sleep(0.2)
    assert db.evgeny_get(1)['mode'] == 'coach' and db.evgeny_get(2)['mode'] == 'sales'


def test_из_ответа_координатора_убираются_фразы_про_оплату():
    u"""gpt-4.1-mini 06.11.2026 и с запретом называла цену и «уточню у команды»."""
    assert evgeny.without_money(u'Тепло в груди — энергия пошла. По оплате уточню у команды. '
                                u'Что ещё почувствовал?') == u'Тепло в груди — энергия пошла. Что ещё почувствовал?'
    assert evgeny.without_money(u'Месяц стоит 12 000 ₽. Что ещё почувствовал во втором дне?') == '', \
        'остался один вопрос — на вопрос о цене это звучит как уход от ответа'


async def test_запись_к_мастеру_команде_и_свой_текст_коллеги(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY]'))                # суть модель не написала
    await support.to_support(FakeMessage(text=u'Хочу записаться на диагностику', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert any(u'Хочет на диагностику к мастеру' in p for p in _команде(bot))
    await evgeny.pay_tick(bot, time.time() + evgeny.PAY_WAIT + 1)
    assert _человеку(bot, 1)[-1] == texts.EVGENY_PAY_COLLEAGUE_MASTER


async def test_суть_для_команды_до_первой_точки(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] спрашивает цену. Что ещё хочешь узнать?'))
    await support.to_support(FakeMessage(text=u'Сколько стоит?', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert db.evgeny_get(1)['pay_ask'] == u'спрашивает цену'


async def test_координатору_в_конце_запроса_напоминание_про_теги(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    фальшивый = ИИ(u'Хорошо 🙏')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await support.to_support(FakeMessage(text=u'Спасибо', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert фальшивый.запросы[0][-1]['content'] == texts.EVGENY_COACH_REPLY_TASK


# ------------------------------------------------------------------ по ревью 06.11.2026

def test_старые_выключенные_узнают_причину_и_отказавшимся_координатор_не_пишет(tmp_path):
    u"""База до 06.11: причина только словами — миграция переводит её для бота."""
    import sqlite3
    path = str(tmp_path / 'old.db')
    old = sqlite3.connect(path)
    old.execute('CREATE TABLE evgeny (user_id INTEGER PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, '
                'since REAL NOT NULL, by_id INTEGER, pending INTEGER NOT NULL DEFAULT 0, last_out REAL, '
                'last_in REAL, nudges INTEGER NOT NULL DEFAULT 0, stopped_at REAL, reason TEXT)')
    for uid, active, reason in ((1, 0, u'человек отказался'), (2, 0, u'выключил(а) в пульте AleX'),
                                (3, 0, u'ответил(а) сам(а) Павел'), (4, 1, None),
                                (5, 0, u'выключили всех: AleX')):
        old.execute('INSERT INTO evgeny (user_id, active, since, reason) VALUES (?, ?, 0, ?)',
                    (uid, active, reason))
    old.commit()
    old.close()
    db.connect(path)
    kinds = {uid: (db.evgeny_get(uid) or {}).get('off_kind') for uid in (1, 2, 3, 4, 5)}
    assert kinds == {1: 'refused', 2: 'manual', 3: 'team', 4: None, 5: 'manual'}
    assert db.evgeny_get(4)['mode'] == 'sales'
    db.connect(path)                                     # повторный запуск ничего не ломает
    assert db.evgeny_get(1)['off_kind'] == 'refused'


def test_выключить_всех_и_включить_координатора_снова_он_ведёт_тех_же():
    _участник(1)
    db.evgeny_coach_on(1)
    evgeny.stop_all(u'выключили всех: AleX')
    assert db.evgeny_get(1)['off_kind'] == 'auto'
    evgeny.set_coach(True)
    assert evgeny._coach_may(1), 'координатор снова включён — продолжает вести человека'


async def test_выключенный_координатор_отдаёт_сообщения_команде_с_получили(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    evgeny.set_coach(False)
    фальшивый = ИИ(u'Не должен ответить')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    вопрос = FakeMessage(text=u'Было тепло', user=FakeUser(1), bot=bot)
    await support.to_support(вопрос)
    await asyncio.sleep(0.2)
    assert вопрос.answers and вопрос.answers[0].startswith(u'Получили'), 'человек не остался без ответа'
    assert фальшивый.запросы == []

    evgeny.set_coach(True)
    monkeypatch.setattr(config, 'AI_API_KEY', '')
    второй = FakeMessage(text=u'Ау', user=FakeUser(1), bot=bot)
    await support.to_support(второй)
    assert второй.answers and второй.answers[0].startswith(u'Получили'), 'без ключа ИИ — тоже обычным путём'


async def test_команда_ответила_пока_ии_думал_евгений_не_шлёт_следом(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')

    async def медленный(messages, **kw):
        evgeny.team_wrote(1, u'AleX')                 # ответ команды пришёл, пока ИИ думал
        return u'Ответ Евгения'

    monkeypatch.setattr(ai, 'complete', медленный)
    await support.to_support(FakeMessage(text=u'Вопрос', user=FakeUser(1), bot=bot))
    await asyncio.sleep(0.2)
    assert u'Ответ Евгения' not in _человеку(bot, 1)


def test_в_массовый_дожим_не_берём_тех_с_кем_говорила_команда():
    _участник(1)
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (1, 'day', '4', ?)", (time.time() - 4 * 86400,))
    db.evgeny_coach_on(1)
    evgeny.team_wrote(1, u'AleX')
    assert evgeny.candidates() == []


async def test_на_серию_голосовых_одна_просьба_написать_текстом(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _шаг(bot, 1, 'evgeny_intro')
    for _ in range(3):
        голос = FakeMessage(user=FakeUser(1), bot=bot)
        голос.voice = type('Voice', (), {'file_id': 'v1'})()
        await support.to_support(голос)
    await asyncio.sleep(0.1)
    assert _человеку(bot, 1).count(texts.EVGENY_VOICE_TY) == 1


async def test_сообщение_евгения_в_очереди_не_марафон_для_повторного_старта():
    from bot.handlers import start
    _участник(1)
    scheduler.start_chain(1, 'evgeny_day4')
    assert start._repeat_start_text(1) != texts.ALREADY_RUNNING


def test_разбор_новых_тегов():
    text, tags = evgeny.parse(u'Понял тебя 🙏\n[FEEDBACK 2]\n[PAY] хочет на диагностику')
    assert text == u'Понял тебя 🙏'
    assert tags['feedback'] == [2] and tags['pay'] == u'хочет на диагностику'
