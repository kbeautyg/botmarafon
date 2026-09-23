# -*- coding: utf-8 -*-
u"""Клавиатуры.

Кнопка службы заботы — reply, а не inline: по ТЗ она должна быть на виду
всегда, с самого запуска бота. Inline живёт при своём сообщении и уезжает
вверх с историей, reply остаётся под полем ввода на любом экране.
"""
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup, WebAppInfo)

from . import config, texts


def care() -> ReplyKeyboardMarkup:
    u"""Постоянная клавиатура со службой заботы."""
    return _keys([texts.CARE_BUTTON])


def _keys(titles) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=title) for title in titles]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=u'Напишите нам, если что-то нужно')


def poll(name: str) -> InlineKeyboardMarkup:
    u"""Да/Нет для опросника name — ответ несёт имя опросника в себе."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.POLL_YES, callback_data='poll:%s:yes' % name),
        InlineKeyboardButton(text=texts.POLL_NO, callback_data='poll:%s:no' % name)]])


def stats_menu(section: str, period: str) -> InlineKeyboardMarkup:
    u"""Кнопки под /stats: разделы по два в ряд, периоды, выгрузка.

    Текущие раздел и период помечены точкой. Нажатие правит то же
    сообщение, а не шлёт новое — чат не превращается в ленту отчётов.
    """
    from . import insights

    def button(title: str, data: str, on: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=(u'• ' + title) if on else title, callback_data=data)

    sections = list(insights.SECTIONS.items())
    rows = [[button(title, 'st:%s:%s' % (key, period), key == section)
             for key, title in sections[i:i + 2]] for i in range(0, len(sections), 2)]
    rows.append([button(title, 'st:%s:%s' % (section, key), key == period)
                 for key, title in insights.PERIOD_BUTTONS])
    rows.append([InlineKeyboardButton(text=u'📥 Все люди таблицей (Excel)',
                                      callback_data='st:csv:%s' % period)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def leads_launch(count: int) -> InlineKeyboardMarkup:
    u"""Кнопка админу: отправить марафон людям с заявки, пришедшим до 14.09.2026."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.LEADS_BUTTON % count, callback_data='leads:launch')]])


def restart() -> InlineKeyboardMarkup:
    u"""Кнопка «пройти заново» под ответом на повторный /start.

    Inline, а не автоматический перезапуск: человек мог нажать /start
    случайно, и обрывать ему марафон на середине без спроса нельзя.
    """
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.RESTART_BUTTON, callback_data='restart')]])


def care_link() -> InlineKeyboardMarkup | None:
    u"""Кнопка «написать в службу заботы».

    Ведёт в переписку с живым аккаунтом заботы. Бот не может написать
    человеку первым, поэтому единственный рабочий путь — открыть человеку
    чат, а не пытаться доставить сообщение за него.
    """
    if not config.CARE_CONTACT:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=u'Написать в службу заботы',
                             url='https://t.me/%s' % config.CARE_CONTACT)]])


def pay(url: str) -> InlineKeyboardMarkup:
    u"""Кнопка на оплату под ответом на «купить» (Павел 17.09.2026)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.PAY_BUTTON, url=url)]])


def day(number: int, url: str | None = None) -> InlineKeyboardMarkup | None:
    u"""Кнопки под самой записью дня: посмотреть и ответить.

    Павел 18.09.2026 поменял текст дней на «чтобы пришёл следующий день,
    нажми Да или Нет на кнопках ниже» — а кнопки приходили отдельным
    сообщением через два с половиной часа. Люди искали их под записью и
    писали, что «второй день не приходит, кнопка не кликабельна»
    (20.09.2026). Теперь кнопки там, где про них написано.

    После четвёртого дня вопроса нет — там только кнопки покупки.
    """
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text=texts.WATCH_BUTTON, url=url)])
    if number < 4:
        name = 'day%d' % number
        rows.append([
            InlineKeyboardButton(text=texts.POLL_YES, callback_data='poll:%s:yes' % name),
            InlineKeyboardButton(text=texts.POLL_NO, callback_data='poll:%s:no' % name)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def watch(url: str) -> InlineKeyboardMarkup:
    u"""Кнопка «смотреть запись» под днём, выложенным ссылкой."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.WATCH_BUTTON, url=url)]])


