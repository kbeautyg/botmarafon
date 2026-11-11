# -*- coding: utf-8 -*-
u"""Евгений после проверки Павла 11.11.2026 вечером (тестовый аккаунт @Evgenve4niy).

Sharp: «в конце херню несёт: он написал „хорошо, посмотрю“… пусть отвечает „хорошо,
приятного просмотра“», «должен говорить не спортзал, а энергоспортзал… спортзал — это
где качки», «он не должен обращаться по имени, не должен видеть имя пользователя».
В той проверке Евгений назвал человека «Евгением» (так подписан аккаунт) и на «пока нет
вопросов» снова спросил про страхи дня финансов. ИИ здесь поддельный.
"""
import asyncio
import os
import random
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, texts                             # noqa: E402
from bot.handlers import admin, support                                   # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
ТЕСТЕР = 7692784223                         # @Evgenve4niy, в Telegram подписан «Евгений»


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'manners.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX, ТЕСТЕР))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'AI_API_KEY', 'test-key')
    monkeypatch.setattr(evgeny, 'DEBOUNCE', 0.05)
    evgeny._waiting.clear()
    evgeny._locks.clear()
    evgeny._voice_told.clear()
    for day in (1, 2, 3, 4):
        db.put_content('day%d' % day, 'link', 'https://example.com/day%d' % day)
    evgeny.set_coach(True)
    yield


class ИИ:
    u"""Поддельный ИИ: отвечает заготовками по очереди и запоминает запросы."""

    def __init__(self, *ответы):
        self.ответы = list(ответы)
        self.запросы = []

    async def __call__(self, messages, **kw):
        self.запросы.append(messages)
        return self.ответы.pop(0) if self.ответы else u'Благодарю 🙏'


def _участник(uid, name=u'Виолетта'):
    db.remember_user(uid, 'user%d' % uid, name)
    db.mark_launched(uid)
    db.evgeny_on(uid, uid, mode='coach')


def _евгений_писал(uid, text):
    db.save_message(uid, 'out', 'text', text, None, author=evgeny.EVGENY)


def _человеку(bot, uid):
    return [p for k, c, p in bot.sent if c == uid and k == 'text']


async def _пишет(bot, uid, text):
    await support.to_support(FakeMessage(text=text, user=FakeUser(uid), bot=bot))
    await asyncio.sleep(0.2)


# ------------------------------------------------------------------ по имени не обращается

def test_имени_человека_ии_не_видит():
    _участник(1, u'Виолетта')
    db.save_message(1, 'in', 'text', u'Было тепло в руках')
    запрос = evgeny._request(1, texts.EVGENY_COACH_REPLY_TASK)
    assert not any(u'Виолетта' in m['content'] for m in запрос)
    assert u'Имя' not in evgeny._card(1)
    _участник(ТЕСТЕР, u'Евгений')
    assert u'Евгений' not in evgeny._card(ТЕСТЕР), 'аккаунт «Евгений» Евгений назвал Евгением'


def test_по_имени_не_обращается__правило_и_знакомство_без_имени():
    assert u'По имени к человеку не обращайся никогда' in texts.EVGENY_PROMPT
    assert u'Начни с приветствия, без имени' in texts.EVGENY_OPEN_TASK
    assert u'Мансур' not in texts.EVGENY_OPEN_TASK
    assert u'по имени' not in texts.EVGENY_COACH_TASK, 'род — по словам человека, а не по имени'
    assert all(u'{name}' not in text for text in texts.EVGENY_OPENERS)
    assert {evgeny.opener(random.Random(n)) for n in range(20)} <= set(texts.EVGENY_OPENERS)


# ------------------------------------------------------------------ «Энергоспортзал»

@pytest.mark.parametrize('было, стало', [
    (u'Спортзал правда помогает с таким', u'Энергоспортзал правда помогает с таким'),
    (u'самое время продолжить в спортзале.', u'самое время продолжить в Энергоспортзале.'),
    (u'В «Энергетическом спортзале» Павел работает глубже', u'В «Энергоспортзале» Павел работает глубже'),
    (u'в „Энергетическом спортзале“', u'в „Энергоспортзале“'),
    (u'Купить энерго спортзал по акции', u'Купить Энергоспортзал по акции'),
    (u'А расскажите, что за спортзал', u'А расскажите, что за Энергоспортзал'),
    (u'Это программа Павла — Энергоспортзал', u'Это программа Павла — Энергоспортзал'),
    (u'как тренажёрный зал для тела', u'как тренажёрный зал для тела'),
    (u'', u''),
])
def test_энергоспортзал_а_не_спортзал(было, стало):
    assert evgeny.energo(было) == стало


