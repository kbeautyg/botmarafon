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


def start_menu() -> ReplyKeyboardMarkup:
    u"""Приветственная клавиатура: запуск и забота.

    Обе кнопки в одном ряду и в одном сообщении с приветствием. Раньше
    «Запустить» было inline-кнопкой, а inline и обычную клавиатуру в одном
    сообщении телеграм не отдаёт — приходилось слать вдогонку пустое «👇».
    Человек видел два сообщения подряд, второе без смысла.
    """
    return _keys([texts.LAUNCH_BUTTON, texts.CARE_BUTTON])


def _keys(titles) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=title) for title in titles]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=u'Напишите нам, если что-то нужно')


def launch() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.LAUNCH_BUTTON, callback_data='launch')]])


def poll(name: str) -> InlineKeyboardMarkup:
    u"""Да/Нет для опросника name — ответ несёт имя опросника в себе."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.POLL_YES, callback_data='poll:%s:yes' % name),
        InlineKeyboardButton(text=texts.POLL_NO, callback_data='poll:%s:no' % name)]])


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
