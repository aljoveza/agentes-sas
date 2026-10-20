import base64
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('nexo', Path(__file__).parents[1] / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_trace_paginates_and_filters_unrelated_activity():
    table = MagicMock()
    table.query.return_value = {'Items': [{'pk': 'ORDER#ONE', 'sk': 'META'}]}
    table.scan.side_effect = [
        {'Items': [], 'LastEvaluatedKey': {'pk': 'continue'}},
        {'Items': [{'pk': 'TASK#T1', 'sk': 'META', 'taskId': 'T1'}]},
    ]
    resource = MagicMock()
    resource.Table.return_value = table
    logs = MagicMock()
    logs.get_paginator.return_value.paginate.return_value = [{'events': [
        {'message': '{"detail":{"correlationId":"ONE","timestamp":"1","eventType":"WorkAssigned"}}'},
        {'message': '{"detail":{"correlationId":"OTHER","timestamp":"2"}}'},
    ]}]
    activity = {'agents': [{'id': 'fulfillment', 'onThisOrder': True, 'tool': 'reserve_inventory'}],
                'timeline': [{'agentId': 'fulfillment', 'kind': 'tool_started', 'tool': 'reserve_inventory'}]}
    with patch.object(server.boto3, 'resource', return_value=resource), \
         patch.object(server.boto3, 'client', return_value=logs), \
         patch.object(server, 'order_activity', return_value=(activity, [])):
        trace = server.collect_order('ONE')
    assert len(trace['events']) == 1
    assert trace['activity']['timeline'][0]['tool'] == 'reserve_inventory'
    assert trace['warnings'] == []
    assert table.scan.call_count == 2


def test_all_scenario_fixtures_available():
    assert len(server.scenarios()) == 6


def test_transcript_failures_visible_per_agent():
    with patch.object(server.subprocess, 'run', return_value=MagicMock(returncode=1, stderr='not running')):
        result = server.transcripts('ONE')
    assert len(result) == 7
    assert all(r['error'] == 'not running' for r in result)


def test_run_uses_valid_fresh_ids_and_preserves_duplicate_scenario():
    import asyncio
    import json
    import re
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    captured = []
    async def fake_run(path, run_id):
        captured.append((json.loads(path.read_text()), run_id))
        return 0

    # Exercise request parsing and response behavior without submitting business work.
    class LocalHandler(server.Handler):
        def log_message(self, *_):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(server, 'run', fake_run):
            responses = []
            for _ in range(2):
                request = urllib.request.Request(
                    f'http://127.0.0.1:{http.server_port}/api/run',
                    data=b'{"scenario":"04-duplicate-order"}',
                    headers={'Host': '127.0.0.1:8090', 'Origin': 'http://127.0.0.1:8090',
                             'Content-Type': 'application/json'})
                responses.append(json.load(urllib.request.urlopen(request)))
        assert responses[0]['orders'] != responses[1]['orders']
        assert all(re.fullmatch(r'[A-Z0-9][A-Z0-9-]{1,63}', r['orders'][0]) for r in responses)
        assert all(s['orders'][0]['repeat'] == 2 for s, _ in captured)
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


class FakeMCP:
    def __init__(self):
        self.orders = {}
        self.placements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def call_tool(self, tool, arguments, **kwargs):
        if tool == 'get_inventory':
            return MagicMock(is_error=False, structured_content={
                'productId': arguments['product_id'], 'name': 'Producto', 'unitPrice': '120.00', 'available': 10})
        assert tool == 'place_order'
        self.placements.append(arguments)
        self.orders.setdefault(arguments['order_id'], {**arguments, 'status': 'PLACED'})
        return MagicMock(is_error=False, structured_content=self.orders[arguments['order_id']])


def cart_body():
    return {'requestId': '41a9bfed-34ec-4656-a563-87e2997f439d', 'customerId': 'CUST-ACME',
            'lines': [{'productId': 'KEYBOARD-MX', 'quantity': 3}], 'discount': '25',
            'subtotal': '0.01', 'payment_amount': '0.01'}


def test_checkout_prices_server_side_and_retries_keep_order_identity():
    import asyncio
    mcp = FakeMCP()
    with patch.object(server, 'company_client', return_value=mcp):
        first = asyncio.run(server.checkout(cart_body()))
        second = asyncio.run(server.checkout(cart_body()))
    assert first['orders'] == second['orders']
    assert len(mcp.orders) == 1
    assert mcp.placements[0]['payment_amount'] == '270.00'
    assert mcp.placements[0]['requested_discount_percent'] == '25'
    assert mcp.placements[0]['order_id'].isupper()


def test_invalid_carts_never_call_company_tools():
    import asyncio
    import pytest
    mutations = [
        {'discount': 'NaN'}, {'discount': 'Infinity'}, {'discount': -1}, {'discount': 101},
        {'lines': []}, {'lines': [{'productId': 'KEYBOARD-MX', 'quantity': True}]},
        {'lines': [{'productId': 'KEYBOARD-MX', 'quantity': 0}]},
        {'lines': [{'productId': 'KEYBOARD-MX', 'quantity': 1.5}]},
        {'lines': [{'productId': 'UNKNOWN', 'quantity': 1}]},
        {'lines': [{'productId': 'KEYBOARD-MX', 'quantity': 1}] * 2},
        {'customerId': 'CUST-ADMIN'}, {'requestId': '../../bad'},
    ]
    with patch.object(server, 'company_client') as client:
        for change in mutations:
            with pytest.raises(ValueError):
                asyncio.run(server.checkout({**cart_body(), **change}))
        client.assert_not_called()


def test_activity_service_down_is_a_warning_not_an_error():
    with patch.object(server.urllib.request, 'urlopen', side_effect=server.urllib.error.URLError('refused')):
        activity, warnings = server.order_activity('ONE')
    assert activity == {'agents': [], 'timeline': []}
    assert 'refused' in warnings[0]


def request(port, path, method='GET', headers=None, data=None):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def serve():
    class QuietHandler(server.Handler):
        def log_message(self, *_):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    return http


def test_behind_the_load_balancer_everything_but_health_needs_the_password():
    http = serve()
    port = http.server_port
    good = 'Basic ' + base64.b64encode(b'demo:s3cret').decode()
    bad = 'Basic ' + base64.b64encode(b'demo:wrong').decode()
    host = {'Host': 'nexo.example.on.aws'}
    try:
        with patch.object(server, 'PASSWORD', 's3cret'):
            assert request(port, '/health', headers=host) == (200, {'status': 'ok'})
            assert request(port, '/api/scenarios', headers=host)[0] == 401
            assert request(port, '/api/scenarios', headers=host | {'Authorization': bad})[0] == 401
            status, scenarios = request(port, '/api/scenarios', headers=host | {'Authorization': good})
            assert status == 200 and scenarios
            # Orders only from the page itself, served over HTTPS by the load balancer.
            post = host | {'Authorization': good, 'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https'}
            for origin in ('http://nexo.example.on.aws', 'https://evil.example'):
                assert request(port, '/api/checkout', 'POST', post | {'Origin': origin}, b'{}')[0] == 403
            # Same origin gets through to cart validation (an empty cart is rejected).
            assert request(port, '/api/checkout', 'POST', post | {'Origin': 'https://nexo.example.on.aws'},
                           b'{}')[0] == 400
        with patch.object(server, 'PASSWORD', 'CHANGE_ME'):
            assert request(port, '/api/scenarios', headers=host | {'Authorization': good})[0] == 503
    finally:
        http.shutdown()
        http.server_close()


def test_locally_only_loopback_host_names_are_served():
    http = serve()
    try:
        assert request(http.server_port, '/api/scenarios', headers={'Host': 'evil.example'})[0] == 403
        assert request(http.server_port, '/api/scenarios', headers={'Host': '127.0.0.1:8090'})[0] == 200
    finally:
        http.shutdown()
        http.server_close()


def test_transcripts_can_be_turned_off():
    with patch.object(server, 'TRANSCRIPTS', 'off'):
        result = server.transcripts('ORDER-1')
    assert len(result) == 7 and all(r['error'] and r['sessions'] == [] for r in result)


def test_in_aws_transcripts_come_from_the_session_archive():
    class Body:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode()

    archived = {'sessions/archives/ceo/ORDER-1/20261016_s2.md': '# second',
                'sessions/archives/ceo/ORDER-1/20261016_s1.md': '# first'}
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.side_effect = lambda Bucket, Prefix: [
        {'Contents': [{'Key': k} for k in archived if k.startswith(Prefix)]}]
    s3.get_object.side_effect = lambda Bucket, Key: {'Body': Body(archived[Key])}
    with patch.object(server, 'TRANSCRIPTS', 's3'), patch.object(server, 'SESSION_ARCHIVE_BUCKET', 'company'), \
            patch.object(server.boto3, 'client', return_value=s3):
        result = {r['agent']: r for r in server.transcripts('ORDER-1')}
    assert result['ceo']['sessions'] == [{'session': '20261016_s1', 'text': '# first'},
                                         {'session': '20261016_s2', 'text': '# second'}]
    assert result['accountant']['sessions'] == [] and 'error' not in result['accountant']
