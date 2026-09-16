# -*- coding: utf-8 -*-
u"""Сборка роутеров.

Порядок важен. Админский идёт первым: он ловит видео с подписью day1 и
команды, и только не подошедшее достаётся службе заботы, которая забирает
из лички вообще всё. Поставь заботу выше — записи дней уходили бы в чат
поддержки вместо базы.
"""
from . import admin, blacklist, poll, purchase, start, support

ROUTERS = (start.router, poll.router, purchase.router, blacklist.router,
           admin.router, support.router)


def setup(dispatcher) -> None:
    u"""Роутеры и фильтр чёрного списка — одним вызовом: так собирают и
    боевой бот (main.py), и проверки маршрутов в тестах."""
    from .. import blacklist as ban_list

    # Снаружи роутеров: сообщение из чёрного списка не доходит ни до одного
    # обработчика — ни до /start, ни до заботы, ни до кнопок (AleX 16.09.2026).
    dispatcher.message.outer_middleware(ban_list.Gate())
    dispatcher.callback_query.outer_middleware(ban_list.Gate())
    for router in ROUTERS:
        dispatcher.include_router(router)
