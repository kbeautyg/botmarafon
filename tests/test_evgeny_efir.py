# -*- coding: utf-8 -*-
u"""Эфиры Павла после прощания и вопрос о них через сутки (Павел 11.11.2026, голосовые ночью).

«Всем людям, кто говорит, что у них нету сейчас возможности оплатить… он всегда, когда
прощается, после последнего сообщения должен отправлять вот эти два сообщения. Текст не надо
изменять… присылает их подряд через полминутки, и всё — затихает, пока человек ему не напишет».
«После затишья, если в течение 24 часов человек ничего не пишет, он должен спросить:
„Приветствую вас, ну как, просмотрели записи эфиров, есть какие-то вопросы?“ — и опять склонять
потихонечку к покупке энергетического спортзала». ИИ здесь поддельный.
"""
import asyncio
import os
import sqlite3
import sys
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import ai, config, db, evgeny, stats, texts                      # noqa: E402
from bot.handlers import admin, support                                   # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

SHARP, PAVEL, ALEX = 7874595355, 312701042, 350631550
ТЕСТЕР = 7692784223                         # @Evgenve4niy
ЧАС = 3600
ПРЕДЛОЖЕНИЕ = (u'Мы с вами прошли весь марафон — самое время продолжить в Энергоспортзале. '
               u'Есть вопросы?')
ОТКАЗ = u'Пока нету вопросов , и денег если честно нету'          # слово в слово из проверки Павла
ПРОЩАНИЕ = (u'Понимаю, всё в порядке. Записи марафона открыты ещё 72 часа — к ним можно '
            u'вернуться 🙏')
ПЕРВЫЙ, ВТОРОЙ = texts.EVGENY_EFIRY


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'efir.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (PAVEL, ALEX, ТЕСТЕР))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', SHARP)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', (PAVEL, ALEX))
    monkeypatch.delenv('PURCHASE_TO', raising=False)
    monkeypatch.setattr(config, 'AI_API_KEY', 'test-key')
    monkeypatch.setattr(evgeny, 'DEBOUNCE', 0.05)
    monkeypatch.setattr(evgeny, 'EFIR_GAP', 0.0)
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

    def __init__(self, *ответы):
        self.ответы = list(ответы)
        self.запросы = []

    async def __call__(self, messages, **kw):
        self.запросы.append(messages)
        return self.ответы.pop(0) if self.ответы else u'Благодарю 🙏'


def _день(uid, day, at):
    db._run("INSERT INTO events (user_id, kind, ref, at) VALUES (?, 'day', ?, ?)", (uid, str(day), at))


def _прошёл_марафон(uid, часов_назад=3.0, рассказал_про_4=True):
    u"""Участник, которому 4-й день пришёл часов_назад, а координатор предложил Энергоспортзал."""
    db.remember_user(uid, 'user%d' % uid, u'Виолетта')
    db.mark_launched(uid)
    for day in (1, 2, 3):
        _день(uid, day, time.time() - (часов_назад + 30 - day) * ЧАС)
    _день(uid, 4, time.time() - часов_назад * ЧАС)
    db.evgeny_on(uid, uid, mode='coach')
    if рассказал_про_4:
        db.log_event(uid, 'feedback', '4')
    db.save_message(uid, 'out', 'text', ПРЕДЛОЖЕНИЕ, None, author=evgeny.EVGENY)


def _человеку(bot, uid):
    return [p for k, c, p in bot.sent if c == uid and k == 'text']


async def _пишет(bot, uid, text, ждать=0.2):
    await support.to_support(FakeMessage(text=text, user=FakeUser(uid), bot=bot))
    await asyncio.sleep(ждать)


def _последнее_системное(запрос):
    return запрос[-1]['content']


def _в_мск(час):
    u"""Сегодня в час по Москве — эфиры спрашиваются только днём."""
    return datetime.now(stats.MSK).replace(hour=час, minute=0, second=0, microsecond=0).timestamp()


# ------------------------------------------------------------------ тексты Павла

