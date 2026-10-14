# -*- coding: utf-8 -*-
u"""Панель владельца (вторая админка): вход по паролю, выгрузка, очистка.

Главное, как и у пульта, — дверь: панель на публичном адресе, и внутрь
пускаем только по паролю, с замком от перебора. Плюс проверяем, что
выгрузка кода не выносит секреты, а очистка требует подтверждения.
"""
import io
import os
import sys
import zipfile

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, owner, web                                    # noqa: E402
from tests.fakes import FakeBot                                           # noqa: E402

TOKEN = '123456:TESTTOKEN'
PW = 'fivedecember'


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'owner.db'))
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'OWNER_PASSWORD', PW)
    monkeypatch.setattr(config, 'WEBAPP_URL', '')      # локально по http
    media = tmp_path / 'media'                          # не настоящие медиа проекта
    (media / 'circles').mkdir(parents=True)
    (media / 'circles' / 'welcome.mp4').write_bytes(b'x')
    monkeypatch.setattr(owner, 'MEDIA_DIR', str(media))
    web._hits.clear(); web._window_from[0] = 0.0
    owner._login_hits.clear(); owner._login_from[0] = 0.0
    yield


@pytest.fixture
async def панель(база):
    client = TestClient(TestServer(web.build(FakeBot())))
    await client.start_server()
    yield client
    await client.close()


async def _войти(client, password=PW):
    return await client.post('/owner/api/login', json={'password': password})


# ------------------------------------------------------------------ дверь

async def test_без_пароля_панель_выключена(панель, monkeypatch):
    monkeypatch.setattr(config, 'OWNER_PASSWORD', '')
    assert (await панель.get('/owner')).status == 404
    assert (await панель.get('/owner/api/overview')).status == 404
    assert (await _войти(панель)).status == 404


async def test_страница_открывается(панель):
    r = await панель.get('/owner')
    assert r.status == 200
    assert 'Панель владельца' in await r.text()


async def test_неверный_пароль_не_пускает(панель):
    r = await _войти(панель, 'wrong')
    assert r.status == 403
    # без входа сводка закрыта
    assert (await панель.get('/owner/api/overview')).status == 401


async def test_верный_пароль_открывает_сводку(панель):
    assert (await _войти(панель)).status == 200      # cookie осталась у клиента
    r = await панель.get('/owner/api/overview')
    assert r.status == 200
    data = await r.json()
    assert 'stats' in data and 'users' in data['tables']


async def test_замок_после_серии_попыток(панель):
    for _ in range(owner.LOGIN_TRIES):
        assert (await _войти(панель, 'bad')).status == 403
    # дальше — замок, даже верный пароль не примут до конца окна
    locked = await _войти(панель)
    assert locked.status == 429


async def test_выход_закрывает_доступ(панель):
    await _войти(панель)
    assert (await панель.get('/owner/api/overview')).status == 200
    await панель.post('/owner/api/logout')
    assert (await панель.get('/owner/api/overview')).status == 401


# ------------------------------------------------------------------ выгрузка

async def test_выгрузка_кода_без_секретов(панель, tmp_path):
    await _войти(панель)
    r = await панель.get('/owner/api/export/project')
    assert r.status == 200
    assert r.headers['Content-Type'] == 'application/zip'
    names = zipfile.ZipFile(io.BytesIO(await r.read())).namelist()
    assert any(n.replace('\\', '/').endswith('bot/owner.py') for n in names)
    # ни .env, ни база, ни .git в архив не попали
    joined = '\n'.join(names)
    assert '.env\n' not in joined + '\n' and '.git/' not in joined.replace('\\', '/')
    assert not any(n.endswith('.db') for n in names)


async def test_выгрузка_данных_со_снимком_базы(панель):
    db.remember_user(555, 'kent', u'Человек')
    await _войти(панель)
    r = await панель.get('/owner/api/export/data')
    assert r.status == 200
    zf = zipfile.ZipFile(io.BytesIO(await r.read()))
    assert 'marathon.db' in zf.namelist()


async def test_выгрузка_требует_входа(панель):
    assert (await панель.get('/owner/api/export/data')).status == 401
    assert (await панель.get('/owner/api/export/project')).status == 401


# ------------------------------------------------------------------ очистка

async def test_очистка_только_со_словом(панель):
    db.remember_user(777, 'nick', u'Имя')
    await _войти(панель)
    assert (await панель.post('/owner/api/wipe', json={})).status == 400
    assert db.stats()['users'] == 1               # без слова — ничего не стёрли
    r = await панель.post('/owner/api/wipe', json={'confirm': u'УДАЛИТЬ'})
    assert r.status == 200
    assert db.stats()['users'] == 0


async def test_очистка_требует_входа(панель):
    assert (await панель.post('/owner/api/wipe', json={'confirm': u'УДАЛИТЬ'})).status == 401


async def test_очистка_стирает_и_копии_базы_перед_запуском(панель):
    u"""backup.before_start держит рядом с базой копии с теми же данными —
    после «полной очистки» их на диске оставаться не должно."""
    копия = db.path() + '.before-20260101-000000'
    with open(копия, 'wb') as f:
        f.write(b'old data')
    await _войти(панель)
    r = await панель.post('/owner/api/wipe', json={'confirm': u'УДАЛИТЬ'})
    assert r.status == 200
    assert not os.path.exists(копия)
