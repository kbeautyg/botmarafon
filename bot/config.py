# -*- coding: utf-8 -*-
u"""Настройки из окружения.

Всё, что нельзя класть в репозиторий, живёт в .env: токен, чаты, админы.
Проверяем наличие на старте — бот, поднявшийся без чата покупок, молча
потеряет первую же заявку, и узнаем мы об этом от заказчика.
"""
import os

import re

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Что в ADMIN_IDS / STATS_IDS не удалось понять как id — для /status.
IDS_SKIPPED: list[str] = []


def _ids(raw: str | None) -> tuple[int, ...]:
    u"""Список числовых id: «111, 222», «111;222», «111 222», в кавычках — всё годится.

    Раньше строка делилась только по запятой и каждый кусок шёл в int():
    одна кавычка или пробел вместо запятой роняли бота целиком на старте, а
    @ник вместо числа — тоже. Sharp 10.09.2026 добавил AleX в переменные, а
    доступа так и не появилось. Теперь непонятное пропускаем и запоминаем —
    /status покажет, что именно бот не понял.
    """
    out = []
    for token in re.split(r'[\s,;]+', str(raw or '')):
        token = token.strip().strip('"\'')
        if not token:
            continue
        if token.lstrip('-').isdigit():
            out.append(int(token))
        else:
            IDS_SKIPPED.append(token)
    return tuple(dict.fromkeys(out))


def _int(raw: str | None, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float(raw: str | None, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()

# Куда падают заявки на покупку и вопросы в службу заботы.
PURCHASE_CHAT_ID = _int(os.getenv('PURCHASE_CHAT_ID'), 0)
# Чат для почасовой сводки: сколько новых людей и откуда (AleX, 07.09.2026).
# Пусто — сводка не шлётся, остаётся команда /stats.
STATS_CHAT_ID = _int(os.getenv('STATS_CHAT_ID'), 0)
SUPPORT_CHAT_ID = _int(os.getenv('SUPPORT_CHAT_ID'), 0)

ADMIN_IDS = _ids(os.getenv('ADMIN_IDS', ''))

# Кому можно смотреть статистику (/stats, /кто, /ссылки, /status), не
# будучи админом. Sharp 10.09.2026: заказчик набрал /stats, а бот принял
# это за вопрос в заботу — команды были только для ADMIN_IDS. Пусто —
# статистика только админам и участникам командных чатов (ниже).
# Команда заказчика: статистика открыта им всегда, без переменных Railway.
# 11.09.2026 правка ушла не туда: id AleX добавили в TG_TO другого сервиса
# (кому слать заявки сайта), а бот марафона его так и не увидел. Числа — не
# секрет: работают только в этом боте и только для чтения статистики.
TEAM_STATS_IDS = (
    312701042,   # Павел Андреев
    350631550,   # AleX K.E.N.T.
)
STATS_IDS = tuple(dict.fromkeys(_ids(os.getenv('STATS_IDS', '')) + TEAM_STATS_IDS))


def can_stats(user_id: int, chat_id: int | None = None) -> bool:
    u"""Статистику видят админы, STATS_IDS и любой, кто пишет из
    командного чата — сводки, поддержки или заявок: там только свои,
    и заводить под них ещё одну переменную незачем."""
    if user_id in ADMIN_IDS or user_id in STATS_IDS:
        return True
    team = {c for c in (STATS_CHAT_ID, SUPPORT_CHAT_ID, PURCHASE_CHAT_ID) if c}
    return chat_id is not None and chat_id in team

# Аккаунт службы заботы: кнопка ведёт прямо в переписку с ним. У заказчика
# это @Metod_Finish_Official — живой аккаунт, а не группа, поэтому писать
# туда ботом нельзя, только приводить человека за руку.
CARE_CONTACT = os.getenv('CARE_CONTACT', '').strip().lstrip('@')

def _db_path():
    u"""Где держать базу.

    На Railway файловая система контейнера эфемерная: без подключённого
    диска база пропадёт при первом же перезапуске, а вместе с ней очередь
    отложенных шагов и запомненные записи дней — люди зависнут посреди
    воронки. Поэтому если диск примонтирован в /data, пишем туда.
    """
    told = os.getenv('DB_PATH')
    if told:
        return told
    if os.path.isdir('/data'):
        return '/data/marathon.db'
    return os.path.join(ROOT, 'data', 'marathon.db')


DB_PATH = _db_path()

# Запасной источник записей дней. База без диска на Railway не переживает
# деплой (2 сентября так пропали все четыре дня), а переменные окружения
# переживают: DAY1…DAY4 = file_id видео или ссылка на запись. Если в базе
# запись есть, она важнее переменной.
DAY_ENV = {n: os.getenv('DAY%d' % n, '').strip() for n in (1, 2, 3, 4)}


def on_railway() -> bool:
    return bool(os.getenv('RAILWAY_ENVIRONMENT'))


def db_persistent() -> bool:
    u"""Лежит ли база на примонтированном томе, а не в файловой системе контейнера.

    Идём вверх от папки базы до корня: том может быть примонтирован и выше
    (DB_PATH=/data/bot/marathon.db). Сам корень не считаем — в контейнере
    он всегда «примонтирован», но живёт до первого деплоя.
    """
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    while folder != os.path.dirname(folder):
        if os.path.ismount(folder):
            return True
        folder = os.path.dirname(folder)
    return False


CIRCLES_DIR = os.path.join(ROOT, 'media', 'circles')
# Лента отзывов: img1-4 — сторис с отзывами, vid1-5 — видеоотзывы заказчика.
REVIEWS_DIR = os.path.join(ROOT, 'media', 'reviews')
# Обложки записей дней — превью дня с энергией в сферу (tools/days.py).
# Без своей обложки Telegram берёт первый кадр, а он у заставки тёмный.
COVERS_DIR = os.path.join(ROOT, 'media', 'intro')

# Если человек не ответил на опросник, воронка встанет навсегда: следующий
# день привязан к его ответу. Через столько часов ведём по ветке «нет» —
# она мягкая («понимаю, бывают дела») и как раз для тех, кто не посмотрел.
# 0 отключает добивание и оставляет человека ждать ответа бесконечно.
POLL_FALLBACK_HOURS = _int(os.getenv('POLL_FALLBACK_HOURS'), 12)

# Как часто планировщик заглядывает в очередь отложенных шагов. Полсекунды:
# в сценарии есть шаги через 2 и 3 секунды (отзывы идут «каждые две
# секунды»), и при редком тике они заметно плывут против ТЗ.
TICK_SECONDS = _float(os.getenv('TICK_SECONDS'), 0.5)

# Сколько шагов разбирать за тик. Телеграм принимает от бота около тридцати
# сообщений в секунду на всех; двенадцать за полсекунды — с запасом.
JOBS_PER_TICK = _int(os.getenv('JOBS_PER_TICK'), 12)


def check() -> list[str]:
    u"""Чего не хватает для боевого запуска. Пустой список — всё на месте."""
    missing = []
    if not BOT_TOKEN:
        missing.append(u'BOT_TOKEN — токен бота от @BotFather')
    if not PURCHASE_CHAT_ID:
        missing.append(u'PURCHASE_CHAT_ID — чат, куда падают заявки на покупку')
    if not SUPPORT_CHAT_ID and not CARE_CONTACT:
        missing.append(u'CARE_CONTACT или SUPPORT_CHAT_ID — куда идут вопросы')
    if not ADMIN_IDS:
        missing.append(u'ADMIN_IDS — кому можно загружать записи дней')
    return missing
