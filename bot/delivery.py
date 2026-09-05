# -*- coding: utf-8 -*-
u"""Отправка шагов воронки.

Здесь же кэш file_id. Кружок весит 5-7 МБ; заливать его заново каждому
человеку — это мегабайты и секунды на ровном месте. Телеграм после первой
отправки возвращает file_id, дальше файл уходит по нему мгновенно, и
хранить этот id надо в базе, иначе он теряется на перезапуске.
"""
import logging
import os

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Bot
from aiogram.types import FSInputFile, Message

from . import config, db, funnel, keyboards, texts

log = logging.getLogger(__name__)


class Gone(Exception):
    u"""Человек закрыл бота — вести его дальше некуда."""


async def _guard(coro):
    u"""Отправить и отличить «закрыл бота» от прочих бед."""
    try:
        return await coro
    except TelegramForbiddenError:
        raise Gone()


async def send_circle(bot: Bot, user_id: int, name: str) -> Message | None:
    u"""Кружок по имени. Нет файла — шаг молча пропускается.

    Пропуск нужен для ask_day3: этот кружок заказчик ещё не прислал, и
    без него опросник должен уйти текстом, а не падать.
    """
    key = 'circle:%s' % name
    known = db.get_content(key)
    if known:
        try:
            return await _guard(bot.send_video_note(user_id, known[1]))
        except TelegramBadRequest:
            # file_id мог протухнуть (редко, но бывает) — зальём заново.
            log.warning(u'file_id кружка %s не принят, шлём файлом', name)

    path = os.path.join(config.CIRCLES_DIR, name + '.mp4')
    if not os.path.exists(path):
        log.warning(u'нет кружка %s — шаг пропущен', name)
        return None

    msg = await _guard(bot.send_video_note(user_id, FSInputFile(path)))
    if msg and msg.video_note:
        db.put_content(key, 'circle', msg.video_note.file_id)
    return msg


# Файл отзыва → как его слать. Часть видеоотзывов заказчика — кружки,
# экспорт видеосообщений из Telegram (круг в кадре 9:16); они лежат как
# vidN.note.mp4, вырезанные в квадрат, и уходят обратно видеосообщением.
REVIEW_KINDS = (('.note.mp4', 'circle'), ('.mp4', 'video'), ('.jpg', 'photo'), ('.png', 'photo'))


def review_file(name: str) -> tuple[str, str] | None:
    u"""(вид, путь) файла отзыва в media/reviews или None, если его ещё нет."""
    for ext, kind in REVIEW_KINDS:
        path = os.path.join(config.REVIEWS_DIR, name + ext)
        if os.path.exists(path):
            return kind, path
    return None


def review_path(name: str) -> str | None:
    found = review_file(name)
    return found[1] if found else None


async def _send_media(bot: Bot, user_id: int, kind: str, media) -> Message | None:
    if kind == 'circle':
        return await _guard(bot.send_video_note(user_id, media))
    if kind == 'video':
        return await _guard(bot.send_video(user_id, media))
    return await _guard(bot.send_photo(user_id, media))


def _file_id(msg: Message | None, kind: str) -> str | None:
    if msg is None:
        return None
    if kind == 'circle' and getattr(msg, 'video_note', None):
        return msg.video_note.file_id
    if kind == 'video' and getattr(msg, 'video', None):
        return msg.video.file_id
    if kind == 'photo' and getattr(msg, 'photo', None):
        return msg.photo[-1].file_id
    return None


