# -*- coding: utf-8 -*-
u"""Сценарий воронки: сроки заказчика и целостность переходов.

Проверять его вживую нельзя: между шагами по два с половиной часа, и один
прогон занял бы полсуток. Зато сценарий — данные, и весь путь человека
проходится тестом за миллисекунды.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import funnel                                            # noqa: E402
from bot.funnel import CHAINS, MINUTE                             # noqa: E402


def walk(chain, answers):
    u"""Пройти воронку от начала, отвечая на опросники по списку.

    Возвращает список (цепочка, шаг, накопленные секунды) — то есть весь
    путь человека с отметками времени.
    """
    path, clock, pos = [], 0, 0
    replies = list(answers)

    while True:
        step = funnel.step_at(chain, pos)
        if step is None:
            break
        clock += step.delay
        path.append((chain, step, clock))

        if step.kind == 'poll':
            if not replies:
                break
            chain, pos = funnel.POLL_BRANCHES[step.ref][replies.pop(0)], 0
            clock += CHAINS[chain].steps[0].delay
            continue

        following = funnel.next_after(chain, pos)
        if not following:
            break
        chain, pos, _ = following

    return path


def test_каждая_цепочка_ведёт_в_существующую():
    for name, chain in CHAINS.items():
        if chain.next:
            assert chain.next in CHAINS, u'%s ведёт в никуда: %s' % (name, chain.next)


def test_у_каждого_опросника_есть_обе_ветки():
    polls = {step.ref for chain in CHAINS.values()
             for step in chain.steps if step.kind == 'poll'}
    assert polls == set(funnel.POLL_BRANCHES)
    for poll, branches in funnel.POLL_BRANCHES.items():
        assert set(branches) == {'yes', 'no'}
        for target in branches.values():
            assert target in CHAINS


def test_запуск_отдаёт_восемь_отзывов_и_первый_день():
    steps = CHAINS['launch'].steps
    reviews = [s for s in steps if s.kind == 'review']
    assert [s.ref for s in reviews] == list(range(1, 9))

    # ТЗ: отзывы через 60 секунд после кружка, дальше каждые две секунды.
    assert reviews[0].delay == 60
    assert all(s.delay == 2 for s in reviews[1:])

    assert steps[-1].kind == 'day' and steps[-1].ref == 1


def test_сроки_между_днями_как_в_тз():
    ждать = {name: chain.steps[0].delay
             for name, chain in CHAINS.items() if name.startswith('after_')}
    assert ждать['after_day1'] == 150 * MINUTE     # «через 150 минут»
    assert ждать['after_day2'] == 120 * MINUTE     # «ровно через 120 минут»
    assert ждать['after_day3'] == 120 * MINUTE
    assert ждать['after_day4'] == 60 * MINUTE      # «через 60 минут»


def test_пауза_перед_записью_дня_как_в_тз():
    пауза = lambda chain: [s for s in CHAINS[chain].steps if s.kind == 'day'][0].delay
    assert пауза('day1_yes') == 5 and пауза('day1_no') == 5     # «через 5 секунд»
    assert пауза('day2_yes') == 30 and пауза('day2_no') == 30   # «через 30 секунд»
    assert пауза('day3_yes') == 3 and пауза('day3_no') == 3     # «через 3 секунды»


def test_обе_ветки_приводят_к_одному_дню():
    for day, poll in ((2, 'day1'), (3, 'day2'), (4, 'day3')):
        for answer in ('yes', 'no'):
            chain = CHAINS[funnel.POLL_BRANCHES[poll][answer]]
            days = [s.ref for s in chain.steps if s.kind == 'day']
            assert days == [day], u'%s/%s ведёт не в день %d' % (poll, answer, day)


def test_человек_проходит_все_четыре_дня_любыми_ответами():
    for answers in (('yes', 'yes', 'yes'), ('no', 'no', 'no'), ('yes', 'no', 'yes')):
        path = walk('launch', answers)
        days = [step.ref for _, step, _ in path if step.kind == 'day']
        assert days == [1, 2, 3, 4], u'ответы %s дали дни %s' % (answers, days)

        kinds = [step.kind for _, step, _ in path]
        assert kinds.count('poll') == 3
        assert 'offer' in kinds, u'кнопки покупки не дошли при ответах %s' % (answers,)


def test_кнопки_покупки_идут_после_кружка_и_перед_заботой():
    kinds = [s.kind for s in CHAINS['after_day4'].steps]
    assert kinds == ['circle', 'offer', 'circle']
    # «после кружка сразу через 2 секунды прилетают кнопки»
    assert CHAINS['after_day4'].steps[1].delay == 2
    assert CHAINS['after_day4'].steps[2].ref == 'care'


def test_весь_путь_укладывается_в_обещанные_сроки():
    u"""От запуска до кнопок покупки — семь с половиной часов.

    Это сумма пауз заказчика: 150 + 120 + 120 + 60 минут. Человек, начавший
    в десять утра, доходит до продажи к вечеру того же дня — и ни один шаг
    не приходится на ночь. Если сроки поедут, тест это поймает.
    """
    path = walk('launch', ('yes', 'yes', 'yes'))
    до_кнопок = [clock for _, step, clock in path if step.kind == 'offer'][0]
    минуты = до_кнопок / 60.0
    assert 450 <= минуты < 455, u'до продажи %.1f минут' % минуты
