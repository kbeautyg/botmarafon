# -*- coding: utf-8 -*-
u"""Евгений после проверки Павла (09.11.2026, голосовые через AleX).

На «вы»; предложил человек сам «давай на ты» — Евгений переходит и помнит это.
ИИ видит, как Евгений уже начинал и заканчивал сообщения этому человеку, — чтобы
не писать под копирку. Врача в обратной связи не советует — только при
неотложном состоянии. ИИ здесь поддельный.
"""
import asyncio
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, scheduler, texts                  # noqa: E402
from bot.handlers import support                                          # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'style.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX))
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


def _участник(uid, name=u'Анна'):
    db.remember_user(uid, 'user%d' % uid, name)
    db.mark_launched(uid)


def _человеку(bot, uid):
    return [p for k, c, p in bot.sent if c == uid and k == 'text']


async def _знакомство(bot, uid):
    u"""Шаг воронки «Евгений знакомится» — как планировщик."""
    db.add_job(uid, 'evgeny_intro', 0, time.time() - 1)
    job = next(j for j in db.user_jobs(uid) if j['chain'] == 'evgeny_intro')
    await scheduler.run_job(bot, job)


async def _пишет(bot, uid, text):
    await support.to_support(FakeMessage(text=text, user=FakeUser(uid), bot=bot))
    await asyncio.sleep(0.2)


# ------------------------------------------------------------------ «вы» и «ты»

def test_знакомство_и_вопросы_про_день_на_вы():
    на_ты = re.compile(u'\\b(?:ты|тебя|тебе|твоим|напиши|начинай|пиши|давал)\\b', re.I)
    на_вы = re.compile(u'\\b(?:вы|вас|вам|вашим|напишите|давали)\\b', re.I)
    for text in (texts.EVGENY_COACH_INTRO, texts.EVGENY_COACH_ASK, texts.EVGENY_COACH_AGAIN):
        assert на_вы.search(text) and not на_ты.search(text), text
    for text in (texts.EVGENY_COACH_ASK_TY, texts.EVGENY_COACH_AGAIN_TY):
        assert на_ты.search(text) and not на_вы.search(text), text


async def test_по_умолчанию_на_вы_и_ии_об_этом_знает(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)
    assert _человеку(bot, 1)[-1] == texts.EVGENY_COACH_INTRO
    assert not evgeny.on_ty(1)
    assert u'Обращение: на «вы»' in evgeny._card(1)
    assert evgeny.coach_text(1, 'day1') == texts.EVGENY_COACH_ASK.format(day=u'первого')


@pytest.mark.parametrize('text', [
    u'Давай на ты', u'Можно на «ты»?', u'давайте перейдём на ты', u'Пишите мне на ты',
    u'Обращайся ко мне на ты 🙂', u'Не надо на вы, мы же свои', u'Евгений, можно со мной на ты',
])
def test_узнаёт_предложение_перейти_на_ты(text):
    assert evgeny.offers_ty(text)


@pytest.mark.parametrize('text', [
    u'Спасибо, вы очень помогли', u'Можно не на ты?', u'А ты кто?', u'Ты бот?',
    u'Давай дальше', u'Не надо на ты', u'Что значит на тонком плане?', u'', None,
])
def test_не_путает_с_предложением_перейти_на_ты(text):
    assert not evgeny.offers_ty(text)


async def test_предложил_сам_на_ты__евгений_переходит_и_помнит(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)
    фальшивый = ИИ(u'Давай, с радостью 🙏 Как прошёл первый день?')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, u'Евгений, давай на ты')
    assert evgeny.on_ty(1)
    карточка = фальшивый.запросы[-1][1]['content']
    assert u'Обращение: на «ты»' in карточка, 'ИИ узнаёт об этом уже в этом ответе'
    assert evgeny.coach_text(1, 'day1') == texts.EVGENY_COACH_ASK_TY.format(day=u'первого')
    db.log_event(1, 'feedback', '1')
    assert evgeny.coach_text(1, 'day2').startswith(u'Дорогой мой друг, так же как ты давал')


async def test_тег_ty__ии_сам_понял_что_перешли_на_ты(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)
    monkeypatch.setattr(ai, 'complete', ИИ(u'Конечно, давай на ты 🙂\n[TY]'))
    await _пишет(bot, 1, u'Мы же можем общаться попроще?')
    assert evgeny.on_ty(1)
    assert _человеку(bot, 1)[-1] == u'Конечно, давай на ты 🙂', 'тег человеку не уходит'


def test_разбор_тега_ty():
    assert evgeny.parse(u'Давай 🙂\n[TY]') == (u'Давай 🙂', {'ty': ''})


