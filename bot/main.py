# -*- coding: utf-8 -*-
u"""Точка входа."""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import backup, config, db, delivery, handlers, scheduler, texts

log = logging.getLogger('marathon')


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-7s %(name)s | %(message)s',
        stream=sys.stdout)
    logging.getLogger('aiogram.event').setLevel(logging.WARNING)


async def run():
    missing = config.check()
    if missing:
        # Молча стартовать нельзя: бот без чата покупок потеряет первую же
        # заявку, и заметит это только заказчик.
        raise SystemExit(u'Не заданы настройки:\n  ' + u'\n  '.join(missing))

    db.connect(config.DB_PATH)

    bot = Bot(config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    for router in handlers.ROUTERS:
        dispatcher.include_router(router)

    me = await bot.get_me()
    log.info(u'бот @%s запущен, база %s', me.username, config.DB_PATH)
    # Приветствие заказчика — на пустом экране до кнопки «Старт», а не
    # сообщением после неё (03.09.2026). Не вышло — не беда, бот работает.
    try:
        await bot.set_my_description(description=texts.WELCOME)
        await bot.set_my_short_description(short_description=texts.SHORT_DESCRIPTION)
    except Exception as err:
        log.warning(u'описание бота не обновили: %s', err)
    restored = await backup.restore(bot)
    if restored:
        log.info(u'из закрепа у админа восстановлено записей: %d', restored)
    if config.on_railway() and not config.db_persistent():
        # Молчать нельзя: узнаем о потере базы от заказчика, как 2 сентября.
        log.warning(u'база %s не на диске — пропадёт при деплое', config.DB_PATH)
        await delivery.alert_admins(bot, texts.DB_EPHEMERAL_ADMIN)

    # Планировщик живёт рядом с опросом обновлений: пауза в два с половиной
    # часа никого не держит, состояние очереди целиком в базе.
    worker = asyncio.create_task(scheduler.loop(bot))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        worker.cancel()
        await bot.session.close()


def main():
    _setup_logging()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit) as stop:
        if str(stop):
            print(stop)


if __name__ == '__main__':
    main()
