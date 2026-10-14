#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""control_tests.py — КОНТРОЛЬНЫЕ ТЕСТЫ Либерти (регрессия).

Что это: набор фиксированных заданий с эталонным ответом. Гоняем их
регулярно и смотрим, не сломалось ли. КАЖДЫЙ результат помечается:
  • какой МОЗГ ответил (cerebras/groq/deepseek/ollama)
  • какой МОДЕЛЬЮ
  • сколько времени, токенов
  • прошёл/провалил

Зачем пометка модели: если сегодня ответил cerebras, а завтра groq —
мы видим, ЧТО изменилось, и не путаем «сломался код» с «сменился мозг».

Запуск:
  python3 control_tests.py            # все тесты
  python3 control_tests.py --list     # список тестов
  python3 control_tests.py --save     # сохранить эталон
  python3 control_tests.py --diff     # сравнить с эталоном
"""
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, "/opt/liberti_skills")
import brain_chain as bc  # noqa: E402

RESULTS_FILE = "/opt/irena_v5/data/control_tests.json"
BASELINE_FILE = "/opt/irena_v5/data/control_tests_baseline.json"

# ─── ТЕСТЫ: задание → проверка ответа ───
# check = функция(ответ) → True/False. Строгие, простые, без «воды».
TESTS = [
    {
        "id": "math_add",
        "q": "Сколько будет 17 плюс 25? Ответь ТОЛЬКО числом.",
        "check": lambda a: "42" in a,
    },
    {
        "id": "math_mul",
        "q": "Сколько будет 6 умножить на 7? Ответь ТОЛЬКО числом.",
        "check": lambda a: "42" in a,
    },
    {
        "id": "json_format",
        "q": 'Верни ТОЛЬКО JSON без пояснений: {"ok": true, "n": 5}',
        "check": lambda a: _is_json(a) and '"ok"' in a.replace("'", '"'),
    },
    {
        "id": "lang_ru",
        "q": "Столица Франции? Ответь одним словом.",
        "check": lambda a: "париж" in a.lower() or "paris" in a.lower(),
    },
    {
        "id": "code_python",
        "q": "Напиши ТОЛЬКО код функции python: def add(a, b) возвращает сумму. Без пояснений.",
        "check": lambda a: "def add" in a and "return" in a,
    },
    {
        "id": "logic_seq",
        "q": "Продолжи: 2, 4, 8, 16, ? Ответь ТОЛЬКО числом.",
        "check": lambda a: "32" in a,
    },
    {
        "id": "instruction_only",
        "q": "Ответь ровно одним словом: БАНАН",
        "check": lambda a: a.strip().upper().startswith("БАНАН"),
    },
    {
        "id": "no_hallucination",
        "q": "Сколько спутников у планеты Земля? Ответь числом.",
        "check": lambda a: "1" in a and "луна" not in a.lower(),
    },
]


def _is_json(a: str) -> bool:
    try:
        json.loads(re.sub(r"^```(?:json)?|```$", "", a.strip(), flags=re.M).strip())
        return True
    except Exception:
        return False


def _ask(q: str) -> dict:
    """Спросить через цепочку мозгов. Возвращает {ok, brain, content, sec}."""
    t0 = time.time()
    try:
        res = bc.ask([{"role": "user", "content": q}])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "brain": None, "content": "", "sec": round(time.time() - t0, 1),
                "error": str(e)[:80]}
    return {
        "ok": bool(res.get("ok")),
        "brain": res.get("brain"),
        "content": (res.get("content") or "").strip(),
        "sec": round(time.time() - t0, 1),
        "error": res.get("error", ""),
    }


def run_all() -> dict:
    """Прогнать все контрольные тесты. Возвращает отчёт."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = {"ts": stamp, "results": [], "passed": 0, "total": len(TESTS), "brains": {}}
    print("═" * 60)
    print(f"  🧪 КОНТРОЛЬНЫЕ ТЕСТЫ — {stamp}")
    print("═" * 60)
    for t in TESTS:
        r = _ask(t["q"])
        ok = bool(r["ok"]) and t["check"](r["content"])
        out["results"].append({
            "id": t["id"], "ok": ok, "brain": r["brain"], "model": _model_of(r["brain"]),
            "sec": r["sec"], "answer": r["content"][:70], "error": r.get("error", ""),
        })
        out["passed"] += 1 if ok else 0
        br = r["brain"] or "—"
        out["brains"][br] = out["brains"].get(br, 0) + 1
        mark = "✅" if ok else "❌"
        print(f"  {mark} {t['id']:<16} мозг={br:<10} {r['sec']:>5}с  {r['content'][:38]}")
    print("─" * 60)
    print(f"  ИТОГ: {out['passed']}/{out['total']} · мозги: {out['brains']}")
    _save_results(out)
    return out


def _model_of(brain) -> str:
    return {
        "cerebras": bc._env("CEREBRAS_MODEL") or "qwen-3.8-27b",
        "groq": bc._env("GROQ_MODEL") or "qwen/qwen3.8-27b",
        "deepseek": bc._env("DEEPSEEK_MODEL") or "deepseek-chat",
        "ollama": bc._env("OLLAMA_MODEL") or "phi4-mini:latest",
    }.get(brain or "", "")


def _save_results(out: dict) -> None:
    try:
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        hist = []
        if os.path.exists(RESULTS_FILE):
            try:
                hist = json.load(open(RESULTS_FILE, encoding="utf-8"))
            except Exception:
                hist = []
        hist.append(out)
        hist = hist[-50:]           # держим 50 последних прогонов
        json.dump(hist, open(RESULTS_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ не сохранить результаты: {e}")


def save_baseline(out: dict) -> None:
    """Зафиксировать эталон (с какой моделью и что ответила)."""
    base = {r["id"]: {"ok": r["ok"], "brain": r["brain"], "model": r["model"], "answer": r["answer"]}
            for r in out["results"]}
    json.dump({"ts": out["ts"], "items": base},
              open(BASELINE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  ✅ эталон сохранён: {BASELINE_FILE}")


def diff_baseline(out: dict) -> None:
    """Сравнить прогон с эталоном — что изменилось."""
    if not os.path.exists(BASELINE_FILE):
        print("  ⚠ эталона нет — сначала запусти с --save")
        return
    base = json.load(open(BASELINE_FILE, encoding="utf-8"))["items"]
    print("─" * 60)
    print("  СРАВНЕНИЕ С ЭТАЛОНОМ:")
    changed = 0
    for r in out["results"]:
        b = base.get(r["id"])
        if not b:
            print(f"  + новый тест: {r['id']}")
            changed += 1
            continue
        if b["ok"] != r["ok"]:
            print(f"  ⚠ {r['id']}: было ok={b['ok']} ({b['brain']}) → стало ok={r['ok']} ({r['brain']})")
            changed += 1
        elif b["brain"] != r["brain"]:
            print(f"  ℹ {r['id']}: мозг сменился {b['brain']} → {r['brain']} (ответ тот же)")
            changed += 1
    if not changed:
        print("  ✅ всё как в эталоне — ничего не сломалось")
    else:
        print(f"  изменений: {changed}")


def main() -> None:
    if "--list" in sys.argv:
        for t in TESTS:
            print(f"  {t['id']:<16} {t['q'][:50]}")
        return
    out = run_all()
    if "--save" in sys.argv:
        save_baseline(out)
    if "--diff" in sys.argv:
        diff_baseline(out)


if __name__ == "__main__":
    main()
