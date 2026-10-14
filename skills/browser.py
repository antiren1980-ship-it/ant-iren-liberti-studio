#!/usr/bin/env python3
"""browser.py — Браузер-скилл агента (camofox + Playwright fallback).

Главный движок: **camofox-browser** — анти-детект браузер (Firefox/Camoufox),
обходит Cloudflare и 403 там, где Playwright/curl отбиваются
(проверено 15.09.2026: Upwork, StackOverflow, LinkedIn — OK; Reddit — блок по IP).

Если camofox недоступен — автоматический откат на Playwright (как было раньше).

Использование (CLI):
  python3 browser.py fetch <url>                  — HTML страницы
  python3 browser.py text <url>                   — чистый текст (accessibility-снимок)
  python3 browser.py screenshot <url> [out.png]   — скриншот
  python3 browser.py links <url> [n]              — ссылки со страницы

Импорт:
  import browser
  browser.fetch(url) / browser.text(url) / browser.links(url) / browser.search(q)
  browser.status()  — здоровье camofox
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("browser")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# camofox-browser (systemd: camofox.service, порт 9377)
CAMOFOX_URL = os.environ.get("CAMOFOX_URL", "http://localhost:9377")
CAMOFOX_USER = os.environ.get("CAMOFOX_USER", "liberti")
CAMOFOX_SESSION = os.environ.get("CAMOFOX_SESSION", "default")

# Анти-бот ожидание (находка 13.10.2026): Cloudflare turnstile/Anubis проходят за пару секунд.
ANTIBOT_TRIES = int(os.environ.get("ANTIBOT_TRIES", "4"))    # сколько раз проверять снимок
ANTIBOT_WAIT = float(os.environ.get("ANTIBOT_WAIT", "2.5"))  # пауза между проверками, сек

# Признаки стены защиты в снимке (если текст содержит это — считаем, что ещё проверка).
_BLOCK_MARKERS = (
    "just a moment", "security verification", "performing security",
    "checking your browser", "verify you are human", "attention required",
    "cf-challenge", "not a bot", "enable javascript and cookies",
    "ddos protection", "please wait while we verify",
    # короткие заглушки (тренировка №2, 13.10): гео/IP-блоки выдавали себя ими
    "whoa there", "blocked due", "access denied", "forbidden",
    "не соединения", "выключите vpn", "restricted", "unavailable in your",
    "not available in your country", "geo-block", "region blocked",
)


def _looks_blocked(text: str) -> bool:
    """Похож ли снимок на страницу-заглушку защиты (а не на реальный контент)."""
    if not text:
        return True
    head = text[:4000].lower()
    if any(m in head for m in _BLOCK_MARKERS):
        return True
    # очень короткая страница (<250 симв) почти всегда = заглушка, а не контент
    if len(text.strip()) < 250:
        return True
    return False


# ─────────────────────────── camofox (основной движок) ───────────────────────────

def _camofox_alive(timeout: int = 5) -> bool:
    """Проверить, поднят ли camofox."""
    try:
        with urllib.request.urlopen(f"{CAMOFOX_URL}/", timeout=timeout) as r:
            return json.load(r).get("enabled", False)
    except Exception as exc:  # noqa: BLE001 — status-чек, молчание = «не жив»
        log.debug("camofox alive-чек не удался: %r", exc)
        return False


def _camofox_open(url: str, timeout: int = 90) -> str | None:
    """Открыть вкладку в camofox. Возвращает tabId или None при неудаче."""
    body = json.dumps({
        "url": url,
        "userId": CAMOFOX_USER,
        "sessionKey": CAMOFOX_SESSION,
    }).encode()
    req = urllib.request.Request(f"{CAMOFOX_URL}/tabs", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
            return d.get("tabId")
    except Exception as exc:  # noqa: BLE001 — откроем через Playwright-откат
        log.warning("camofox не открыл вкладку: %r", exc)
        return None


def _camofox_snapshot(tab_id: str, timeout: int = 60) -> dict:
    """Получить accessibility-снимок вкладки (компактнее HTML ~на 90%)."""
    q = urllib.parse.urlencode({"userId": CAMOFOX_USER, "sessionKey": CAMOFOX_SESSION})
    try:
        with urllib.request.urlopen(f"{CAMOFOX_URL}/tabs/{tab_id}/snapshot?{q}",
                                    timeout=timeout) as r:
            return json.load(r)
    except Exception as exc:  # noqa: BLE001 — откат на HTML, если снимок не вышел
        log.warning("camofox snapshot не удался: %r", exc)
        return {}


def _via_camofox(url: str, wait: float = 3.0) -> dict:
    """Открыть страницу через camofox и вернуть {'text','html','status','ok'}.

    Умное ожидание (находка 13.10.2026): Cloudflare turnstile / Anubis
    проходят НЕ мгновенно. Если снимок похож на стену защиты — ждём и
    перепроверяем (до ANTIBOT_TRIES раз), беря самый длинный снимок.
    """
    tab = _camofox_open(url)
    if not tab:
        return {"ok": False}
    if wait:
        time.sleep(wait)  # дать странице дорисоваться (SPA)
    text, snap = "", {}
    for i in range(ANTIBOT_TRIES):
        snap = _camofox_snapshot(tab)
        cur = snap.get("snapshot", "") or ""
        if len(cur) > len(text):
            text = cur
        if text and not _looks_blocked(text):
            break                     # стены нет — выходим сразу
        time.sleep(ANTIBOT_WAIT)      # стена или пусто — ждём прохождения проверки
    return {
        "ok": bool(text),
        "text": text,
        "html": text,  # camofox отдаёт accessibility-снимок вместо сырого HTML
        "status": snap.get("httpStatus"),
        "url": snap.get("url", url),
        "chars": len(text),
    }


# ─────────────────────────── Playwright (запасной движок) ───────────────────────────

def _playwright_page():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
    return p, browser, ctx.new_page()


def _pw_fetch(url: str, timeout: int = 30000) -> str:
    p, b, pg = _playwright_page()
    try:
        pg.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return pg.content()
    finally:
        b.close()
        p.stop()


def _pw_text(url: str, timeout: int = 30000) -> str:
    p, b, pg = _playwright_page()
    try:
        pg.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return pg.inner_text("body")
    finally:
        b.close()
        p.stop()


# ─────────────────────────── публичный API ───────────────────────────

def fetch(url: str, timeout: int = 30000, engine: str = "auto") -> str:
    """HTML/снимок страницы. engine: auto | camofox | playwright."""
    if engine in ("auto", "camofox") and _camofox_alive():
        r = _via_camofox(url)
        if r.get("ok"):
            return r["html"]
        if engine == "camofox":
            return ""
    return _pw_fetch(url, timeout)


def text(url: str, timeout: int = 30000, engine: str = "auto") -> str:
    """Чистый текст страницы. По умолчанию — через camofox (обход блокировок)."""
    if engine in ("auto", "camofox") and _camofox_alive():
        r = _via_camofox(url)
        if r.get("ok"):
            return r["text"]
        if engine == "camofox":
            return ""
    return _pw_text(url, timeout)


def screenshot(url: str, path: str, timeout: int = 30000, full_page: bool = False) -> str:
    """Скриншот страницы (Playwright — camofox отдаёт base64 в снимке)."""
    p, b, pg = _playwright_page()
    try:
        pg.goto(url, timeout=timeout, wait_until="domcontentloaded")
        pg.screenshot(path=path, full_page=full_page)
        return path
    finally:
        b.close()
        p.stop()


def status() -> dict:
    """Здоровье движков: camofox жив? playwright стоит?"""
    out = {"camofox": False, "camofox_url": CAMOFOX_URL, "playwright": False}
    try:
        with urllib.request.urlopen(f"{CAMOFOX_URL}/", timeout=5) as r:
            d = json.load(r)
            out["camofox"] = bool(d.get("enabled"))
            out["camofox_running"] = bool(d.get("running"))
            out["camofox_engine"] = d.get("engine")
    except Exception as e:  # noqa: BLE001 — причина уже пишется в out["camofox_error"]
        out["camofox_error"] = str(e)[:80]
    try:
        import playwright  # noqa: F401
        out["playwright"] = True
    except Exception as exc:  # noqa: BLE001 — просто отметим, что движка нет
        log.debug("playwright недоступен: %r", exc)
    return out


def search(query: str, n: int = 8, lang: str = "en") -> list:
    """Веб-поиск через Wikipedia API (работает с дата-центровых IP)."""
    api = (f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search"
           f"&srsearch={urllib.parse.quote(query)}&format=json&srlimit={n}")
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    out = []
    for it in data.get("query", {}).get("search", []):
        title = it.get("title", "")
        out.append({
            "title": title,
            "url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "snippet": (it.get("snippet", "") or "").replace('<span class="searchmatch">', '').replace("</span>", ""),
        })
    return out


def links(url: str, n: int = 50, engine: str = "auto") -> list:
    """Собрать внешние ссылки со страницы."""
    if engine in ("auto", "camofox") and _camofox_alive():
        r = _via_camofox(url)
        if r.get("ok"):
            # из accessibility-снимка: строки вида '- /url: https://...'
            out = []
            for line in r["text"].splitlines():
                line = line.strip()
                if line.startswith("- /url: http"):
                    href = line.split("/url: ", 1)[1].strip()
                    if href not in out:
                        out.append(href)
                    if len(out) >= n:
                        break
            if out:
                return out
    out = []
    p, b, pg = _playwright_page()
    try:
        pg.goto(url, timeout=30000, wait_until="domcontentloaded")
        for a in pg.query_selector_all("a[href]")[:n]:
            href = a.get_attribute("href") or ""
            if href.startswith("http"):
                out.append(href)
    finally:
        b.close()
        p.stop()
    return out


# ═══════════════════ БОЕВЫЕ РУКИ ЧЕРЕЗ CAMOFOX (13.10.2026, тренировка №5) ═══════════════════
#
# ЗАЧЕМ: раньше руки (клик/печать) были только на Playwright — а он защит НЕ проходит
# (0 символов на защищённом сайте). camofox же проходит ВСЁ и умеет то же самое.
# Здесь соединяем: ГЛАЗА (snapshot) + РУКИ (type/click/press/evaluate) → боевая рука.
# Документация: http://127.0.0.1:9377/openapi.json

def camo_call(path: str, data: dict | None = None, timeout: int = 90,
              method: str | None = None) -> dict:
    """Универсальный вызов camofox API (боевая рука)."""
    body = json.dumps(data).encode() if data is not None else None
    m = method or ("POST" if data is not None else "GET")
    req = urllib.request.Request(f"{CAMOFOX_URL}{path}", data=body, method=m)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001 — не всё отдаёт JSON
            return {"raw": raw}


def _camo_user() -> str:
    return CAMOFOX_USER


def camo_open(url: str, wait: float = 3.0) -> dict:
    """Открыть вкладку и дождаться прохождения защиты. → {tab, url, chars, blocked}."""
    d = camo_call("/tabs/open", {"userId": _camo_user(), "url": url})
    tab = d.get("tabId") or d.get("targetId")
    if not tab:
        return {"ok": False, "error": "вкладка не открылась", "raw": d}
    if wait:
        time.sleep(wait)
    text = ""
    for _ in range(ANTIBOT_TRIES):
        snap = camo_call(f"/tabs/{tab}/snapshot?userId={_camo_user()}")
        cur = snap.get("snapshot", "") or ""
        if len(cur) > len(text):
            text = cur
        if text and not _looks_blocked(text):
            break
        time.sleep(ANTIBOT_WAIT)
    return {"ok": bool(text), "tab": tab, "url": url, "chars": len(text),
            "blocked": _looks_blocked(text), "text": text}


def camo_type(tab: str, selector: str, text: str, clear: bool = True,
              submit: bool = False) -> dict:
    """Напечатать в поле. selector — CSS. → {ok}."""
    return camo_call(f"/tabs/{tab}/type", {
        "userId": _camo_user(), "selector": selector,
        "text": text, "clear": clear, "submit": submit})


def camo_click(tab: str, selector: str = "", ref: str = "") -> dict:
    """Кликнуть. Лучше по ref (из snapshot), CSS тоже можно. → {ok}."""
    d = {"userId": _camo_user()}
    if ref:
        d["ref"] = ref
    elif selector:
        d["selector"] = selector
    return camo_call(f"/tabs/{tab}/click", d)


def camo_press(tab: str, key: str = "Enter") -> dict:
    """Нажать клавишу (Enter/Tab/Escape/ArrowDown...). → {ok}."""
    return camo_call(f"/tabs/{tab}/press", {"userId": _camo_user(), "key": key})


def camo_js(tab: str, expression: str) -> dict:
    """Выполнить JavaScript на странице (боевое зрение/чтение). → {result}."""
    return camo_call(f"/tabs/{tab}/evaluate",
                     {"userId": _camo_user(), "expression": expression})


def camo_snapshot(tab: str) -> str:
    """Текстовый accessibility-снимок вкладки (глаза)."""
    d = camo_call(f"/tabs/{tab}/snapshot?userId={_camo_user()}")
    return d.get("snapshot", "") or ""


def camo_find_ref(tab: str, label: str) -> str:
    """НАЙТИ элемент по подписи в снимке и вернуть его ref (напр. 'e13').

    Это и есть «соединение глаз и рук»: не знаем CSS — ищем по тексту.
    """
    import re as _re
    snap = camo_snapshot(tab)
    label_l = label.lower()
    for line in snap.split("\n"):
        if label_l in line.lower():
            m = _re.search(r"\[(e\d+)\]", line)
            if m:
                return m.group(1)
    return ""


# Псевдонимы полей: по имени поля ищем подходящее (умное автозаполнение).
_FIELD_HINTS = {
    "email": ["email", "e-mail", "почта", "correo", "courriel", "メール", "이메일"],
    "username": ["username", "user name", "login", "ник", "имя", "name", "ユーザー"],
    "password": ["password", "passwd", "пароль", "contraseña", "パスワード", "비밀번호"],
    "phone": ["phone", "tel", "mobile", "телефон", "teléfono", "電話", "전화"],
    "name": ["first name", "full name", "your name", "customer name", "имя",
             "фио", "полное имя", "vorname", "nombre", "名字", "이름"],
    "company": ["company", "organisation", "organization", "компания"],
    "message": ["message", "comment", "сообщение", "mensaje", "コメント"],
}


def camo_fill_form(tab: str, values: dict, submit_label: str = "") -> dict:
    """УМНОЕ АВТОЗАПОЛНЕНИЕ: сам находит поля по смыслу и печатает.

    values: {'email': 'x@y.z', 'name': 'Иван', ...} — ключи как в _FIELD_HINTS.
    submit_label: подпись кнопки (напр. 'Submit', 'Отправить') → найдём её ref и кликнем.
    → {filled:{field:ok}, submitted:bool}
    """
    snap = camo_snapshot(tab)
    lines = snap.split("\n")
    filled: dict[str, bool] = {}
    for field, val in values.items():
        hints = _FIELD_HINTS.get(field, [field])
        sel = ""
        ref_hit = ""
        for line in lines:
            low = line.lower()
            if not any(h in low for h in hints):
                continue
            # A) поле описано как textbox "Подпись" [eN] → берём ref и печатаем по нему
            if any(k in low for k in ("textbox", "searchbox", "input", "textarea")):
                import re as _re
                mr = _re.search(r"\[(e\d+)\]", line)
                if mr:
                    ref_hit = mr.group(1)
                    break
                # B) если есть name/id — используем CSS
                m = _re.search(r"(name|id)=\"?'?([\w\-]+)", line)
                if m:
                    sel = f"input[{m.group(1)}='{m.group(2)}']"
                    break
        if ref_hit:
            res = camo_call(f"/tabs/{tab}/type",
                            {"userId": _camo_user(), "ref": ref_hit,
                             "text": val, "clear": True})
            if not res.get("ok"):
                # печатаем по ref через фокус-клик + type без selector
                camo_click(tab, ref=ref_hit)
                res = camo_call(f"/tabs/{tab}/type",
                                {"userId": _camo_user(), "text": val, "clear": True})
            filled[field] = bool(res.get("ok"))
        elif sel:
            filled[field] = bool(camo_type(tab, sel, val).get("ok"))
        else:
            filled[field] = False
    submitted = False
    if submit_label:
        ref = camo_find_ref(tab, submit_label)
        if ref:
            submitted = bool(camo_click(tab, ref=ref).get("ok"))
    return {"filled": filled, "submitted": submitted}


# ─────────────────────── ВЗАИМОДЕЙСТВИЕ (задача 1-3, 30.09.2026) ───────────────────────
#
# До этого browser.py умел только читать (fetch/text/screenshot/links).
# Теперь умеет ДЕЙСТВОВАТЬ: click, fill, select, scroll, wait, login (с сохранением сессии).
# Реализация — живая сессия Playwright: открываем страницу один раз и работаем в ней.

COOKIES_DIR = os.environ.get("LIBERTI_COOKIES_DIR", "/opt/irena_v5/data/browser_sessions")


class Session:
    """Живая браузерная сессия. Работает, пока не вызовешь close()."""

    def __init__(self, headless: bool = True, storage: str | None = None):
        self._p = None
        self._b = None
        self._ctx = None
        self._pg = None
        self._storage = storage          # путь к JSON с cookies/localStorage
        self._headless = headless

    def start(self) -> "Session":
        from playwright.sync_api import sync_playwright
        self._p = sync_playwright().start()
        self._b = self._p.chromium.launch(headless=self._headless)
        kwargs = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}}
        if self._storage and os.path.exists(self._storage):
            kwargs["storage_state"] = self._storage
        self._ctx = self._b.new_context(**kwargs)
        self._ctx.set_default_timeout(20000)
        self._pg = self._ctx.new_page()
        return self

    def close(self) -> None:
        try:
            if self._storage and self._ctx:
                os.makedirs(os.path.dirname(self._storage), exist_ok=True)
                self._ctx.storage_state(path=self._storage)
        except Exception as exc:                       # noqa: BLE001
            log.debug("не сохранил сессию: %r", exc)
        for obj, meth in ((self._b, "close"), (self._p, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:                          # noqa: BLE001
                pass

    def __enter__(self) -> "Session":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def _page(self):
        if self._pg is None:
            self.start()
        return self._pg

    def open(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> str:
        """Открыть URL в сессии. Возвращает финальный URL (после редиректов)."""
        self._page().goto(url, wait_until=wait_until, timeout=timeout)
        return self._page().url

    def click(self, selector: str, timeout: int = 15000) -> bool:
        """Кликнуть по элементу — УМНЫЙ поиск.

        Пробует по очереди:
          1) CSS-селектор (pg.click)
          2) locator (для text=, role=, XPath)
          3) если selector — это ссылка вида http..., ищем по link и по href-фрагменту
          4) если selector — обычный текст, пробуем клик по видимому тексту
        30.09.2026: CSS-селектор с кириллицей/%-кодировкой в href часто НЕ находится
        (query_selector возвращает 0), а get_by_role("link", name=...) — находит.
        """
        pg = self._page()
        # 1) обычный CSS-клик
        try:
            pg.click(selector, timeout=timeout)
            return True
        except Exception:                              # noqa: BLE001
            pass
        # 2) через locator
        try:
            pg.locator(selector).first.click(timeout=timeout)
            return True
        except Exception:                              # noqa: BLE001
            pass
        # 3) ссылка: ищем по имени ссылки (текст или href-хвост) и по ПОДСТРОКЕ href
        link_name = selector
        m = __import__("re").search(r'href=["\']?([^"\']+)', selector)
        if m:
            href_val = m.group(1)
            tail = href_val.rstrip("/").split("/")[-1]
            link_name = __import__("urllib.parse", fromlist=["unquote"]).unquote(tail).replace("_", " ")
            # 3a) по ПОДСТРОКЕ href (решает процентную кодировку: a[href*="..."])
            try:
                pg.locator('a[href*="%s"]' % href_val).first.click(timeout=timeout)
                return True
            except Exception:                          # noqa: BLE001
                pass
        try:
            pg.get_by_role("link", name=link_name).first.click(timeout=timeout)
            return True
        except Exception:                              # noqa: BLE001
            pass
        # 4) как текст
        try:
            pg.get_by_text(link_name, exact=False).first.click(timeout=timeout)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("click(%s) не удался: %r", selector, exc)
            return False

    def click_text(self, text: str, exact: bool = False, timeout: int = 15000) -> bool:
        """Кликнуть по видимому тексту (русский/любой)."""
        try:
            self._page().get_by_text(text, exact=exact).first.click(timeout=timeout)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("click_text(%r) не удался: %r", text, exc)
            return False

    def fill(self, selector: str, value: str, timeout: int = 15000) -> bool:
        """Заполнить поле ввода."""
        try:
            self._page().fill(selector, value, timeout=timeout)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("fill(%s) не удался: %r", selector, exc)
            return False

    def type_text(self, selector: str, value: str, delay: int = 40, timeout: int = 15000) -> bool:
        """Печатать по буквам (для сайтов, что следят за вводом)."""
        try:
            self._page().type(selector, value, delay=delay, timeout=timeout)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("type(%s) не удался: %r", selector, exc)
            return False

    def select(self, selector: str, value: str = "", label: str = "", timeout: int = 15000) -> bool:
        """Выбрать в <select> по value или label."""
        try:
            if label:
                self._page().select_option(selector, label=label, timeout=timeout)
            else:
                self._page().select_option(selector, value=value, timeout=timeout)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("select(%s) не удался: %r", selector, exc)
            return False

    def scroll(self, x: int = 0, y: int = 0, to_bottom: bool = False,
               selector: str = "", timeout: int = 15000) -> bool:
        """Прокрутить: до элемента (selector), вниз на y пикселей, либо до конца."""
        try:
            pg = self._page()
            if selector:
                pg.locator(selector).scroll_into_view_if_needed(timeout=timeout)
            elif to_bottom:
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                pg.evaluate(f"window.scrollBy({x}, {y})")
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("scroll не удался: %r", exc)
            return False

    def wait(self, ms: int = 1000) -> bool:
        """Просто подождать миллисекунды."""
        try:
            self._page().wait_for_timeout(ms)
            return True
        except Exception:                              # noqa: BLE001
            return False

    def wait_for(self, selector: str, timeout: int = 15000, state: str = "visible") -> bool:
        """Ждать появления элемента."""
        try:
            self._page().wait_for_selector(selector, timeout=timeout, state=state)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("wait_for(%s) не удался: %r", selector, exc)
            return False

    def text(self) -> str:
        """Текст текущей страницы."""
        try:
            return self._page().inner_text("body")
        except Exception:                              # noqa: BLE001
            return ""

    def html(self) -> str:
        """HTML текущей страницы."""
        try:
            return self._page().content()
        except Exception:                              # noqa: BLE001
            return ""

    def url(self) -> str:
        try:
            return self._page().url
        except Exception:                              # noqa: BLE001
            return ""

    def screenshot(self, path: str, full_page: bool = False) -> str:
        self._page().screenshot(path=path, full_page=full_page)
        return path

    def find(self, selector: str, attr: str = "") -> str:
        """Текст (или атрибут) первого совпавшего элемента.

        attr="value" читает текущее значение поля (через input_value) — 30.09.2026.
        """
        try:
            el = self._page().query_selector(selector)
            if not el:
                return ""
            if attr == "value":
                try:
                    return el.input_value()
                except Exception:                      # noqa: BLE001
                    return el.get_attribute("value") or ""
            return el.get_attribute(attr) if attr else (el.inner_text() or "").strip()
        except Exception:                              # noqa: BLE001
            return ""

    def count(self, selector: str) -> int:
        """Сколько элементов на странице."""
        try:
            return len(self._page().query_selector_all(selector))
        except Exception:                              # noqa: BLE001
            return 0

    def login(self, url: str, user_sel: str, user: str, pass_sel: str, password: str,
              submit_sel: str = "", success_sel: str = "", wait_ms: int = 2500) -> dict:
        """Войти на сайт и СОХРАНИТЬ сессию (cookies пишутся при close())."""
        out = {"ok": False, "url": "", "reason": ""}
        try:
            self.open(url)
            self.wait(800)
            if success_sel and self.count(success_sel) > 0:
                out.update(ok=True, url=self.url(), reason="уже авторизован (сессия из файла)")
                return out
            if not self.fill(user_sel, user):
                out["reason"] = f"не нашёл поле логина: {user_sel}"
                return out
            if not self.fill(pass_sel, password):
                out["reason"] = f"не нашёл поле пароля: {pass_sel}"
                return out
            self.wait(300)
            if submit_sel:
                self.click(submit_sel)
            else:
                self._page().keyboard.press("Enter")
            self.wait(wait_ms)
            out.update(ok=True, url=self.url(), reason="форма отправлена")
            if success_sel and self.count(success_sel) == 0:
                out.update(ok=False, reason="после входа не появился признак успеха")
            return out
        except Exception as exc:                       # noqa: BLE001
            out["reason"] = f"{type(exc).__name__}: {str(exc)[:100]}"
            return out


# ─── быстрые одноразовые функции (без ручного Session) ───

def click(url: str, selector: str, wait_ms: int = 1200) -> dict:
    """Открыть URL, кликнуть, вернуть {ok, url, text}. Одноразовая сессия."""
    with Session() as s:
        s.open(url)
        ok = s.click(selector)
        s.wait(wait_ms)
        return {"ok": ok, "url": s.url(), "text": s.text()[:5000]}


def fill(url: str, selector: str, value: str, submit: str = "", wait_ms: int = 1500) -> dict:
    """Открыть URL, заполнить поле (и, если дано, отправить)."""
    with Session() as s:
        s.open(url)
        ok = s.fill(selector, value)
        if ok and submit:
            s.click(submit)
        s.wait(wait_ms)
        return {"ok": ok, "url": s.url(), "text": s.text()[:5000]}


def form(url: str, fields: dict, submit: str = "", wait_ms: int = 2000) -> dict:
    """Заполнить ФОРМУ (несколько полей) и отправить."""
    with Session() as s:
        s.open(url)
        filled = {}
        for sel, val in fields.items():
            filled[sel] = s.fill(sel, str(val))
        if submit:
            s.click(submit)
        s.wait(wait_ms)
        return {"ok": all(filled.values()), "filled": filled, "url": s.url(), "text": s.text()[:5000]}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        url = sys.argv[2] if len(sys.argv) > 2 else "https://example.com"
        if cmd == "fetch":
            print(fetch(url))
        elif cmd == "text":
            print(text(url))
        elif cmd == "screenshot":
            out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/shot.png"
            print(screenshot(url, out))
        elif cmd == "links":
            n = int(sys.argv[3]) if len(sys.argv) > 3 else 50
            print("\n".join(links(url, n)))
        elif cmd == "click":
            sel = sys.argv[3] if len(sys.argv) > 3 else "a"
            r = click(url, sel)
            print(json.dumps({"ok": r["ok"], "url": r["url"]}, ensure_ascii=False))
        elif cmd == "fill":
            sel = sys.argv[3] if len(sys.argv) > 3 else "input"
            val = sys.argv[4] if len(sys.argv) > 4 else ""
            sub = sys.argv[5] if len(sys.argv) > 5 else ""
            r = fill(url, sel, val, sub)
            print(json.dumps({"ok": r["ok"], "url": r["url"]}, ensure_ascii=False))
        else:
            print("unknown command", file=sys.stderr)
            sys.exit(1)
