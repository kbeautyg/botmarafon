# -*- coding: utf-8 -*-
u"""Сценарий воронки — данными, а не кодом.

Воронка описана цепочками шагов. У шага две вещи: что отправить и через
сколько секунд после предыдущего. Цепочка кончилась — начинается та, что
указана в next; на опроснике цепочка обрывается и ждёт ответа человека.

Так сделано ради двух вещей. Во-первых, сроки заказчика («через 150 минут»,
«через 5 секунд») читаются здесь одной таблицей и правятся без похода в
код отправки. Во-вторых, сценарий целиком проверяется тестами: пройти его
на бумаге можно, не поднимая ни бота, ни телеграм.
"""
from collections import namedtuple

MINUTE = 60

# kind — что делаем, ref — над чем, delay — пауза перед шагом в секундах.
#   circle — кружок из media/circles
#   review — отзыв номер ref
#   day    — запись дня ref плюс текст под ней
#   poll   — вопрос с кнопками «Да»/«Нет»; цепочка встаёт и ждёт ответа
#   offer  — кнопки покупки
Step = namedtuple('Step', ('kind', 'delay', 'ref'))
Chain = namedtuple('Chain', ('steps', 'next'))

REVIEW_COUNT = 8


def _launch() -> Chain:
    u"""Запуск: приветствие, отзывы, первый день.

    Приветственный кружок у заказчика один, но идёт 84 секунды — телеграм
    столько в кружок не пишет. Разрезан надвое по концу фразы, части уходят
    подряд. Шестьдесят секунд до отзывов отсчитываются от второй части.
    """
    steps = [Step('circle', 0, 'welcome_1'),
             Step('circle', 2, 'welcome_2')]
    for i in range(1, REVIEW_COUNT + 1):
        steps.append(Step('review', 60 if i == 1 else 2, i))
    steps.append(Step('day', 2, 1))
    return Chain(tuple(steps), 'after_day1')


def _ask(circle: str, poll: str, wait: int) -> Chain:
    u"""Пауза, кружок с вопросом и сам опросник."""
    return Chain((Step('circle', wait, circle), Step('poll', 0, poll)), None)


def _answer(circle: str, day: int, pause: int, follow: str) -> Chain:
    u"""Реакция на ответ: кружок ветки и следом запись дня."""
    return Chain((Step('circle', 0, circle), Step('day', pause, day)), follow)


CHAINS = {
    'launch': _launch(),

    # «Через 150 минут после того, как прилетел первый день марафона»
    'after_day1': _ask('ask_day1', 'day1', 150 * MINUTE),
    'day1_yes': _answer('day2_yes', 2, 5, 'after_day2'),
    'day1_no': _answer('day2_no', 2, 5, 'after_day2'),

    # «Ровно через 120 минут»
    'after_day2': _ask('ask_day2', 'day2', 120 * MINUTE),
    'day2_yes': _answer('day3_yes', 3, 30, 'after_day3'),
    'day2_no': _answer('day3_no', 3, 30, 'after_day3'),

    # Кружка ask_day3 заказчик не прислал — шаг отправки его пропустит,
    # вопрос уйдёт текстом. Появится файл — начнёт уходить и кружок.
    'after_day3': _ask('ask_day3', 'day3', 120 * MINUTE),
    'day3_yes': _answer('day4_yes', 4, 3, 'after_day4'),
    'day3_no': _answer('day4_no', 4, 3, 'after_day4'),

    # «Через 60 минут после записи 4 дня» — кружок, через 2 секунды кнопки.
    # Кружок про службу заботы идёт следом; паузу заказчик не назвал, взяли
    # минуту: сразу за кнопками он читается как продолжение продажи.
    'after_day4': Chain((Step('circle', 60 * MINUTE, 'offer'),
                         Step('offer', 2, None),
                         Step('circle', 60, 'care')), None),
}

# Какой ответ какую цепочку запускает.
POLL_BRANCHES = {
    'day1': {'yes': 'day1_yes', 'no': 'day1_no'},
    'day2': {'yes': 'day2_yes', 'no': 'day2_no'},
    'day3': {'yes': 'day3_yes', 'no': 'day3_no'},
}


def step_at(chain: str, pos: int) -> Step | None:
    u"""Шаг по номеру или None, если цепочка кончилась."""
    steps = CHAINS[chain].steps
    return steps[pos] if 0 <= pos < len(steps) else None


def next_after(chain: str, pos: int) -> tuple[str, int, int] | None:
    u"""Что идёт следом: (цепочка, позиция, пауза) либо None, если всё.

    Внутри цепочки — следующий шаг с его паузой. На конце — первый шаг
    цепочки next с её собственной паузой. У опросника продолжения нет:
    его ставит ответ человека.
    """
    steps = CHAINS[chain].steps
    if pos + 1 < len(steps):
        return chain, pos + 1, steps[pos + 1].delay

    follow = CHAINS[chain].next
    if not follow:
        return None
    return follow, 0, CHAINS[follow].steps[0].delay
