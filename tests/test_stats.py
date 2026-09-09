# -*- coding: utf-8 -*-
u"""Статистика источников: как читаются метки и что показывает разбивка.

AleX 09.09.2026 гоняет несколько рассылок в телеграме разом и хочет
понять, какая из них лучше. Значит, метка обязана доживать до сводки
целиком, а сводка — показывать не только «пришло», но и что из этих
людей вышло.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import db, stats                                             # noqa: E402


@pytest.fixture(autouse=True)
def база(tmp_path):
    db.connect(str(tmp_path / 'stats.db'))
    yield


# ------------------------------------------------------------------ метки

def test_метка_рассылки_читается_целиком():
    u"""tg_storis5 — это «Telegram ← storis5», а не «неизвестно»."""
    assert stats.label('tg_storis5') == u'Telegram ← storis5'
    assert stats.label('tg_1') == u'Telegram ← 1'


def test_известные_источники_называются_по_человечески():
    assert stats.label('ig') == u'Instagram'
    assert stats.label('site_fb') == u'Сайт ← Facebook'
    assert stats.label('zayavka') == u'Заявка с сайта'
    assert stats.label('') == stats.DIRECT


def test_чужая_метка_показывается_как_есть():
    u"""Незнакомую метку прячем — и человек не узнает, что ссылка живая."""
    assert stats.label('vebinar') == 'vebinar'


def test_метка_с_подчёркиванием_но_чужим_началом_не_ломается():
    assert stats.label('rассылка_2') == 'rассылка_2'


# ------------------------------------------------------------- разбивка

def _человек(user_id, source, launched=False, ответы=(), покупка=False):
    db.remember_user(user_id, 'u%d' % user_id, u'Человек', source)
    if launched:
        db.mark_launched(user_id)
    for poll in ответы:
        db.save_answer(user_id, poll, 'yes')
    if покупка:
        db.add_purchase(user_id, 'gym')


def test_разбивка_показывает_весь_путь_каждой_ссылки():
    u"""Рассылка может привести толпу зевак и ни одного дошедшего — по
    одному числу «пришло» этого не видно."""
    _человек(1, 'tg_1', launched=True, ответы=('day1', 'day3'), покупка=True)
    _человек(2, 'tg_1', launched=True, ответы=('day1',))
    _человек(3, 'tg_1')
    _человек(4, 'tg_2', launched=True)

    строки = {r['src']: r for r in db.source_funnel(0)}

    первая = строки['tg_1']
    assert первая['people'] == 3
    assert первая['launched'] == 2
    assert первая['active'] == 2
    assert первая['finished'] == 1          # дошёл до опросника третьего дня
    assert первая['buys'] == 1

    вторая = строки['tg_2']
    assert (вторая['people'], вторая['launched'], вторая['finished']) == (1, 1, 0)


def test_разбивка_учитывает_срок():
    _человек(1, 'tg_1')
    db._run('UPDATE users SET started_at=? WHERE user_id=1', (time.time() - 90000,))
    _человек(2, 'tg_2')

    свежие = [r['src'] for r in db.source_funnel(time.time() - 3600)]
    assert свежие == ['tg_2']


def test_в_сводке_видно_и_проценты_и_названия_рассылок():
    _человек(1, 'tg_storis5', launched=True, ответы=('day1', 'day3'))
    _человек(2, 'tg_storis5')

    текст = stats.funnel_lines(0)
    assert u'Telegram ← storis5' in текст
    assert u'пришло 2' in текст
    assert u'запустили 1 (50%)' in текст


def test_пустая_разбивка_говорит_словами():
    assert u'никто не заходил' in stats.funnel_lines(0)


# ----------------------------------------------------------------- кто

def test_список_зашедших_показывает_ник_источник_и_запустил_ли():
    _человек(1, 'tg_1', launched=True)
    _человек(2, 'ig')

    текст = stats.who_report()
    assert u'@u1' in текст and u'@u2' in текст
    assert u'Telegram ← 1' in текст and u'Instagram' in текст
    assert u'не запустил' in текст


def test_список_пуст_когда_никого_нет():
    assert u'никто не заходил' in stats.who_report()


def test_готовые_ссылки_содержат_имя_бота_и_метки():
    текст = stats.links_report('finish_marafon_bot')
    assert 'https://t.me/finish_marafon_bot?start=tg_1' in текст
    assert 'start=ig' in текст
