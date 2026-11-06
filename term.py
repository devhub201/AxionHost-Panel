"""AxionHost Panel - in-browser terminal sessions.
One session = one `docker exec -it` / `lxc exec` process on a pty. The browser (xterm.js) sends keystrokes with
POST and receives output by long-polling (works behind Cloudflare Tunnel / waitress, no websocket needed).
Only argv lists built from validated container names are executed - never a shell string from the user."""
import base64, fcntl, os, pty, secrets, signal, struct, subprocess, termios, threading, time

SHELL_CMD = "command -v bash >/dev/null 2>&1 && exec bash -l || exec sh -l"
BUF_MAX = 1 << 20            # keep the last 1 MiB of output per session


class Session:
    def __init__(self, uid, sid, argv, cols, rows, env=None):
        self.uid, self.sid, self.created, self.last_in, self.last_out = uid, sid, time.time(), time.time(), time.time()
        self.master, slave = pty.openpty()
        self._winsize(cols, rows)
        env = dict(env if env is not None else os.environ, TERM="xterm-256color")
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True, env=env)
        os.close(slave)
        self.buf, self.base, self.closed = bytearray(), 0, False      # self.base = absolute offset of buf[0]
        self.cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _winsize(self, cols, rows):
        try: fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError: pass

    def _reader(self):
        while True:
            try: data = os.read(self.master, 65536)
            except OSError: data = b""
            with self.cond:
                if not data:
                    self.closed = True; self.cond.notify_all(); break
                self.buf += data; self.last_out = time.time()
                if len(self.buf) > BUF_MAX:
                    cut = len(self.buf) - BUF_MAX; del self.buf[:cut]; self.base += cut
                self.cond.notify_all()

    def read(self, pos, wait):
        with self.cond:
            if pos < self.base: pos = self.base
            end = lambda: self.base + len(self.buf)
            if pos >= end() and not self.closed and wait > 0: self.cond.wait(wait)
            data = bytes(self.buf[pos - self.base:]) if pos < end() else b""
            return data, pos + len(data), self.closed and pos + len(data) >= end()

    def write(self, data):
        self.last_in = time.time()
        try: os.write(self.master, data)
        except OSError: pass

    def resize(self, cols, rows):
        self._winsize(cols, rows)
        try: os.killpg(self.proc.pid, signal.SIGWINCH)
        except (ProcessLookupError, PermissionError, OSError): pass

    def close(self):
        try: os.killpg(self.proc.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError, OSError): pass
        def reap():
            try: self.proc.wait(3)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError): pass
                try: self.proc.wait(3)
                except subprocess.TimeoutExpired: pass
            try: os.close(self.master)
            except OSError: pass
        threading.Thread(target=reap, daemon=True).start()


class TermManager:
    def __init__(self, per_user=2, total=24, idle=900, max_age=4 * 3600):
        self.s, self.lock = {}, threading.Lock()
        self.per_user, self.total, self.idle, self.max_age = per_user, total, idle, max_age
        threading.Thread(target=self._reap, daemon=True).start()

    def _reap(self):
        while True:
            time.sleep(20)
            now = time.time()
            with self.lock: items = list(self.s.items())
            for tid, se in items:
                if se.closed or now - se.last_in > self.idle or now - se.created > self.max_age:
                    self.close(tid)

    def open(self, uid, sid, argv, cols=100, rows=30, env=None):
        cols, rows = max(20, min(int(cols), 300)), max(5, min(int(rows), 120))
        with self.lock:
            mine = [t for t, x in self.s.items() if x.uid == uid and not x.closed]
            if len(mine) >= self.per_user:                       # replace the oldest instead of failing
                old = min(mine, key=lambda t: self.s[t].created); self._drop(old)
            if len(self.s) >= self.total: raise RuntimeError("Too many open terminals on this panel. Try again shortly.")
            tid = secrets.token_urlsafe(18)
            self.s[tid] = Session(uid, sid, argv, cols, rows, env)
        return tid

    def _drop(self, tid):
        se = self.s.pop(tid, None)
        if se: se.close()

    def get(self, tid, uid):
        se = self.s.get(tid)
        return se if se and se.uid == uid else None

    def close(self, tid):
        with self.lock: self._drop(tid)

    def close_server(self, sid):
        with self.lock:
            for t in [t for t, x in self.s.items() if x.sid == sid]: self._drop(t)


def b64(data): return base64.b64encode(data).decode()