async def test_голосовое__просьба_написать_текстом_на_ты_тому_кто_предложил(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)
    evgeny._set_ty(1)
    голос = FakeMessage(user=FakeUser(1), bot=bot)
    голос.voice = type('Voice', (), {'file_id': 'v1'})()
    await support.to_support(голос)
    await asyncio.sleep(0.1)
    assert _человеку(bot, 1)[-1] == texts.EVGENY_VOICE_TY


async def test_новая_проверка_командой_снова_на_вы():
    assert await evgeny.try_start(PAVEL, 'pavel', u'Павел') == ''
    evgeny._set_ty(PAVEL)
    assert evgeny.on_ty(PAVEL)
    assert await evgeny.try_start(PAVEL, 'pavel', u'Павел') == ''
    assert not evgeny.on_ty(PAVEL), 'проверка с начала — и обращение с начала'


# ------------------------------------------------------------------ не под копирку

def test_ии_видит_как_евгений_уже_начинал_и_заканчивал():
    _участник(1)
    db.save_message(1, 'out', 'text', u'Тянуло справа — это мужская линия рода. Посмотрите '
                    u'третий день целиком, в спокойной обстановке, и напишите мне, какие будут '
                    u'ощущения, хорошо?', author=evgeny.EVGENY)
    db.save_message(1, 'out', 'text', u'Привет от команды', author=PAVEL)
    db.save_message(1, 'in', 'text', u'После третьего дня дрожь в теле')
    last = evgeny._request(1, texts.EVGENY_COACH_REPLY_TASK)[-1]
    assert last['role'] == 'system' and last['content'].startswith(texts.EVGENY_COACH_REPLY_TASK)
    assert u'«Тянуло справа — это мужская линия рода.»' in last['content']
    assert u'какие будут ощущения, хорошо?»' in last['content']
    assert u'Привет от команды' not in last['content'], 'только свои сообщения Евгения'


def test_ии_видит_сколько_раз_уже_звучали_слова_метода():
    _участник(1)
    for text in (u'Идёт очищение тонкого плана и застоев в самом теле.',
                 u'Слева — женская линия рода, это нарушение на тонком плане родовой системы.'):
        db.save_message(1, 'out', 'text', text, author=evgeny.EVGENY)
    echo = evgeny._echo(1)
    assert u'«тонкий план» — 2' in echo and u'«застои» — 1' in echo
    assert u'«родовая система» — 1' in echo and u'«линия рода» — 1' in echo
    assert u'низкочастотные' not in echo, 'чего не было — не перечисляем'


def test_евгений_ещё_не_писал__в_задании_задача_и_жребий_без_повторов():
    _участник(1)
    db.save_message(1, 'in', 'text', u'Здравствуйте')
    последнее = evgeny._request(1, texts.EVGENY_REPLY_TASK)[-1]['content']
    assert последнее.startswith(texts.EVGENY_REPLY_TASK + u'\n\nКАК НАПИСАТЬ ЭТОТ ОТВЕТ')
    assert u'ТАК ТЫ УЖЕ ПИСАЛ' not in последнее


def test_берёт_только_последние_сообщения_и_обрезает_длинные():
    _участник(1)
    for n in range(evgeny.ECHO + 2):
        db.save_message(1, 'out', 'text', u'Сообщение %d. %s' % (n, u'слово ' * 40),
                        author=evgeny.EVGENY)
    echo = evgeny._echo(1)
    assert u'Сообщение 0.' not in echo and u'Сообщение %d.' % (evgeny.ECHO + 1) in echo
    цитаты = re.findall(u'«([^»]*)»', echo)
    assert len(цитаты) == 2 * evgeny.ECHO
    assert all(len(цитата) <= 112 for цитата in цитаты)


# ------------------------------------------------------------------ от дня к дню (11.11.2026)

def _команда(args):
    return type('Cmd', (), {'args': args})()


def test_от_дня_к_дню__касание_на_2_м_продажа_на_3_м_и_4_м():
    u"""Sharp 11.11.2026: «на втором дне он только касается; на третьем и 4 дне он уже продаёт
    энергоспортзал — что он реально помогает»."""
    задача = texts.EVGENY_COACH_TASK
    assert u'после 2-го — обратная связь, один пример' in задача
    assert u'лёгкое касание Энергоспортзала: одной фразой, мимоходом, без рекомендации и продажи' in задача
    assert (u'после 3-го и после 4-го — короткая обратная связь и продажа: Энергоспортзал реально '
            u'помогает') in задача
    assert u'тег [PAY] хочет в Энергоспортзал' in задача
    assert u'Спортзал сам не предлагаешь и не продаёшь' not in задача


