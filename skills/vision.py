#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision.py — ГЛАЗА Либерти. Смотреть картинки, скриншоты, PDF-страницы, документы.

Создано 20.09.2026 по задаче Брата «оживи себя» — чтобы я мог ВИДЕТЬ.
Работает на нашем существующем GEMINI_API_KEY (Google AI Studio, free tier).

Использование:
    python3 vision.py see <файл>              # описать картинку
    python3 vision.py see <файл> "вопрос"     # ответить на вопрос по картинке
    python3 vision.py url <URL>               # посмотреть картинку по ссылке
    python3 vision.py read <файл>             # прочитать текст с картинки (OCR)
    python3 vision.py compare <ф1> <ф2>       # сравнить две картинки
    python3 vision.py check                   # проверить, что глаза работают

Импорт:
    import vision
    print(vision.see("/path/img.jpg", "что тут?"))
    print(vision.read_text("/path/doc.png"))

stdlib-only. Ключ берётся из /opt/irena_v5/.env (GEMINI_API_KEY).
"""

import base64
import json
import logging
import mimetypes
import os
import sys
import urllib.request

log = logging.getLogger("vision")

ENV_PATH = "/opt/irena_v5/.env"
# Модели со зрением (проверено живьём 20.09.2026):
# gemini-flash-latest и gemini-3-flash-preview — рабочие.
# gemini-2.5-flash — отвалилась для новых пользователей.
MODELS = [
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _key():
    """Читает GEMINI_API_KEY из .env."""
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as exc:  # noqa: BLE001 — .env может не быть, возьмём из окружения
        log.debug("не прочитал GEMINI_API_KEY из .env: %r", exc)
    return os.environ.get("GEMINI_API_KEY", "")


def _mime(path):
    m = mimetypes.guess_type(path)[0]
    return m or "image/jpeg"


def _b64_file(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _b64_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return base64.b64encode(data).decode()


def _call(model, parts, key, timeout=90):
    body = json.dumps({"contents": [{"parts": parts}]}).encode()
    url = API.format(model=model, key=key)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    cand = data.get("candidates") or []
    if not cand:
        raise RuntimeError(data.get("error", {}).get("message", "пустой ответ"))
    return "".join(p.get("text", "") for p in cand[0]["content"]["parts"])


def _ask(prompt, images, timeout=90):
    """images: список (base64, mime). Пробует модели по очереди."""
    key = _key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY не найден в " + ENV_PATH)
    last = None
    for model in MODELS:
        try:
            parts = []
            for b64, mime in images:
                parts.append({"inline_data": {"mime_type": mime, "data": b64}})
            parts.append({"text": prompt})
            return _call(model, parts, key, timeout)
        except Exception as e:  # noqa: BLE001 — модель не ответила, пробуем следующую
            log.debug("модель зрения не ответила: %r", e)
            last = e
            continue
    raise RuntimeError("все модели зрения не ответили: %s" % last)


def see(path_or_url, question="Опиши подробно, что на изображении. Отвечай по-русски."):
    """Посмотреть картинку (файл или URL) и ответить на вопрос."""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        b64, mime = _b64_url(path_or_url), "image/jpeg"
    else:
        if not os.path.exists(path_or_url):
            raise FileNotFoundError(path_or_url)
        b64, mime = _b64_file(path_or_url), _mime(path_or_url)
    return _ask(question, [(b64, mime)])


def read_text(path_or_url, lang="русском"):
    """OCR — вытащить весь текст с картинки."""
    q = ("Извлеки ВЕСЬ текст с изображения дословно, как он написан, на %s языке. "
         "Сохрани порядок строк. Если текста нет — так и напиши." % lang)
    return see(path_or_url, q)


def compare(path1, path2, question="В чём разница между этими двумя изображениями? Отвечай по-русски."):
    """Сравнить две картинки."""
    imgs = []
    for p in (path1, path2):
        if p.startswith("http"):
            imgs.append((_b64_url(p), "image/jpeg"))
        else:
            imgs.append((_b64_file(p), _mime(p)))
    return _ask(question, imgs)


def check():
    """Проверить, что глаза работают. Возвращает (ok, текст)."""
    # 1x1 красный PNG — минимальный тест
    px = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    try:
        out = _ask("Что это за изображение? Одним предложением.", [(px, "image/png")], timeout=40)
        return True, out.strip()
    except Exception as e:  # noqa: BLE001 — причина возвращается вызывающему
        return False, str(e)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    try:
        if cmd == "see" and len(args) >= 2:
            print(see(args[1], args[2] if len(args) > 2 else "Опиши подробно, что на изображении. Отвечай по-русски."))
        elif cmd == "url" and len(args) >= 2:
            print(see(args[1], args[2] if len(args) > 2 else "Опиши подробно, что на изображении. Отвечай по-русски."))
        elif cmd == "read" and len(args) >= 2:
            print(read_text(args[1]))
        elif cmd == "compare" and len(args) >= 3:
            print(compare(args[1], args[2]))
        elif cmd == "check":
            ok, txt = check()
            print(("✅ ГЛАЗА РАБОТАЮТ: " if ok else "❌ ПРОБЛЕМА: ") + txt)
        else:
            print(__doc__)
    except Exception as e:  # noqa: BLE001 — CLI-обёртка: печатаем причину и выходим
        print("ОШИБКА: %s" % e)
        sys.exit(1)
