#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mail.py — ПОЧТА ЛИБЕРТИ (новый орган, 21.09.2026)

Что умею:
  inbox   [N]        — последние N писем (тема, от кого, дата)
  read    <N|id>     — прочитать письмо целиком
  search  <запрос>   — найти письма по слову
  wait    <слово> [сек] — ЖДАТЬ письмо с словом (для кодов подтверждения)
  code    [сек]      — ждать письмо и ВЫТАЩИТЬ КОД/ССЫЛКУ (для регистраций)
  send    <кому> <тема> <текст>  — отправить письмо
  sendfile <кому> <тема> <файл_текста> [вложение ...] — письмо из файла + вложения

ПРОФИЛИ (аккаунты) — переключаются переменной MAIL_PROFILE:
  (по умолчанию)     — faiter11@mail.ru   (SMTP_* / IMAP_*)
  MAIL_PROFILE=gmail — antiren1980@gmail.com (SMTP_GMAIL_* / IMAP_GMAIL_*)

ТОЛЬКО стандартная библиотека. Ключи из /opt/irena_v5/.env
"""
import imaplib
import smtplib
import email
import os
import re
import sys
import time
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

ENV_PATH = "/opt/irena_v5/.env"


def env(key, default=""):
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


# ── ПРОФИЛИ: gmail / mail.ru ────────────────────────────────────────────
PROFILE = os.environ.get("MAIL_PROFILE", "").strip().lower()
if PROFILE == "gmail":
    _P = "GMAIL_"
else:
    _P = ""

IMAP_HOST = env(_P + "IMAP_HOST", "imap.gmail.com" if PROFILE == "gmail" else "imap.mail.ru")
IMAP_USER = env(_P + "IMAP_USER")
IMAP_PASS = env(_P + "IMAP_PASS")
SMTP_HOST = env(_P + "SMTP_HOST", "smtp.gmail.com" if PROFILE == "gmail" else "smtp.mail.ru")
SMTP_PORT = int(env(_P + "SMTP_PORT", "465") or 465)
SMTP_USER = env(_P + "SMTP_USER")
SMTP_PASS = env(_P + "SMTP_PASS")
SMTP_FROM = env(_P + "SMTP_FROM", SMTP_USER)


def dec(s):
    """Декодировать заголовок письма."""
    if not s:
        return ""
    out = ""
    for txt, enc in decode_header(s):
        if isinstance(txt, bytes):
            try:
                out += txt.decode(enc or "utf-8", errors="ignore")
            except (LookupError, UnicodeError):
                out += txt.decode("utf-8", errors="ignore")
        else:
            out += txt
    return out


def connect():
    """Подключиться к почте (IMAP)."""
    m = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    m.login(IMAP_USER, IMAP_PASS)
    return m


def body_text(msg):
    """Достать текст письма."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="ignore")
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="ignore")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="ignore")
        except Exception:
            return str(msg.get_payload())
    return ""


def get_letters(limit=20, query=None):
    """Вернуть список писем (новые сверху)."""
    m = connect()
    m.select("INBOX")
    crit = "(UNSEEN)" if query == ":unseen:" else "ALL"
    typ, data = m.search(None, crit)
    ids = data[0].split()
    if query and query != ":unseen:":
        ids = ids  # фильтруем по тексту ниже
    out = []
    for i in ids[-limit:][::-1]:
        typ, d = m.fetch(i, "(RFC822)")
        msg = email.message_from_bytes(d[0][1])
        out.append({
            "id": i.decode(),
            "subject": dec(msg.get("Subject", "")),
            "from": dec(msg.get("From", "")),
            "date": msg.get("Date", ""),
            "msg": msg,
        })
    m.logout()
    return out


def extract_code(text):
    """Вытащить код или ссылку подтверждения из письма."""
    links = re.findall(r"https?://[^\s\"'<>)]+", text)
    codes = re.findall(r"\b\d{4,8}\b", text)
    return {"links": links[:5], "codes": codes[:5]}


def cmd_inbox(limit=15):
    print(f"📬 ПОЧТА {IMAP_USER} — последние {limit}:\n")
    for L in get_letters(limit):
        print(f"  [{L['id']}] {L['date'][:25]}")
        print(f"      От: {L['from'][:55]}")
        print(f"      Тема: {L['subject'][:70]}\n")


def cmd_read(mid):
    m = connect()
    m.select("INBOX")
    typ, d = m.fetch(mid.encode() if isinstance(mid, str) else mid, "(RFC822)")
    msg = email.message_from_bytes(d[0][1])
    print(f"Тема: {dec(msg.get('Subject',''))}")
    print(f"От:   {dec(msg.get('From',''))}")
    print(f"Дата: {msg.get('Date','')}")
    print("-" * 50)
    print(body_text(msg)[:4000])
    m.logout()