def test_тексты_эфиров__слово_в_слово_как_у_павла():
    u"""«Текст не надо изменять, пускай такой же остаётся» — со всеми его знаками."""
    assert ПЕРВЫЙ == (
        u'Так же в эти 72 часа рекомендую посмотреть два эфира \n'
        u'Тут вы сможете понять глубину работы Павла в личных сессиях \n'
        u'И сможете услышать те жизненные ситуации которые привели людей уже на обучение к Павлу \n\n'
        u'Ниже первый эфир тут как раз про глубину работы \n\n'
        u'https://t.me/Tm_Finish/5366')
    assert ВТОРОЙ == (u'А теперь второй эфир тут уже обратная связь людей , где они сами '
                      u'рассказывают свою историю 🙏\n\n\nhttps://t.me/Tm_Finish/5316')
    assert evgeny.energo(ПЕРВЫЙ) == ПЕРВЫЙ and evgeny.energo(ВТОРОЙ) == ВТОРОЙ
    # 12.11.2026 вечером: «через 15 часов спросить — смотрели вы эфиры, которые я прислал?»
    assert texts.EVGENY_EFIR_ASK == u'Приветствую вас! Смотрели вы эфиры, которые я прислал?'
    assert texts.EVGENY_EFIR_ASK_TY == u'Приветствую! Удалось посмотреть эфиры, которые я прислал?'
    assert evgeny.EFIR_QUIET == 15 * ЧАС
    assert u'предложи попробовать месяц в Энергоспортзале' in texts.EVGENY_EFIR_TALK
    assert u'все сферы жизни' in texts.EVGENY_EFIR_TALK


def test_в_подсказке__тег_прощания_и_72_часа():
    assert u'[BYE]' in texts.EVGENY_PROMPT and u'[BYE]' in texts.EVGENY_COACH_TASK
    assert u'[BYE]' in texts.EVGENY_SALES_TASK
    assert u'скажи, что записи марафона открыты ещё 72 часа, и поставь тег [BYE]' in texts.EVGENY_COACH_TASK
    for text in (texts.EVGENY_EFIR_TALK, texts.EVGENY_CLOSING_EFIR):
        assert evgeny.energo(text) == text


@pytest.mark.parametrize('text', [
    ОТКАЗ, u'Денег сейчас нет', u'Нет возможности сейчас', u'Подумаю', u'Не сейчас, может позже',
    u'Для меня дорого', u'Нет', u'нет)', u'Нет, спасибо', u'Пока не готова', u'С зарплаты посмотрю',
    u'Средств пока не хватает',
])
def test_узнаёт_отказ(text):
    assert evgeny.declined(text)


@pytest.mark.parametrize('text', [
    u'Да, хочу', u'Было тепло в груди', u'А сколько стоит?', u'Хочу вступить, как оплатить',
    u'Спасибо, дорогой Евгений', u'Нет ощущений не было, но картинки были яркие', u'', None,
])
def test_не_путает_с_отказом(text):
    assert not evgeny.declined(text)


# ------------------------------------------------------------------ прощание и эфиры

async def test_отказ_после_предложения__прощание_и_следом_два_эфира(monkeypatch):
    u"""Как в проверке Павла: предложение 4-го дня — «пока нету вопросов, и денег нету»."""
    _прошёл_марафон(1)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ПРОЩАНИЕ + u'\n[BYE]'))
    await _пишет(bot, 1, ОТКАЗ)
    assert _человеку(bot, 1) == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ], 'прощание без тега, следом эфиры'
    assert [m['text'] for m in db.chat_history(1)][-2:] == [ПЕРВЫЙ, ВТОРОЙ], 'и в переписке'
    assert db.evgeny_get(1)['efir_at']


async def test_эфиры_через_полминуты_каждый(monkeypatch):
    u"""«Присылает их подряд через полминутки»: пауза перед каждым — полминуты вместе с «печатает…»."""
    _прошёл_марафон(1)
    monkeypatch.setattr(evgeny, 'EFIR_GAP', 30.0)
    monkeypatch.setattr(evgeny, '_typing_seconds', lambda text: 6.0)
    паузы = []

    async def сон(секунд):
        паузы.append(секунд)

    monkeypatch.setattr(evgeny.asyncio, 'sleep', сон)
    assert await evgeny._efir(FakeBot(), 1)
    assert паузы[:1] == [24.0] and паузы.count(24.0) == 2, 'полминуты = 24 с тишины + 6 с «печатает…»'