async def send_review(bot: Bot, user_id: int, name: str) -> Message | None:
    u"""Отзыв из ленты: картинка-сторис или видеоотзыв.

    Заказчик 03.09.2026: «картинка, видеоотзыв, картинка, видеоотзыв…
    четыре картинки и пять видеоотзывов, а не просто Евгения и текст».
    Файлы лежат в media/reviews (img1-4, vid1-5); после первой отправки
    file_id кэшируется, как у кружков. Админ может подменить любой шаг
    фото или видео с подписью reviewN. Нет файла — шаг молча пропускается:
    видеоотзывы заказчик присылает отдельно.
    """
    index = funnel.REVIEW_SEQUENCE.index(name) + 1
    override = db.get_content('review%d' % index)
    if override:
        return await _send_media(bot, user_id, override[0], override[1])

    cached = db.get_content('review:%s' % name)
    if cached:
        try:
            return await _send_media(bot, user_id, cached[0], cached[1])
        except TelegramBadRequest:
            log.warning(u'file_id отзыва %s не принят, шлём файлом', name)

    found = review_file(name)
    if not found:
        log.warning(u'нет отзыва %s — шаг пропущен', name)
        return None
    kind, path = found
    msg = await _send_media(bot, user_id, kind, FSInputFile(path))
    file_id = _file_id(msg, kind)
    if file_id:
        db.put_content('review:%s' % name, kind, file_id)
    return msg


def _day_from_env(day: int):
    u"""Запись дня из переменной DAYn: ссылка, если начинается с http, иначе file_id."""
    value = config.DAY_ENV.get(day, '')
    if not value:
        return None
    return ('link', value) if value.lower().startswith('http') else ('video', value)


async def send_day(bot: Bot, user_id: int, day: int,
                   admins_alert: bool = True) -> Message | None:
    u"""Запись дня и текст под ней.

    Запись задаёт админ: видео (file_id) или ссылка. Если её нет, человек
    получает текст и обещание, а админы — тревогу: тихо отдать пустой день
    хуже всего, об этом узнаешь только от заказчика.
    """
    text = texts.DAY_TEXTS[day]
    stored = db.get_content('day%d' % day) or _day_from_env(day)

    if stored and stored[0] == 'video':
        return await _guard(bot.send_video(user_id, stored[1], caption=text))
    if stored and stored[0] == 'link':
        return await _guard(bot.send_message(user_id, u'%s\n\n%s' % (stored[1], text)))

    await _guard(bot.send_message(user_id, u'%s\n\n%s' % (text, texts.DAY_MISSING_USER)))
    db.mark_missed(user_id, day)
    if admins_alert:
        await alert_admins(bot, texts.DAY_MISSING_ADMIN.format(day=day))
    return None


async def resend_day(bot: Bot, user_id: int, day: int) -> bool:
    u"""Дослать запись тому, кому день ушёл без неё (/resend).

    Только запись с короткой подводкой: текст дня человек уже читал.
    False — записи всё ещё нет, слать нечего.
    """
    stored = db.get_content('day%d' % day) or _day_from_env(day)
    if not stored:
        return False
    lead = texts.DAY_RESEND.format(day=day)
    if stored[0] == 'video':
        await _guard(bot.send_video(user_id, stored[1], caption=lead))
    else:
        await _guard(bot.send_message(user_id, u'%s\n\n%s' % (lead, stored[1])))
    db.clear_missed(user_id, day)
    return True


async def send_poll(bot: Bot, user_id: int, name: str) -> Message | None:
    db.set_poll(user_id, name)
    return await _guard(bot.send_message(user_id, texts.POLL_QUESTIONS[name],
                                         reply_markup=keyboards.poll(name)))


async def send_offer(bot: Bot, user_id: int) -> Message | None:
    return await _guard(bot.send_message(user_id, texts.OFFER_PROMPT,
                                         reply_markup=keyboards.offer()))


async def alert_admins(bot: Bot, text: str) -> None:
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, text)
        except Exception as err:                      # админ мог не начать чат
            log.warning(u'не доставили админу %s: %s', admin, err)


HANDLERS = {
    'circle': lambda bot, uid, ref: send_circle(bot, uid, ref),
    'review': lambda bot, uid, ref: send_review(bot, uid, ref),
    'day': lambda bot, uid, ref: send_day(bot, uid, ref),
    'poll': lambda bot, uid, ref: send_poll(bot, uid, ref),
    'offer': lambda bot, uid, ref: send_offer(bot, uid),
}


async def perform(bot: Bot, user_id: int, step) -> Message | None:
    u"""Выполнить один шаг сценария."""
    handler = HANDLERS.get(step.kind)
    if not handler:
        log.error(u'неизвестный шаг: %s', step.kind)
        return None
    return await handler(bot, user_id, step.ref)
