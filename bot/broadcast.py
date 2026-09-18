# -*- coding: utf-8 -*-
u"""Рассылка всем: «уведомить всех» одним сообщением.

AleX 18.09.2026: «одной кнопкой написать абсолютно всем пользователям одно
сообщение, + добавление медиа в это сообщение: фото либо видео, а также
рабочие ссылки, к примеру как на новый подкаст».

Сообщение не пересобираем, а копируем то самое, что прислал человек из
команды: так уходит всё разом — текст, фото, видео, кружок, подпись,
жирный шрифт и рабочие ссылки, — и ничего не теряется по дороге.

Рассылка идёт в базе, а не в памяти. Тысяча человек — это минута работы, и
за эту минуту бот может перезапуститься (деплой, сбой связи). Поэтому
помним, докуда дошли: после перезапуска рассылка продолжается сама, а не
начинается заново — второй раз одно и то же людям не приходит.
"""
import asyncio
import logging
import time

from . import config, db, delivery, texts

log = logging.getLogger(__name__)

# Телеграм принимает от бота около тридцати сообщений в секунду на всех.
# Двадцать — с запасом: рассылка не должна мешать самой воронке.
PER_SECOND = 20
PAUSE = 1.0 / PER_SECOND
# Как часто докладывать о ходе тому, кто запустил рассылку.
REPORT_EVERY = 200


async def send_one(bot, user_id: int, chat_id: int, message_id: int) -> str:
    u"""Копия сообщения одному человеку. 'ok' | 'gone' | 'fail'."""
    try:
        await delivery._guard(bot.copy_message(user_id, chat_id, message_id))
    except delivery.Gone:
        db.mark_blocked(user_id)
        return 'gone'
    except Exception as err:
        log.warning(u'рассылка %s: %s не ушло: %s', user_id, chat_id, err)
        return 'fail'
    return 'ok'


async def run(bot, broadcast_id: int) -> dict:
    u"""Разослать. Возвращает итог: кому ушло, кто закрыл бота, где сбой."""
    task = db.broadcast(broadcast_id)
    if not task or task['status'] not in ('ready', 'going'):
        return {}
    db.broadcast_status(broadcast_id, 'going')

    while True:
        people = db.broadcast_targets(task['cursor'], limit=200)
        if not people:
            break
        for user_id in people:
            result = await send_one(bot, user_id, task['chat_id'], task['message_id'])
            task = db.broadcast_step(broadcast_id, user_id, result)
            if task['sent'] and task['sent'] % REPORT_EVERY == 0:
                await _progress(bot, task)
            await asyncio.sleep(PAUSE)

    db.broadcast_status(broadcast_id, 'done')
    task = db.broadcast(broadcast_id)
    await _finish(bot, task)
    return task


async def _progress(bot, task: dict) -> None:
    try:
        await bot.send_message(task['author'], texts.BROADCAST_PROGRESS.format(
            sent=task['sent'], gone=task['gone'], failed=task['failed']))
    except Exception:
        pass                                   # доклад о ходе — не повод падать


async def _finish(bot, task: dict) -> None:
    text = texts.BROADCAST_DONE.format(sent=task['sent'], gone=task['gone'],
                                       failed=task['failed'])
    for chat in dict.fromkeys((task['author'],) + config.purchase_recipients()):
        try:
            await bot.send_message(chat, text)
        except Exception:
            continue
    log.info(u'рассылка %s закончена: ушло %s, закрыли бота %s, сбоев %s',
             task['id'], task['sent'], task['gone'], task['failed'])


async def resume(bot) -> int:
    u"""Продолжить рассылку, прерванную перезапуском. Сколько продолжили."""
    started = 0
    for task in db.broadcasts_going():
        log.info(u'продолжаем рассылку %s с %s', task['id'], task['cursor'])
        asyncio.create_task(run(bot, task['id']))
        started += 1
    return started


def preview(task: dict) -> str:
    u"""Сколько человек получит рассылку и когда она кончится."""
    left = db.broadcast_left(task['cursor'])
    minutes = max(1, int(left * PAUSE / 60 + 0.5))
    return texts.BROADCAST_ASK.format(count=left, minutes=minutes,
                                      when=time.strftime('%H:%M'))
