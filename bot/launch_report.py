# -*- coding: utf-8 -*-
u"""Запуск бота — сайту спортзала, а оттуда в рекламу Meta.

Таргетолог 15.09.2026: реклама учится на клике по ссылке на бота (сайт
отдаёт Lead в момент нажатия), а «Запустить» нажимают не все. Учиться надо
на настоящем запуске.

Сайт дописывает в ссылку на бота код своей страницы: ?start=site_fb--k3v9x0a1b2c4.
Здесь код отрезается до разбора источника — статистика видит прежнее
site_fb, — а после первого запуска уходит на сайт. Сайт по коду находит
клик (метку рекламы, адрес, браузер) и отправляет запуск в Meta. Сами мы
о человеке сайту ничего не сообщаем: только код.

Отправка в фоне и не мешает марафону: сайт недоступен — несколько попыток
с паузами, потом запись в журнал и всё.
"""
import asyncio
import json
import logging
import re

import aiohttp

from . import config

log = logging.getLogger(__name__)

TAIL = re.compile(r'^(.*)--([a-z0-9]{12})$')
PAUSES = (0, 10, 60, 300)                  # секунд перед каждой попыткой
TIMEOUT = 20

# Задачи держим ссылкой, иначе сборщик мусора может снять их на полпути.
_pending: set[asyncio.Task] = set()


def split(payload: str) -> tuple[str, str]:
    u"""«site_fb--k3v9x0a1b2c4» → («site_fb», «k3v9x0a1b2c4»); без кода — (payload, '')."""
    found = TAIL.match(payload or '')
    if not found:
        return payload or '', ''
    return found.group(1), found.group(2)


async def _post(url: str, token: str) -> int:
    body = json.dumps({'t': token})
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=body,
                                headers={'Content-Type': 'application/json'}) as resp:
            return resp.status


def _retry(status: int) -> bool:
    return status == 429 or status >= 500


async def send(token: str, post=None, sleep=None) -> bool:
    u"""Сообщить сайту код. True — сайт принял (что дальше с Meta — его забота)."""
    url = config.SITE_BOT_START_URL
    if not url:
        return False
    post = post or _post
    sleep = sleep or asyncio.sleep
    why = ''
    for pause in PAUSES:
        if pause:
            await sleep(pause)
        try:
            status = await post(url, token)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            why = str(err) or err.__class__.__name__
            continue
        if 200 <= status < 300:
            return True
        why = u'ответ %d' % status
        if not _retry(status):
            break
    log.warning(u'запуск бота не передан сайту для Meta (%s): %s', token, why)
    return False


def report(token: str) -> None:
    u"""Отправить в фоне; вызывающий не ждёт и не падает."""
    if not token or not config.SITE_BOT_START_URL:
        return
    task = asyncio.create_task(send(token))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
