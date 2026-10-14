#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ant Leads Finder — MVP (13.10.2026)

Идея Брата: даёшь ТЕМУ → ИИ читает КОНТЕКСТ собранных заказов →
находит потенциальных КЛИЕНТОВ → отдаёт список (ник/email + суть + почему наш).

Конкурентов 0 (проверено в agent402). У нас уже есть:
  - orders_feed.json  (живой сбор заказов, 90 TG-чатов + Kwork/FL)
  - бесплатный ИИ      (цепочка Groq/Cerebras, 127.0.0.1:8799)

Маржа ~99%: данные + ИИ бесплатны.

Использование:
  python3 leads_finder.py --topic "нужен парсер"        # искать по теме
  python3 leads_finder.py --topic "телеграм бот" --top 20
  python3 leads_finder.py --open-only                   # только с контактом
  python3 leads_finder.py --save                        # сохранить в leads_out/

Не тратит деньги: ИИ бесплатный, данные локальные.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/opt/liberti_skills")

FEED = Path("/opt/liberti_skills/orders_feed.json")
OUT_DIR = Path("/opt/liberti_skills/leads_out")
LOG = Path("/opt/liberti_skills/leads_finder.log")


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_orders() -> list[dict]:
    if not FEED.exists():
        return []
    d = json.loads(FEED.read_text(encoding="utf-8"))
    orders = d if isinstance(d, list) else d.get("orders", [])
    return [o for o in orders if isinstance(o, dict)]


def get_contacts(o: dict) -> tuple[list[str], list[str]]:
    """Возвращает (emails, telegrams) без мусора."""
    c = o.get("contacts") or {}
    if not isinstance(c, dict):
        return [], []
    emails = [e.strip() for e in (c.get("emails") or []) if "@" in str(e)]
    tgs = [t.strip().lstrip("@") for t in (c.get("telegram") or []) if t]
    # отсеиваем авто-ботов/агрегаторы
    bad_e = (".co.uk", "mail.org", "@163.com", "programmiererjobboerse", "heise.de")
    emails = [e for e in emails if not e.lower().endswith(bad_e)]
    tgs = [t for t in tgs if not t.lower().endswith(("bot", "robot"))]
    return emails, tgs


def keyword_match(o: dict, topic_words: list[str]) -> bool:
    text = f"{o.get('title','')} {o.get('snippet','')} {o.get('desc','')}".lower()
    return any(w in text for w in topic_words) if topic_words else True


def ai_context_check(o: dict) -> tuple[bool, str]:
    """ИИ читает КОНТЕКСТ: это живой клиент с задачей по нашей теме?"""
    text = f"{o.get('title','')}\n{o.get('snippet','') or o.get('desc','')}"[:700]
    try:
        import auto_client as ac
    except Exception as e:
        return False, f"ИИ недоступен: {e}"
    prompt = (
        "Ты ищешь КЛИЕНТОВ (людей с задачей) для команды Python-разработчиков.\n"
        "Мы делаем: Telegram-ботов, парсеры, скрипты, автоматизацию, API, ИИ.\n"
        "Прочитай объявление и ответь СТРОГО в формате:\n"
        "ДА|кратко суть задачи (до 12 слов)\n"
        "либо\n"
        "НЕТ|кратко почему не подходит\n\n"
        f"Объявление:\n«{text}»"
    )
    try:
        ans = ac.ask(prompt, timeout=45) or ""
    except Exception as e:
        return False, f"ИИ ошибка: {str(e)[:50]}"
    ans = ans.strip()
    first = ans.split("\n")[0].strip()
    m = re.match(r"(ДА|НЕТ)\s*\|?\s*(.*)", first, re.IGNORECASE)
    if not m:
        return False, ans[:50]
    ok = m.group(1).upper() == "ДА"
    return ok, m.group(2).strip()[:80] or first[:80]


def build(topic: str | None, top: int, open_only: bool, use_ai: bool) -> list[dict]:
    orders = load_orders()
    if not orders:
        log("нет orders_feed.json — нечего искать")
        return []
    word_map = {
        "парсер": ["парсер", "парсинг", "scrap", "сбор данн", "собирать"],
        "бот": ["бот", "telegram", "телеграм", "tg "],
        "автоматиз": ["автоматиз", "скрипт", "рутин"],
        "python": ["python", "питон"],
    }
    topic_words: list[str] = []
    if topic:
        t = topic.lower()
        for key, words in word_map.items():
            if key in t:
                topic_words += words
        if not topic_words:
            topic_words = [w for w in re.findall(r"\w{3,}", t)]

    # шаг 1: грубый отбор по теме
    cand = [o for o in orders if keyword_match(o, topic_words)]
    cand.sort(key=lambda o: -(o.get("score") or 0))
    log(f"тема={topic!r}: кандидатов {len(cand)} из {len(orders)}")

    leads: list[dict] = []
    checked = 0
    for o in cand:
        emails, tgs = get_contacts(o)
        has = bool(emails or tgs)
        if open_only and not has:
            continue
        item = {
            "title": (o.get("title") or "")[:120],
            "url": o.get("url", ""),
            "score": o.get("score"),
            "emails": emails,
            "telegram": tgs,
            "has_contact": has,
            "why_ai": "",
        }
        # ИИ читает контекст (только если есть контакт — экономим: главное — клиент!)
        if use_ai and has:
            ok, why = ai_context_check(o)
            item["why_ai"] = why
            item["our"] = ok
            checked += 1
            if checked % 3 == 0:
                time.sleep(1)
        else:
            item["our"] = None
        leads.append(item)
        if len(leads) >= top:
            break

    # сортируем: сначала с контактом и подтверждённые ИИ
    leads.sort(key=lambda x: (not x["has_contact"], x.get("our") is False, -(x.get("score") or 0)))
    return leads


def show(leads: list[dict]) -> None:
    if not leads:
        print("\n  ничего не нашлось — источники скудны (см. дневник)\n")
        return
    print(f"\n{'='*66}\n  ЖИВЫЕ ЛИДЫ: {len(leads)}\n{'='*66}")
    for i, l in enumerate(leads, 1):
        mark = "🎯" if l["has_contact"] else "  "
        our = {True: "✅ НАШ", False: "❌ не наш", None: "—"}.get(l.get("our"), "—")
        print(f"\n{mark} [{i}] {our} (score={l['score']})")
        print(f"    {l['title']}")
        if l["emails"]:
            print(f"    📧 {', '.join(l['emails'])}")
        if l["telegram"]:
            print(f"    💬 @{', @'.join(l['telegram'])}")
        if l["why_ai"]:
            print(f"    🧠 {l['why_ai']}")
        if l["url"]:
            print(f"    🔗 {l['url'][:80]}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Ant Leads Finder — ИИ находит клиентов по контексту")
    ap.add_argument("--topic", "-t", default=None, help="тема (парсер/бот/автоматизация/python)")
    ap.add_argument("--top", "-n", type=int, default=15, help="сколько лидов")
    ap.add_argument("--open-only", action="store_true", help="только с открытым контактом")
    ap.add_argument("--no-ai", action="store_true", help="без ИИ (быстро, только по словам)")
    ap.add_argument("--save", action="store_true", help="сохранить результат")
    args = ap.parse_args()

    use_ai = not args.no_ai
    leads = build(args.topic, args.top, args.open_only, use_ai)
    show(leads)

    if args.save and leads:
        OUT_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        f = OUT_DIR / f"leads_{stamp}.json"
        f.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  💾 сохранено: {f}")

    log(f"итог: лидов {len(leads)} (с контактом {sum(1 for x in leads if x['has_contact'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
