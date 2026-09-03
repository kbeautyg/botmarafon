# -*- coding: utf-8 -*-
u"""Настройки из окружения.

Всё, что нельзя класть в репозиторий, живёт в .env: токен, чаты, админы.
Проверяем наличие на старте — бот, поднявшийся без чата покупок, молча
потеряет первую же заявку, и узнаем мы об этом от заказчика.
"""
import os

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ids(raw: str) -> tuple[int, ...]:
    u"""Список числовых id из строки «111, 222»."""
    return tuple(int(x) for x in raw.replace(';', ',').split(',') if x.strip())


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
SUPPORT_CHAT_ID = _int(os.getenv('SUPPORT_CHAT_ID'), 0)

ADMIN_IDS = _ids(os.getenv('ADMIN_IDS', ''))

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
