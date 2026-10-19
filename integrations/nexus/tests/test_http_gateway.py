"""Real Nexus HTTP -> lazy tools -> isolated Portal integration tests.

Requires the current Nexus HTTP runtime (fastmcp, httpx, pytest). No production
credentials, network services or recipients are used. Portal itself is stdlib.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastmcp import Client

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'integrations/nexus'))
from library import Library
from server import PortalServer

EXPECTED = {
    'check_connection', 'search_reports', 'get_report', 'create_report',
    'update_report', 'edit_report_details', 'list_versions', 'restore_version',
    'list_shares', 'create_share', 'update_share', 'revoke_share',
}


@pytest.fixture
def gateway(tmp_path):
    lib = Library(tmp_path / 'data', tmp_path / 'storage')
    portal = PortalServer(('127.0.0.1', 0), lib)
    thread = threading.Thread(target=portal.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env_file = tmp_path / 'empty.env'
    env_file.write_text('')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('NEXUS_', 'PORTAL_'))}
    env.update({
        'PYTHONPATH': str(ROOT / 'integrations/nexus') + os.pathsep + os.environ.get('PYTHONPATH', ''),
        'NEXUS_ENV_FILE': str(env_file),
        'NEXUS_TOOL_PACKAGES': 'nexus_tools_portal',
        'NEXUS_TOOL_POLICY_MODE': 'unrestricted',
        'NEXUS_HTTP_HOST': '127.0.0.1',
        'NEXUS_HTTP_PORT': str(port),
        'NEXUS_HTTP_PATH': '/mcp',
        'NEXUS_HTTP_BEARER_TOKENS': 'isolated-test-bearer',
        'PORTAL_BASE_URL': f'http://127.0.0.1:{portal.server_port}',
        'PORTAL_API_KEY': lib.setting('api_key'),
        'FASTMCP_CHECK_FOR_UPDATES': 'off',
    })
    proc = subprocess.Popen([sys.executable, '-m', 'nexus.http_server'], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail('Nexus HTTP exited: ' + proc.stderr.read())
            try:
                if httpx.get(url + '/healthz', timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail('Nexus HTTP health timeout')
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        proc.stderr.close()
        portal.shutdown()
        portal.server_close()
        thread.join()


def test_http_discovery_and_complete_report_lifecycle(gateway):
    assert httpx.post(gateway + '/mcp', json={'jsonrpc': '2.0'}).status_code == 401
    asyncio.run(_lifecycle(gateway))


async def _lifecycle(url):
    async with Client(url + '/mcp', auth='isolated-test-bearer') as client:
        assert {t.name for t in await client.list_tools()} == {
            'run_code', 'search_tools', 'get_tool', 'list_tool_namespaces',
        }

        async def call(name, args):
            result = await client.call_tool(name, args)
            payload = json.loads(result.content[0].text)
            assert payload['success'], payload
            return payload

        ns = (await call('list_tool_namespaces', {}))['namespaces']
        assert len(ns) == 1
        assert ns[0]['name'] == 'portal'
        assert ns[0]['display_name'] == 'Portal'
        assert ns[0]['tool_count'] == len(EXPECTED)
        search = await call('search_tools', {'query': '', 'namespace': 'portal',
                                            'limit': 100, 'detail_level': 'name'})
        assert {t['name'] for t in search['tools']} == {'portal.' + n for n in EXPECTED}
        meta = await call('get_tool', {'name': 'portal.create_report'})
        assert 'html' in str(meta)
        status = await call('run_code', {'code': "RESULT = load_tool('portal.check_connection')()"})
        assert status['result'] == {'reachable': True, 'report_count': 0}
        created = (await call('run_code', {'code': "RESULT = load_tool('portal.create_report')(html='<title>HTTP test</title><h1>Research</h1><link rel=\"stylesheet\" href=\"assets/style.css\">', assets=[{'path':'assets/style.css','text':'body{color:navy}'}], topic='Tests')"}))['result']
        rid = created['id']
        read = (await call('run_code', {'code': f"RESULT = load_tool('portal.get_report')(report_id={rid!r}, asset_paths=['assets/style.css'])"}))['result']
        assert 'Research' in read['html']
        assert read['assets'][0]['path'] == 'assets/style.css'
        code = f"""
update = load_tool('portal.update_report')
second = update(report_id={rid!r}, expected_version={created['current_version']!r}, html='<title>Revised</title><p>Updated findings</p>')
share = load_tool('portal.create_share')(report_id={rid!r}, expires_in_days=1)
shares = load_tool('portal.list_shares')(report_id={rid!r})
RESULT = {{'second': second, 'share': share, 'shares': shares}}
"""
        changed = (await call('run_code', {'code': code}))['result']
        assert changed['second']['reader_url'] == created['reader_url']
        share = changed['share']
        assert share['expires_at']
        assert changed['shares']['shares'][0]['token'] == share['token']
        # Verify the published snapshot with a separate HTTP read, not just API success.
        response = httpx.get(share['url'], follow_redirects=True)
        assert response.status_code == 200
        assert 'Updated findings' in response.text
        revoked = await call('run_code', {'code': f"RESULT = load_tool('portal.revoke_share')(token={share['token']!r})"})
        assert revoked['result']['revoked']
        assert httpx.get(share['url'], follow_redirects=True).status_code == 404
        remaining = await call('run_code', {'code': f"RESULT = load_tool('portal.list_shares')(report_id={rid!r})"})
        assert remaining['result']['shares'] == []


def test_settings_are_lazy_rotatable_and_secret_safe(monkeypatch):
    from nexus_tools_portal.settings import PortalSettings
    from nexus_tools_portal.client import get_client
    monkeypatch.setenv('NEXUS_ENV_FILE', '/nonexistent/isolated-portal-test.env')
    monkeypatch.setenv('PORTAL_BASE_URL', 'https://portal.example.com')
    monkeypatch.setenv('PORTAL_API_KEY', 'synthetic-first-key')
    monkeypatch.setenv('PORTAL_TIMEOUT_SECONDS', '12.5')
    assert 'synthetic-first-key' not in repr(PortalSettings.load())
    first = get_client()
    assert first.timeout == 12.5
    monkeypatch.setenv('PORTAL_API_KEY', 'synthetic-rotated-key')
    assert get_client()._key == 'synthetic-rotated-key'
    for value in ('0', '-1', '121', 'nan', 'inf', 'oops'):
        monkeypatch.setenv('PORTAL_TIMEOUT_SECONDS', value)
        with pytest.raises(ValueError, match='PORTAL_TIMEOUT_SECONDS'):
            get_client()


def test_catalog_discovery_needs_no_portal_credentials(monkeypatch):
    from nexus import tool_catalog
    monkeypatch.setenv('NEXUS_ENV_FILE', '/nonexistent/isolated-portal-test.env')
    monkeypatch.setenv('NEXUS_TOOL_PACKAGES', 'nexus_tools_portal')
    monkeypatch.delenv('PORTAL_API_KEY', raising=False)
    monkeypatch.delenv('PORTAL_BASE_URL', raising=False)
    catalog = tool_catalog.get_catalog(refresh=True)
    assert set(catalog) == {'portal.' + n for n in EXPECTED}
    assert not tool_catalog.get_catalog_problems()
    assert catalog['portal.revoke_share'].tool_class == 'destructive'
    assert catalog['portal.create_share'].tool_class == 'write'
    assert catalog['portal.check_connection'].tool_class == 'read'