def test_в_подсказке_программа_павла__энергоспортзал():
    assert u'Программу Павла называй «Энергоспортзал»' in texts.EVGENY_PROMPT
    for text in (texts.EVGENY_COACH_TASK, texts.EVGENY_SALES_TASK, texts.EVGENY_SENSES,
                 texts.EVGENY_COACH_REPLY_TASK, texts.EVGENY_REPLY_TASK, texts.EVGENY_OPEN_TASK,
                 texts.EVGENY_PUSH_TASK, texts.EVGENY_ASK_TASK, texts.EVGENY_CLOSING_TASK):
        assert evgeny.energo(text) == text, text[:60]
    # в фактах — только полное название, которым подписана кнопка вступления
    assert re.findall(u'(?<![\\w\\-])спортзал\\w*', texts.EVGENY_FACTS, re.I) == [u'спортзал']
    assert u'как тренажёрный зал для тела' in texts.EVGENY_FACTS


async def test_ии_написал_спортзал__человеку_ушёл_энергоспортзал(monkeypatch):
    _участник(1)
    _евгений_писал(1, u'Как прошёл третий день?')
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Ломота в пальцах — то, что тянется в роду. Спортзал '
                                           u'правда помогает: у Светланы за месяц там ушла обида.'))
    await _пишет(bot, 1, u'Ломило пальцы')
    ушло = _человеку(bot, 1)[-1]
    assert u'Энергоспортзал правда помогает' in ушло and u' Спортзал' not in ушло
    assert db.chat_history(1)[-1]['text'] == ушло, 'и в переписке — как ушло человеку'


def test_истории_команды_ии_видит_с_энергоспортзалом():
    evgeny.set_cases(u'Мария: после 1-го дня пошла в спортзал. За месяц стало легче.')
    _участник(1)
    правила = evgeny._request(1, texts.EVGENY_REPLY_TASK)[0]['content']
    assert u'пошла в Энергоспортзал' in правила and u'в спортзал.' not in правила
    assert u'пошла в спортзал' in evgeny.cases(), 'сами истории команды не меняются'


# ------------------------------------------------------------------ человек закрывает разговор

@pytest.mark.parametrize('text', [
    u'Хорошо, посмотрю', u'хорошо посмотрю', u'Ок', u'Спасибо!', u'Спасибо большое 🙏',
    u'Понятно, спасибо', u'Пока нету вопросов', u'Хорошо, я подумаю', u'🙏', u'Спасибо, Евгений',
    u'Благодарю за марафон', u'Хорошо, посмотрю четвёртый день ещё раз', u'И вам спасибо',
])
def test_узнаёт_прощание(text):
    assert evgeny.closing(text)


@pytest.mark.parametrize('text', [
    u'Да', u'Нет', u'Хорошо, а сколько стоит', u'Спасибо, было тепло в груди',
    u'Пока нету вопросов , и денег если честно нету', u'?', u'Спасибо. А можно вопрос?', u'', None,
    u'Спасибо ' * 30,
])
def test_не_путает_с_прощанием(text):
    assert not evgeny.closing(text)


def test_на_вопрос_бота_хорошо__это_ответ_а_не_прощание():
    u"""«Рассказать, как там всё устроено?» — «Хорошо» значит «да, расскажите»."""
    assert not evgeny.closing(u'Хорошо', asked=True)
    assert not evgeny.closing(u'👍', asked=True)
    assert evgeny.closing(u'Хорошо, посмотрю', asked=True), '«посмотрю» прощается всегда'
    assert evgeny.closing(u'Пока нет вопросов', asked=True), 'на «Есть вопросы?»'


async def test_хорошо_посмотрю__одна_короткая_фраза_без_вопроса(monkeypatch):
    u"""Как в проверке Павла: после предложения 4-го дня — «хорошо, посмотрю»."""
    _участник(1)
    _евгений_писал(1, u'Записи открыты ещё 72 часа, к ним можно вернуться 🙏 Есть вопросы?')
    bot = FakeBot()
    фальшивый = ИИ(u'Приятного просмотра 🙏 А страхи на дне финансов поднялись сильнее всего '
                   u'остального?\n[FEEDBACK 4]')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, u'Хорошо, посмотрю')
    assert _человеку(bot, 1) == [u'Приятного просмотра 🙏']
    последнее = фальшивый.запросы[-1][-1]['content']
    assert u'Человек закрывает разговор: «Хорошо, посмотрю»' in последнее
    assert u'КАК НАПИСАТЬ ЭТОТ ОТВЕТ' not in последнее, 'жребий формы прощанию не нужен'


