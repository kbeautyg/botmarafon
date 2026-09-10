# -*- coding: utf-8 -*-
u"""Разбор списков id из переменных окружения.

Sharp 10.09.2026 добавил AleX в переменные Railway, а доступа не появилось.
Список обязан пониматься в любом разумном виде и не ронять бота, если в
нём мусор.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config                                                 # noqa: E402


def test_запятая_пробел_точка_с_запятой_и_перевод_строки():
    assert config._ids('312701042, 350631550') == (312701042, 350631550)
    assert config._ids('312701042,350631550') == (312701042, 350631550)
    assert config._ids('312701042;350631550') == (312701042, 350631550)
    assert config._ids('312701042 350631550') == (312701042, 350631550)
    assert config._ids('312701042\n350631550') == (312701042, 350631550)


def test_кавычки_вокруг_значения_не_мешают():
    assert config._ids('"312701042,350631550"') == (312701042, 350631550)
    assert config._ids("'350631550'") == (350631550,)


def test_мусор_пропускается_и_запоминается_а_бот_не_падает():
    config.IDS_SKIPPED.clear()
    assert config._ids('350631550, @FinancialFlow23, AleX') == (350631550,)
    assert config.IDS_SKIPPED == ['@FinancialFlow23', 'AleX']
    config.IDS_SKIPPED.clear()


def test_пусто_и_повторы():
    assert config._ids('') == ()
    assert config._ids(None) == ()
    assert config._ids('350631550, 350631550') == (350631550,)
