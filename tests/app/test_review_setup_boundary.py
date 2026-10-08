"""Loopback-only synthetic requests; credentials and child processes are mocked."""
from http.client import HTTPConnection
import threading
from urllib.parse import urlencode

from scripts import gemini_local_setup as setup


def test_setup_rejects_spoofed_missing_duplicate_and_cross_origin_headers(tmp_path, monkeypatch):
    effects = []
    for name in ('forget_api_key', 'save_api_key', 'enable_remember', 'start_live_child'):
        monkeypatch.setattr(setup, name, lambda *a, _name=name, **kw: effects.append(_name))
    monkeypatch.setattr(setup, 'loopback_port_in_use', lambda port: False)
    server = setup.SetupServer(('127.0.0.1', 0), 8010, data_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    host = f'127.0.0.1:{port}'
    origin = f'http://{host}'
    body = urlencode({'csrf': server.csrf_token, 'approved': 'yes', 'remember': 'yes',
                      'api_key': 'synthetic-valid-key'}).encode('ascii')

    def request(method, hosts, origins):
        connection = HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.putrequest(method, '/' if method == 'GET' else '/start', skip_host=True)
            for value in hosts:
                connection.putheader('Host', value)
            for value in origins:
                connection.putheader('Origin', value)
            connection.putheader('Content-Type', 'application/x-www-form-urlencoded')
            connection.putheader('Content-Length', str(len(body) if method == 'POST' else 0))
            connection.endheaders(body if method == 'POST' else None)
            response = connection.getresponse()
            return response.status, response.read().decode('utf-8')
        finally:
            connection.close()

    try:
        for origins in ([], [origin]):
            status, rendered = request('GET', [host], origins)
            assert status == 200 and server.csrf_token in rendered
        wrong_hosts = [[], ['attacker.invalid'], [f'localhost:{port}'],
                       [f'127.0.0.1:{port + 1}'], [host, host], [host, 'attacker.invalid']]
        wrong_origins = [['http://attacker.invalid'], ['null'], [f'https://{host}'],
                         [f'http://127.0.0.1:{port + 1}'], [origin, origin], [origin, 'null']]
        for method in ('GET', 'POST'):
            for hosts in wrong_hosts:
                status, rendered = request(method, hosts, [origin])
                assert status == 403, (method, hosts)
                assert server.csrf_token not in rendered and effects == []
            for origins in wrong_origins + ([[]] if method == 'POST' else []):
                status, rendered = request(method, [host], origins)
                assert status == 403, (method, origins)
                assert server.csrf_token not in rendered and effects == []
        assert server.child is None
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
