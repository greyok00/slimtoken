#!/usr/bin/env python3

import sqlite3, sys, os
from pathlib import Path

DB = Path.home() / ".config/cortexllm/cortexllm.db"


def query(sql, params=()):
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def list_categories():
    rows = query("SELECT category, COUNT(*) as c FROM Coding_Practices GROUP BY category ORDER BY c DESC")
    print(f"{'Category':<30} {'Count':>6}")
    print(f"{'─'*30} {'─'*6}")
    for cat, count in rows:
        print(f"{cat:<30} {count:>6}")
    total = sum(r[1] for r in rows)
    print(f"{'─'*30} {'─'*6}")
    print(f"{'TOTAL':<30} {total:>6}")


def list_by_category(category):
    rows = query("SELECT practice, description, source, priority FROM Coding_Practices WHERE category=? ORDER BY priority", (category,))
    print(f"\n📂 {category} ({len(rows)} practices)")
    for practice, desc, source, priority in rows:
        pri = {"critical": "🔴", "high": "🟡", "medium": "🟢", "low": "⚪"}.get(priority, "⚪")
        print(f"\n  {pri} {practice}")
        print(f"     {desc[:150]}")
        print(f"     📖 {source}")


def list_by_source(source):
    rows = query("SELECT practice, category, priority FROM Coding_Practices WHERE source LIKE ? ORDER BY category", (f"%{source}%",))
    print(f"\n📖 Source: {source} ({len(rows)} practices)")
    for practice, category, priority in rows:
        pri = {"critical": "🔴", "high": "🟡", "medium": "🟢", "low": "⚪"}.get(priority, "⚪")
        print(f"  {pri} [{category}] {practice}")


def list_by_priority(priority):
    rows = query("SELECT practice, category, source FROM Coding_Practices WHERE priority=? ORDER BY category", (priority,))
    print(f"\n{'🔴 Critical' if priority == 'critical' else '🟡 High' if priority == 'high' else '🟢 Medium'} Practices ({len(rows)})")
    for practice, category, source in rows:
        print(f"  [{category}] {practice} ({source})")


def search(term):
    rows = query("SELECT practice, category, description, source, priority FROM Coding_Practices WHERE practice LIKE ? OR description LIKE ? ORDER BY priority", (f"%{term}%", f"%{term}%"))
    print(f"\n🔍 Search: '{term}' ({len(rows)} results)")
    for practice, category, desc, source, priority in rows:
        pri = {"critical": "🔴", "high": "🟡", "medium": "🟢", "low": "⚪"}.get(priority, "⚪")
        print(f"\n  {pri} [{category}] {practice}")
        print(f"     {desc[:200]}")
        print(f"     📖 {source}")


def dump_all():
    rows = query("SELECT category, practice, description, source, priority FROM Coding_Practices ORDER BY category, priority")
    current_cat = None
    for cat, practice, desc, source, priority in rows:
        if cat != current_cat:
            print(f"\n{'='*60}")
            print(f"  {cat}")
            print(f"{'='*60}")
            current_cat = cat
        pri = {"critical": "🔴", "high": "🟡", "medium": "🟢", "low": "⚪"}.get(priority, "⚪")
        print(f"\n  {pri} {practice}")
        print(f"     {desc[:200]}")
        print(f"     📖 {source}")


if __name__ == "__main__":
    if not DB.exists():
        print("Coding Practices database not found. Run the setup script first.")
        sys.exit(1)

    if len(sys.argv) == 1:
        list_categories()
    elif sys.argv[1] == "--all":
        dump_all()
    elif sys.argv[1] == "--category" and len(sys.argv) > 2:
        list_by_category(sys.argv[2])
    elif sys.argv[1] == "--source" and len(sys.argv) > 2:
        list_by_source(sys.argv[2])
    elif sys.argv[1] == "--priority" and len(sys.argv) > 2:
        list_by_priority(sys.argv[2])
    elif sys.argv[1] == "--search" and len(sys.argv) > 2:
        search(sys.argv[2])
    else:
        print(__doc__)
