# -*- coding: utf-8 -*-
u"""Подробная статистика: докуда дошёл человек, разделы, выгрузка.

Sharp 11.09.2026: «статистика в боте маленькая, сильно расширь». Каждая
цифра выводится из известного входа — проверяем, что ровно так, как
обещает раздел, и что ни один раздел не выходит за предел сообщения.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, funnel, insights, scheduler                # noqa: E402
from tests.fakes import FakeBot                                        # noqa: E402

ADMIN = 777
NOW = time.time()


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'insights.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (ADMIN,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    yield


def _человек(uid, source='', launched=True, name=u'Человек', ago=3600):
    db.remember_user(uid, 'u%d' % uid, name, source)
    db._run('UPDATE users SET started_at=? WHERE user_id=?', (NOW - ago, uid))
    if launched:
        db.mark_launched(uid)


# ------------------------------------------------------------ путь

def test_докуда_дошёл_по_шагам_ответам_очереди_и_покупкам():
    _человек(1); db.log_event(1, 'day', 3)                     # записанный шаг
    _человек(2); db.add_job(2, 'after_day1', 0, NOW + 60)     # очередь: день 1 ушёл
    _человек(3); db.set_poll(3, 'day2')                        # ждёт ответа про день 2
    _человек(4); db.save_answer(4, 'day2', 'no')               # ответил про день 2 — день 3 ушёл
    _человек(5); db.add_purchase(5, 'gym')
    _человек(6, launched=False)

    люди = insights.model()['people']
    дошли = {uid: insights.FUNNEL[p['reached']][0] for uid, p in люди.items()}
    assert дошли == {1: 'day3', 2: 'day1', 3: 'day2', 4: 'day3', 5: 'bought', 6: 'came'}


def test_команда_не_считается_и_воронка_только_убывает():
    _человек(ADMIN)
    for uid in (10, 11, 12):
        _человек(uid)
    db.log_event(10, 'day', 4)

    m = insights.model()
    assert ADMIN not in m['people'] and m['team'] == 1
    c = insights.reach_counts(list(m['people'].values()))
    assert c[0] == 3
    assert c == sorted(c, reverse=True)


def test_где_люди_сейчас():
    _человек(30); db.add_job(30, 'launch', 3, NOW + 10)
    _человек(31); db.set_poll(31, 'day1'); db.add_job(31, 'day1_no', 0, NOW + 43200)
    _человек(32); db.add_job(32, 'day1_yes', 1, NOW + 5)
    _человек(33); db.mark_blocked(33)
    _человек(34, launched=False)

    места = {uid: p['position'] for uid, p in insights.model()['people'].items()}
    assert места == {30: 'launch', 31: 'a1', 32: 'd2', 33: 'blocked', 34: 'not_launched'}
    assert u'заблокировали бота — <b>1</b>' in insights.render('now', '7', now=NOW)


def test_написал_боту_после_закрытия_значит_вернулся():
    _человек(35)
    db.mark_blocked(35)
    db.remember_user(35, 'u35', u'Человек')
    assert db.get_user(35)['blocked_at'] is None


# ---------------------------------------------------------- разделы

def test_вопросы_да_нет_молчат():
    for uid, answer in ((20, 'yes'), (21, 'no'), (22, None)):
        _человек(uid)
        db.log_event(uid, 'day', 1)
        db.log_event(uid, 'poll', 'day1')
        if answer:
            db.save_answer(uid, 'day1', answer)
    text = insights.render('days', '7', now=NOW)
    assert u'да 1' in text and u'нет 1' in text and u'молчат 1' in text


def test_каждый_раздел_влезает_в_сообщение_и_экранирует_имена():
    for uid in range(100, 160):
        _человек(uid, source='tg_r%d' % (uid % 20), name=u'<b>злой</b>')
        db.add_purchase(uid, 'gym')
    for section in insights.SECTIONS:
        for period in insights.PERIODS:
            text = insights.render(section, period, now=NOW)
            assert len(text) <= 4096, (section, period, len(text))
            assert u'<b>злой</b>' not in text
            assert text.count(u'<pre>') == text.count(u'</pre>')


def test_пустая_база_не_ломает_ни_один_раздел():
    for section in insights.SECTIONS:
        assert insights.render(section, 'all', now=NOW)


def test_неизвестный_раздел_и_период_дают_сводку_за_неделю():
    text = insights.render('ерунда', 'год', now=NOW)
    assert u'сводка' in text and u'за 7 дней' in text


def test_источники_считают_каждую_ссылку_отдельно():
    for uid in (40, 41, 42):
        _человек(uid, source='tg_storis5')
    db.log_event(40, 'day', 4)
    _человек(43, source='ig')
    text = insights.render('src', '7', now=NOW)
    assert u'Telegram ← storis5</b> — пришли 3' in text
    assert u'Instagram</b> — пришли 1' in text


# ---------------------------------------------------------- выгрузка

def test_выгрузка_таблицей():
    _человек(50, source='ig', name=u'Анна')
    db.save_answer(50, 'day1', 'yes')
    db.add_purchase(50, 'course')
    data = insights.csv_bytes()
    assert data.startswith(b'\xef\xbb\xbf')                     # Excel поймёт кириллицу
    строки = [s for s in data.decode('utf-8-sig').splitlines() if s]
    assert строки[0].startswith(u'id;ник;имя')
    assert u';u50;' in строки[1] and u'Instagram' in строки[1]
    assert u'обучение' in строки[1] and u';да;' in строки[1]


def test_имя_как_формула_в_таблице_не_выполняется():
    u"""Ревью 11.09.2026: имя «=HYPERLINK(…)» Excel выполнил бы как формулу."""
    import csv
    import io
    опасные = (u'=HYPERLINK("http://x")', u'+7 900', u'-1', u'@all')
    for uid, name in zip((51, 52, 53, 54), опасные):
        _человек(uid, name=name)
    строки = list(csv.reader(io.StringIO(insights.csv_bytes().decode('utf-8-sig')), delimiter=';'))
    имена = {строка[2] for строка in строки[1:]}
    assert имена == {u"'" + name for name in опасные}
    for строка in строки[1:]:
        for ячейка in строка:
            assert not ячейка.startswith(insights.FORMULA_START), ячейка


# ------------------------------------------------ учёт в планировщике

async def test_планировщик_записывает_день_и_закрытие_бота():
    _человек(60)
    pos = len(funnel.CHAINS['launch'].steps) - 1             # запись 1-го дня
    job = {'id': 1, 'user_id': 60, 'chain': 'launch', 'pos': pos, 'run_at': NOW, 'tries': 0}
    await scheduler.run_job(FakeBot(), job)
    assert insights.model()['people'][60]['days'] == {1}

    _человек(61)
    await scheduler.run_job(FakeBot(forbidden=True), dict(job, id=2, user_id=61))
    assert db.get_user(61)['blocked_at'] is not None


def test_заявки_с_сайта_не_тянут_воронку_марафона_вниз():
    u"""Со ссылки из заявки марафон не запускается — это не отвал, а другой путь."""
    for uid in (70, 71):
        _человек(uid)                                          # марафон запустили
    _человек(72, source='zayavka', launched=False)             # ждёт менеджера

    m = insights.model()
    c = insights.reach_counts(insights.cohort(m, 0, NOW + 1))
    assert c[0] == 2 and c[1] == 2                             # 100% запустили
    assert len(insights.cohort(m, 0, NOW + 1, leads=True)) == 1
    assert m['people'][72]['position'] == 'lead'

    сводка = insights.render('sum', '7', now=NOW)
    assert u'Запустили марафон: <b>2</b> — 100%' in сводка
    assert u'Пришли по заявке с сайта, марафон не получали: 1' in сводка


# ------------------------------------------------- находки ревью 11.09

def test_ответ_на_вопрос_значит_следующий_день_ушёл():
    _человек(80); db.save_answer(80, 'day1', 'yes')                    # ветки нет — день 2 ушёл
    _человек(81); db.save_answer(81, 'day1', 'yes'); db.add_job(81, 'day1_yes', 1, NOW + 5)
    _человек(82); db.save_answer(82, 'day3', 'no')                     # прошёл до конца
    дошли = {u: insights.FUNNEL[p['reached']][0] for u, p in insights.model()['people'].items()}
    assert дошли == {80: 'day2', 81: 'day1', 82: 'offer'}


def test_идущие_по_расписанию_не_считаются_потерянными():
    _человек(83); db.log_event(83, 'day', 1); db.add_job(83, 'after_day1', 0, NOW + 60)
    _человек(84); db.log_event(84, 'day', 1)                           # остановился
    _человек(85); db.log_event(85, 'day', 2)
    group = list(insights.model()['people'].values())
    assert insights.stopped_counts(group)[2] == 1                      # только 84
    текст = insights.render('fun', '7', now=NOW)
    assert u'Ещё в пути по расписанию: 1' in текст


def test_устаревший_вопрос_не_держит_в_неответивших():
    _человек(86); db.set_poll(86, 'day3'); db.log_event(86, 'day', 4)  # ветки «нет» уже нет
    assert insights.model()['people'][86]['position'] != 'a3'


def test_заново_стирает_шаги_прошлого_прогона():
    _человек(87); db.log_event(87, 'poll', 'day1')
    db.reset_funnel(87)
    assert insights.model()['people'][87]['polls'] == set()


def test_при_старте_закрываются_вопросы_без_ветки_нет():
    _человек(88); db.set_poll(88, 'day1'); db.add_job(88, 'day1_no', 0, NOW + 43200)
    _человек(89); db.set_poll(89, 'day2')                               # устарел
    assert db.clear_stale_polls() == 1
    assert db.get_user(88)['poll'] == 'day1' and db.get_user(89)['poll'] is None


async def test_ветка_нет_по_таймеру_закрывает_вопрос_и_старая_кнопка_молчит():
    u"""Баг в боте, найден ревью: без ответа через 12 ч бот вёл дальше, а
    старая кнопка под вопросом потом запускала ветку второй раз."""
    from bot.handlers import poll
    from tests.fakes import FakeCall
    _человек(90); db.set_poll(90, 'day1')
    job = {'id': 5, 'user_id': 90, 'chain': 'day1_no', 'pos': 0, 'run_at': NOW, 'tries': 0}
    await scheduler.run_job(FakeBot(), job)
    assert db.get_user(90)['poll'] is None

    call = FakeCall('poll:day1:yes', user=__import__('tests.fakes', fromlist=['FakeUser']).FakeUser(90))
    await poll.on_answer(call)
    assert u'Этот вопрос уже закрыт' in call.answers
    assert 'day1_yes' not in {j['chain'] for j in db.user_jobs(90)}


async def test_сбой_записи_статистики_не_останавливает_марафон(monkeypatch):
    _человек(91)
    def ломается(*args):
        raise RuntimeError(u'диск полон')
    monkeypatch.setattr(db, 'log_event', ломается)
    pos = len(funnel.CHAINS['launch'].steps) - 1
    await scheduler.run_job(FakeBot(), {'id': 6, 'user_id': 91, 'chain': 'launch',
                                        'pos': pos, 'run_at': NOW, 'tries': 0})
    assert 'after_day1' in {j['chain'] for j in db.user_jobs(91)}


async def test_дошедшее_сообщение_снимает_отметку_закрыл_бота():
    _человек(92); db.mark_blocked(92)
    pos = len(funnel.CHAINS['launch'].steps) - 1
    await scheduler.run_job(FakeBot(), {'id': 7, 'user_id': 92, 'chain': 'launch',
                                        'pos': pos, 'run_at': NOW, 'tries': 0})
    assert db.get_user(92)['blocked_at'] is None


async def test_закрыл_бота_на_ветке_нет_вопрос_остаётся_для_возврата():
    u"""Вернётся — кнопка под вопросом продолжит марафон, как и раньше."""
    _человек(93); db.set_poll(93, 'day2')
    job = {'id': 8, 'user_id': 93, 'chain': 'day2_no', 'pos': 0, 'run_at': NOW, 'tries': 0}
    await scheduler.run_job(FakeBot(forbidden=True), job)
    assert db.get_user(93)['poll'] == 'day2' and db.get_user(93)['blocked_at']
    assert db.clear_stale_polls() == 0                                  # закрывших не трогаем


async def test_шаг_ветки_нет_не_ушёл_вопрос_снова_открыт():
    _человек(94); db.set_poll(94, 'day1'); db.add_job(94, 'day1_no', 0, NOW)
    await scheduler.run_job(FakeBot(fail_times=1), db.user_jobs(94)[0])
    assert db.get_user(94)['poll'] == 'day1'                            # повтор через минуту
    assert db.user_jobs(94)[0]['tries'] == 1