async def test_второе_спасибо_подряд__евгений_молчит(monkeypatch):
    _участник(1)
    _евгений_писал(1, u'Есть вопросы?')
    bot = FakeBot()
    фальшивый = ИИ(u'Приятного просмотра 🙏', u'Рад был помочь 🙏', u'Рад, что откликнулось 🙏')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, u'Хорошо, посмотрю')
    await _пишет(bot, 1, u'Спасибо')
    assert _человеку(bot, 1) == [u'Приятного просмотра 🙏'], 'на «спасибо» за «приятного» — тишина'
    assert len(фальшивый.запросы) == 1
    _евгений_писал(1, u'Как прошёл второй день?')         # дальше — новый шаг марафона
    await _пишет(bot, 1, u'Спасибо, посмотрю вечером')
    assert _человеку(bot, 1)[-1] == u'Рад был помочь 🙏', 'после нового сообщения бота — снова ответ'


async def test_новая_проверка_прощание_не_помнит(monkeypatch):
    _участник(PAVEL, u'Павел')
    db.put_content(evgeny.CLOSED_KEY % PAVEL, 'ts', str(time.time()))
    db.evgeny_forget(PAVEL)
    assert db.get_content(evgeny.CLOSED_KEY % PAVEL) is None


@pytest.mark.parametrize('ответ_ии', [
    u'А что запомнилось больше всего?',
    u'Энергоспортзал ждёт вас, неделя за неделей.',
    u'',
])
async def test_ии_ответил_на_прощание_не_то__заготовка(monkeypatch, ответ_ии):
    _участник(1)
    _евгений_писал(1, u'Записи открыты ещё 72 часа 🙏')
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ответ_ии))
    await _пишет(bot, 1, u'Хорошо, посмотрю')
    assert _человеку(bot, 1) == [u'Приятного просмотра 🙏']


async def test_на_ты__заготовка_тоже_на_ты(monkeypatch):
    _участник(1)
    db.put_content(evgeny.TY_KEY % 1, 'evgeny', '1')
    _евгений_писал(1, u'Мы с тобой прошли весь марафон 🙏')
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Как тебе такое?'))
    await _пишет(bot, 1, u'Хорошо, подумаю')
    assert _человеку(bot, 1) == [u'Конечно, подумай спокойно 🙏']


async def test_хорошо_на_вопрос_бота__обычный_ответ(monkeypatch):
    _участник(1)
    _евгений_писал(1, u'Рассказать, как там всё устроено?')
    bot = FakeBot()
    фальшивый = ИИ(u'Это программа Павла: 4 недели, каждая — одна сфера жизни.')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, u'Хорошо')
    assert u'КАК НАПИСАТЬ ЭТОТ ОТВЕТ' in фальшивый.запросы[-1][-1]['content']
    assert _человеку(bot, 1) == [u'Это программа Павла: 4 недели, каждая — одна сфера жизни.']


def test_жребий_не_заставляет_спрашивать_без_повода():
    u"""Вопрос в конце — только если человек сейчас рассказал об ощущениях: в проверке Павла
    на «пока нет вопросов» Евгений спросил про страхи дня финансов."""
    спрашивать = [c for c in evgeny._CLOSINGS if u'вопросом' in c]
    assert спрашивать and all(u'если он о них сейчас рассказал' in c for c in спрашивать)
    assert u'О чём уже поговорили, к тому сам не возвращайся' in texts.EVGENY_PROMPT
    assert u'что денег нет, — прими спокойно и тепло закрой разговор' in texts.EVGENY_COACH_TASK


# ------------------------------------------------------------------ знакомство в проверке

class БотСПечатью(FakeBot):
    u"""Записывает «печатает…»: знакомство в проверке уходит без него."""

    async def send_chat_action(self, chat_id, action, **kw):
        self.sent.append(('action', chat_id, action))


async def test_знакомство_в_проверке_сразу__до_кнопок_дней():
    u"""Павел 11.11.2026 нажал «1-й день», пока знакомство «печаталось» 8 секунд: вопрос про
    день пришёл первым, знакомство — следом, второй раз."""
    bot = БотСПечатью()
    сообщение = FakeMessage(text=texts.EVGENY_TRY_BUTTON, user=FakeUser(ТЕСТЕР, 'Evgenve4niy',
                                                                       u'Евгений'), bot=bot)
    await admin.on_evgeny_try(сообщение)
    assert 'action' not in [k for k, c, p in bot.sent], 'без «печатает…»'
    знакомство, пометка = _человеку(bot, ТЕСТЕР)
    assert знакомство == texts.EVGENY_COACH_INTRO, 'сначала знакомство, потом кнопки дней'
    assert пометка == сообщение.answers[0] and u'Выше — его первое сообщение' in пометка
    assert await evgeny.try_day(bot, ТЕСТЕР, 1) == ''
    вопрос = _человеку(bot, ТЕСТЕР)[-1]
    assert not вопрос.startswith(texts.EVGENY_COACH_HELLO), 'уже познакомились — не представляется'
