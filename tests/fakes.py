# -*- coding: utf-8 -*-
u"""Подделки телеграма для тестов обработчиков.

Обработчику нужны message.answer, call.message.edit_text и bot.send_message —
и ничего больше. Поднимать ради этого настоящего бота не нужно: подделки
записывают вызовы, и проверять можно то, что человек реально получил.
"""
import os
from types import SimpleNamespace


class FakeUser(object):
    def __init__(self, user_id=1, username='tester', full_name=u'Тестер'):
        self.id = user_id
        self.username = username
        self.full_name = full_name
        self.first_name = full_name.split()[0]


class FakeBot(object):
    u"""Считает отправленное. fail_times — сколько первых вызовов упадут."""

    def __init__(self, fail_times=0, forbidden=False):
        self.sent = []
        # Клавиатура каждой отправки, тем же порядком, что и sent: кнопки —
        # это то, чем человек продолжает марафон, и их пропажу надо ловить.
        # Имя не markups: так называют свои списки FakeMessage и наследники.
        self.keys_sent = []
        self.fail_times = fail_times
        self.forbidden = forbidden
        self._next_id = 100
        self.by_id = {}        # message_id -> FakeMessage, чтобы править и закреплять
        self.pinned = {}       # chat_id -> закреплённое сообщение

    async def _record(self, kind, chat_id, payload=None, keys=None):
        from bot import delivery
        if self.forbidden:
            raise delivery.Gone()
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(u'телеграм не в духе')
        self.sent.append((kind, chat_id, payload))
        self.keys_sent.append(keys)
        self._next_id += 1
        message = FakeMessage(text=payload, message_id=self._next_id, bot=self,
                              chat_id=chat_id)
        self.by_id[self._next_id] = message
        return message

    async def send_message(self, chat_id, text, **kw):
        return await self._record('text', chat_id, text, kw.get('reply_markup'))

    async def send_video_note(self, chat_id, video, **kw):
        # Кружок уходит объектом файла — записываем имя, иначе в проверках
        # видно только адрес объекта в памяти и ветку не отличить.
        name = getattr(video, 'path', video)
        return await self._record('circle', chat_id,
                                  os.path.splitext(os.path.basename(str(name)))[0])

    async def send_video(self, chat_id, video, **kw):
        return await self._record('video', chat_id, video, kw.get('reply_markup'))

    async def send_photo(self, chat_id, photo, **kw):
        return await self._record('photo', chat_id, photo)

    async def send_audio(self, chat_id, audio, **kw):
        return await self._record('audio', chat_id, audio)

    async def send_animation(self, chat_id, animation, **kw):
        return await self._record('animation', chat_id, animation)

    async def send_document(self, chat_id, document, **kw):
        return await self._record('document', chat_id, document)

    async def send_document(self, chat_id, document, **kw):
        # выгрузка статистики уходит файлом — записываем его имя
        return await self._record('document', chat_id, getattr(document, 'filename', None))

    async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
        return await self._record('copy', chat_id, (from_chat_id, message_id))

    # Чёрный список банит в каналах и чатах Павла (bot/blacklist.py).
    async def ban_chat_member(self, chat_id, user_id, **kw):
        self.sent.append(('ban', chat_id, user_id))
        return True

    async def unban_chat_member(self, chat_id, user_id, **kw):
        self.sent.append(('unban', chat_id, (user_id, kw.get('only_if_banned'))))
        return True

    # Закреп у админа — хранилище записей дней (bot/backup.py).
    async def get_chat(self, chat_id):
        return SimpleNamespace(pinned_message=self.pinned.get(chat_id))

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.pinned[chat_id] = self.by_id[message_id]

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kw):
        self.by_id[message_id].text = text


class FakeMessage(object):
    def __init__(self, text=None, caption=None, user=None, chat_id=None,
                 message_id=1, bot=None, reply_to=None, photo=None, video=None):
        self.text = text
        self.caption = caption
        self.from_user = user or FakeUser()
        self.chat = type('Chat', (), {'id': chat_id if chat_id is not None
                                      else self.from_user.id, 'type': 'private'})()
        self.message_id = message_id
        self.bot = bot or FakeBot()
        self.reply_to_message = reply_to
        self.photo = photo
        self.video = video
        self.document = None
        self.video_note = None
        self.answers = []
        self.replies = []
        self.markups = []

    async def answer(self, text, reply_markup=None, **kw):
        self.answers.append(text)
        self.markups.append(reply_markup)
        return await self.bot._record('text', self.chat.id, text)

    async def reply(self, text, **kw):
        self.replies.append(text)
        return await self.bot._record('text', self.chat.id, text)


class FakeCall(object):
    def __init__(self, data, user=None, bot=None, message=None):
        self.data = data
        self.from_user = user or FakeUser()
        self.bot = bot or FakeBot()
        self.message = message or FakeMessage(bot=self.bot, user=self.from_user)
        self.answers = []
        self.edited = []
        self.markup_cleared = False

        call = self

        async def edit_text(text, **kw):
            call.edited.append(text)

        async def edit_reply_markup(**kw):
            call.markup_cleared = True

        self.message.edit_text = edit_text
        self.message.edit_reply_markup = edit_reply_markup

    async def answer(self, text=None, **kw):
        self.answers.append(text)


class FakePhoto(object):
    def __init__(self, file_id):
        self.file_id = file_id


class FakeVideo(object):
    def __init__(self, file_id):
        self.file_id = file_id


# ---------------------------------------------- настоящий разбор aiogram

from datetime import datetime                                   # noqa: E402

from aiogram.client.session.base import BaseSession             # noqa: E402
from aiogram.methods import CopyMessage, SendMessage            # noqa: E402
from aiogram.types import Chat, Message, MessageId              # noqa: E402


class ЗаписьСессия(BaseSession):
    u"""Сессия бота без сети: записывает вызовы API и отвечает правдоподобно —
    для проверок через dispatcher.feed_raw_update."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.next_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        self.next_id += 1
        if isinstance(method, CopyMessage):
            return MessageId(message_id=self.next_id)
        if isinstance(method, SendMessage):
            return Message(message_id=self.next_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type='private'), text=method.text)
        return True

    async def stream_content(self, *a, **kw):                  # pragma: no cover
        yield b''

    async def close(self):
        pass