def live(url: str) -> InlineKeyboardMarkup:
    u"""Кнопка «смотреть эфир» под анонсом и напоминаниями."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.LIVE_BUTTON, url=url)]])


def live_confirm(live_id: int, count: int) -> InlineKeyboardMarkup:
    u"""Анонс уходит всем сразу — значит спрашиваем перед отправкой."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.TRY_BUTTON,
                              callback_data='live:me:%d' % live_id)],
        [InlineKeyboardButton(text=texts.LIVE_TEST_BUTTON,
                              callback_data='live:test:%d' % live_id)],
        [InlineKeyboardButton(text=texts.LIVE_PICK_BUTTON,
                              callback_data='live:pick:%d' % live_id)],
        [InlineKeyboardButton(text=texts.LIVE_GO.format(count=count),
                              callback_data='live:go:%d' % live_id)],
        [InlineKeyboardButton(text=texts.BROADCAST_CANCEL,
                              callback_data='live:no:%d' % live_id)]])


def stuck(count: int) -> InlineKeyboardMarkup:
    u"""Подтверждение досылки: людям уйдёт сообщение, значит спрашиваем."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.TRY_BUTTON, callback_data='stuck:me')],
        [InlineKeyboardButton(text=texts.STUCK_GO.format(count=count),
                              callback_data='stuck:go')]])


def panel_only(chat_id: int | None = None) -> InlineKeyboardMarkup | None:
    u"""Одна кнопка пульта — под списком, где дальше надо написать человеку.
    В группе мини-приложение Telegram не принимает, там кнопки нет."""
    button = panel_button() if chat_id is None or chat_id > 0 else None
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def menu(chat_id: int | None = None) -> InlineKeyboardMarkup:
    u"""Меню команды: всё, что умеет бот, — кнопками.

    AleX 19.09.2026: «нельзя это кнопками всё прилепить, чтобы команду не
    называть». Пульт — кнопка мини-приложения, остальное — обычные
    нажатия; без адреса пульта первая кнопка просто не показывается.

    В рабочем чате кнопки пульта нет: телеграм не принимает мини-приложение
    в группе и отвергает ВСЮ клавиатуру целиком — то есть меню не пришло бы
    вовсе. Тот же приём, что и под уведомлениями о человеке (ban_ask).
    chat_id больше нуля — личная переписка.
    """
    rows = []
    panel = panel_button() if chat_id is None or chat_id > 0 else None
    if panel:
        rows.append([panel])
    rows.append([InlineKeyboardButton(text=texts.MENU_BROADCAST, callback_data='mn:bc'),
                 InlineKeyboardButton(text=texts.MENU_DAILY, callback_data='mn:day')])
    rows.append([InlineKeyboardButton(text=texts.MENU_STATS, callback_data='mn:stats'),
                 InlineKeyboardButton(text=texts.MENU_WHO, callback_data='mn:who')])
    rows.append([InlineKeyboardButton(text=texts.MENU_LIVE, callback_data='mn:live'),
                 InlineKeyboardButton(text=texts.MENU_STUCK, callback_data='mn:stuck')])
    rows.append([InlineKeyboardButton(text=texts.MENU_BUYS, callback_data='mn:buys'),
                 InlineKeyboardButton(text=texts.MENU_BAN, callback_data='mn:ban')])
    rows.append([InlineKeyboardButton(text=texts.MENU_LINKS, callback_data='mn:links'),
                 InlineKeyboardButton(text=texts.MENU_CHATS, callback_data='mn:chats')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def team_keys() -> ReplyKeyboardMarkup:
    u"""Кнопка «Меню» под полем ввода у своих — чтобы не искать команду."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.MENU_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=u'Ник и текст — уйдёт человеку от имени бота')


