# -*- coding: utf-8 -*-
u"""Сборка роутеров.

Порядок важен. Админский идёт первым: он ловит видео с подписью day1 и
команды, и только не подошедшее достаётся службе заботы, которая забирает
из лички вообще всё. Поставь заботу выше — записи дней уходили бы в чат
поддержки вместо базы.
"""
from . import admin, poll, purchase, start, support

ROUTERS = (start.router, poll.router, purchase.router, admin.router, support.router)
