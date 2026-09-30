# -*- coding: utf-8 -*-
u"""Записи марафона закрываются через 72 часа после четвёртого дня.

AleX 29.09.2026: «всем, кто посмотрел четвёртый день и им пришла кнопка
«купить», но они не сделали свой выбор, — чтобы через 3 дня видео
четырёх дней марафона удалялись и приходило как баннер: короткое и
цикличное видео с текстом», с кнопкой «купить» и ссылкой на сайт
энергозала. И — «для всех, кто там есть, кроме админов и разработчиков».

Удалить своё сообщение бот может только в первые 48 часов — так устроен
Bot API. Через 72 часа запись уже не удалить, поэтому бот её подменяет:
видео в том же сообщении становится картинкой «запись закрыта» (своё
сообщение бот правит в любой срок). Посмотреть запись больше нельзя —
ровно то, о чём просили; кнопки «Да»/«Нет» под ней уходят вместе с видео.

Править можно только сообщение, номер которого бот знает. С 29.09.2026 он
пишет их сам (db.day_messages), более ранние находит в переписках
bot/history.py. Запись, номера которой бот не знает, остаётся — баннер
приходит всё равно; найдётся номер позже — запись закроется следующим
кругом, без второго баннера.

Кому: четвёртый день пришёл 72 часа назад или раньше (заказчик 30.09.2026:
«после момента, как 4-й день пришёл, через 72 часа»; в /test срок сжат,
как весь марафон), «купить» после него не нажимал, бота не закрывал, не в
чёрном списке, не из команды проекта, закрытия ещё не было.

Заказчик 01.10.2026: закрываются только «4 видео длинных, про темы сфер
жизни, остальные короткие и текстовые остаются» — бот и помнит одни записи
дней; «отслеживалось постоянно и каждый час» — проверка раз в минуту;
баннер — «видео заставка последней версии» (media/banner.mp4) с кнопками
«Вступить в „Энергетический спортзал“» (рабочая покупка, как в конце
марафона) и «Сайт».

Включает закрытие админ — кнопкой в /баннер, посмотрев баннер на себе.
Сразу после включения баннер уходит всем, у кого 72 часа уже прошли, — а
таких после выкладки сотни, и без явного «да» писать им нельзя. Пока
закрытие выключено или баннера нет, никому ничего не уходит.
"""
import asyncio
import html
import logging
import os
import time

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile, InputMediaPhoto

from . import config, db, delivery, keyboards, texts

log = logging.getLogger(__name__)

AFTER = 72 * 3600           # через сколько после четвёртого дня закрывать
EVERY = 60                  # как часто проверять («каждый час» — с запасом)
PAUSE = 0.05                # между сообщениями: Telegram принимает ~30 в секунду
BANNER = 'banner'           # баннер в content: (video | animation | photo, file_id)
                            # или (file, версия) — ролик из репозитория, ещё не уходил
SWITCH = 'closing:on'       # включено ли закрытие: '1' — да
BROKEN_EVERY = 6 * 3600     # как часто напоминать админам, что баннер не уходит

CLOSED_DIR = os.path.join(config.ROOT, 'media', 'closed')

# Баннер по умолчанию — финальный ролик с Павлом (30.09.2026, promo-banner/):
# лежит в репозитории, загружать его админу не нужно. Обложка — кадр, где
# луч уже светит: первые пять секунд ролика тучи ещё закрыты.
DEFAULT = os.path.join(config.ROOT, 'media', 'banner.mp4')
DEFAULT_COVER = os.path.join(config.ROOT, 'media', 'banner_cover.jpg')
DEFAULT_META = {'width': 1080, 'height': 1440, 'duration': 14}
DEFAULT_MARK = 'banner:default'     # какая версия ролика уже стала баннером

# Ответы на правку, после которых закрывать в сообщении уже нечего: человек
# удалил его сам, или оно уже подменено (круг прервался после правки, но до
# отметки в базе).
_NOTHING_LEFT = ('message to edit not found', 'message is not modified',
                 "message can't be edited", 'message_id_invalid')