def daily(base: int, chosen: list) -> InlineKeyboardMarkup:
    u"""Календарь под отчётом: дни строками по четыре, выбранные — с точкой.

    Нажатие добавляет или убирает день, и отчёт пересчитывается в том же
    сообщении (AleX 18.09.2026: «выбрать сразу день или несколько дней по
    календарю, и отчёт этот мини обновляется»).
    """
    from . import daily

    picked = set(chosen)
    buttons = []
    for number in range(base, base - daily.CALENDAR_DAYS, -1):
        mark = u'• ' if number in picked else u''
        chosen_now = (picked - {number}) if number in picked else (picked | {number})
        if not chosen_now:
            chosen_now = {number}          # хотя бы один день должен остаться
        buttons.append(InlineKeyboardButton(
            text=mark + daily.title(number).split(', ')[-1],
            callback_data=daily.pack(base, list(chosen_now))))
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text=u'Сегодня',
                                      callback_data=daily.pack(base, [base])),
                 InlineKeyboardButton(text=u'Вчера',
                                      callback_data=daily.pack(base, [base - 1])),
                 InlineKeyboardButton(text=u'7 дней',
                                      callback_data=daily.pack(base, list(range(base - 6, base + 1))))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast(broadcast_id: int, count: int) -> InlineKeyboardMarkup:
    u"""Подтверждение рассылки: разослать всем или отменить.

    Рассылку нельзя отозвать, поэтому отправка — вторым касанием, и на
    кнопке сразу видно, скольким людям уйдёт.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.TRY_BUTTON,
                              callback_data='bc:me:%d' % broadcast_id)],
        [InlineKeyboardButton(text=texts.BROADCAST_GO.format(count=count),
                              callback_data='bc:go:%d' % broadcast_id)],
        [InlineKeyboardButton(text=texts.BROADCAST_CANCEL,
                              callback_data='bc:no:%d' % broadcast_id)]])


def offer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.OFFER_GYM, callback_data='buy:gym')],
        [InlineKeyboardButton(text=texts.OFFER_COURSE, callback_data='buy:course')]])


# ---------------------------------------------------------- чёрный список
#
# Под уведомлениями команде (AleX 16.09.2026): «в одно касание в чёрный
# список». Меню долгого нажатия Telegram ботам не открывает, поэтому
# кнопка — под самим уведомлением. Бан в каналах Павла не отменить до
# конца (человеку придётся вернуться самому), поэтому второе касание —
# подтверждение: промахнуться мимо «Ответить» слишком легко.

def panel_button(user_id: int | None = None) -> InlineKeyboardButton | None:
    u"""Кнопка «открыть пульт», при user_id — сразу на диалог с человеком.

    Мини-приложение Telegram открывает только по https и только в личной
    переписке: в группах такую кнопку телеграм не принимает вовсе. Поэтому
    без адреса пульта (WEBAPP_URL) её просто нет.
    """
    if not config.WEBAPP_URL:
        return None
    url = config.WEBAPP_URL + ('/?id=%d' % user_id if user_id else '/')
    return InlineKeyboardButton(
        text=texts.PANEL_REPLY if user_id else texts.PANEL_BUTTON,
        web_app=WebAppInfo(url=url))


def panel() -> InlineKeyboardMarkup | None:
    button = panel_button()
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def ban_ask(user_id: int, chat_id: int | None = None) -> InlineKeyboardMarkup:
    u"""Кнопки под уведомлением о человеке: ответить в пульте и в чёрный список.

    Пульт добавляется только в личной переписке (chat_id > 0) — в командном
    чате телеграм отказывается принимать кнопку мини-приложения.
    """
    rows = []
    if chat_id is not None and chat_id > 0:
        button = panel_button(user_id)
        if button:
            rows.append([button])
    rows.append([InlineKeyboardButton(text=texts.BAN_BUTTON, callback_data='bl:ask:%d' % user_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ban_confirm(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BAN_YES, callback_data='bl:yes:%d' % user_id),
        InlineKeyboardButton(text=texts.BAN_NO, callback_data='bl:no:%d' % user_id)]])


def ban_undo(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.UNBAN_BUTTON, callback_data='bl:undo:%d' % user_id)]])