async def test_ии_прощаясь_забыл_тег__эфиры_всё_равно(monkeypatch):
    _прошёл_марафон(1)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ПРОЩАНИЕ))
    await _пишет(bot, 1, ОТКАЗ)
    assert _человеку(bot, 1) == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ]


async def test_подумаю__короткая_фраза_с_72_часами_и_эфиры(monkeypatch):
    _прошёл_марафон(1)
    bot = FakeBot()
    короткий = u'Конечно, подумайте спокойно 🙏 Записи марафона открыты ещё 72 часа.'
    фальшивый = ИИ(короткий)
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, u'Подумаю')
    assert _человеку(bot, 1) == [короткий, ПЕРВЫЙ, ВТОРОЙ]
    последнее = _последнее_системное(фальшивый.запросы[-1])
    assert u'Человек закрывает разговор: «Подумаю»' in последнее
    assert texts.EVGENY_CLOSING_EFIR in последнее, 'о каких 72 часах эфиры — сказать в прощании'


async def test_после_эфиров_хорошо_посмотрю__приятного_просмотра_и_эфиры_не_повторяются(monkeypatch):
    _прошёл_марафон(1)
    bot = FakeBot()
    фальшивый = ИИ(ПРОЩАНИЕ + u'\n[BYE]', u'Приятного просмотра 🙏', u'Рад был помочь 🙏')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, ОТКАЗ)
    await _пишет(bot, 1, u'Хорошо, посмотрю')
    assert _человеку(bot, 1) == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ, u'Приятного просмотра 🙏']
    assert texts.EVGENY_CLOSING_EFIR not in _последнее_системное(фальшивый.запросы[-1])
    await _пишет(bot, 1, u'Спасибо')
    assert _человеку(bot, 1)[-1] == u'Приятного просмотра 🙏', 'второе «спасибо» подряд — тишина'
    assert _человеку(bot, 1).count(ПЕРВЫЙ) == 1


async def test_спасибо_пока_уходили_эфиры__ответом_и_были_эфиры(monkeypatch):
    u"""Между эфирами полминуты: человек пишет «спасибо» — Евгений не встревает между ними и не
    отвечает «рад был помочь» после ссылок; а на «хорошо, посмотрю» после эфиров — отвечает."""
    _прошёл_марафон(1)
    monkeypatch.setattr(evgeny, 'EFIR_GAP', 0.3)
    bot = FakeBot()
    фальшивый = ИИ(ПРОЩАНИЕ + u'\n[BYE]', u'Приятного просмотра 🙏')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, ОТКАЗ, ждать=0.15)
    assert _человеку(bot, 1) == [ПРОЩАНИЕ], 'эфиры ещё в пути'
    await _пишет(bot, 1, u'Спасибо 🙏', ждать=1.0)
    assert _человеку(bot, 1) == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ]
    assert len(фальшивый.запросы) == 1
    await _пишет(bot, 1, u'Хорошо, посмотрю')
    assert _человеку(bot, 1)[-1] == u'Приятного просмотра 🙏'


async def test_вопрос_пока_уходили_эфиры__ответ_после_них(monkeypatch):
    _прошёл_марафон(1)
    monkeypatch.setattr(evgeny, 'EFIR_GAP', 0.3)
    bot = FakeBot()
    фальшивый = ИИ(ПРОЩАНИЕ + u'\n[BYE]', u'Конечно, можно — записи открыты ещё 72 часа.')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, ОТКАЗ, ждать=0.15)
    await _пишет(bot, 1, u'А можно вступить потом, через месяц', ждать=1.0)
    assert _человеку(bot, 1) == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ,
                                 u'Конечно, можно — записи открыты ещё 72 часа.']
    история = [m['content'] for m in фальшивый.запросы[-1] if m['role'] != 'system']
    assert история[-4:] == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ, u'А можно вступить потом, через месяц'], \
        'для ИИ эфиры — сразу за прощанием, вопрос человека — после них'


