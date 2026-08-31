# -*- coding: utf-8 -*-
u"""Узнать id людей и чатов для .env.

Для запуска бота нужны три числа: кто админ, куда падают заявки на покупку
и где сидит служба заботы. Своего id человек не знает, а id группы тем более
— он отрицательный и нигде не показывается.

Скрипт слушает бота и печатает id всех, кто ему написал или добавил его в
группу. Запускается ДО основного бота: телеграм отдаёт обновления только
одному слушателю, вместе они работать не будут.

Найденное сразу пишется в tools/_whoami.txt. Это не для красоты: телеграм
отдаёт каждое обновление ровно один раз, и если вывод осел в буфере, а
процесс остановили — id потерян, человеку придётся писать боту заново.

Запуск:  python tools/whoami.py        (Ctrl+C чтобы остановить)
"""
from __future__ import print_function

import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config                                            # noqa: E402

API = 'https://api.telegram.org/bot%s/%s'
FOUND = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_whoami.txt')


def note(line):
    u"""Печатаем и тут же кладём на диск, не полагаясь на буфер."""
    print(line, flush=True)
    with io.open(FOUND, 'a', encoding='utf-8') as handle:
        handle.write(line + os.linesep)


def call(method, **params):
    url = API % (config.BOT_TOKEN, method)
    data = json.dumps(params).encode('utf-8')
    request = urllib.request.Request(
        url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=70) as answer:
        return json.loads(answer.read().decode('utf-8'))


def describe(update):
    u"""Что интересного в обновлении: кто написал и откуда."""
    message = (update.get('message') or update.get('edited_message')
               or update.get('channel_post') or {})
    chat = message.get('chat') or {}
    user = message.get('from') or {}
    rows = []

    if user:
        rows.append(u'человек: %s (@%s) — id %s'
                    % (user.get('first_name', u'без имени'),
                       user.get('username', u'без ника'), user.get('id')))
    if chat and chat.get('type') != 'private':
        rows.append(u'ЧАТ «%s» (%s) — id %s'
                    % (chat.get('title', u'без названия'), chat.get('type'), chat.get('id')))
    elif chat:
        rows.append(u'личка — id %s' % chat.get('id'))

    joined = message.get('new_chat_members') or []
    if any(member.get('is_bot') for member in joined):
        rows.append(u'  ↑ бота только что добавили сюда')
    if message.get('text'):
        rows.append(u'  текст: %s' % message['text'][:80])
    return rows


def main():
    if not config.BOT_TOKEN:
        raise SystemExit(u'Нет BOT_TOKEN в .env')

    me = call('getMe')['result']
    note(u'Слушаем @%s. Напишите боту в личку и в каждую группу — '
         u'здесь появятся их id.' % me['username'])

    offset = None
    seen = set()
    while True:
        answer = call('getUpdates', offset=offset, timeout=60)
        if not answer.get('ok'):
            note(u'ошибка: %s' % answer.get('description'))
            break
        for update in answer['result']:
            offset = update['update_id'] + 1
            rows = describe(update)
            key = u'|'.join(rows)
            if not rows or key in seen:
                continue
            seen.add(key)
            for row in rows:
                note(row)
            note(u'')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(u'\nостановлено')
