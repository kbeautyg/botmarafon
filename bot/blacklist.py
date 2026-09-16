# -*- coding: utf-8 -*-
u"""Чёрный список.

AleX 16.09.2026: «в одно касание в чёрный список, с авто удалением из бота
и из всех каналов и чатов Павла; в статистику — ник, дата добавления и
удаления, с возможностью убрать из чёрного списка».

Что значит «удалить из бота». Удалить переписку или заблокировать человека
у себя бот в Telegram не может — может перестать с ним работать: марафон
снимается, его сообщения и нажатия бот не принимает (Gate ниже), команде
они не приходят, дожим и рассылки его обходят.

Каналы и чаты Павла. Банить бот может только там, где он админ с правом
«Блокировка пользователей». О таких чатах бот узнаёт сам, когда его
назначают админом (handlers/blacklist.py), — заводить список руками не нужно.
"""
import html
import logging
import time
from datetime import datetime

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from . import config, contact, db, stats, texts

log = logging.getLogger(__name__)


def by_line(user) -> str:
    u"""Кто из команды нажал — для истории: имя и ник, без ссылки."""
    name = (getattr(user, 'full_name', None) or u'').strip() or u'id %s' % user.id
    return u'%s (@%s)' % (name, user.username) if getattr(user, 'username', None) else name


def when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts or time.time(), stats.MSK).strftime('%d.%m.%Y %H:%M')


def who(user_id: int) -> str:
    u"""Человек строкой для команды: из базы бота, а нет — из записи списка."""
    known = db.get_user(user_id) or db.ban_entry(user_id) or {}
    return contact.line(user_id, known.get('first_name'), known.get('username'))


async def _each_chat(bot, action, user_id: int) -> str:
    u"""Пройти по чатам Павла, где бот может банить. Итог — строкой для команды."""
    chats = db.ban_chats()
    if not chats:
        return texts.BAN_NO_CHATS
    done, failed = [], []
    for chat in chats:
        title = chat.get('title') or str(chat['chat_id'])
        try:
            await action(chat['chat_id'], user_id)
            done.append(title)
        except Exception as err:
            failed.append(u'«%s» — %s' % (title, err))
            log.warning(u'чёрный список: %s в чате %s не вышло: %s', user_id, chat['chat_id'], err)
    note = u'%d из %d' % (len(done), len(chats))
    if done:
        note += u': ' + u', '.join(u'«%s»' % t for t in done)
    if failed:
        note += u'\nНе вышло: ' + u'; '.join(failed)
    return html.escape(note)


async def ban_everywhere(bot, user_id: int) -> str:
    return await _each_chat(bot, lambda chat, uid: bot.ban_chat_member(chat, uid), user_id)


async def add(bot, user_id: int, by_user) -> tuple[bool, str]:
    u"""Внести в список. (новый ли, отчёт для команды)."""
    if config.is_team(user_id):
        return False, texts.BAN_TEAM
    entry = db.ban_entry(user_id)
    if entry and entry.get('removed_at') is None:
        return False, texts.BAN_ALREADY.format(
            who=who(user_id), when=when(entry['added_at']), by=html.escape(entry.get('added_by') or u'—'),
            chats=html.escape(entry.get('chats_note') or u''))

    known = db.get_user(user_id) or {}
    db.ban_add(user_id, known.get('username'), known.get('first_name'), by_line(by_user))
    # Сразу, до банов в чатах: пока они идут, шаг марафона уже не уйдёт.
    db.stop_funnel(user_id)
    log.info(u'чёрный список: %s внесён, добавил %s', user_id, by_user.id)
    chats = await ban_everywhere(bot, user_id)
    db.ban_note(user_id, u'бан в чатах: ' + html.unescape(chats))
    return True, texts.BAN_DONE.format(who=who(user_id), by=html.escape(by_line(by_user)),
                                       when=when(None), chats=chats)


async def remove(bot, user_id: int, by_user) -> tuple[bool, str]:
    u"""Убрать из списка и снять баны в чатах. (был ли в списке, отчёт)."""
    if not db.ban_remove(user_id, by_line(by_user)):
        return False, texts.UNBAN_NOT.format(who=who(user_id))
    log.info(u'чёрный список: %s убран, убрал %s', user_id, by_user.id)
    chats = await _each_chat(
        bot, lambda chat, uid: bot.unban_chat_member(chat, uid, only_if_banned=True), user_id)
    return True, texts.UNBAN_DONE.format(who=who(user_id), by=html.escape(by_line(by_user)),
                                         when=when(None), chats=chats)


class Gate(BaseMiddleware):
    u"""Сообщения и нажатия от людей из чёрного списка дальше не идут.

    Стоит снаружи всех роутеров: /start, служба заботы, опросы и покупки —
    каждому обработчику не нужно помнить про список. Команду не трогает,
    даже если её id когда-то попал в список.
    """

    async def __call__(self, handler, event, data):
        user = data.get('event_from_user') or getattr(event, 'from_user', None)
        if user is not None and not config.is_team(user.id) and db.is_banned(user.id):
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()           # чтобы кнопка у него не крутилась
                except Exception:
                    pass
            log.info(u'чёрный список: от %s пропущено', user.id)
            return None
        return await handler(event, data)