@pytest.mark.parametrize('ответ_ии', [
    u'Понимаю 🙏 Хотите, расскажу про рассрочку?',            # Евгений спросил — разговор идёт
    u'[PAY] хочет в Энергоспортзал',
    u'Передам команде 🙏\n[TEAM] спрашивает про скидку',
])
async def test_разговор_не_закончен__эфиров_нет(monkeypatch, ответ_ии):
    _прошёл_марафон(1)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ответ_ии))
    await _пишет(bot, 1, u'Денег сейчас нет')
    assert ПЕРВЫЙ not in _человеку(bot, 1)
    assert not db.evgeny_get(1)['efir_at']


async def test_на_3_м_дне_эфиров_нет__предложение_ещё_впереди(monkeypatch):
    u"""Про 3-й день Евгений спрашивает, когда 4-й уже пришёл: «денег нет» на рекомендацию 3-го дня —
    не прощание с марафоном."""
    _прошёл_марафон(1, часов_назад=0.5, рассказал_про_4=False)
    db.log_event(1, 'feedback', '3')
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Понимаю 🙏 Впереди день финансов.\n[BYE]'))
    await _пишет(bot, 1, u'Денег сейчас нет')
    assert _человеку(bot, 1) == [u'Понимаю 🙏 Впереди день финансов.']


async def test_записи_уже_закрыты__эфиров_нет(monkeypatch):
    u"""Эфиры начинаются словами «в эти 72 часа» — позже их текст неправда."""
    _прошёл_марафон(1, часов_назад=80)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ПРОЩАНИЕ + u'\n[BYE]'))
    await _пишет(bot, 1, ОТКАЗ)
    assert _человеку(bot, 1) == [ПРОЩАНИЕ]


РАССКАЗ_ПАВЛА = (u'Куда то провалился и понял что на дне , финансов нету , и так все остальные '
                 u'сферы тоже впринцепи просели')        # проверка Павла 12.11.2026, слово в слово
ПРЕДЛОЖЕНИЕ_БЕЗ_ВОПРОСА = (u'Провалиться и увидеть дно — так начинается выход. Мы с вами прошли '
                          u'весь марафон, и сейчас самое время продолжить уже в Энергоспортзале 🙏')


async def test_финансов_нету_в_рассказе_о_4_м_дне__не_отказ(monkeypatch):
    u"""Павел рассказал об ощущениях 4-го дня («финансов нету»), Евгений ответил предложением без
    вопроса — эфиры ушли следом, хотя Павел не отказывался. Эфиры — только на отказ после него."""
    _прошёл_марафон(1, рассказал_про_4=False)
    db.log_event(1, 'feedback', '3')
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ПРЕДЛОЖЕНИЕ_БЕЗ_ВОПРОСА + u'\n[FEEDBACK 4]',
                                           ПРОЩАНИЕ + u'\n[BYE]'))
    await _пишет(bot, 1, РАССКАЗ_ПАВЛА)
    assert _человеку(bot, 1) == [ПРЕДЛОЖЕНИЕ_БЕЗ_ВОПРОСА], 'предложение — без эфиров следом'
    assert not db.evgeny_get(1)['efir_at']
    await _пишет(bot, 1, ОТКАЗ)
    assert _человеку(bot, 1)[-3:] == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ], 'а на отказ после него — эфиры'


async def test_после_предложения_ещё_рассказ_об_ощущениях__не_отказ(monkeypatch):
    _прошёл_марафон(1)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(u'Дно — это то, что стало видно и уходит 🙏\n[FEEDBACK 4]'))
    await _пишет(bot, 1, u'Ещё вспомнил: понял, что финансов нету, всё на дне')
    assert ПЕРВЫЙ not in _человеку(bot, 1)


