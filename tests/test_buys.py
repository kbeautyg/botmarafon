# -*- coding: utf-8 -*-
u"""Кто нажал «купить» — список для тех, кто звонит.

Павел 23.09.2026: «Где блин эти заявки, что нажали купить?». Заявка на
каждое нажатие уходила ему в личку сразу, но тонула среди уведомлений о
каждом входе, а в отчёте стояло одно число — и за ним ни одного имени.

Отдельная ловушка: число в отчёте считает нажатия, а заявки менеджерам по
повторным нажатиям в течение суток не уходят. Отсюда «нажали 4», а в
личке ни одной новой заявки. Поэтому в списке повторы названы.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, daily, db, stats                                 # noqa: E402
from bot.handlers import admin                                           # noqa: E402
from tests.fakes import FakeMessage, FakeUser                            # noqa: E402

SHARP = 777                # единственный админ
ПАВЕЛ = 312701042          # не админ: получает заявки и смотрит отчёты
ЧУЖОЙ = 555

ДЕНЬ = 86400


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'buys.db'))
    monkeypatch.setattr(config, 'ADMIN_IDS', (SHARP,))
    monkeypatch.setattr(config, 'STATS_IDS', (ПАВЕЛ,))
    monkeypatch.setattr(config, 'SUPPORT_CHAT_ID', 0)
    yield


def _нажал(uid, name=u'Наталья', nick=None, product='gym', когда=None):
    u"""Человек в базе и его нажатие «купить» в нужный момент."""
    if not db.get_user(uid):
        db.remember_user(uid, nick, name)
        db.mark_launched(uid)
    number = db.add_purchase(uid, product)
    if когда is not None:
        db._run('UPDATE purchases SET at=? WHERE id=?', (когда, number))
    return number


# ------------------------------------------------------------- список

def test_в_списке_видно_кто_что_и_когда(база):
    _нажал(1, u'Наталья', 'nataliya')
    текст = stats.purchases_report(db.purchases_list(0, None, 10), 7)
    assert u'Наталья' in текст and u'@nataliya' in текст
    assert u'Энерго спортзал' in текст
    assert u'1' in текст                      # id, по нему ищут в пульте


def test_повтор_в_течение_суток_назван_и_не_считается_новой_заявкой(база):
    u"""Иначе «нажали 4» в отчёте и ноль заявок в личке не сходятся."""
    сейчас = time.time()
    _нажал(1, когда=сейчас - 3600)
    _нажал(1, когда=сейчас - 60)              # тот же человек, та же кнопка

    строки = db.purchases_list(0, None, 10)
    текст = stats.purchases_report(строки, 7)
    assert u'повторное нажатие' in текст
    assert u'из них новых заявок 1' in текст


def test_нажатие_через_двое_суток_снова_заявка(база):
    сейчас = time.time()
    _нажал(1, когда=сейчас - 3 * ДЕНЬ)
    _нажал(1, когда=сейчас - 60)
    текст = stats.purchases_report(db.purchases_list(0, None, 10), 7)
    assert u'повторное нажатие' not in текст
    assert u'из них новых заявок 2' in текст


def test_закрывшего_бота_называем_отдельно(база):
    u"""Такому человеку бот не напишет — звонить, а не писать."""
    _нажал(1)
    db.mark_blocked(1)
    текст = stats.purchases_report(db.purchases_list(0, None, 10), 7)
    assert u'закрыл бота' in текст


def test_за_срок_никто_не_нажимал_говорим_прямо(база):
    assert u'никто не нажимал' in stats.purchases_report([], 7)


def test_список_берём_за_нужный_срок(база):
    сейчас = time.time()
    _нажал(1, когда=сейчас - 30 * ДЕНЬ)
    _нажал(2, u'Пётр', когда=сейчас - 60)
    assert len(db.purchases_list(сейчас - 7 * ДЕНЬ, None, 10)) == 1
    assert len(db.purchases_list(сейчас - 60 * ДЕНЬ, None, 10)) == 2


# ------------------------------------------------------------- команда

async def test_команда_заявки_работает_не_только_у_админа(база):
    u"""Павел и AleX не админы. До 23.09.2026 «/заявки» у них молча не
    работала вовсе — команда просто ничего не отвечала."""
    _нажал(1, u'Наталья', 'nataliya')
    сообщение = FakeMessage(text='/заявки', user=FakeUser(ПАВЕЛ))
    await admin.on_buys(сообщение)
    assert сообщение.answers and u'Наталья' in сообщение.answers[0]


async def test_постороннему_список_не_показываем(база):
    _нажал(1)
    сообщение = FakeMessage(text='/заявки', user=FakeUser(ЧУЖОЙ))
    await admin.on_buys(сообщение)
    assert сообщение.answers == []


# ------------------------------------------------------------- отчёт

def test_в_дневном_отчёте_видно_кого_звать(база):
    u"""Одно число Павлу ничего не даёт: ему звонить этим людям."""
    номер = daily.day_number()
    начало, _ = daily.bounds(номер)
    _нажал(1, u'Наталья', 'nataliya', когда=начало + 3600)

    отчёт = daily.report([номер])
    assert u'Нажали «купить»: <b>1</b>' in отчёт
    assert u'Наталья' in отчёт and u'@nataliya' in отчёт


def test_в_отчёте_без_покупок_лишней_строки_нет(база):
    номер = daily.day_number()
    отчёт = daily.report([номер])
    assert u'Нажали «купить»: <b>0</b>' in отчёт
    assert u'•  ·' not in отчёт


def test_за_две_недели_отчёт_не_раздувается(база):
    u"""Календарём можно выбрать сразу много дней: без ограничения отчёт
    перерос бы предел телеграма в 4096 знаков и не ушёл бы вовсе."""
    сегодня = daily.day_number()
    дни = [сегодня - n for n in range(14)]
    for n, номер in enumerate(дни):
        начало, _ = daily.bounds(номер)
        for k in range(3):
            _нажал(100 + n * 10 + k, u'Человек%d' % (n * 10 + k), когда=начало + 60 * k)

    отчёт = daily.report(дни)
    assert u'и ещё' in отчёт and u'/заявки' in отчёт
    assert len(отчёт) < 4096
