"""Nexo: the demo store and live company view. Places orders through the Company MCP; never
participates in business processing itself.

Locally it listens on loopback only. In AWS (ECS Express Mode, behind a public HTTPS load balancer)
NEXO_PASSWORD is set and every page except /health needs HTTP basic auth."""
import asyncio
import base64
import hmac
import json
import os
from decimal import Decimal, InvalidOperation
import re
import subprocess
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import boto3
from boto3.dynamodb.conditions import Attr, Key
from order_simulator.__main__ import run, identity_headers
from order_simulator.market import PRODUCT_IDS, CUSTOMERS
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / 'static'
ACTIVITY_URL = os.environ.get('AGENT_ACTIVITY_URL', 'http://localhost:8113').rstrip('/')
TABLE_NAME = os.environ.get('TABLE_NAME', 'agentes-sas-local-company')
EVENTS_LOG_GROUP = os.environ.get('BUSINESS_EVENTS_LOG_GROUP', '/aws/events/agentes-sas-local-business')
TRANSCRIPTS = os.environ.get('TRANSCRIPTS', 'docker')  # docker (local) | s3 (AWS) | off
SESSION_ARCHIVE_BUCKET = os.environ.get('SESSION_ARCHIVE_BUCKET', '')
HOST = os.environ.get('NEXO_HOST', '127.0.0.1')
PORT = int(os.environ.get('NEXO_PORT', '8090'))
PASSWORD = os.environ.get('NEXO_PASSWORD', '')
LOCAL_HOSTS = (f'127.0.0.1:{PORT}', f'localhost:{PORT}')
AGENTS = ('ceo', 'accounting-manager', 'accountant', 'operations-manager', 'fulfillment', 'commercial-manager', 'sales-rep')


def scenarios():
    return {p.stem: (p, json.loads(p.read_text())) for p in sorted((ROOT / 'demo/scenarios').glob('*.json'))}



def company_client():
    return Client(StreamableHttpTransport(os.environ.get('COMPANY_MCP_URL', 'http://localhost:8080/mcp'),
                                         headers=identity_headers()))


async def read_catalog(mcp):
    products = []
    for product_id in PRODUCT_IDS:
        result = await mcp.call_tool('get_inventory', {'product_id': product_id}, raise_on_error=False)
        if result.is_error:
            raise RuntimeError(result.content[0].text)
        item = result.structured_content
        products.append({k: item[k] for k in ('productId', 'name', 'unitPrice', 'available')})
    return products


async def catalog():
    async with company_client() as mcp:
        return {'products': await read_catalog(mcp),
                'customers': [{'id': key, 'name': value[0]} for key, value in CUSTOMERS.items()]}


def validate_cart(body):
    if not isinstance(body, dict):
        raise ValueError('El carrito no es válido.')
    if not isinstance(body.get('requestId'), str):
        raise ValueError('El identificador de compra no es válido.')
    request_id = str(uuid.UUID(body['requestId'])).replace('-', '').upper()
    if body.get('customerId') not in CUSTOMERS:
        raise ValueError('Selecciona un cliente válido.')
    lines = body.get('lines')
    if not isinstance(lines, list) or not 1 <= len(lines) <= len(PRODUCT_IDS):
        raise ValueError('Agrega al menos un producto al carrito.')
    seen = set()
    for line in lines:
        if not isinstance(line, dict) or line.get('productId') not in PRODUCT_IDS:
            raise ValueError('Producto desconocido.')
        if line['productId'] in seen:
            raise ValueError('El carrito contiene productos repetidos.')
        seen.add(line['productId'])
        if type(line.get('quantity')) is not int or not 1 <= line['quantity'] <= 100:
            raise ValueError('La cantidad debe ser un entero entre 1 y 100.')
    discount = Decimal(str(body.get('discount', 0)))
    if not discount.is_finite() or not 0 <= discount <= 100:
        raise ValueError('El descuento debe estar entre 0 y 100.')
    return request_id, lines, discount