# ------------------------------------------------------------------ разнообразие (11.11.2026)

async def _шаг(bot, uid, chain):
    db._run('DELETE FROM jobs WHERE user_id=? AND chain=?', (uid, chain))
    db.add_job(uid, chain, 0, time.time() - 1)
    job = next(j for j in db.user_jobs(uid) if j['chain'] == chain)
    await scheduler.run_job(bot, job)


def test_ответу_человеку_жребий_как_начать_и_закончить():
    _участник(1)
    db.save_message(1, 'in', 'text', u'Было тепло в руках')
    последнее = evgeny._request(1, texts.EVGENY_COACH_REPLY_TASK)[-1]['content']
    assert u'КАК НАПИСАТЬ ЭТОТ ОТВЕТ' in последнее
    assert u'КАК НАПИСАТЬ' not in evgeny._request(1, texts.EVGENY_OPEN_TASK)[-1]['content'], \
        'у знакомства и напоминаний форма своя'
    import random
    жребии = {evgeny._style(random.Random(n)) for n in range(12)}
    assert len(жребии) >= 6, 'жребий правда разный'
    assert all(u'правила «ОТ ДНЯ К ДНЮ»' in s for s in жребии), 'правила этапа важнее жребия'


def test_ии_видит_повторы_слов_отклик_и_зажим():
    _участник(1)
    db.save_message(1, 'out', 'text', u'Сильный отклик! Ушёл зажим. Хороший отклик.', author=evgeny.EVGENY)
    echo = evgeny._echo(1)
    assert u'«отклик» — 2' in echo and u'«зажим» — 1' in echo


async def test_вопрос_про_день_пишет_ии_своими_словами(monkeypatch):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)
    фальшивый = ИИ(u'Как прошёл для вас первый день — что было в теле? Может, приходили образы?')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _шаг(bot, 1, 'evgeny_day1')
    assert _человеку(bot, 1)[-1] == u'Как прошёл для вас первый день — что было в теле? Может, приходили образы?'
    задача = фальшивый.запросы[-1][-1]['content']
    assert задача.startswith(texts.EVGENY_ASK_TASK.format(day=1, name=u'Предназначение', before=u''))
    db.log_event(1, 'feedback', '1')
    фальшивый.ответы.append(u'А как отозвался день отношений? Расскажите, что чувствовали.')
    await _шаг(bot, 1, 'evgeny_day2')
    assert texts.EVGENY_ASK_BEFORE.format(day=1) in фальшивый.запросы[-1][-1]['content'], \
        'ИИ знает, что после 1-го дня человек уже рассказывал'


@pytest.mark.parametrize('ответ', [
    u'', u'Как прошёл день?\n[FEEDBACK 1]', u'Очень длинно. ' * 40, ai.AIError(u'нет связи'),
])
async def test_ии_не_справился__вопрос_уходит_текстом_заказчика(monkeypatch, ответ):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)

    async def ии(messages, **kw):
        if isinstance(ответ, Exception):
            raise ответ
        return ответ
    monkeypatch.setattr(ai, 'complete', ии)
    await _шаг(bot, 1, 'evgeny_day1')
    assert _человеку(bot, 1)[-1] == texts.EVGENY_COACH_ASK.format(day=u'первого')


async def test_ии_думает_слишком_долго__шаг_воронки_не_ждёт(monkeypatch):
    monkeypatch.setattr(evgeny, 'ASK_BY_AI', True)
    monkeypatch.setattr(evgeny, 'ASK_TIMEOUT', 0.05)
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)

    async def ии(messages, **kw):
        await asyncio.sleep(5)
        return u'Поздно'
    monkeypatch.setattr(ai, 'complete', ии)
    начало = time.time()
    await _шаг(bot, 1, 'evgeny_day1')
    assert time.time() - начало < 2
    assert _человеку(bot, 1)[-1] == texts.EVGENY_COACH_ASK.format(day=u'первого')


async def test_по_умолчанию_в_проверках_вопрос_текстом_заказчика():
    assert evgeny.ASK_BY_AI is False, 'conftest: без подделки ИИ запрос ушёл бы в сеть'


def test_пишет_коротко():
    u"""Sharp 11.11.2026: «пусть короче пишет» — в правилах и в последнем слове запроса."""
    assert u'2–3 коротких предложения, до 300 знаков' in texts.EVGENY_PROMPT
    assert u'Длиннее — никогда' in texts.EVGENY_PROMPT
    assert texts.EVGENY_COACH_REPLY_TASK.endswith(u'с примером или Энергоспортзалом — до 450.')
    assert u'до 300 знаков' in texts.EVGENY_REPLY_TASK
    assert u'скажи про каждое, коротко' not in texts.EVGENY_SENSES


