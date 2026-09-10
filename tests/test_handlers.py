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

async def test_старт_сразу_запускает_воронку_и_даёт_кнопку_заботы():
    u"""Заказчик 03.09.2026: «старт нажал — и пошло-поехало», без «Запустить».
    Служба заботы — под полем ввода с первого же сообщения."""
    message = FakeMessage(text='/start')
    await start.on_start(message)

    assert db.get_user(1)['launched_at'] is not None
    assert [j['chain'] for j in db.user_jobs(1)] == ['launch']
    assert message.answers == [texts.START_TEXT]
    кнопки = [b.text for row in message.markups[0].keyboard for b in row]
    assert кнопки == [texts.CARE_BUTTON]


async def test_повторный_старт_не_удваивает_воронку():
    await start.on_start(FakeMessage(text='/start'))
    второй = FakeMessage(text='/start')
    await start.on_start(второй)

    assert len(db.user_jobs(1)) == 1
    assert второй.answers == [texts.ALREADY_RUNNING]


async def test_повторный_старт_предлагает_пройти_заново():
    u"""AleX 09.09.2026: удалил переписку, нажал старт — «уже запущен» и тишина.
    Теперь рядом кнопка, которой человек возвращает себе марафон."""
    await start.on_start(FakeMessage(text='/start'))
    второй = FakeMessage(text='/start')
    await start.on_start(второй)

    кнопки = [b.text for row in второй.markups[0].inline_keyboard for b in row]
    assert кнопки == [texts.RESTART_BUTTON]


async def test_кнопка_заново_запускает_воронку_с_первого_дня():
    await start.on_start(FakeMessage(text='/start'))
    db.save_answer(1, 'day1', 'yes')
    scheduler.schedule(1, 'day2', 0, 60)

    call = FakeCall('restart')
    await start.on_restart(call)

    assert [j['chain'] for j in db.user_jobs(1)] == ['launch']
    assert db.get_user(1)['launched_at'] is not None
    assert call.message.answers == [texts.RESTART_DONE]
    assert call.markup_cleared                      # второй раз не нажать


# ------------------------------------------------ кому открыта статистика

async def test_чужой_stats_не_уходит_в_заботу_а_объясняет():
    u"""Sharp 10.09.2026: заказчик набрал /stats, а бот принял это за вопрос."""
    message = FakeMessage(text='/stats', user=FakeUser(555))
    assert not admin.from_admin(message)

    await support.on_stray_command(message)
    assert u'555' in message.answers[0]
    assert not [s for s in message.bot.sent if s[1] == CARE_CHAT]


async def test_stats_ids_открывает_статистику_но_не_загрузку(monkeypatch):
    monkeypatch.setattr(config, 'STATS_IDS', (555,))
    stats_msg = FakeMessage(text='/stats', user=FakeUser(555))
    assert admin.from_admin(stats_msg)
    await admin.on_stats(stats_msg)
    assert u'Откуда приходят' in stats_msg.answers[0]

    # видео с подписью day1 от такого человека в базу не попадает
    video = FakeMessage(caption='day1', user=FakeUser(555), video=FakeVideo('f1'))
    assert not admin.from_admin(video)


async def test_status_показывает_кого_бот_видит_в_доступе(monkeypatch):
    u"""Sharp 10.09.2026: добавил AleX в переменные — а бот его не видел.
    /status обязан показать списки такими, какими их прочитал бот."""
    monkeypatch.setattr(config, 'STATS_IDS', (350631550,))
    monkeypatch.setattr(config, 'IDS_SKIPPED', ['@FinancialFlow23'])
    message = FakeMessage(text='/status', user=FakeUser(ADMIN))
    await admin.on_status(message)

    текст = message.answers[0]
    assert u'админы: %d' % ADMIN in текст
    assert u'(STATS_IDS): 350631550' in текст
    assert u'@FinancialFlow23' in текст


async def test_из_командного_чата_статистика_доступна_всем_участникам():
    message = FakeMessage(text='/stats@finish_marafon_bot', user=FakeUser(555), chat_id=CARE_CHAT)
    assert admin.from_admin(message)


# ------------------------------------------------- заявка с сайта в боте

async def test_заявка_с_сайта_не_запускает_марафон_а_зовёт_менеджера():
    u"""Павел 09.09.2026: люди не пишут сами, менеджер пишет первым — и его
    аккаунт ограничивают. Ссылка из заявки ведёт в бота: «Запустить» и есть
    первое сообщение человека. Марафон при этом не начинается — человек
    оставил заявку на спортзал и ждёт менеджера."""
    message = FakeMessage(text='/start zayavka57')
    await start.on_start(message)

    assert db.get_user(1)['launched_at'] is None
    assert db.user_jobs(1) == []
    assert u'№57' in message.answers[0]

    ушло = [s for s in message.bot.sent if s[1] == CARE_CHAT]
    assert len(ушло) == 1
    assert u'№57' in ушло[0][2] and u'@tester' in ушло[0][2]


async def test_ответ_менеджера_на_заявку_возвращается_человеку():
    u"""Мост тот же, что у службы заботы: реплай в чате — ответ в бота."""
    message = FakeMessage(text='/start zayavka57')
    await start.on_start(message)

    голова = message.bot.by_id[max(message.bot.by_id)]
    assert db.care_target(CARE_CHAT, голова.message_id) == 1


