# -*- coding: utf-8 -*-
u"""Клавиатуры.

Кнопка службы заботы — reply, а не inline: по ТЗ она должна быть на виду
всегда, с самого запуска бота. Inline живёт при своём сообщении и уезжает
вверх с историей, reply остаётся под полем ввода на любом экране.
"""
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)

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


def offer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.OFFER_GYM, callback_data='buy:gym')],
        [InlineKeyboardButton(text=texts.OFFER_COURSE, callback_data='buy:course')]])
