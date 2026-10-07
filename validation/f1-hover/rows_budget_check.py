"""Live check of the explicit-rows budget (scale UI review F1) on a running v2 server behind the TLS terminator,
after validation/v2-integrate/final/tools/tracks_budget_check.py. It reads the served rows from browse pages
(15 page reads of 48), then sends rows= reads of 64 distinct served rows (the most one request may ask for) until
the first refusal or 700 reads, with the server's CPU before and after (ps cputime of the server process). Then it
sends the page reads a visitor makes (browse, the next page, a lookup with facets, a refinement) and one anonymous
search, and records their statuses.
Usage: rows_budget_check.py <https-origin> <server-pid> <out.json>"""
import http.client
import json
import ssl
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

ORIGIN, PID, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
host = urlsplit(ORIGIN)


def cpu_seconds():
    text = subprocess.run(['ps', '-o', 'cputime=', '-p', PID], capture_output=True, text=True).stdout.strip()
    seconds = 0.0
    for part in text.replace('-', ':').split(':'):
        seconds = seconds * 60 + float(part)
    return seconds


connection = http.client.HTTPSConnection(host.hostname, host.port, context=ssl._create_unverified_context(), timeout=60)


def get(path):
    connection.request('GET', path)
    reply = connection.getresponse()
    body = reply.read()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return reply.status, reply.getheader('Retry-After'), data


served, offset, setup = [], 0, []
while True:
    status, _, page = get(f'/collection/tracks?limit=48&offset={offset}')
    setup.append(status)
    if status != 200 or not page['rows']:
        break
    served += [row['row'] for row in page['rows']]
    offset += 48
    if offset >= page['total']:
        break
statuses, first_refusal = [], None
cpu0, t0 = cpu_seconds(), time.monotonic()
for n in range(1, 701):
    part = [served[(n * 64 + k) % len(served)] for k in range(64)]
    status, retry, body = get('/collection/tracks?rows=' + ','.join(map(str, dict.fromkeys(part))))
    statuses.append(status)
    if status == 429:
        first_refusal = {'request': n, 'retryAfter': retry, 'body': body}
        break
elapsed, cpu1 = time.monotonic() - t0, cpu_seconds()
admitted = statuses.count(200)
after = {}
for name, path in (('browse', '/collection/tracks?offset=0&limit=12&facets=1'), ('next page', '/collection/tracks?offset=12&limit=12'),
                   ('lookup', '/collection/tracks?q=love&offset=0&limit=12&facets=1'), ('refinement', '/collection/tracks?q=love&text=night&offset=0&limit=12')):
    status, retry, body = get(path)
    after[name] = {'status': status, 'retryAfter': retry, 'error': (body or {}).get('error'), 'total': (body or {}).get('total')}
manifest_conn = http.client.HTTPSConnection(host.hostname, host.port, context=ssl._create_unverified_context(), timeout=60)
manifest_conn.request('GET', '/v1/manifest')
manifest = json.loads(manifest_conn.getresponse().read())
search_body = json.dumps({'requestId': str(uuid.uuid4()), 'generation': 1, 'query': 'warm acoustic guitar with soft vocals',
                          'engineId': manifest['engineId'], 'catalogId': manifest['catalogId'],
                          'deploymentGeneration': manifest['deploymentGeneration'], 'k': 16, 'ef': 32, 'trace': True,
                          'traceLimit': 128})
manifest_conn.request('POST', '/v1/search', body=search_body, headers={'Content-Type': 'application/json', 'Origin': ORIGIN})
search = manifest_conn.getresponse()
search.read()
report = {'origin': ORIGIN, 'servedRowsRead': len(served), 'setupPageReads': setup, 'rowsPerRead': 64,
          'admitted': admitted, 'otherStatuses': sorted(set(statuses) - {200}), 'firstRefusal': first_refusal,
          'seconds': round(elapsed, 2), 'serverCpuSeconds': round(cpu1 - cpu0, 3),
          'serverCpuMsPerRequest': round(1000 * (cpu1 - cpu0) / max(1, len(statuses)), 3),
          'pageReadsAfterwards': after, 'searchAfterwards': search.status,
          'loadavg': subprocess.run(['sysctl', '-n', 'vm.loadavg'], capture_output=True, text=True).stdout.strip()}
open(OUT, 'w').write(json.dumps(report, indent=1) + '\n')
print(json.dumps(report))