def test_спортзал_не_продаёт_как_лечение():
    u"""Немедицинской услуге закон о рекламе не даёт обещать лечебный эффект: истории «ушли
    головные боли» Евгений результатом спортзала не рассказывает."""
    assert u'не рассказывай, что у кого-то прошла болезнь или симптом' in texts.EVGENY_PROMPT
    assert u'спортзал — не лечение' in texts.EVGENY_PROMPT


def test_людей_для_примеров_не_придумывает():
    assert u'Людей, имена и случаи не придумывай никогда' in texts.EVGENY_PROMPT
    assert u'обобщённые отклики без имён — так и говори' in texts.EVGENY_PROMPT
    _участник(1)
    правила = evgeny._request(1, texts.EVGENY_REPLY_TASK)[0]['content']
    assert правила.endswith(u'ИСТОРИИ УЧАСТНИКОВ\n' + texts.EVGENY_CASES_NONE), \
        'историй нет — Евгению прямо сказано говорить обобщённо'


async def test_истории_участников_команда_заменяет_дописывает_и_очищает():
    from bot.handlers import admin
    bot = FakeBot()
    первая = u'Ирина, 52 года: на втором дне сильно кололо слева, потом ушла обида на маму.'
    вторая = u'Олег: на третьем дне тянуло справа.'
    сообщение = FakeMessage(text=u'/евгений_примеры', user=FakeUser(ALEX), bot=bot)
    await admin.on_evgeny_cases(сообщение, _команда(u''))
    assert u'Пока пусто' in сообщение.answers[-1]
    await admin.on_evgeny_cases(сообщение, _команда(первая))
    assert evgeny.cases() == первая
    await admin.on_evgeny_cases(сообщение, _команда(u'добавить\n' + вторая))
    assert evgeny.cases() == первая + u'\n\n' + вторая
    _участник(1)
    assert evgeny._request(1, texts.EVGENY_REPLY_TASK)[0]['content'].endswith(
        u'ИСТОРИИ УЧАСТНИКОВ\n' + первая + u'\n\n' + вторая)
    await admin.on_evgeny_cases(сообщение, _команда(u''))
    assert u'Ирина, 52 года' in сообщение.answers[-1]
    await admin.on_evgeny_cases(сообщение, _команда(u'сброс'))
    assert evgeny.cases() == ''


async def test_истории_правит_только_команда():
    from bot.handlers import admin
    чужой = FakeMessage(text=u'/евгений_примеры', user=FakeUser(5), bot=FakeBot())
    await admin.on_evgeny_cases(чужой, _команда(u'Мария Петровна: всё прошло'))
    assert evgeny.cases() == '' and чужой.answers == []


async def test_согласие_на_спортзал__команде_хочет_в_спортзал(monkeypatch):
    _участник(1)
    bot = FakeBot()
    await _знакомство(bot, 1)
    monkeypatch.setattr(ai, 'complete', ИИ(u'[PAY] хочет в спортзал'))
    await _пишет(bot, 1, u'Да, хочу в спортзал')
    команде = [p for k, c, p in bot.sent if c in (SHARP, PAVEL, ALEX) and k == 'text']
    assert any(texts.EVGENY_PAY_TITLE_GYM in p for p in команде), команде
    assert _человеку(bot, 1)[-1] == texts.EVGENY_COACH_INTRO, 'об оплате Евгений молчит'


def test_на_ты__и_когда_человек_сам_пишет_на_ты():
    assert u'Перешёл человек сам на «ты» — пишет тебе на «ты»' in texts.EVGENY_COACH_TASK
    assert u'сам пишет тебе на «ты» или предложил перейти' in texts.EVGENY_PROMPT
    assert u'Сам на «ты» не переходи, даже если' not in texts.EVGENY_COACH_TASK


# ------------------------------------------------------------------ врач

def test_врача_в_обратной_связи_не_советует__только_при_неотложном():
    assert u'советуешь обратиться к врачу' not in texts.EVGENY_PROMPT
    assert u'к врачу, не списывать' not in texts.EVGENY_FACTS
    assert u'ни советов сходить к врачу' in texts.EVGENY_PROMPT
    assert u'103 или 112' in texts.EVGENY_PROMPT and u'[TEAM] срочно' in texts.EVGENY_PROMPT
    assert u'как вы сейчас себя чувствуете' in texts.EVGENY_COACH_TASK   # как запрет
    assert u'целиком, в спокойной обстановке, — скажи один раз' in texts.EVGENY_COACH_TASK
