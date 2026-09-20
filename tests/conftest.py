# -*- coding: utf-8 -*-
u"""Общее для проверок маршрутов: один диспетчер на весь прогон.

Роутер aiogram подключается к диспетчеру один раз за жизнь процесса —
второй диспетчер с теми же роутерами падает («already attached»). Поэтому
собираем его здесь, тем же handlers.setup, что и боевой бот.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='session')
def dispatcher():
    from aiogram import Dispatcher

    from bot import handlers

    dp = Dispatcher()
    handlers.setup(dp)
    return dp


@pytest.fixture(autouse=True)
def без_памяти_между_проверками():
    u"""Отметки «админам про этот день уже сказали» живут в памяти процесса.

    В боте это правильно — тревога про незалитую запись не должна уходить
    на каждого из десятков человек. В прогоне тестов один процесс на все
    файлы, и отметка от одной проверки глушила бы тревогу в следующей.
    """
    from bot import delivery

    delivery._missed_told.clear()
    yield
    delivery._missed_told.clear()
