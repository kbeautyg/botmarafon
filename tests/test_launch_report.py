# -*- coding: utf-8 -*-
u"""Запуск бота по ссылке с сайта уходит сайту — для рекламы Meta.

Таргетолог 15.09.2026: реклама учится на клике, а надо — на запуске.
Сайт дописывает код клика в ссылку (site_fb--k3v9x0a1b2c4), бот отдаёт его
сайту после первого запуска.
"""
import asyncio
import os
import sys

import aiohttp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, launch_report                                 # noqa: E402
from bot.handlers import start                                            # noqa: E402
from tests.fakes import FakeBot, FakeMessage, FakeUser                    # noqa: E402

CODE = 'k3v9x0a1b2c4'


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'launch.db'))
    monkeypatch.setattr(config, 'ENTRY_CHAT_ID', 0)
    monkeypatch.setattr(config, 'PURCHASE_CHAT_ID', 0)
    monkeypatch.setattr(config, 'TEAM_PURCHASE_IDS', ())
    monkeypatch.setattr(config, 'SITE_BOT_START_URL', 'https://site.test/api/bot-start')
    start._launching.clear()
    yield


@pytest.fixture
def отчёты(monkeypatch):
    sent = []
    monkeypatch.setattr(launch_report, 'report', lambda code: sent.append(code))
    return sent


# ------------------------------------------------------------ разбор ссылки

def test_код_отрезается_от_источника():
    assert launch_report.split('site_fb--' + CODE) == ('site_fb', CODE)
    assert launch_report.split('site--' + CODE) == ('site', CODE)
    assert launch_report.split('site_popup_ig--' + CODE) == ('site_popup_ig', CODE)


def test_без_кода_и_с_чужим_хвостом_ссылка_как_была():
    assert launch_report.split('site_fb') == ('site_fb', '')
    assert launch_report.split('zayavka57') == ('zayavka57', '')
    assert launch_report.split('site--short') == ('site--short', '')
    assert launch_report.split('') == ('', '')


# ------------------------------------------------------------ /start

async def test_первый_запуск_с_сайта_уходит_сайту_источник_прежний(отчёты):
    await start.on_start(FakeMessage(text='/start site_fb--' + CODE, user=FakeUser(1), bot=FakeBot()))
    assert отчёты == [CODE]
    assert db.get_user(1)['source'] == 'site_fb'


async def test_повторный_start_второго_запуска_не_даёт(отчёты):
    bot = FakeBot()
    await start.on_start(FakeMessage(text='/start site_fb--' + CODE, user=FakeUser(1), bot=bot))
    await start.on_start(FakeMessage(text='/start site_fb--aaaaaaaaaaaa', user=FakeUser(1), bot=bot))
    assert отчёты == [CODE]


async def test_запуск_не_с_сайта_ничего_не_шлёт(monkeypatch):
    called = []

    async def post(url, token):
        called.append(token)
        return 204

    monkeypatch.setattr(launch_report, '_post', post)
    await start.on_start(FakeMessage(text='/start ig', user=FakeUser(2), bot=FakeBot()))
    await asyncio.sleep(0)
    assert called == []


async def test_отчёт_уходит_в_фоне_и_доходит(monkeypatch):
    got = []

    async def post(url, token):
        got.append((url, token))
        return 204

    monkeypatch.setattr(launch_report, '_post', post)
    await start.on_start(FakeMessage(text='/start site--' + CODE, user=FakeUser(3), bot=FakeBot()))
    await asyncio.gather(*list(launch_report._pending))
    assert got == [('https://site.test/api/bot-start', CODE)]


# ------------------------------------------------------------ доставка

async def _no_sleep(_):
    return None


async def test_сайт_моргнул_повторяем_и_доставляем():
    answers = [aiohttp.ClientConnectionError('нет связи'), 502, 204]

    async def post(url, token):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    assert await launch_report.send(CODE, post=post, sleep=_no_sleep) is True
    assert answers == []


async def test_отказ_сайта_не_повторяем():
    calls = []

    async def post(url, token):
        calls.append(token)
        return 400

    assert await launch_report.send(CODE, post=post, sleep=_no_sleep) is False
    assert len(calls) == 1


async def test_сайт_лежит_попытки_кончаются_без_падения():
    calls = []

    async def post(url, token):
        calls.append(token)
        raise asyncio.TimeoutError()

    assert await launch_report.send(CODE, post=post, sleep=_no_sleep) is False
    assert len(calls) == len(launch_report.PAUSES)


async def test_адрес_не_задан_выключено(monkeypatch):
    monkeypatch.setattr(config, 'SITE_BOT_START_URL', '')
    launch_report.report(CODE)
    assert not launch_report._pending