async def test_проверка__финансов_нету_на_4_м_дне__эфиров_нет(monkeypatch):
    u"""То же у своего в проверке: кнопка «4-й день» сама по себе — ещё не предложение."""
    evgeny.set_coach(False)
    bot = FakeBot()
    monkeypatch.setattr(ai, 'complete', ИИ(ПРЕДЛОЖЕНИЕ_БЕЗ_ВОПРОСА + u'\n[FEEDBACK 4]'))
    await admin.on_evgeny_try(FakeMessage(text=texts.EVGENY_TRY_BUTTON,
                                          user=FakeUser(PAVEL, 'pavel', u'Павел'), bot=bot))
    assert await evgeny.try_day(bot, PAVEL, 4) == ''
    await _пишет(bot, PAVEL, РАССКАЗ_ПАВЛА)
    assert _человеку(bot, PAVEL)[-1] == ПРЕДЛОЖЕНИЕ_БЕЗ_ВОПРОСА
    assert ПЕРВЫЙ not in _человеку(bot, PAVEL)


async def test_команда_ответила_пока_ждали__эфиры_не_уходят(monkeypatch):
    _прошёл_марафон(1)
    bot = FakeBot()
    evgeny.team_wrote(1, u'Павел')
    assert not await evgeny._efir(bot, 1)
    assert _человеку(bot, 1) == []


# ------------------------------------------------------------------ через сутки — «как эфиры?»

def _эфиры_ушли(uid, часов_назад, ty=False):
    _прошёл_марафон(uid, часов_назад=часов_назад + 1)
    at = _в_мск(12) - часов_назад * ЧАС
    db._run('UPDATE evgeny SET efir_at=?, last_out=? WHERE user_id=?', (at, at, uid))
    if ty:
        db.put_content(evgeny.TY_KEY % uid, 'evgeny', '1')


async def test_15_часов_тишины__спрашивает_смотрели_ли_эфиры_один_раз():
    _эфиры_ушли(1, часов_назад=16)
    bot = FakeBot()
    assert await evgeny.efir_tick(bot, _в_мск(12)) == 1
    assert _человеку(bot, 1) == [texts.EVGENY_EFIR_ASK]
    assert db.chat_history(1)[-1]['author'] == evgeny.EVGENY, 'в переписке — как слова Евгения'
    assert await evgeny.efir_tick(bot, _в_мск(12) + 60) == 0, 'один раз'


async def test_на_ты__спрашивает_на_ты():
    _эфиры_ушли(1, часов_назад=25, ty=True)
    bot = FakeBot()
    await evgeny.efir_tick(bot, _в_мск(12))
    assert _человеку(bot, 1) == [texts.EVGENY_EFIR_ASK_TY]


@pytest.mark.parametrize('часов, час_мск', [(14, 12), (16, 3)])
async def test_рано_или_ночь__не_спрашивает(часов, час_мск):
    _эфиры_ушли(1, часов_назад=часов + (12 - час_мск))
    bot = FakeBot()
    assert await evgeny.efir_tick(bot, _в_мск(час_мск)) == 0
    assert not db.evgeny_get(1)['efir_asked'], 'спросит днём, когда пройдут 15 часов'


async def test_человек_писал_после_эфиров__15_часов_от_его_слов():
    _эфиры_ушли(1, часов_назад=30)
    db._run('UPDATE evgeny SET last_in=? WHERE user_id=?', (_в_мск(12) - 2 * ЧАС, 1))
    assert await evgeny.efir_tick(FakeBot(), _в_мск(12)) == 0


async def test_купил_после_эфиров__не_спрашивает():
    _эфиры_ушли(1, часов_назад=25)
    db._run('INSERT INTO purchases (user_id, product, at) VALUES (1, ?, ?)', ('gym', _в_мск(12) - ЧАС))
    assert await evgeny.efir_tick(FakeBot(), _в_мск(12)) == 0


async def test_евгения_выключили__не_спрашивает():
    _эфиры_ушли(1, часов_назад=25)
    evgeny.team_wrote(1, u'Павел')
    assert await evgeny.efir_tick(FakeBot(), _в_мск(12)) == 0


async def test_после_вопроса_об_эфирах__снова_подводит_к_энергоспортзалу(monkeypatch):
    _эфиры_ушли(1, часов_назад=25)
    bot = FakeBot()
    await evgeny.efir_tick(bot, _в_мск(12))
    фальшивый = ИИ(u'Здорово, что откликнулось про отношения 🙏')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await _пишет(bot, 1, u'Посмотрела, очень откликнулось про отношения')
    последнее = _последнее_системное(фальшивый.запросы[-1])
    assert texts.EVGENY_EFIR_TALK in последнее and u'КАК НАПИСАТЬ ЭТОТ ОТВЕТ' in последнее
    assert _человеку(bot, 1)[-1] == u'Здорово, что откликнулось про отношения 🙏'