def cmd_search(q):
    print(f"🔍 Ищу '{q}':\n")
    found = 0
    for L in get_letters(60):
        if q.lower() in (L["subject"] + L["from"]).lower():
            print(f"  [{L['id']}] {L['date'][:25]} | {L['from'][:40]}")
            print(f"      {L['subject'][:70]}")
            found += 1
            if found >= 20:
                break
    if not found:
        print("  ничего не найдено")


def cmd_wait(word, seconds=180):
    """Ждать письмо со словом."""
    print(f"⏳ Жду письмо со словом '{word}' (макс {seconds} сек)...")
    t0 = time.time()
    seen = set()
    while time.time() - t0 < seconds:
        try:
            for L in get_letters(10):
                if L["id"] in seen:
                    continue
                seen.add(L["id"])
                if word.lower() in (L["subject"] + L["from"]).lower():
                    print(f"\n✅ НАШЁЛ! [{L['id']}] {L['subject'][:70]}")
                    print(f"   От: {L['from'][:55]}")
                    print("   " + "-" * 45)
                    print(body_text(L["msg"])[:2500])
                    return L
        except Exception as e:
            print(f"  сбой: {str(e)[:60]}")
        time.sleep(10)
    print("❌ не дождался")
    return None


def cmd_code(seconds=180):
    """Ждать письмо и вытащить код/ссылку."""
    print(f"🎯 Жду письмо с кодом подтверждения (макс {seconds} сек)...")
    t0 = time.time()
    seen = set()
    while time.time() - t0 < seconds:
        try:
            for L in get_letters(10):
                if L["id"] in seen:
                    continue
                seen.add(L["id"])
                txt = body_text(L["msg"])
                r = extract_code(txt)
                # ищем письма про подтверждение/регистрацию
                blob = (L["subject"] + " " + L["from"] + " " + txt).lower()
                if any(k in blob for k in ["verif", "confirm", "code", "код",
                                           "подтвержд", "sign in", "signin",
                                           "login", "register", "welcome"]):
                    print(f"\n✅ ПИСЬМО: {L['subject'][:70]}")
                    print(f"   От: {L['from'][:55]}")
                    if r["codes"]:
                        print(f"\n   🔑 КОДЫ: {r['codes']}")
                    if r["links"]:
                        print(f"\n   🔗 ССЫЛКИ:")
                        for l in r["links"]:
                            print(f"      {l[:110]}")
                    print("\n   --- текст ---")
                    print(txt[:1500])
                    return r
        except Exception as e:
            print(f"  сбой: {str(e)[:60]}")
        time.sleep(10)
    print("❌ код не пришёл")
    return None


def cmd_send(to, subj, text, files=None):
    """Отправить письмо (с вложениями, если files не пуст)."""
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subj
    msg.attach(MIMEText(text, "plain", "utf-8"))

    for path in (files or []):
        if not os.path.isfile(path):
            print(f"⚠️  вложения нет, пропускаю: {path}")
            continue
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        name = os.path.basename(path)
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)
        print(f"📎 вложено: {name} ({os.path.getsize(path)} байт)")

    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    s.login(SMTP_USER, SMTP_PASS)
    s.sendmail(SMTP_FROM, [to], msg.as_string())
    s.quit()
    print(f"✅ Отправлено: {to} (профиль: {PROFILE or 'mail.ru'})")


def cmd_sendfile(to, subj, text_file, files=None):
    """Отправить письмо, текст которого лежит в файле (1-я строка 'Тема: ...')."""
    with open(text_file, encoding="utf-8") as f:
        raw = f.read()
    lines = raw.split("\n")
    if lines and lines[0].lower().startswith("тема:"):
        subj = lines[0].split(":", 1)[1].strip()
        raw = "\n".join(lines[1:]).lstrip("\n")
    cmd_send(to, subj, raw, files)


def check():
    try:
        m = connect()
        m.select("INBOX")
        typ, data = m.search(None, "ALL")
        n = len(data[0].split()) if data[0] else 0
        m.logout()
        print(f"✅ ПОЧТА РАБОТАЕТ: {IMAP_USER}, писем: {n}")
        return True
    except Exception as e:
        print(f"❌ ПОЧТА НЕ РАБОТАЕТ: {e}")
        return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    c = sys.argv[1]
    try:
        if c == "inbox":
            cmd_inbox(int(sys.argv[2]) if len(sys.argv) > 2 else 15)
        elif c == "read":
            cmd_read(sys.argv[2])
        elif c == "search":
            cmd_search(sys.argv[2])
        elif c == "wait":
            cmd_wait(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 180)
        elif c == "code":
            cmd_code(int(sys.argv[2]) if len(sys.argv) > 2 else 180)
        elif c == "send":
            cmd_send(sys.argv[2], sys.argv[3], sys.argv[4])
        elif c == "sendfile":
            cmd_sendfile(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
        elif c == "check":
            check()
        else:
            print(__doc__)
    except IndexError:
        print("мало аргументов")
        print(__doc__)


if __name__ == "__main__":
    main()