# Ответы на отправку баннера, которые говорят о человеке, а не о баннере.
_CHAT_GONE = ('chat not found', 'peer_id_invalid', 'user not found')

_broken_told = 0.0


class BannerBroken(Exception):
    u"""Telegram не принял баннер — он не уйдёт никому, пока его не заменят."""


def _default_version() -> str | None:
    u"""Версия ролика из репозитория — его размер, как у отзывов. None — ролика нет."""
    try:
        return str(os.path.getsize(DEFAULT))
    except OSError:
        return None


def _default_id() -> str | None:
    u"""file_id, под которым ролик этой версии уже ушёл, — или None."""
    known = db.get_content('%s:%s' % (DEFAULT_MARK, _default_version()))
    return known[1] if known else None


def banner() -> tuple[str, str] | None:
    u"""Баннер. Что пришло позже, то и уходит: новый ролик в репозитории (с
    выкладкой) или видео от админа с подписью banner.

    Ролик ставится баннером один раз на версию: видео от админа следующая
    выкладка не перетрёт — перетрёт только новый ролик.
    """
    version = _default_version()
    if version:
        seen = db.get_content(DEFAULT_MARK)
        if not seen or seen[1] != version:
            db.put_content(BANNER, 'file', version)
            db.put_content(DEFAULT_MARK, 'default', version)
    stored = db.get_content(BANNER)
    if stored and stored[0] == 'file' and stored[1] != version:
        return None             # ролик убрали из репозитория, так ни разу и не отправив
    return stored


def enabled() -> bool:
    known = db.get_content(SWITCH)
    return bool(known and known[1] == '1')


def switch(on: bool) -> None:
    db.put_content(SWITCH, 'closing', '1' if on else '')


def picture(day: int) -> str:
    u"""Картинка «запись закрыта» для дня (собирает tools/closed_covers.py)."""
    return os.path.join(CLOSED_DIR, 'day%d.jpg' % day)


def due(now: float | None = None) -> list[int]:
    u"""Кому пора закрыть записи. Команду проекта это не касается."""
    now = time.time() if now is None else now
    return [uid for uid in db.closing_candidates(now, AFTER) if not config.is_team(uid)]


def state(now: float | None = None) -> str:
    u"""Строка для /баннер: включено ли закрытие и сколько людей его ждёт."""
    count = len(due(now))
    if enabled():
        return texts.BANNER_STATE_ON.format(due=count, closed=db.stats()['closed'])
    return texts.BANNER_STATE_OFF.format(due=count)


def _said(err: Exception, marks: tuple) -> bool:
    text = str(err).lower()
    return any(mark in text for mark in marks)


# ------------------------------------------------------------- записи

async def _closed_photo(bot, day: int, send):
    u"""Картинка «запись закрыта»: по file_id, а в первый раз — файлом.

    send(media) — правка сообщения или отправка нового. Ключ кэша привязан
    к размеру файла, как у отзывов: подменили картинку в репозитории — и
    уходит новая, а не запомненный старый file_id.
    """
    path = picture(day)
    key = 'closed:day%d:%d' % (day, os.path.getsize(path))
    known = db.get_content(key)
    if known:
        try:
            return await send(known[1])
        except TelegramBadRequest as err:
            if _said(err, _NOTHING_LEFT):
                raise
            log.warning(u'file_id картинки «запись закрыта» не принят (%s) — шлём файлом', err)
    result = await send(FSInputFile(path))
    photo = getattr(result, 'photo', None)
    if photo:
        db.put_content(key, 'photo', photo[-1].file_id)
    return result


async def _close_one(bot, user_id: int, row: dict) -> None:
    caption = texts.DAY_CLOSED.format(day=row['day'])
    if row['kind'] == 'link':
        # Записи ссылкой уходили 2–5 сентября: текст со ссылкой → «закрыта».
        await delivery._guard(bot.edit_message_text(
            text=caption, chat_id=user_id, message_id=row['tg_id'], reply_markup=None))
        return

    async def edit(media):
        return await delivery._guard(bot.edit_message_media(
            media=InputMediaPhoto(media=media, caption=caption),
            chat_id=user_id, message_id=row['tg_id'], reply_markup=None))

    await _closed_photo(bot, row['day'], edit)