async def checkout(body):
    request_id, lines, discount = validate_cart(body)
    order_id = 'TIENDA-' + request_id
    async with company_client() as mcp:
        products = {p['productId']: p for p in await read_catalog(mcp)}
        subtotal = sum((Decimal(str(products[line['productId']]['unitPrice'])) * line['quantity']
                        for line in lines), Decimal(0))
        payment = (subtotal * (100 - discount) / 100).quantize(Decimal('0.01'))
        # Price and payment are derived from the real catalog, never from browser totals.
        result = await mcp.call_tool('place_order', {
            'order_id': order_id, 'customer_id': body['customerId'], 'lines': lines,
            'payment_reference': 'PAY-' + order_id, 'payment_amount': str(payment),
            'requested_discount_percent': str(discount),
        }, raise_on_error=False)
        if result.is_error:
            raise ValueError(result.content[0].text)
        return {'orders': [order_id], 'order': result.structured_content}

def collect_order(order):
    table = boto3.resource('dynamodb').Table(TABLE_NAME)
    records = []
    for method, args in [(table.query, {'KeyConditionExpression': Key('pk').eq('ORDER#' + order)}),
                         (table.scan, {'FilterExpression': Attr('correlationId').eq(order)})]:
        while True:
            page = method(**args)
            records.extend(page['Items'])
            if 'LastEvaluatedKey' not in page:
                break
            args['ExclusiveStartKey'] = page['LastEvaluatedKey']
    records = list({(r['pk'], r['sk']): r for r in records}.values())
    events = []
    for page in boto3.client('logs').get_paginator('filter_log_events').paginate(
        logGroupName=EVENTS_LOG_GROUP, filterPattern=json.dumps(order)
    ):
        for row in page['events']:
            event = json.loads(row['message']).get('detail', {})
            if event.get('correlationId') == order:
                events.append(event)
    activity, warnings = order_activity(order)
    return {'events': sorted(events, key=lambda x: x.get('timestamp', '')),
            'records': records, 'activity': activity, 'warnings': warnings}


def order_activity(order):
    """Live agent activity for one order, from the agent-activity service (observability only)."""
    try:
        with urllib.request.urlopen(f'{ACTIVITY_URL}/orders/{order}/activity', timeout=3) as response:
            return json.load(response), []
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {'agents': [], 'timeline': []}, [f'Actividad en vivo no disponible ({ACTIVITY_URL}): {exc}']


# Export through the supported Hermes CLI, using a unique temporary directory per request.
EXPORT = '''import tempfile, subprocess, pathlib, json, sys, re
with tempfile.TemporaryDirectory() as d:
 p = subprocess.run(['hermes','sessions','export',d,'--format','md','--min-messages','0','--yes','--redact'],capture_output=True,text=True)
 if p.returncode: raise RuntimeError(p.stderr or p.stdout)
 if not list(pathlib.Path(d).rglob('*.md')) and 'Refusing' in p.stdout: raise RuntimeError(p.stdout)
 pattern = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(sys.argv[1]) + r'(?![A-Za-z0-9_-])')
 print(json.dumps([{'session':f.stem,'text':f.read_text()} for f in pathlib.Path(d).rglob('*.md') if pattern.search(f.read_text())]))
'''


def archived_transcripts(order):
    """AWS: each agent archives its turns at sessions/archives/<agent>/<order>/<session>.md."""
    s3 = boto3.client('s3')

    def get(agent):
        try:
            keys = sorted(item['Key'] for page in s3.get_paginator('list_objects_v2').paginate(
                Bucket=SESSION_ARCHIVE_BUCKET, Prefix=f'sessions/archives/{agent}/{order}/')
                for item in page.get('Contents', []))
            return {'agent': agent, 'sessions': [
                {'session': Path(key).stem,
                 'text': s3.get_object(Bucket=SESSION_ARCHIVE_BUCKET, Key=key)['Body'].read().decode()}
                for key in keys]}
        except Exception as exc:
            return {'agent': agent, 'error': str(exc), 'sessions': []}
    with ThreadPoolExecutor(max_workers=7) as pool:
        return list(pool.map(get, AGENTS))


