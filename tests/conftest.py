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
