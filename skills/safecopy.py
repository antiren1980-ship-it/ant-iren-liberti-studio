#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""safecopy.py — БЕЗОПАСНАЯ КОПИЯ ПРОЕКТА ДЛЯ ПРОВЕРКИ.

Зачем: прежде чем что-то менять в живом проекте — делаем копию и пробуем на ней.
В копию НЕ попадают секреты, базы, логи, кэш — только код и конфиги-шаблоны.

Запуск:
  python3 safecopy.py make <проект>            # сделать копию
  python3 safecopy.py list                     # список копий
  python3 safecopy.py rm <имя>                 # удалить копию
  python3 safecopy.py diff <имя> <файл>        # сравнить файл копии с оригиналом

Пример:
  python3 safecopy.py make /opt/agent_v1
"""
import fnmatch
import os
import shutil
import subprocess
import sys
import time

COPIES_DIR = "/opt/liberti_copies"

# ─── что НЕ копируем (секреты, данные, мусор) ───
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", "venv", ".venv",
    "env", "logs", "backups", ".backups", "data", "generated_projects",
}
EXCLUDE_GLOBS = [
    "*.env", ".env", "*env*.py",          # секреты
    "*.db", "*.sqlite", "*.sqlite3",      # базы данных
    "*.log", "*.db-wal", "*.db-shm",      # логи
    "*.bak", "*.bak_*", "*.old", "*.pyc",  # бэкапы/кэш
    "*token*", "*secret*", "*credential*", "*password*",
    "*.zip", "*.tar", "*.gz",             # архивы
]


def _skip(name: str) -> bool:
    for g in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(name.lower(), g):
            return True
    return False


def make(project: str) -> str:
    """Сделать безопасную копию проекта. Возвращает путь копии."""
    project = os.path.abspath(project.rstrip("/"))
    if not os.path.isdir(project):
        print(f"  ❌ нет папки: {project}")
        return ""
    base = os.path.basename(project)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(COPIES_DIR, f"{base}_{stamp}")
    os.makedirs(dst, exist_ok=True)

    copied = skipped = 0
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel = os.path.relpath(root, project)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for f in files:
            if _skip(f):
                skipped += 1
                continue
            try:
                shutil.copy2(os.path.join(root, f), os.path.join(target, f))
                copied += 1
            except Exception:  # noqa: BLE001
                skipped += 1

    # отметка о копии
    size = _du(dst)
    with open(os.path.join(dst, "_КОПИЯ.txt"), "w", encoding="utf-8") as f:
        f.write(f"Безопасная копия проекта: {project}\n"
                f"Сделана: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Файлов скопировано: {copied}, пропущено (секреты/данные): {skipped}\n"
                f"Размер: {size}\n\n"
                "Это КОПИЯ для проверки. Оригинал не тронут.\n")
    print(f"  ✅ копия: {dst}")
    print(f"     файлов: {copied} · пропущено (секреты/данные): {skipped} · размер: {size}")
    return dst


def _du(path: str) -> str:
    total = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except Exception:  # noqa: BLE001
                pass
    for u, n in (("ГБ", 1 << 30), ("МБ", 1 << 20), ("КБ", 1 << 10)):
        if total >= n:
            return f"{total/n:.1f} {u}"
    return f"{total} Б"


def ls() -> None:
    if not os.path.isdir(COPIES_DIR):
        print("  (копий нет)")
        return
    items = sorted(os.listdir(COPIES_DIR))
    if not items:
        print("  (копий нет)")
        return
    for it in items:
        p = os.path.join(COPIES_DIR, it)
        n = sum(len(fs) for _, _, fs in os.walk(p))
        print(f"  {it}  ({n} файлов, {_du(p)})")


def rm(name: str) -> None:
    p = os.path.join(COPIES_DIR, name)
    if not os.path.isdir(p):
        print(f"  ❌ нет копии: {name}")
        return
    shutil.rmtree(p)
    print(f"  ✅ удалена копия: {name}")


def diff(name: str, relfile: str) -> None:
    """Сравнить файл из копии с оригиналом (что изменилось)."""
    copy_path = os.path.join(COPIES_DIR, name, relfile)
    # оригинал угадываем по имени копии (до _timestamp)
    orig_base = "_".join(name.split("_")[:-2])
    orig_path = os.path.join("/opt", orig_base, relfile)
    if not os.path.exists(copy_path):
        print(f"  ❌ нет в копии: {relfile}")
        return
    if not os.path.exists(orig_path):
        print(f"  ❌ нет в оригинале: {orig_path}")
        return
    r = subprocess.run(["diff", "-u", orig_path, copy_path],
                       capture_output=True, text=True)
    print(r.stdout or "  ✅ файлы идентичны")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "make" and len(sys.argv) > 2:
        make(sys.argv[2])
    elif cmd == "list":
        ls()
    elif cmd == "rm" and len(sys.argv) > 2:
        rm(sys.argv[2])
    elif cmd == "diff" and len(sys.argv) > 3:
        diff(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
