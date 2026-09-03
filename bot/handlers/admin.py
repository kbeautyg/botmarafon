# -*- coding: utf-8 -*-
u"""Админка: загрузка записей дней, проверка готовности, тестовый прогон.

Записи дней марафона весят гигабайты, и заливать их боту файлом с диска
нельзя — у ботов свой предел на отправку. Поэтому запись один раз кидают
самому боту из телеграма: он запоминает file_id и потом рассылает его
мгновенно, без перезаливки и без ограничения по размеру.
"""
import logging
import os
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .. import config, db, funnel, scheduler, texts

log = logging.getLogger(__name__)
router = Router(name='admin')

DAY_TAG = re.compile(r'^day([1-4])\b', re.I)
REVIEW_TAG = re.compile(r'^review([1-8])\b', re.I)
LINK = re.compile(r'https?://\S+')

# Во сколько раз ускорить паузы в тестовом прогоне: два с половиной часа
# превращаются в две с половиной минуты, все четыре дня — минут в десять.
TEST_SPEED = 1.0 / 60


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def from_admin(message: Message) -> bool:
    u"""Пропускать в этот роутер только админов.

    Фильтр обязателен, а не для красоты. Обработчики ниже ловят любое видео
    и любое фото — без фильтра сообщение обычного человека попадало бы сюда,
    молча отсеивалось по _is_admin и до службы заботы уже не доходило:
    aiogram останавливает разбор на первом подошедшем роутере.

    Проверяем функцией, а не F.from_user.id.in_(...): список админов должен
    читаться на каждом сообщении, иначе он застынет на моменте импорта.
    """
    return bool(message.from_user) and _is_admin(message.from_user.id)


router.message.filter(from_admin)


@router.message(Command('help', 'admin'))
async def on_help(message: Message):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(texts.ADMIN_HELP)


@router.message(Command('day1', 'day2', 'day3', 'day4'))
async def on_day_command(message: Message, command: CommandObject):
    u"""Ждать запись дня следующим сообщением.

    Подпись к видео работает, только когда файл отправляют заново. Гораздо
    чаще запись уже лежит в каком-то чате и её пересылают — а у пересылки
    подписи нет. Тогда сначала команда, потом сама пересылка.
    """
    day = int(command.command[-1])
    db.put_content('await:%d' % message.from_user.id, 'await', str(day))
    await message.answer(u'Жду запись дня %d — пришлите или перешлите видео '
                         u'следующим сообщением.' % day)


@router.message(F.video | F.document | F.video_note)
async def on_video(message: Message):
    u"""Запись дня: видео с подписью day1…day4 либо после команды /day1."""
    if not _is_admin(message.from_user.id):
        return

    tag = DAY_TAG.match((message.caption or '').strip())
    waiting = db.get_content('await:%d' % message.from_user.id)
    if not tag and not (waiting and waiting[1]):
        return

    day = int(tag.group(1)) if tag else int(waiting[1])
    db.put_content('await:%d' % message.from_user.id, 'await', '')
    media = message.video or message.document or message.video_note
    db.put_content('day%d' % day, 'video', media.file_id)
    note = u'Записал день %d ✅ Теперь он уходит людям.' % day
    if config.on_railway() and not config.db_persistent():
        note += (u'\n\nБаза не на диске — чтобы запись пережила деплой, добавьте '
                 u'в Railway переменную <code>DAY%d</code> со значением:\n'
                 u'<code>%s</code>' % (day, media.file_id))
    await message.reply(note)
    log.info(u'админ %s задал день %d', message.from_user.id, day)


@router.message(F.photo)
async def on_photo(message: Message):
    u"""Фото с подписью review1…review8 — отзыв картинкой вместо текста."""
    if not _is_admin(message.from_user.id):
        return
    tag = REVIEW_TAG.match((message.caption or '').strip())
    if not tag:
        return

    index = int(tag.group(1))
    db.put_content('review%d' % index, 'photo', message.photo[-1].file_id)
    await message.reply(u'Отзыв %d будет уходить картинкой ✅' % index)