def transcripts(order):
    if TRANSCRIPTS == 's3':
        return archived_transcripts(order)
    if TRANSCRIPTS != 'docker':
        return [{'agent': agent, 'sessions': [],
                 'error': 'Las transcripciones no están disponibles en este entorno.'} for agent in AGENTS]

    def get(agent):
        try:
            p = subprocess.run(['docker', 'compose', 'exec', '-T', '--user', 'hermes', 'agent-' + agent, 'python', '-c', EXPORT, order],
                               cwd=ROOT / 'local', capture_output=True, text=True, timeout=60)
            if p.returncode:
                return {'agent': agent, 'error': p.stderr.strip() or p.stdout.strip(), 'sessions': []}
            return {'agent': agent, 'sessions': json.loads(p.stdout)}
        except (subprocess.TimeoutExpired, ValueError) as exc:
            return {'agent': agent, 'error': str(exc), 'sessions': []}
    with ThreadPoolExecutor(max_workers=7) as pool:
        return list(pool.map(get, AGENTS))


def password_ok(header):
    """HTTP basic auth against NEXO_PASSWORD (any user name)."""
    if not header or not header.startswith('Basic '):
        return False
    try:
        _, _, given = base64.b64decode(header[6:]).decode().partition(':')
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(given.encode(), PASSWORD.encode())


class Handler(BaseHTTPRequestHandler):
    def send(self, data, status=200, mime='application/json', headers=()):
        body = (json.dumps(data, default=str) if mime == 'application/json' else data).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def refused(self):
        """Why this request may not proceed, or None. Locally: loopback host names only (no DNS
        rebinding). Behind the load balancer: the password."""
        if not PASSWORD:
            return None if self.headers.get('Host') in LOCAL_HOSTS else ({'error': 'Local host required'}, 403, ())
        if PASSWORD == 'CHANGE_ME':
            return {'error': 'Set the Nexo password parameter first.'}, 503, ()
        if not password_ok(self.headers.get('Authorization')):
            return {'error': 'Authentication required'}, 401, (('WWW-Authenticate', 'Basic realm="Nexo"'),)
        return None

    def same_origin(self):
        scheme = self.headers.get('X-Forwarded-Proto', 'http')
        return self.headers.get('Origin') == f"{scheme}://{self.headers.get('Host', '')}"

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/health':
            return self.send({'status': 'ok'})
        if refusal := self.refused():
            data, status, headers = refusal
            return self.send(data, status, headers=headers)
        try:
            if url.path == '/':
                return self.send((STATIC / 'index.html').read_text(), mime='text/html')
            if url.path in ('/app.mjs', '/model.mjs', '/style.css'):
                mime = 'text/css' if url.path.endswith('.css') else 'text/javascript'
                return self.send((STATIC / url.path[1:]).read_text(), mime=mime)
            if url.path == '/api/catalog':
                return self.send(asyncio.run(catalog()))
            if url.path == '/api/scenarios':
                return self.send([{'id': k, **v} for k, (_, v) in scenarios().items()])
            order = parse_qs(url.query).get('order', [''])[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', order):
                return self.send({'error': 'Ingresa un identificador de pedido válido.'}, 400)
            if url.path == '/api/trace':
                return self.send(collect_order(order))
            if url.path == '/api/transcripts':
                return self.send(transcripts(order))
            self.send({'error': 'Not found'}, 404)
        except Exception as exc:
            self.send({'error': str(exc)}, 503)

    def do_POST(self):
        if refusal := self.refused():
            data, status, headers = refusal
            return self.send(data, status, headers=headers)
        # Only same-origin browser requests may place orders.
        if not self.same_origin():
            return self.send({'error': 'Same-origin requests required'}, 403)
        try:
            if self.path not in ('/api/run', '/api/checkout'):
                return self.send({'error': 'Not found'}, 404)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size < 4096:
                return self.send({'error': 'Invalid request size'}, 400)
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/checkout':
                return self.send(asyncio.run(checkout(body)))
            path, scenario = scenarios()[body['scenario']]
            run_id = uuid.uuid4().hex[:12].upper()
            code = asyncio.run(run(path, run_id))
            self.send({'orders': [o['order_id'] + '-R' + run_id for o in scenario['orders']],
                       'error': 'No se pudo enviar el escenario. Revisa los registros de Company MCP.' if code else None}, 502 if code else 200)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            self.send({'error': str(exc) or 'Solicitud no válida.'}, 400)
        except Exception as exc:
            self.send({'error': str(exc)}, 503)


if __name__ == '__main__':
    print(f'Nexo: http://{HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