async def close_records(bot, user_id: int) -> int:
    u"""Закрыть записи, номера которых бот знает. Возвращает, сколько закрыто.

    Каждое сообщение отмечается сразу: круг, прерванный сбоем связи,
    продолжится с того же места. Сообщение, которого больше нет (человек
    удалил), или уже подменённое — тоже закрыто: смотреть там нечего.
    """
    closed = 0
    for row in db.open_day_messages(user_id):
        try:
            await _close_one(bot, user_id, row)
        except TelegramBadRequest as err:
            if not _said(err, _NOTHING_LEFT):
                raise
            log.info(u'запись дня %s у %s закрывать нечего: %s', row['day'], user_id, err)
        db.close_day_message(user_id, row['tg_id'])
        closed += 1
        await asyncio.sleep(PAUSE)
    return closed


# -------------------------------------------------------------- баннер

async def _send_default(bot, chat_id: int, stored, keys):
    u"""Ролик из репозитория — с обложкой-кадром, где луч уже светит.

    Ролик и обложка уходят файлом один раз, дальше — по file_id. Обложку по
    file_id Telegram не принял — шлём её файлом, как картинку «запись закрыта».
    """
    kind, value = stored
    video = FSInputFile(DEFAULT) if kind == 'file' else value
    has_cover = os.path.exists(DEFAULT_COVER)
    cover_key = 'banner:cover:%d' % os.path.getsize(DEFAULT_COVER) if has_cover else None
    known = db.get_content(cover_key) if cover_key else None

    async def send(cover):
        return await delivery._guard(bot.send_video(
            chat_id, video, caption=texts.BANNER_CAPTION, reply_markup=keys, cover=cover,
            supports_streaming=True, **DEFAULT_META))

    sent = None
    if known:
        try:
            sent = await send(known[1])
        except TelegramBadRequest as err:
            if _said(err, _CHAT_GONE):
                raise
            log.warning(u'file_id обложки баннера не принят (%s) — шлём файлом', err)
    if sent is None:
        sent = await send(FSInputFile(DEFAULT_COVER) if has_cover else None)
        covers = getattr(getattr(sent, 'video', None), 'cover', None)
        if covers and cover_key:
            db.put_content(cover_key, 'photo', covers[-1].file_id)

    file_id = getattr(getattr(sent, 'video', None), 'file_id', None)
    if kind == 'file' and file_id:
        db.put_content('%s:%s' % (DEFAULT_MARK, value), 'video', file_id)
        # Админ мог за это время прислать свой баннер — его не трогаем.
        if db.get_content(BANNER) == stored:
            db.put_content(BANNER, 'video', file_id)
    return sent


async def send_banner(bot, chat_id: int, stored, place: str = 'banner'):
    u"""Баннер с кнопками «вступить» и «сайт». BannerBroken — Telegram не
    принял сам баннер (а не человека): он не уйдёт и остальным."""
    kind, file_id = stored
    keys = keyboards.finish(place)
    try:
        if kind == 'photo':                      # баннер-картинка (30.09.2026)
            return await delivery._guard(bot.send_photo(
                chat_id, file_id, caption=texts.BANNER_CAPTION, reply_markup=keys))
        if kind == 'animation':
            return await delivery._guard(bot.send_animation(
                chat_id, file_id, caption=texts.BANNER_CAPTION, reply_markup=keys))
        if kind == 'file' or file_id == _default_id():
            return await _send_default(bot, chat_id, stored, keys)
        return await delivery._guard(bot.send_video(
            chat_id, file_id, caption=texts.BANNER_CAPTION, reply_markup=keys,
            supports_streaming=True))
    except TelegramBadRequest as err:
        if _said(err, _CHAT_GONE):
            raise delivery.Gone()
        raise BannerBroken(str(err)) from err


