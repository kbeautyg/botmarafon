# -*- coding: utf-8 -*-
u"""Голосовые и кружки, записанные в пульте (AleX 19.09.2026).

Браузер отдаёт запись в своём формате, Telegram принимает голосовое только
ogg/opus, а кружок — только квадратным mp4. Здесь проверяется вся цепочка:
подпись на входе, предел размера, перекодировка и то, каким видом запись
уходит человеку, если перекодировать не вышло.
"""
import os
import shutil
import subprocess
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, record, web                                  # noqa: E402
from tests.fakes import FakeBot                                          # noqa: E402
from tests.test_panel import TOKEN, СВОЙ, ЧУЖОЙ, подпись                 # noqa: E402

ЕСТЬ_FFMPEG = bool(shutil.which('ffmpeg'))


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'record.db'))
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'ADMIN_IDS', (СВОЙ,))
    monkeypatch.setattr(config, 'STATS_IDS', ())
    web._hits.clear()
    web._window_from[0] = 0.0
    db.remember_user(1, 'nick_one', u'Человек')
    db.mark_launched(1)
    yield


class Диктофон(FakeBot):
    u"""Запоминает, чем именно ушла запись."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.voices = []
        self.notes = []
        self.docs = []

    async def send_voice(self, chat_id, voice, **kw):
        self.voices.append((chat_id, len(voice.data), kw.get('caption')))
        return await self._record('voice', chat_id, kw.get('caption'))

    async def send_video_note(self, chat_id, video, **kw):
        if hasattr(video, 'data'):
            self.notes.append((chat_id, len(video.data)))
            return await self._record('note', chat_id, None)
        return await super().send_video_note(chat_id, video, **kw)

    async def send_document(self, chat_id, document, **kw):
        self.docs.append((chat_id, getattr(document, 'filename', '')))
        return await self._record('document', chat_id, kw.get('caption'))


@pytest.fixture
async def пульт(база):
    bot = Диктофон()
    client = TestClient(TestServer(web.build(bot)))
    await client.start_server()
    client.bot = bot
    yield client
    await client.close()


def запись(seconds=1, video=False):
    u"""Настоящая запись из браузера — делаем её ffmpeg'ом."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_rec.webm')
    args = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'sine=frequency=440:duration=%d' % seconds]
    if video:
        args += ['-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=15:duration=%d' % seconds,
                 '-c:v', 'libvpx', '-b:v', '200k']
    args += ['-c:a', 'libopus', '-shortest', path]
    subprocess.check_call(args)
    data = open(path, 'rb').read()
    os.remove(path)
    return data


async def отправить(пульт, data, kind='voice', подпись_=None, name='voice.webm', text=u''):
    import aiohttp

    form = {'initData': подпись_ or подпись(), 'id': '1', 'kind': kind, 'text': text}
    writer = aiohttp.FormData()
    for key, value in form.items():
        writer.add_field(key, value)
    writer.add_field('file', data, filename=name, content_type='application/octet-stream')
    return await пульт.post('/api/record', data=writer)


# ------------------------------------------------------------------ дверь

async def test_без_подписи_запись_не_принимаем(пульт):
    ответ = await отправить(пульт, b'x' * 100, подпись_='мусор')
    assert ответ.status == 403
    assert пульт.bot.sent == []


async def test_посторонний_запись_не_отправит(пульт):
    ответ = await отправить(пульт, b'x' * 100, подпись_=подпись(ЧУЖОЙ))
    assert ответ.status == 403


async def test_слишком_большая_запись_не_проходит(пульт, monkeypatch):
    monkeypatch.setattr(web, 'MAX_UPLOAD', 1024)
    ответ = await отправить(пульт, b'x' * 4096)
    assert ответ.status == 413
    assert u'короче' in (await ответ.json())['error']


async def test_пустая_запись_не_уходит(пульт):
    ответ = await отправить(пульт, b'')
    assert ответ.status == 400


# ------------------------------------------------------------ что уходит

@pytest.mark.skipif(not ЕСТЬ_FFMPEG, reason=u'без ffmpeg перекодировать нечем')
async def test_запись_уходит_голосовым_и_ложится_в_переписку(пульт):
    ответ = await отправить(пульт, запись(), 'voice', text=u'Добрый день')
    assert ответ.status == 200
    assert len(пульт.bot.voices) == 1 and пульт.bot.voices[0][0] == 1
    последнее = db.chat_history(1)[-1]
    assert последнее['kind'] == 'voice' and последнее['text'] == u'Добрый день'


@pytest.mark.skipif(not ЕСТЬ_FFMPEG, reason=u'без ffmpeg перекодировать нечем')
async def test_запись_с_камеры_уходит_кружком(пульт):
    ответ = await отправить(пульт, запись(video=True), 'note', name='note.webm')
    assert ответ.status == 200
    assert len(пульт.bot.notes) == 1
    assert db.chat_history(1)[-1]['kind'] == 'video_note'


async def test_без_ffmpeg_запись_всё_равно_доходит_файлом(пульт, monkeypatch):
    u"""Лучше файл, чем молчание: человек получит запись в любом случае."""
    monkeypatch.setattr(record, 'have_ffmpeg', lambda: False)
    ответ = await отправить(пульт, u'притворимся записью'.encode('utf-8'))
    assert ответ.status == 200
    assert (await ответ.json())['kind'] == 'document'
    assert len(пульт.bot.docs) == 1
    assert db.chat_history(1)[-1]['kind'] == 'document'


async def test_закрывшему_бота_запись_не_уходит_и_он_отмечен(пульт, monkeypatch):
    monkeypatch.setattr(record, 'have_ffmpeg', lambda: False)
    пульт.app['bot'] = Диктофон(forbidden=True)
    ответ = await отправить(пульт, u'запись'.encode('utf-8'))
    assert ответ.status == 409
    assert db.get_user(1)['blocked_at'] is not None


# -------------------------------------------------------- перекодировка

@pytest.mark.skipif(not ЕСТЬ_FFMPEG, reason=u'без ffmpeg перекодировать нечем')
async def test_голосовое_становится_ogg_а_кружок_квадратным_mp4(tmp_path):
    источник = str(tmp_path / 'raw.webm')
    open(источник, 'wb').write(запись(video=True))

    голос = await record.to_voice(источник)
    assert голос and голос.endswith('.ogg') and os.path.getsize(голос) > 0

    кружок = await record.to_note(источник)
    assert кружок and кружок.endswith('.mp4')
    размер = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'csv=p=0', кружок]).decode()
    ширина, высота = [int(n) for n in размер.strip().rstrip(',').split(',')[:2]]
    assert ширина == высота == record.NOTE_SIDE
    record.forget(голос, кружок, источник)
