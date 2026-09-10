# -*- coding: utf-8 -*-
u"""Залить записи дней в Telegram самим ботом — без человека и без предела 50 МБ.

Зачем. Записи дней весят 300–800 МБ. Bot API с сервера принимает файлы до
50 МБ, поэтому до сих пор их отправлял боту человек из Telegram Desktop с
подписью day1…day4. 10.09.2026 записи пересобрали вертикально, а залить их
было некому — люди продолжали получать старые.

Как. Бот заходит в Telegram по протоколу клиента (MTProto, библиотека
Telethon) — там предел 2 ГБ. Отправляет каждую запись админу в чат с ботом,
затем через обычный Bot API пересылает её же и из ответа берёт file_id —
тот, по которому бот и шлёт записи людям. Пересланная копия удаляется.
file_id пишутся в media/days.json; с деплоем бот применяет их при старте
(bot/backup.py → apply_committed) и обновляет закреп-хранилище.

Запуск из корня проекта (BOT_TOKEN и ADMIN_IDS берутся из .env):

    set TG_API_ID=…  &  set TG_API_HASH=…
    python tools/upload_days_bot.py            # все четыре дня
    python tools/upload_days_bot.py 2 4        # выбранные

TG_API_ID/TG_API_HASH — пара любого клиента Telegram; в код и в репозиторий
не кладутся. Папка с записями — SRC (по умолчанию «Марафон записи/9x16»).
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get('SRC', u'C:/Users/Sharp/Desktop/Марафон записи/9x16')
OUT = os.path.join(ROOT, 'media', 'days.json')
COVERS = os.path.join(ROOT, 'media', 'intro')


def read_env(path):
    env = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    return env


def bot_api(token, method, **params):
    u"""Обычный Bot API: POST формой, ответ — result или исключение."""
    data = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request('https://api.telegram.org/bot%s/%s' % (token, method), data=data)
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    if not body.get('ok'):
        raise RuntimeError('%s: %s' % (method, body.get('description')))
    return body['result']


def probe(path):
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height:format=duration', '-of', 'json', path])
    info = json.loads(out.decode('utf-8'))
    stream = info['streams'][0]
    return int(stream['width']), int(stream['height']), float(info['format']['duration'])


def thumb_for(day, stage):
    u"""Миниатюра 180×320: Telegram берёт превью не больше 320 px по стороне."""
    src = os.path.join(COVERS, 'cover%d.jpg' % day)
    if not os.path.exists(src):
        return None
    dst = os.path.join(stage, 'thumb%d.jpg' % day)
    subprocess.check_call(['ffmpeg', '-v', 'error', '-i', src, '-vf',
                           'scale=180:320:force_original_aspect_ratio=decrease',
                           '-q:v', '4', dst, '-y'])
    return dst


def load_out():
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_out(data):
    # сразу после каждого дня: оборвётся на третьем — два первых не пропадут
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')


async def main(argv):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    env = read_env(os.path.join(ROOT, '.env'))
    env.update(os.environ)
    token = env.get('BOT_TOKEN', '').strip()
    admins = [a.strip() for a in env.get('ADMIN_IDS', '').replace(';', ',').split(',') if a.strip()]
    api_id = env.get('TG_API_ID', '').strip()
    api_hash = env.get('TG_API_HASH', '').strip()
    if not (token and admins and api_id.isdigit() and api_hash):
        sys.exit(u'Нужны BOT_TOKEN и ADMIN_IDS (.env) и TG_API_ID/TG_API_HASH (переменные).')
    admin = int(admins[0])
    days = [int(a) for a in argv] or [1, 2, 3, 4]

    from telethon import TelegramClient
    from telethon.tl.types import DocumentAttributeVideo

    stage = tempfile.mkdtemp(prefix='marathon-upload-')
    session = os.path.join(stage, 'bot')
    # Прокси. Прямой протокол Telegram в российской сети режется: 10.09.2026
    # соединение с DC вставало, а за минуту уходил мегабайт. Telegram Desktop
    # у Sharp ходит через FlClash — через его же локальный порт идём и мы:
    # TG_PROXY=socks5://127.0.0.1:7890 (или http://…). Пусто — напрямую.
    proxy = None
    raw_proxy = env.get('TG_PROXY', '').strip()
    if raw_proxy:
        parsed = urllib.parse.urlparse(raw_proxy)
        proxy = (parsed.scheme or 'socks5', parsed.hostname or '127.0.0.1', parsed.port or 7890)
        print(u'через прокси %s://%s:%d' % proxy)
    client = TelegramClient(session, int(api_id), api_hash, proxy=proxy)
    await client.start(bot_token=token)
    me = await client.get_me()
    print(u'вошли как @%s' % me.username)

    result = load_out()
    try:
        for day in days:
            path = os.path.join(SRC, u'День %d.mp4' % day)
            if not os.path.exists(path):
                print(u'нет файла дня %d: %s' % (day, path))
                continue
            w, h, dur = probe(path)
            size = os.path.getsize(path)
            print(u'день %d: %d МБ, %dx%d, %d мин — заливаю' % (day, size >> 20, w, h, dur // 60))
            started = time.time()
            shown = [-1]

            def progress(sent, total, day=day):
                pct = int(sent * 100 / total) if total else 0
                if pct >= shown[0] + 10:
                    shown[0] = pct - pct % 10
                    speed = sent / max(1.0, time.time() - started) / 1048576
                    print(u'  день %d: %d%% (%.1f МБ/с)' % (day, pct, speed))
                    sys.stdout.flush()

            msg = await client.send_file(
                admin, path,
                caption=u'day%d — новая вертикальная запись (залита ботом автоматически)' % day,
                supports_streaming=True,
                attributes=[DocumentAttributeVideo(duration=int(round(dur)), w=w, h=h,
                                                   supports_streaming=True)],
                thumb=thumb_for(day, stage),
                part_size_kb=512,
                progress_callback=progress)

            # file_id в формате Bot API: пересылаем эту же запись и читаем ответ
            copy = bot_api(token, 'forwardMessage', chat_id=admin, from_chat_id=admin,
                           message_id=msg.id, disable_notification='true')
            video = copy.get('video') or copy.get('document')
            if not video:
                raise RuntimeError(u'в пересланном сообщении дня %d нет видео' % day)
            try:
                bot_api(token, 'deleteMessage', chat_id=admin, message_id=copy['message_id'])
            except Exception as err:                    # копия лишняя, но не вредная
                print(u'  копию не удалили: %s' % err)
            result['day%d' % day] = video['file_id']
            save_out(result)
            print(u'день %d: готово за %d с, file_id записан' % (day, time.time() - started))
            sys.stdout.flush()
    finally:
        # выходим из своей сессии — это только наш вход, боевой Bot API не трогаем
        try:
            await client.log_out()
        except Exception:
            await client.disconnect()
    print(u'\nmedia/days.json: %s' % u', '.join(sorted(result)))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