def test_напоминания_дожима__после_эфиров_сначала_вопрос_о_них():
    u"""Ушли эфиры — через сутки вопрос об эфирах, а не обычное напоминание дожима."""
    db.remember_user(9, 'u9', u'Ольга')
    db.evgeny_on(9, 0, mode='sales')
    db._run('UPDATE evgeny SET last_out=? WHERE user_id=9', (time.time() - 30 * ЧАС,))
    assert [r['user_id'] for r in db.evgeny_silent(time.time() - 24 * ЧАС, 2, 10)] == [9]
    db.evgeny_efir(9)
    db._run('UPDATE evgeny SET last_out=? WHERE user_id=9', (time.time() - 30 * ЧАС,))
    assert db.evgeny_silent(time.time() - 24 * ЧАС, 2, 10) == []
    db.evgeny_efir_asked(9)
    assert [r['user_id'] for r in db.evgeny_silent(time.time() - 24 * ЧАС, 2, 10)] == [9]


# ------------------------------------------------------------------ проверка командой

async def test_проверка__отказ_эфиры_и_через_2_минуты_вопрос(monkeypatch):
    u"""Своему в проверке ждать сутки нельзя (проверка гаснет через сутки) — вопрос об эфирах
    через 2 минуты тишины, и он видит пометку об этом."""
    evgeny.set_coach(False)                          # участникам Евгений ещё не запущен
    bot = FakeBot()
    фальшивый = ИИ(u'Страхи выходят дрожью 🙏 Самое время продолжить в Энергоспортзале. Есть '
                   u'вопросы?\n[FEEDBACK 4]',
                   ПРОЩАНИЕ + u'\n[BYE]')
    monkeypatch.setattr(ai, 'complete', фальшивый)
    await admin.on_evgeny_try(FakeMessage(text=texts.EVGENY_TRY_BUTTON,
                                          user=FakeUser(ТЕСТЕР, 'Evgenve4niy', u'Евгений'), bot=bot))
    assert await evgeny.try_day(bot, ТЕСТЕР, 4) == ''
    await _пишет(bot, ТЕСТЕР, u'Сегодня поднимались страхи, от них дрожало тело')
    await _пишет(bot, ТЕСТЕР, ОТКАЗ)
    пришло = _человеку(bot, ТЕСТЕР)
    # 12.11.2026 Павел: пометка после эфиров — «хрень, техническое какое-то сообщение»; её нет
    assert пришло[-3:] == [ПРОЩАНИЕ, ПЕРВЫЙ, ВТОРОЙ], 'после эфиров — ничего технического'
    assert await evgeny.efir_tick(bot, time.time() + 60) == 0
    assert await evgeny.efir_tick(bot, time.time() + evgeny.EFIR_QUIET_TRY + 1) == 1
    assert _человеку(bot, ТЕСТЕР)[-1] == texts.EVGENY_EFIR_ASK


def test_пояснение_проверки__про_эфиры_и_2_минуты():
    пояснение = texts.EVGENY_TRY_NOTE.format(state=u'', minutes=2, hours=15, sendoff=10)
    assert u'два эфира Павла' in пояснение and u'Помолчите 2 мин' in пояснение
    assert u'месяц в Энергоспортзале' in пояснение and u'через 15 часов тишины' in пояснение


def test_новая_проверка__эфиры_заново():
    db.remember_user(ТЕСТЕР, 'Evgenve4niy', u'Евгений')
    db.evgeny_on(ТЕСТЕР, ТЕСТЕР, mode='coach')
    db.evgeny_efir(ТЕСТЕР)
    db.evgeny_forget(ТЕСТЕР)
    assert db.evgeny_get(ТЕСТЕР) is None


# ------------------------------------------------------------------ база

def test_старая_база__колонки_эфиров_добавляются(tmp_path):
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
    assert 'efir_at' in колонки and 'efir_asked' in колонки
