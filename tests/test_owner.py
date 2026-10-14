# -*- coding: utf-8 -*-
u"""Панель владельца: секретный адрес, маскировка под «404: Not Found»,
пароль + код в Телеграм, замки от подбора, копии базы, выгрузки, очистка.

Главное — дверь: снаружи панели не видно, пароль не подобрать, а без
телефона владельца не войти даже с паролем.
"""
import asyncio
import io
import os
import re
import sys
import zipfile

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, owner, web                                    # noqa: E402
from tests.fakes import FakeBot                                           # noqa: E402

TOKEN = '123456:TESTTOKEN'
PW = 'fivedecember'
P = '/t3st-owner-path-0001'
OWNER = '7874595355'


@pytest.fixture(autouse=True)
def база(tmp_path, monkeypatch):
    db.connect(str(tmp_path / 'owner.db'))
    monkeypatch.setattr(config, 'BOT_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'OWNER_PASSWORD', PW)
    monkeypatch.setattr(config, 'OWNER_PATH', P)
    monkeypatch.setattr(config, 'OWNER_TG', OWNER)
    monkeypatch.setattr(config, 'OWNER_2FA', True)
    monkeypatch.setattr(config, 'WEBAPP_URL', '')      # локально по http
    media = tmp_path / 'media'                          # не настоящие медиа проекта
    (media / 'circles').mkdir(parents=True)
    (media / 'circles' / 'welcome.mp4').write_bytes(b'x')
    monkeypatch.setattr(owner, 'MEDIA_DIR', str(media))
    exits = []
    monkeypatch.setattr(owner, '_exit', exits.append)
    web._hits.clear(); web._window_from[0] = 0.0
    owner.reset_limits()
    yield exits


@pytest.fixture
async def мир(база):
    bot = FakeBot()
    client = TestClient(TestServer(web.build(bot)))
    await client.start_server()
    yield client, bot, база
    await client.close()


def _тексты(bot):
    return [m[2] for m in bot.sent if m[0] == 'text' and m[1] == int(OWNER)]


def _код(bot):
    for text in reversed(_тексты(bot)):
        m = re.search(r': (\d{6})\n', text)
        if m:
            return m.group(1)
    return None


async def _ввести(client, value, ip='198.51.100.1', **kw):
    headers = {'X-Real-IP': ip}
    headers.update(kw.pop('headers', {}))
    return await client.post(P + '/e', json={'v': value}, headers=headers, **kw)


async def _войти(client, bot, ip='198.51.100.1'):
    await _ввести(client, PW, ip)
    await asyncio.sleep(0.05)
    code = _код(bot)
    assert code, 'код не пришёл'
    r = await _ввести(client, code, ip)
    assert r.status == 200


# ------------------------------------------------------------------ адрес и маскировка

async def test_без_адреса_панели_нет(база, monkeypatch):
    monkeypatch.setattr(config, 'OWNER_PATH', '')
    client = TestClient(TestServer(web.build(FakeBot())))
    await client.start_server()
    try:
        assert (await client.get(P)).status == 404
        assert (await client.get('/owner')).status == 404
        assert 'выключена' in owner.describe()
    finally:
        await client.close()


async def test_короткий_адрес_не_принимается(monkeypatch):
    monkeypatch.setattr(config, 'OWNER_PATH', '/short')
    assert owner.prefix() == ''
    assert 'ВЫКЛЮЧЕНА' in owner.describe()


async def test_старый_адрес_обычный_404(мир):
    client, _bot, _ = мир
    r = await client.get('/owner')
    assert r.status == 404
    assert await r.text() == '404: Not Found'


async def test_по_секретному_адресу_та_же_404(мир):
    client, _bot, _ = мир
    r = await client.get(P)
    assert r.status == 404
    body = await r.text()
    assert '>404: Not Found</pre>' in body
    assert not re.search(r'парол|password|логин|login|вход|owner|панел', body, re.I)


async def test_апи_без_входа_404(мир):
    client, _bot, _ = мир
    for path in ('/api/overview', '/api/export/data', '/api/backups', '/api/log'):
        r = await client.get(P + path)
        assert r.status == 404, path
        assert await r.text() == '404: Not Found'


async def test_старая_страница_не_раздаётся(мир):
    client, _bot, _ = мир
    assert (await client.get('/static/owner.html')).status == 404


# ------------------------------------------------------------------ пароль и код

async def test_неверный_пароль_снаружи_тот_же_404(мир):
    client, bot, _ = мир
    r = await _ввести(client, 'wrong')
    assert r.status == 404
    assert await r.text() == '404: Not Found'
    assert '_p' in r.cookies                               # cookie ожидания — и на неверный
    await asyncio.sleep(0.05)
    assert _код(bot) is None
    assert any('Неверный пароль' in t for t in _тексты(bot))


async def test_верный_пароль_код_открывает_панель(мир):
    client, bot, _ = мир
    r = await _ввести(client, PW)
    assert r.status == 404
    await asyncio.sleep(0.05)
    code = _код(bot)
    assert re.fullmatch(r'\d{6}', code)
    assert (await _ввести(client, code)).status == 200
    page = await client.get(P)
    assert page.status == 200
    assert 'Панель владельца' in await page.text()
    data = await (await client.get(P + '/api/overview')).json()
    assert 'stats' in data and 'users' in data['tables']
    await asyncio.sleep(0.05)
    assert any('Вход в панель' in t for t in _тексты(bot))


async def test_неверный_код_трижды_сгорает(мир):
    client, bot, _ = мир
    await _ввести(client, PW)
    await asyncio.sleep(0.05)
    code = _код(bot)
    wrong = '111111' if code == '000000' else '000000'
    for _ in range(3):
        assert (await _ввести(client, wrong)).status == 404
    assert (await _ввести(client, code)).status == 404


async def test_пароль_ещё_раз_не_тратит_попытки(мир):
    client, bot, _ = мир
    await _ввести(client, PW)
    await asyncio.sleep(0.05)
    code = _код(bot)
    for _ in range(4):
        await _ввести(client, PW)
    assert (await _ввести(client, code)).status == 200


async def test_пять_ошибок_с_адреса_замок(мир):
    client, bot, _ = мир
    for i in range(5):
        await _ввести(client, 'nope%d' % i, ip='203.0.113.5')
    bot.sent.clear()
    await _ввести(client, PW, ip='203.0.113.5')
    await asyncio.sleep(0.05)
    assert _код(bot) is None
    client.session.cookie_jar.clear()
    await _ввести(client, PW, ip='203.0.113.6')
    await asyncio.sleep(0.05)
    assert _код(bot), 'с другого адреса вход работает'


async def test_двадцать_ошибок_дверь_закрыта(мир):
    client, bot, _ = мир
    for i in range(20):
        await _ввести(client, 'guess%d' % i, ip='192.0.2.%d' % (i + 1))
    await asyncio.sleep(0.05)
    assert any('закрыта на час' in t for t in _тексты(bot))
    bot.sent.clear()
    await _ввести(client, PW, ip='198.51.100.77')
    await asyncio.sleep(0.05)
    assert _код(bot) is None


async def test_кодов_не_больше_трёх(мир):
    client, bot, _ = мир
    for i in range(4):
        client.session.cookie_jar.clear()
        await _ввести(client, PW, ip='198.51.100.%d' % (10 + i))
    await asyncio.sleep(0.05)
    assert sum('Код входа' in t for t in _тексты(bot)) == 3


async def test_чужой_сайт_не_отправит_пароль(мир):
    client, bot, _ = мир
    r = await _ввести(client, PW, headers={'Origin': 'https://evil.example'})
    assert r.status == 404
    await asyncio.sleep(0.05)
    assert _код(bot) is None


async def test_выйти_везде(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    assert (await client.post(P + '/api/logout-all', json={})).status == 200
    assert (await client.get(P + '/api/overview')).status == 404


async def test_история_входов(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    data = await (await client.get(P + '/api/security')).json()
    events = [e['ev'] for e in data['history']]
    assert 'вход' in events and 'код отправлен' in events
    assert data['door']['twoFactor'] is True


# ------------------------------------------------------------------ внутри

async def test_копии_базы(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    made = await (await client.post(P + '/api/backups', json={})).json()
    assert re.fullmatch(r'marathon-\d{8}-\d{6}\.db', made['name'])
    listed = await (await client.get(P + '/api/backups')).json()
    assert [b['name'] for b in listed['backups']] == [made['name']]
    got = await client.get(P + '/api/backups/get', params={'name': made['name']})
    assert got.status == 200
    assert (await got.read())[:15] == b'SQLite format 3'
    assert (await client.get(P + '/api/backups/get', params={'name': '../owner.db'})).status == 404
    assert (await client.post(P + '/api/backups/delete', json={'name': made['name']})).status == 200
    assert (await (await client.get(P + '/api/backups')).json())['backups'] == []


async def test_выгрузка_кода_без_секретов(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    r = await client.get(P + '/api/export/project')
    assert r.status == 200
    names = zipfile.ZipFile(io.BytesIO(await r.read())).namelist()
    assert 'bot/owner.py' in [n.replace('\\', '/') for n in names]
    assert not any(n in ('.env', '.git') or n.endswith('.db') or '.owner-' in n or n.startswith('.git/')
                   for n in names)


async def test_выгрузка_данных(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    r = await client.get(P + '/api/export/data')
    assert r.status == 200
    names = zipfile.ZipFile(io.BytesIO(await r.read())).namelist()
    assert 'marathon.db' in names


async def test_журнал_и_телеграм(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    assert 'lines' in await (await client.get(P + '/api/log')).json()
    bot.sent.clear()
    assert (await client.post(P + '/api/tg-test', json={})).status == 200
    assert any('Проверка связи' in t for t in _тексты(bot))


async def test_перезапуск(мир):
    client, bot, exits = мир
    await _войти(client, bot)
    assert (await client.post(P + '/api/restart', json={})).status == 200
    await asyncio.sleep(0.7)
    assert exits == [0]


async def test_очистка_только_со_словом(мир):
    client, bot, _ = мир
    await _войти(client, bot)
    db.remember_user(1, 'u', 'Человек')
    assert (await client.post(P + '/api/wipe', json={})).status == 400
    assert db.stats()['users'] == 1
    r = await client.post(P + '/api/wipe', json={'confirm': 'УДАЛИТЬ'})
    assert r.status == 200
    assert db.stats()['users'] == 0
    assert not os.listdir(os.path.join(owner.MEDIA_DIR, 'circles'))
