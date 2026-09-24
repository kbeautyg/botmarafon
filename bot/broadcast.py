# -*- coding: utf-8 -*-
u"""Рассылка всем: «уведомить всех» одним сообщением.

AleX 18.09.2026: «одной кнопкой написать абсолютно всем пользователям одно
сообщение, + добавление медиа в это сообщение: фото либо видео, а также
рабочие ссылки, к примеру как на новый подкаст».

Сообщение не пересобираем, а копируем то самое, что прислал человек из
команды: так уходит всё разом — текст, фото, видео, кружок, подпись,
жирный шрифт и рабочие ссылки, — и ничего не теряется по дороге.

Каждому получателю рассылка ложится в его переписку в пульте, пометкой
«рассылка» (AleX 24.09.2026: рассылки ушли, а в пульте у всех последним
висело сообщение из самого пульта — казалось, что не дошло). Прошлые
рассылки, до этого не записанные, дописывает backfill при запуске.

Рассылка идёт в базе, а не в памяти. Тысяча человек — это минута работы, и
за эту минуту бот может перезапуститься (деплой, сбой связи). Поэтому
помним, докуда дошли: после перезапуска рассылка продолжается сама, а не
начинается заново — второй раз одно и то же людям не приходит.
"""
import asyncio
import logging
import time

from . import chatlog, config, db, delivery, texts

log = logging.getLogger(__name__)

# Телеграм принимает от бота около тридцати сообщений в секунду на всех.
# Двадцать — с запасом: рассылка не должна мешать самой воронке.
PER_SECOND = 20
PAUSE = 1.0 / PER_SECOND
# Как часто докладывать о ходе тому, кто запустил рассылку.
REPORT_EVERY = 200


async def _copy(bot, user_id: int, chat_id: int, message_id: int) -> tuple:
    u"""Копия сообщения одному человеку: (итог, номер сообщения у него)."""
    try:
        sent = await delivery._guard(bot.copy_message(user_id, chat_id, message_id))
    except delivery.Gone:
        db.mark_blocked(user_id)
        return 'gone', None
    except Exception as err:
        log.warning(u'рассылка %s: %s не ушло: %s', user_id, chat_id, err)
        return 'fail', None
    return 'ok', getattr(sent, 'message_id', None)


async def send_one(bot, user_id: int, chat_id: int, message_id: int) -> str:
    u"""Копия сообщения одному человеку. 'ok' | 'gone' | 'fail'."""
    return (await _copy(bot, user_id, chat_id, message_id))[0]


def _remember(task: dict, user_id: int, tg_id) -> None:
    u"""Рассылку — в переписку получателя. Сбой записи рассылку не роняет."""
    try:
        db.save_message(user_id, 'out', task.get('kind') or 'text', task.get('text'),
                        task.get('file_id'), task['author'], tg_id, mass=True)
    except Exception as err:
        log.warning(u'рассылка %s: в переписку %s не записали: %s', task['id'], user_id, err)


async def content(bot, task: dict) -> tuple:
    u"""Что в рассылке, если при заведении это не записали (до 24.09.2026).

    Прочитать сообщение по номеру Bot API не даёт, а переслать — даёт, и
    пересланное приходит целиком. Пересылаем автору рассылки без звука и
    тут же удаляем. Не вышло — пишем в переписку честную заглушку.
    """
    try:
        copy = await bot.forward_message(task['author'], task['chat_id'], task['message_id'],
                                         disable_notification=True)
    except Exception as err:
        log.warning(u'рассылка %s: текст не прочитали: %s', task['id'], err)
        return 'text', texts.BROADCAST_LOST, None
    try:
        await bot.delete_message(task['author'], copy.message_id)
    except Exception as err:
        log.warning(u'рассылка %s: служебную пересылку не удалили: %s', task['id'], err)
    return chatlog.parts(copy)


async def run(bot, broadcast_id: int) -> dict:
    u"""Разослать. Возвращает итог: кому ушло, кто закрыл бота, где сбой."""
    task = db.broadcast(broadcast_id)
    if not task or task['status'] not in ('ready', 'going'):
        return {}
    db.broadcast_status(broadcast_id, 'going')
    if not task.get('kind'):
        # заведена до 24.09.2026 и продолжается после перезапуска
        db.broadcast_content(broadcast_id, *(await content(bot, task)))
        db.broadcast_logged(broadcast_id)
        task = db.broadcast(broadcast_id)

    picked = db.broadcast_picked(task)
    while True:
        people = db.broadcast_targets(task['cursor'], 200, picked)
        if not people:
            break
        for user_id in people:
            result, tg_id = await _copy(bot, user_id, task['chat_id'], task['message_id'])
            if result == 'ok':
                _remember(task, user_id, tg_id)
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


# Расхождение восстановленного списка со счётом рассылки, при котором
# прошлую рассылку в переписку не дописываем: лучше не показать, чем
# показать человеку сообщение, которого он, может быть, не получал.
GUESS_SLACK = 0.02


async def backfill(bot) -> int:
    u"""Дописать в переписку рассылки, прошедшие до 24.09.2026. Сколько дописали.

    Поимённо тогда не записывали — получателей восстанавливает
    db.broadcast_recipients_guess. Совпал их счёт со счётом рассылки —
    дописываем; нет — пропускаем и пишем в журнал.
    """
    done = 0
    for task in db.broadcasts_unlogged():
        people = db.broadcast_recipients_guess(task)
        if abs(len(people) - task['sent']) > max(2, task['sent'] * GUESS_SLACK):
            log.warning(u'рассылка %s: восстановили %d получателей, а ушло %d — '
                        u'в переписку не дописываем', task['id'], len(people), task['sent'])
            db.broadcast_logged(task['id'])
            continue
        kind, text, file_id = ((task['kind'], task['text'], task['file_id'])
                               if task.get('kind') else await content(bot, task))
        db.broadcast_content(task['id'], kind, text, file_id)
        db.save_mass([(uid, kind, text, file_id, task['author'], task['at'] + i * PAUSE)
                      for i, uid in enumerate(people)])
        db.broadcast_logged(task['id'])
        log.info(u'рассылка %s дописана в переписку: %d чел.', task['id'], len(people))
        done += 1
    return done


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
    picked = db.broadcast_picked(task)
    left = db.broadcast_left(task['cursor'], picked)
    minutes = max(1, int(left * PAUSE / 60 + 0.5))
    text = texts.BROADCAST_ASK if not picked else texts.BROADCAST_ASK_PICKED
    return text.format(count=left, minutes=minutes, when=time.strftime('%H:%M'))
