"""localhost bridge between the collector and the read-only Chrome extension (chrome_extension/bidguard_reader).

The extension's service worker long-polls GET /ext/next, runs the read-only op in the page frames, and POSTs the
result to /ext/result. Bound to 127.0.0.1 only; requests are accepted only with Host 127.0.0.1/localhost and an
Origin that is absent or chrome-extension:// (optionally pinned to one extension id). Nothing leaves the PC.
/test/* serves the offline test pages used by the live test (step 3).
"""
from __future__ import annotations

import itertools
import json
import mimetypes
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 17321
TESTPAGE_DIR = Path(__file__).resolve().parent / 'testpage'


class ExtensionMissing(RuntimeError):
    """The read-only extension is not polling (not installed / Chrome closed / disabled)."""


class Bridge:
    def __init__(self, port=DEFAULT_PORT, host='127.0.0.1', extension_id='', testpage_dir=None, log=None,
                 poll_wait=8.0):
        self.port, self.host = int(port), host
        self.extension_id = str(extension_id or '').strip()
        self.testpage_dir = Path(testpage_dir or TESTPAGE_DIR)
        self.log = log or (lambda kind, **kw: None)
        self.poll_wait = float(poll_wait)
        self.cv = threading.Condition()
        self.queue = deque()
        self.results = {}
        self.ids = itertools.count(1)
        self.last_poll = 0.0
        self.ext_info = {}
        self.server = None
        self.thread = None
        self.rejected = 0

    # ---------------------------------------------------------------- server
    def start(self):
        bridge = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):  # quiet
                pass

            def _allowed(self):
                host = (self.headers.get('Host') or '').split(':')[0].lower()
                origin = self.headers.get('Origin') or ''
                if host not in ('127.0.0.1', 'localhost'):
                    return False
                if origin and not origin.startswith('chrome-extension://'):
                    return False
                if origin and bridge.extension_id and origin != 'chrome-extension://' + bridge.extension_id:
                    return False
                return True

            def _send(self, code, body=b'', ctype='application/json; charset=utf-8'):
                self.send_response(code)
                origin = self.headers.get('Origin') or ''
                if origin.startswith('chrome-extension://'):
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                    self.send_header('Access-Control-Allow-Methods', 'GET, POST')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, code, obj):
                self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

            def do_OPTIONS(self):  # noqa: N802
                if not self._allowed():
                    bridge.rejected += 1
                    return self._send(403)
                self._send(204)

            def do_GET(self):  # noqa: N802
                if not self._allowed():
                    bridge.rejected += 1
                    return self._send(403)
                path = self.path.split('?', 1)[0]
                if path == '/ext/next':
                    if 'v=' in self.path:
                        bridge.ext_info['version'] = self.path.split('v=', 1)[1].split('&')[0]
                    cmd = bridge._next(bridge.poll_wait)
                    return self._json(200, cmd) if cmd else self._send(204)
                if path == '/ext/ping':
                    return self._json(200, {'ok': True, 'connected': bridge.connected()})
                if path.startswith('/test/'):
                    name = path[len('/test/'):] or 'index.html'
                    f = (bridge.testpage_dir / name).resolve()
                    if bridge.testpage_dir.resolve() not in f.parents or not f.is_file():
                        return self._send(404)
                    ctype = (mimetypes.guess_type(str(f))[0] or 'text/plain') + '; charset=utf-8'
                    return self._send(200, f.read_bytes(), ctype)
                return self._send(404)

            def do_POST(self):  # noqa: N802
                if not self._allowed():
                    bridge.rejected += 1
                    return self._send(403)
                n = int(self.headers.get('Content-Length') or 0)
                if n > 20 * 1024 * 1024:
                    return self._send(413)
                try:
                    data = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
                except Exception:  # noqa: BLE001
                    return self._send(400)
                path = self.path.split('?', 1)[0]
                if path == '/ext/result':
                    bridge._result(data)
                    return self._json(200, {'ok': True})
                if path == '/ext/hello':
                    bridge.ext_info.update({k: data.get(k) for k in ('version', 'ua') if k in data})
                    bridge.last_poll = time.time()
                    return self._json(200, {'ok': True})
                return self._send(404)

        self.server = ThreadingHTTPServer((self.host, self.port), H)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name='bidguard-bridge', daemon=True)
        self.thread.start()
        self.log('BRIDGE_START', port=self.port)
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # ---------------------------------------------------------------- queue
    def _next(self, wait):
        end = time.time() + wait
        with self.cv:
            self.last_poll = time.time()
            while not self.queue:
                left = end - time.time()
                if left <= 0:
                    self.last_poll = time.time()
                    return None
                self.cv.wait(left)
            self.last_poll = time.time()
            return self.queue.popleft()

    def _result(self, data):
        with self.cv:
            self.results[data.get('id')] = data
            self.cv.notify_all()

    def connected(self, max_age=15.0):
        return (time.time() - self.last_poll) < max_age

    def wait_connected(self, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.connected(max_age=self.poll_wait + 4):
                return True
            time.sleep(0.2)
        return False

    def call(self, op, args=None, frames='all', tab=None, timeout=5.0):
        if not self.connected(max_age=self.poll_wait + 7):
            raise ExtensionMissing('BIDGUARD reader extension is not connected (Chrome closed or extension off)')
        cid = next(self.ids)
        cmd = {'id': cid, 'op': op, 'args': args or {}, 'frames': frames, 'tab': tab,
               'timeout_ms': int(max(500, min(timeout * 1000 - 300, 4000)))}
        end = time.time() + timeout + 2.0
        with self.cv:
            self.queue.append(cmd)
            self.cv.notify_all()
            while cid not in self.results:
                left = end - time.time()
                if left <= 0:
                    try:
                        self.queue.remove(cmd)
                    except ValueError:
                        pass
                    raise ExtensionMissing('extension did not answer %s within %.0fs' % (op, timeout))
                self.cv.wait(left)
            return self.results.pop(cid)