async def preview(bot, chat_id: int) -> bool:
    u"""Команде — как человек увидит закрытую запись и баннер. False — баннера нет.

    Кнопка «купить» здесь — просмотр: заявку менеджерам она не создаёт.
    """
    stored = banner()
    if not stored:
        return False
    await bot.send_message(chat_id, texts.BANNER_PREVIEW)
    await _closed_photo(bot, 1, lambda media: bot.send_photo(
        chat_id, media, caption=texts.DAY_CLOSED.format(day=1)))
    await send_banner(bot, chat_id, stored, 'preview')
    return True


def _to_chat(user_id: int, stored, sent) -> None:
    u"""Баннер — в переписку пульта: команда видит, что он ушёл."""
    kind, file_id = stored
    if kind == 'file':                   # ролик ушёл файлом — в переписку его file_id
        kind, file_id = 'video', getattr(getattr(sent, 'video', None), 'file_id', None)
    try:
        db.save_message(user_id, 'out', kind, texts.BANNER_CAPTION, file_id, None,
                        getattr(sent, 'message_id', None), mass=True)
    except Exception as err:
        log.warning(u'баннер %s: в переписку не записали: %s', user_id, err)


async def close_for(bot, user_id: int, stored) -> None:
    u"""Баннер — и закрыть записи.

    Баннер первым: не ушёл — у человека не изменилось ничего. Ушёл —
    закрытие отмечается сразу, а записи, которые не закрыл сбой связи,
    дозакроет следующий круг (closing_leftovers) без второго баннера.
    """
    sent = await send_banner(bot, user_id, stored)
    db.mark_closed(user_id)
    _to_chat(user_id, stored, sent)
    await close_records(bot, user_id)


async def _tell_broken(bot, err: Exception) -> None:
    global _broken_told
    now = time.time()
    if now - _broken_told < BROKEN_EVERY:
        return
    _broken_told = now
    await delivery.alert_admins(bot, texts.BANNER_BROKEN.format(why=html.escape(str(err))[:500]))


async def run_once(bot, now: float | None = None) -> int:
    u"""Закрыть созревшее. Возвращает, скольким ушёл баннер."""
    if not enabled():
        return 0
    stored = banner()
    if not stored:
        return 0
    done = 0
    for uid in due(now):
        # Ролик из репозитория уходит файлом только первому — дальше по file_id.
        stored = banner() or stored
        try:
            await close_for(bot, uid, stored)
            done += 1
            log.info(u'записи марафона закрыты, баннер ушёл %s', uid)
        except delivery.Gone:
            db.mark_blocked(uid)
            log.info(u'%s закрыл бота — закрытие записей подождёт его возвращения', uid)
        except BannerBroken as err:
            # Тот же баннер не примут и у остальных — ждём, пока его заменят.
            log.warning(u'баннер не принят: %s', err)
            await _tell_broken(bot, err)
            return done
        except TelegramRetryAfter as err:
            # Сразу после включения баннер уходит сотням людей, и Telegram
            # может попросить притормозить: продолжим следующим кругом.
            log.warning(u'Telegram просит подождать %s с — закрытие продолжится', err.retry_after)
            return done
        except Exception as err:
            log.warning(u'закрытие у %s не прошло (%s) — повторим следующим кругом', uid, err)
        await asyncio.sleep(PAUSE)

    for uid in db.closing_leftovers():
        if config.is_team(uid):
            continue
        try:
            await close_records(bot, uid)
        except delivery.Gone:
            db.mark_blocked(uid)
        except TelegramRetryAfter:
            break
        except Exception as err:
            log.warning(u'дозакрыть записи у %s не вышло (%s) — повторим', uid, err)
    return done


async def loop(bot) -> None:
    u"""Раз в минуту — созревшее (заказчик 01.10.2026: «отслеживалось постоянно
    и каждый час»). Сбой одного круга не останавливает."""
    while True:
        try:
            await run_once(bot)
        except Exception:
            log.exception(u'сбой закрытия записей')
        await asyncio.sleep(EVERY)
