# -*- coding: utf-8 -*-
u"""Обработчики: нажатия, ответы, загрузка материалов, служба заботы."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, scheduler, texts                           # noqa: E402
from bot.handlers import admin, poll, purchase, start, support         # noqa: E402
from tests.fakes import (FakeBot, FakeCall, FakeMessage, FakePhoto,    # noqa: E402
                         FakeUser, FakeVideo)

ADMIN = 777
CARE_CHAT = -100500
BUY_CHAT = -100777


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'handlers.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', CARE_CHAT)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', BUY_CHAT)
    yield


# ------------------------------------------------------------------ старт

async def test_старт_даёт_кнопку_заботы_сразу():
    u"""ТЗ: служба заботы должна быть на виду с самого начала."""
    message = FakeMessage(text='/start')
    await start.on_start(message)

    assert db.get_user(1) is not None
    assert texts.WELCOME in message.answers


async def test_приветствие_одним_сообщением_с_двумя_кнопками():
    u"""Раньше следом летело пустое «👇» — inline и клавиатуру вместе не шлют."""
    message = FakeMessage(text='/start')
    await start.on_start(message)

    assert len(message.answers) == 1, u'приветствие должно быть одним сообщением'
    кнопки = [b.text for row in message.markups[0].keyboard for b in row]
    assert кнопки == [texts.LAUNCH_BUTTON, texts.CARE_BUTTON]


async def test_кнопка_запустить_текстом_заводит_воронку():
    await start.on_start(FakeMessage(text='/start'))
    нажатие = FakeMessage(text=texts.LAUNCH_BUTTON)
    await start.on_launch_button(нажатие)

    assert [j['chain'] for j in db.user_jobs(1)] == ['launch']
    # После запуска остаётся только забота: запускать второй раз нечего.
    кнопки = [b.text for row in нажатие.markups[0].keyboard for b in row]
    assert кнопки == [texts.CARE_BUTTON]


async def test_повторное_нажатие_запустить_не_удваивает_воронку():
    await start.on_start(FakeMessage(text='/start'))
    await start.on_launch_button(FakeMessage(text=texts.LAUNCH_BUTTON))
    await start.on_launch_button(FakeMessage(text=texts.LAUNCH_BUTTON))
    assert len(db.user_jobs(1)) == 1


async def test_запуск_ставит_первый_шаг():
    await start.on_start(FakeMessage(text='/start'))
    call = FakeCall('launch')
    await start.on_launch(call)

    assert db.get_user(1)['launched_at'] is not None
    assert [j['chain'] for j in db.user_jobs(1)] == ['launch']
    assert call.markup_cleared, u'кнопку запуска надо убрать, чтобы не жали снова'


async def test_повторный_запуск_не_удваивает_воронку():
    await start.on_start(FakeMessage(text='/start'))
    call = FakeCall('launch')
    await start.on_launch(call)
    await start.on_launch(FakeCall('launch'))

    assert len(db.user_jobs(1)) == 1


# --------------------------------------------------------------- опросник

async def test_ответ_запускает_свою_ветку():
    db.remember_user(1, 'tester', u'Тестер')
    db.set_poll(1, 'day1')
    scheduler.schedule(1, 'day1_no', 0, 12 * 3600)      # отложенное добивание

    await poll.on_answer(FakeCall('poll:day1:yes'))

    цепочки = {j['chain'] for j in db.user_jobs(1)}
    assert цепочки == {'day1_yes'}, u'добивание по «нет» должно было сняться'
    assert db.get_user(1)['poll'] is None


async def test_повторное_нажатие_ничего_не_запускает():
    db.remember_user(1, 'tester', u'Тестер')
    db.set_poll(1, 'day1')
    await poll.on_answer(FakeCall('poll:day1:yes'))

    было = len(db.user_jobs(1))
    call = FakeCall('poll:day1:no')
    await poll.on_answer(call)

    assert len(db.user_jobs(1)) == было
    assert u'Этот вопрос уже закрыт' in call.answers


# ---------------------------------------------------------------- покупка

async def test_покупка_падает_в_чат_покупок():
    db.remember_user(1, 'tester', u'Тестер')
    bot = FakeBot()
    call = FakeCall('buy:gym', bot=bot)
    await purchase.on_buy(call)

    в_чат = [body for kind, chat, body in bot.sent if chat == BUY_CHAT]
    assert в_чат, u'менеджер должен увидеть заявку'
    assert texts.OFFER_GYM in в_чат[0] and 'tester' in в_чат[0]
    assert db.stats()['purchases'] == 1


async def test_заявка_остаётся_в_базе_даже_если_чат_недоступен():
    u"""Чат покупок могли не создать — заявку всё равно нельзя терять."""
    db.remember_user(1, 'tester', u'Тестер')
    bot = FakeBot()
    call = FakeCall('buy:course', bot=bot)

    async def падает(chat_id, text, **kw):
        raise RuntimeError(u'чат не найден')
    bot.send_message = падает

    await purchase.on_buy(call)
    assert db.stats()['purchases'] == 1


# ---------------------------------------------------------- служба заботы

async def test_без_чата_поддержки_человек_получает_кнопку_заботы(monkeypatch):
    u"""Заказчик дал живой аккаунт заботы: ботом туда не напишешь."""
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    monkeypatch.setattr(config, 'CARE_CONTACT', 'Metod_Finish_Official')

    вопрос = FakeMessage(text=u'Когда второй день?')
    await support.to_support(вопрос)

    assert texts.CARE_SENT in вопрос.answers
    кнопка = вопрос.markups[0].inline_keyboard[0][0]
    assert кнопка.url == 'https://t.me/Metod_Finish_Official'


async def test_кнопка_заботы_ведёт_на_аккаунт(monkeypatch):
    monkeypatch.setattr(config, 'CARE_CONTACT', 'Metod_Finish_Official')
    message = FakeMessage(text=texts.CARE_BUTTON)
    await support.on_care_button(message)

    assert texts.CARE_PROMPT in message.answers
    assert message.markups[0].inline_keyboard[0][0].url.endswith('Metod_Finish_Official')


async def test_вопрос_уходит_в_чат_заботы_и_возвращается_ответом():
    bot = FakeBot()
    вопрос = FakeMessage(text=u'Когда будет второй день?', bot=bot)
    await support.to_support(вопрос)

    в_заботу = [chat for _, chat, _ in bot.sent if chat == CARE_CHAT]
    assert len(в_заботу) == 2, u'шапка с именем и сам вопрос'
    assert texts.CARE_SENT in вопрос.answers

    # менеджер отвечает реплаем на шапку
    шапка_id = 101
    ответ = FakeMessage(text=u'Через два часа', chat_id=CARE_CHAT, bot=bot,
                        user=FakeUser(ADMIN, 'manager', u'Менеджер'),
                        reply_to=FakeMessage(message_id=шапка_id, chat_id=CARE_CHAT))
    await support.from_support(ответ)

    доставлено = [chat for kind, chat, _ in bot.sent if kind == 'copy' and chat == 1]
    assert доставлено, u'ответ менеджера должен вернуться человеку'


async def test_ответ_не_на_то_сообщение_объясняет_ошибку():
    bot = FakeBot()
    ответ = FakeMessage(text=u'ага', chat_id=CARE_CHAT, bot=bot,
                        reply_to=FakeMessage(message_id=999, chat_id=CARE_CHAT))
    await support.from_support(ответ)
    assert ответ.replies and u'реплаем' in ответ.replies[0]


# ----------------------------------------------------------------- админка

def админ_сообщение(**kw):
    kw.setdefault('user', FakeUser(ADMIN, 'boss', u'Админ'))
    return FakeMessage(**kw)


async def test_видео_с_подписью_становится_записью_дня():
    message = админ_сообщение(caption='day2', video=FakeVideo('VIDEO2'))
    await admin.on_video(message)
    assert db.get_content('day2') == ('video', 'VIDEO2')


async def test_чужое_видео_не_принимается():
    message = FakeMessage(caption='day2', video=FakeVideo('ЧУЖОЕ'),
                          user=FakeUser(2, 'stranger', u'Прохожий'))
    await admin.on_video(message)
    assert db.get_content('day2') is None


async def test_видео_без_подписи_не_трогает_базу():
    await admin.on_video(админ_сообщение(video=FakeVideo('БЕЗ_ПОДПИСИ')))
    assert db.get_content('day1') is None


async def test_запись_дня_принимается_после_команды():
    u"""У пересланного видео подписи нет — сначала /day3, потом пересылка."""
    команда = админ_сообщение(text='/day3')
    await admin.on_day_command(команда, SimpleNamespace(command='day3'))

    пересылка = админ_сообщение(video=FakeVideo('FWD'))
    await admin.on_video(пересылка)
    assert db.get_content('day3') == ('video', 'FWD')

    # Ожидание одноразовое: следующее видео без подписи уже не запись дня.
    другое = админ_сообщение(video=FakeVideo('ЛЕВОЕ'))
    await admin.on_video(другое)
    assert db.get_content('day3') == ('video', 'FWD')


async def test_ссылка_на_запись_сохраняется():
    message = админ_сообщение(text='day4 https://kinescope.io/abc')
    await admin.on_link(message)
    assert db.get_content('day4') == ('link', 'https://kinescope.io/abc')


async def test_отзыв_картинкой_сохраняется():
    message = админ_сообщение(caption='review3', photo=[FakePhoto('PIC3')])
    await admin.on_photo(message)
    assert db.get_content('review3') == ('photo', 'PIC3')


async def test_статус_показывает_чего_не_хватает():
    db.put_content('day1', 'video', 'V1')
    message = админ_сообщение(text='/status')
    await admin.on_status(message)

    отчёт = message.answers[0]
    assert u'День 1 — видео' in отчёт
    assert u'День 2 — <b>НЕ ЗАДАН</b>' in отчёт
    assert u'ask_day3' in отчёт, u'про недостающий кружок надо предупреждать'


async def test_статус_предупреждает_что_база_не_на_диске(monkeypatch):
    u"""На Railway без тома база пропадёт при деплое — /status обязан кричать."""
    monkeypatch.setattr(config, 'on_railway', lambda: True)
    monkeypatch.setattr(config, 'db_persistent', lambda: False)
    message = админ_сообщение(text='/status')
    await admin.on_status(message)
    assert u'НЕ НА ДИСКЕ' in message.answers[0]


async def test_статус_видит_день_из_переменной(monkeypatch):
    monkeypatch.setattr(config, 'DAY_ENV', {1: '', 2: 'https://x/2', 3: '', 4: ''})
    message = админ_сообщение(text='/status')
    await admin.on_status(message)
    assert u'День 2 — из переменной DAY2' in message.answers[0]


async def test_загрузка_без_диска_подсказывает_file_id(monkeypatch):
    monkeypatch.setattr(config, 'on_railway', lambda: True)
    monkeypatch.setattr(config, 'db_persistent', lambda: False)
    message = админ_сообщение(caption='day2', video=FakeVideo('VIDEO2'))
    await admin.on_video(message)
    assert 'DAY2' in message.replies[0] and 'VIDEO2' in message.replies[0]


async def test_статус_не_отвечает_чужому():
    message = FakeMessage(text='/status', user=FakeUser(2, 'stranger', u'Прохожий'))
    await admin.on_status(message)
    assert message.answers == []


def test_админский_роутер_не_глотает_чужие_сообщения():
    u"""Иначе видео или фото от человека не дошло бы до службы заботы."""
    свой = FakeMessage(video=FakeVideo('V'), user=FakeUser(ADMIN, 'boss', u'Админ'))
    чужой = FakeMessage(video=FakeVideo('V'), user=FakeUser(2, 'guest', u'Гость'))

    assert admin.from_admin(свой) is True
    assert admin.from_admin(чужой) is False


async def test_тестовый_прогон_сжимает_паузы():
    message = админ_сообщение(text='/test')
    await admin.on_test(message)

    assert db.get_speed(ADMIN) == admin.TEST_SPEED
    assert [j['chain'] for j in db.user_jobs(ADMIN)] == ['launch']