@router.message(F.text.regexp(r'(?i)^day[1-4]\s+https?://'))
async def on_link(message: Message):
    u"""Ссылка на запись — если дни лежат не в телеграме, а на видеосервисе."""
    if not _is_admin(message.from_user.id):
        return
    day = int(DAY_TAG.match(message.text.strip()).group(1))
    url = LINK.search(message.text).group(0)
    db.put_content('day%d' % day, 'link', url)
    await message.reply(u'День %d будет уходить ссылкой ✅' % day)


def _content_report() -> list[str]:
    u"""Строчки про каждый день, отзывы и кружки."""
    lines = []
    for day in (1, 2, 3, 4):
        stored = db.get_content('day%d' % day)
        if not stored and config.DAY_ENV.get(day):
            lines.append(u'• День %d — из переменной DAY%d' % (day, day))
        elif not stored:
            lines.append(u'• День %d — <b>НЕ ЗАДАН</b>' % day)
        elif stored[0] == 'link':
            lines.append(u'• День %d — ссылка' % day)
        else:
            lines.append(u'• День %d — видео ✅' % day)

    pictured = sum(1 for i in range(1, 9) if db.get_content('review%d' % i))
    lines.append(u'• Отзывы: %d картинкой, остальные текстом' % pictured)

    wanted = {step.ref for chain in funnel.CHAINS.values()
              for step in chain.steps if step.kind == 'circle'}
    missing = sorted(name for name in wanted
                     if not os.path.exists(os.path.join(config.CIRCLES_DIR, name + '.mp4')))
    if missing:
        lines.append(u'• Нет кружков: %s' % u', '.join(missing))
    else:
        lines.append(u'• Кружки на месте: %d' % len(wanted))

    if config.on_railway():
        lines.append(u'• База на диске ✅' if config.db_persistent() else
                     u'• База <b>НЕ НА ДИСКЕ</b> — пропадёт при деплое '
                     u'(Settings → Volumes → Add Volume, mount path /data)')
    return lines


@router.message(Command('status'))
async def on_status(message: Message):
    if not _is_admin(message.from_user.id):
        return
    counters = db.stats()
    lines = _content_report()
    lines.append(u'')
    lines.append(u'Людей в боте: %d, запустили марафон: %d'
                 % (counters['users'], counters['launched']))
    lines.append(u'Шагов в очереди: %d · заявок на покупку: %d'
                 % (counters['jobs'], counters['purchases']))
    await message.answer(u'\n'.join(lines))


@router.message(Command('test'))
async def on_test(message: Message):
    u"""Прогнать всю воронку на себе, сжав паузы в шестьдесят раз."""
    if not _is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    # Админ мог ни разу не нажать /start, и строки в users для него нет.
    # Тогда все UPDATE ниже пройдут вхолостую, паузы останутся боевыми,
    # и «тестовый» прогон растянется на семь с половиной часов.
    db.remember_user(user_id, message.from_user.username, message.from_user.first_name)
    db.reset_funnel(user_id)
    db.set_speed(user_id, TEST_SPEED)
    db.mark_launched(user_id)
    scheduler.start_chain(user_id, 'launch')
    await message.answer(u'Тестовый прогон пошёл: паузы сжаты в 60 раз, '
                         u'все четыре дня займут около десяти минут.\n'
                         u'Вернуть боевые сроки для себя — /live')


@router.message(Command('live'))
async def on_live(message: Message):
    if not _is_admin(message.from_user.id):
        return
    db.remember_user(message.from_user.id, message.from_user.username,
                     message.from_user.first_name)
    db.set_speed(message.from_user.id, 1.0)
    db.reset_funnel(message.from_user.id)
    await message.answer(u'Боевые сроки вернул, свою очередь очистил.')
