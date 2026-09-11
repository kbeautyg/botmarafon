# -*- coding: utf-8 -*-
u"""Админка: загрузка записей дней, проверка готовности, тестовый прогон.

Записи дней марафона весят гигабайты, и заливать их боту файлом с диска
нельзя — у ботов свой предел на отправку. Поэтому запись один раз кидают
самому боту из телеграма: он запоминает file_id и потом рассылает его
мгновенно, без перезаливки и без ограничения по размеру.
"""
import asyncio
import html
import logging
import os
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import (backup, config, db, delivery, funnel, insights, keyboards, scheduler,
                stats, texts)

log = logging.getLogger(__name__)
router = Router(name='admin')

DAY_TAG = re.compile(r'^day([1-4])\b', re.I)
REVIEW_TAG = re.compile(r'^review([1-9])\b', re.I)
LINK = re.compile(r'https?://\S+')

# Во сколько раз ускорить паузы в тестовом прогоне: два с половиной часа
# превращаются в две с половиной минуты, все четыре дня — минут в десять.
TEST_SPEED = 1.0 / 60


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


STATS_COMMANDS = ('stats', 'who', 'кто', 'links', 'ссылки', 'status')


def _command(message: Message) -> str:
    u"""Имя команды без слэша и без @бота: «/stats@finish_marafon_bot 5» → stats."""
    text = (message.text or '').strip()
    if not text.startswith('/'):
        return ''
    return text.split(None, 1)[0][1:].split('@', 1)[0].lower()


def _can_stats(message: Message) -> bool:
    return bool(message.from_user) and config.can_stats(message.from_user.id, message.chat.id)


def from_admin(message: Message) -> bool:
    u"""Пропускать в этот роутер админов — и команды статистики от тех, кому
    она разрешена (config.can_stats).

    Фильтр обязателен, а не для красоты. Обработчики ниже ловят любое видео
    и любое фото — без фильтра сообщение обычного человека попадало бы сюда,
    молча отсеивалось по _is_admin и до службы заботы уже не доходило:
    aiogram останавливает разбор на первом подошедшем роутере.

    Проверяем функцией, а не F.from_user.id.in_(...): список админов должен
    читаться на каждом сообщении, иначе он застынет на моменте импорта.
    """
    if not message.from_user:
        return False
    if _is_admin(message.from_user.id):
        return True
    return _command(message) in STATS_COMMANDS and _can_stats(message)


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

    caption = (message.caption or '').strip()
    review = REVIEW_TAG.match(caption)
    if review and (message.video or message.document):
        # Видеоотзыв в ленту: подменяет шаг reviewN (см. funnel.REVIEW_SEQUENCE).
        db.put_content('review%s' % review.group(1), 'video',
                       (message.video or message.document).file_id)
        await backup.save(message.bot)
        await message.reply(u'Отзыв %s будет уходить этим видео ✅' % review.group(1))
        return

    tag = DAY_TAG.match(caption)
    waiting = db.get_content('await:%d' % message.from_user.id)
    if not tag and not (waiting and waiting[1]):
        return

    day = int(tag.group(1)) if tag else int(waiting[1])
    db.put_content('await:%d' % message.from_user.id, 'await', '')
    media = message.video or message.document or message.video_note
    db.put_content('day%d' % day, 'video', media.file_id)
    await backup.save(message.bot)
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
    await backup.save(message.bot)
    await message.reply(u'Отзыв %d будет уходить картинкой ✅' % index)


@router.message(F.text.regexp(r'(?i)^day[1-4]\s+https?://'))
async def on_link(message: Message):
    u"""Ссылка на запись — если дни лежат не в телеграме, а на видеосервисе."""
    if not _is_admin(message.from_user.id):
        return
    day = int(DAY_TAG.match(message.text.strip()).group(1))
    url = LINK.search(message.text).group(0)
    db.put_content('day%d' % day, 'link', url)
    await backup.save(message.bot)
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

    on_disk = [name for name in funnel.REVIEW_SEQUENCE if delivery.review_path(name)]
    replaced = sum(1 for i in range(1, funnel.REVIEW_COUNT + 1)
                   if db.get_content('review%d' % i))
    missing = [name for name in funnel.REVIEW_SEQUENCE if name not in on_disk]
    lines.append(u'• Отзывы на диске: %d из %d%s%s' % (
        len(on_disk), funnel.REVIEW_COUNT,
        u', нет: %s' % u', '.join(missing) if missing else u'',
        u'; подменено загрузкой: %d' % replaced if replaced else u''))

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
    if not _can_stats(message):
        return
    counters = db.stats()
    lines = _content_report()
    lines.append(u'')
    lines.append(u'Людей в боте: %d, запустили марафон: %d'
                 % (counters['users'], counters['launched']))
    lines.append(u'Шагов в очереди: %d · нажали «купить»: %d'
                 % (counters['jobs'], counters['purchases']))
    # Кого бот реально видит в доступе: правка переменных в Railway не
    # действует, пока её не применили деплоем, — отсюда это видно сразу.
    listed = lambda ids: u', '.join(str(i) for i in ids) or u'—'
    lines.append(u'')
    lines.append(u'Доступ — админы: %s' % listed(config.ADMIN_IDS))
    lines.append(u'Доступ — статистика (STATS_IDS): %s' % listed(config.STATS_IDS))
    if config.IDS_SKIPPED:
        lines.append(u'⚠️ В ADMIN_IDS/STATS_IDS бот не понял: %s — нужны числовые id '
                     u'через запятую' % html.escape(u', '.join(config.IDS_SKIPPED)))
    await message.answer(u'\n'.join(lines))