async def test_источник_заявки_не_дробится_по_номерам():
    u"""Иначе в сводке было бы полсотни строк zayavka1, zayavka2…"""
    await start.on_start(FakeMessage(text='/start zayavka57'))
    await start.on_start(FakeMessage(text='/start zayavka58', user=FakeUser(2)))

    assert db.get_user(1)['source'] == 'zayavka'
    assert db.new_users(0) == [('zayavka', 2)]


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


async def test_загрузка_записи_дублируется_в_закреп_у_админа():
    message = админ_сообщение(caption='day2', video=FakeVideo('VIDEO2'))
    await admin.on_video(message)
    assert 'day2 video VIDEO2' in message.bot.pinned[ADMIN].text


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


# ------------------------------------------------------- дослать запись дня

async def test_дослать_день_получают_те_кому_он_ушёл_без_записи():
    from bot import delivery
    bot = FakeBot()
    for uid in (1, 2):
        db.remember_user(uid, 'u%d' % uid, u'Человек')
        db.mark_launched(uid)
    # Первому день ушёл пустым — записи ещё не было. Второму — с записью.
    await delivery.send_day(bot, 1, 1, admins_alert=False)
    db.put_content('day1', 'link', 'https://example.com/day1')
    await delivery.send_day(bot, 2, 1)
    bot.sent.clear()

    message = админ_сообщение(text='/resend 1', bot=bot)
    await admin.on_resend(message)

    получатели = [chat for kind, chat, _ in bot.sent if kind == 'text' and chat in (1, 2)]
    assert получатели == [1]
    assert 'https://example.com/day1' in [p for k, c, p in bot.sent if c == 1][0]
    assert u'1 чел' in message.answers[-1]
    # Пометка снята: повторный /resend никого не найдёт и не задвоит.
    assert db.missed_users(1) == []


async def test_дослать_всем_идёт_по_очереди_и_не_трогает_тех_кто_день_не_получил():
    bot = FakeBot()
    db.put_content('day1', 'link', 'https://example.com/day1')
    for uid in (1, 2, 3):
        db.remember_user(uid, 'u%d' % uid, u'Человек')
        db.mark_launched(uid)
    db.add_job(1, 'after_day1', 0, 0)      # первый день уже ушёл, ждёт опросник
    db.add_job(2, 'launch', 5, 0)          # ещё на отзывах — первого дня не было
    # у третьего очередь пуста — воронка пройдена

    message = админ_сообщение(text=u'/resend 1 всем', bot=bot)
    await admin.on_resend(message)

    получатели = sorted(chat for kind, chat, _ in bot.sent if kind == 'text' and chat in (1, 2, 3))
    assert получатели == [1, 3]


async def test_дослать_без_записи_отказывает():
    message = админ_сообщение(text='/resend 2')
    await admin.on_resend(message)
    assert u'не задана' in message.answers[-1]


def test_доставка_дня_по_очереди():
    from bot import funnel
    assert funnel.day_delivered(set(), 1)
    assert funnel.day_delivered({'after_day1'}, 1)
    assert not funnel.day_delivered({'launch'}, 1)
    assert not funnel.day_delivered({'day1_no'}, 2)        # ждёт добивание — второго дня не было
    assert funnel.day_delivered({'after_day2'}, 2)
    assert not funnel.day_delivered({'after_day2'}, 3)


# ------------------------------------------------------------- источники

async def test_старт_по_ссылке_запоминает_источник():
    u"""t.me/бот?start=ig приходит как «/start ig» — откуда человек, знаем."""
    await start.on_start(FakeMessage(text='/start ig'))
    assert db.get_user(1)['source'] == 'ig'


async def test_повторный_старт_источник_не_переписывает():
    await start.on_start(FakeMessage(text='/start site_facebook'))
    await start.on_start(FakeMessage(text='/start tg'))
    assert db.get_user(1)['source'] == 'site_facebook'


async def test_старт_без_хвоста_и_с_мусором_даёт_пустой_источник():
    await start.on_start(FakeMessage(text='/start'))
    assert db.get_user(1)['source'] == ''
    await start.on_start(FakeMessage(text=u'/start <script>alert(1)</script>', user=FakeUser(2)))
    assert db.get_user(2)['source'] == ''


async def test_stats_показывает_источники():
    from bot import stats
    for uid, src in ((1, 'ig'), (2, 'ig'), (3, 'site_fb'), (4, '')):
        db.remember_user(uid, 'u%d' % uid, u'Человек', src)
        db.mark_launched(uid)
    message = админ_сообщение(text='/stats')
    await admin.on_stats(message)
    text = message.answers[-1]
    assert u'Instagram 2' in text and u'Сайт ← Facebook 1' in text and u'напрямую 1' in text
    assert u'Всё время:</b> 4 человек, запустили 4' in text

    assert stats.hourly() is not None and u'+4' in stats.hourly()
    assert stats.parse_source('Instagram') == 'ig'
    assert stats.label('site_instagram') == u'Сайт ← Instagram'
