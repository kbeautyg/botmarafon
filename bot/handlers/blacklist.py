# -*- coding: utf-8 -*-
u"""Чёрный список: кнопка под уведомлением, команды, чаты Павла.

Сама логика — bot/blacklist.py. Здесь только входы в неё:

- «🚫 В чёрный список» под уведомлением о человеке → «Да» → бан;
  под отчётом — «↩️ Убрать из чёрного списка»;
- /чс — список, /чс ID или @ник — внести, /разбан ID — убрать;
- бота назначили админом в канале или чате — запоминаем чат, и чёрный
  список банит там; тех, кто уже в списке, банит сразу.
"""
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from .. import blacklist, config, db, delivery, insights, keyboards, texts
from . import support

log = logging.getLogger(__name__)
router = Router(name='blacklist')

BAN_COMMANDS = ('чс', 'ban', 'blacklist')
UNBAN_COMMANDS = ('разбан', 'unban')


def _team(user_id: int, chat_id: int | None) -> bool:
    u"""Вся команда — в личке и в командных чатах (сводки, поддержки, заявок)."""
    return config.is_team(user_id) or config.can_stats(user_id, chat_id)


def _team_message(message: Message) -> bool:
    return message.from_user is not None and _team(message.from_user.id, message.chat.id)


# ------------------------------------------------------------ кнопки

async def _markup(call: CallbackQuery, markup) -> None:
    try:
        await call.message.edit_reply_markup(reply_markup=markup)
    except Exception as err:                       # та же разметка или сообщение старое
        log.debug(u'кнопки чёрного списка не обновили: %s', err)


@router.callback_query(F.data.startswith('bl:'))
async def on_ban_button(call: CallbackQuery):
    chat_id = call.message.chat.id if call.message else None
    if not _team(call.from_user.id, chat_id):
        await call.answer(u'Нет доступа')
        return
    parts = call.data.split(':')
    if len(parts) != 3 or not parts[2].isdigit():
        await call.answer()
        return
    action, user_id = parts[1], int(parts[2])

    if action == 'ask':
        if db.is_banned(user_id):
            await _markup(call, keyboards.ban_undo(user_id))
            await call.answer(u'Уже в чёрном списке')
            return
        await _markup(call, keyboards.ban_confirm(user_id))
        await call.answer(texts.BAN_CONFIRM_TOAST)
    elif action == 'no':
        await _markup(call, keyboards.ban_ask(user_id, call.message.chat.id))
        await call.answer(u'Отменено')
    elif action == 'yes':
        # Подтвердить нажатие сразу: баны по чатам идут дольше, чем Telegram ждёт ответа.
        await call.answer(u'Вношу в чёрный список…')
        _, report = await blacklist.add(call.bot, user_id, call.from_user)
        await _markup(call, keyboards.ban_undo(user_id) if db.is_banned(user_id)
                      else keyboards.ban_ask(user_id, call.message.chat.id))
        await call.message.reply(report)
    elif action == 'undo':
        await call.answer(u'Убираю из чёрного списка…')
        _, report = await blacklist.remove(call.bot, user_id, call.from_user)
        await _markup(call, keyboards.ban_ask(user_id, call.message.chat.id))
        await call.message.reply(report)
    else:
        await call.answer()


# ------------------------------------------------------------ команды

def _resolve(ref: str) -> int | None:
    u"""ID — как есть, даже если человек не заходил в бота (банить в чатах
    Павла можно и его); ник — только из базы бота."""
    ref = ref.strip()
    if ref.isdigit():
        return int(ref)
    found = support._find(ref)
    return found['user_id'] if found else None


def _list_text() -> str:
    return insights.render('bl', 'all') + u'\n\n' + texts.BAN_USAGE


@router.message(Command(*BAN_COMMANDS), _team_message)
async def on_ban_command(message: Message, command: CommandObject):
    ref = (command.args or u'').split()
    if not ref:
        await message.answer(_list_text())
        return
    user_id = _resolve(ref[0])
    if user_id is None:
        await message.answer(texts.WRITE_NOT_FOUND.format(ref=html.escape(ref[0])))
        return
    await message.answer(u'Вношу в чёрный список…')
    _, report = await blacklist.add(message.bot, user_id, message.from_user)
    await message.answer(report, reply_markup=keyboards.ban_undo(user_id)
                         if db.is_banned(user_id) else None)


@router.message(Command(*UNBAN_COMMANDS), _team_message)
async def on_unban_command(message: Message, command: CommandObject):
    ref = (command.args or u'').split()
    if not ref:
        await message.answer(texts.BAN_USAGE)
        return
    user_id = _resolve(ref[0])
    if user_id is None:
        await message.answer(texts.WRITE_NOT_FOUND.format(ref=html.escape(ref[0])))
        return
    _, report = await blacklist.remove(message.bot, user_id, message.from_user)
    await message.answer(report)


# ------------------------------------------------------------ чаты Павла

async def _ban_listed(bot, chat_id: int) -> int:
    u"""Бота только что сделали админом — забанить тех, кто уже в списке."""
    done = 0
    for entry in db.blacklist():
        if entry.get('removed_at') is not None:
            continue
        try:
            await bot.ban_chat_member(chat_id, entry['user_id'])
            done += 1
        except Exception as err:
            log.warning(u'чёрный список: %s в новом чате %s не забанен: %s',
                        entry['user_id'], chat_id, err)
    return done


@router.my_chat_member()
async def on_bot_membership(event: ChatMemberUpdated):
    u"""Бота назначили админом, лишили прав или убрали из канала/чата."""
    chat = event.chat
    if chat.type == 'private':
        return                                     # закрыл/открыл бота человек — не наше
    title = html.escape(chat.title or (u'@' + chat.username if chat.username else str(chat.id)))
    member = event.new_chat_member
    if member.status in ('administrator', 'creator'):
        # У каналов Telegram это право отдельно может и не показать — пробуем
        # банить; не выйдет — причина будет в отчёте о бане.
        can = bool(getattr(member, 'can_restrict_members', False)) or chat.type == 'channel'
        db.chat_seen(chat.id, chat.title, chat.type, can)
        text = (texts.CHAT_ADMIN_OK.format(title=title, done=await _ban_listed(event.bot, chat.id))
                if can else texts.CHAT_ADMIN_NO_RIGHTS.format(title=title))
    elif member.status in ('left', 'kicked'):
        db.chat_left(chat.id)
        text = texts.CHAT_LEFT.format(title=title)
    else:
        db.chat_seen(chat.id, chat.title, chat.type, False)
        text = texts.CHAT_ADMIN_NO_RIGHTS.format(title=title)
    log.info(u'бот в чате %s (%s): %s', chat.id, title, member.status)

    who = event.from_user
    if who and config.is_team(who.id):
        try:
            await event.bot.send_message(who.id, text)
            return
        except Exception as err:
            log.warning(u'не сказали %s про чат %s: %s', who.id, chat.id, err)
    await delivery.alert_admins(event.bot, text)