@router.message(Command('stats'))
async def on_stats(message: Message, command: CommandObject | None = None):
    u"""Подробная статистика разделами с кнопками; «/stats csv» — таблица.

    Sharp 11.09.2026: «статистика маленькая, сильно расширь». Разделы и
    расчёты — bot/insights.py.
    """
    if not _can_stats(message):
        return
    arg = ((command.args if command else '') or '').strip().lower()
    if arg in ('csv', 'таблица', 'выгрузка', 'excel'):
        await _send_csv(message.bot, message.chat.id)
        return
    await message.answer(insights.render('sum', '7'),
                         reply_markup=keyboards.stats_menu('sum', '7'))


async def _send_csv(bot, chat_id: int) -> None:
    u"""Все люди таблицей: откуда пришли, докуда дошли, ответы, покупки."""
    name = 'marathon-%s.csv' % datetime.now(insights.MSK).strftime('%Y-%m-%d')
    await bot.send_document(chat_id, BufferedInputFile(insights.csv_bytes(), filename=name),
                            caption=texts.STATS_CSV_CAPTION)


@router.callback_query(F.data.startswith('st:'))
async def on_stats_button(call: CallbackQuery):
    u"""Кнопки разделов и периодов под /stats — правят то же сообщение."""
    chat_id = call.message.chat.id if call.message else None
    if not config.can_stats(call.from_user.id, chat_id):
        await call.answer(u'Нет доступа')
        return
    parts = (call.data.split(':') + ['', ''])[:3]
    section = parts[1] if parts[1] in insights.SECTIONS or parts[1] == 'csv' else 'sum'
    period = parts[2] if parts[2] in insights.PERIODS else '7'
    if section == 'csv':
        await call.answer(u'Готовлю таблицу…')
        await _send_csv(call.bot, chat_id)
        return
    try:
        await call.message.edit_text(insights.render(section, period),
                                     reply_markup=keyboards.stats_menu(section, period))
    except Exception as err:                       # нажали ту же кнопку — текст не менялся
        if 'not modified' not in str(err):
            log.warning(u'раздел статистики %s не показали: %s', section, err)
    await call.answer()


# Команды по-русски: заказчик набирает их с телефона и латиницу не ищет.
@router.message(Command('who', 'кто'))
async def on_who(message: Message, command: CommandObject):
    u"""Поимённо, кто заходил в бота и по какой ссылке."""
    if not _can_stats(message):
        return
    asked = (command.args or '').strip()
    limit = int(asked) if asked.isdigit() and 0 < int(asked) <= 100 else 30
    for chunk in stats.who_messages(limit):
        await message.answer(chunk)


@router.message(Command('links', 'ссылки'))
async def on_links(message: Message):
    u"""Готовые ссылки с метками — вставить в рассылку."""
    if not _can_stats(message):
        return
    me = await message.bot.get_me()
    await message.answer(stats.links_report(me.username))


@router.message(Command('resend'))
async def on_resend(message: Message):
    u"""Дослать запись дня тем, кому день ушёл без неё.

    Тревога «человеку ушёл только текст» просила «повторить отправку», а
    повторять было нечем: 2–3 сентября записи так и не дошли до людей.
    Теперь бот помнит, кому день ушёл пустым (таблица missed), и по
    /resend N шлёт им запись — только запись, текст дня они читали.

    /resend N всем — запасной ход, когда пометок нет (база пересоздана):
    запись уходит всем, кто этот день уже прошёл, судя по очереди.
    """
    if not _is_admin(message.from_user.id):
        return
    words = (message.text or '').split()
    days = [int(w) for w in words[1:] if w in ('1', '2', '3', '4')]
    if not days:
        await message.answer(u'Какой день? Например: /resend 1')
        return
    day = days[0]
    if not (db.get_content('day%d' % day) or config.DAY_ENV.get(day)):
        await message.answer(u'Запись дня %d ещё не задана — сначала пришлите '
                             u'видео или ссылку (day%d https://…).' % (day, day))
        return

    everyone = any(w.lower() in (u'всем', 'all') for w in words[1:])
    if everyone:
        targets = [uid for uid in db.launched_users()
                   if funnel.day_delivered(db.pending_chains(uid), day)]
    else:
        targets = db.missed_users(day)
    if not targets:
        await message.answer(u'День %d досылать некому: пометок «ушёл без записи» '
                             u'нет. Всем, кто день уже прошёл: /resend %d всем' % (day, day))
        return

    sent = gone = failed = 0
    for uid in targets:
        try:
            if await delivery.resend_day(message.bot, uid, day):
                sent += 1
        except delivery.Gone:
            gone += 1
            db.clear_missed(uid, day)
        except Exception as err:                       # одного не доставили — идём дальше
            failed += 1
            log.warning(u'не дослали день %d человеку %s: %s', day, uid, err)
        await asyncio.sleep(0.05)                      # лимит телеграма ~30 сообщений/с
    await message.answer(u'День %d дослал: %d чел.%s%s'
                         % (day, sent,
                            u', закрыли бота: %d' % gone if gone else u'',
                            u', не доставлено: %d' % failed if failed else u''))
    log.info(u'админ %s дослал день %d: %d/%d', message.from_user.id, day, sent, len(targets))


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
