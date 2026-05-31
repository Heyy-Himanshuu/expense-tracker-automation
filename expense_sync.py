#!/usr/bin/env python3
"""
expense_sync.py — auto-add Indian Bank UPI *debits* from macOS Messages to Notion.

Reads bank transaction SMS out of ~/Library/Messages/chat.db (deposited there by
iPhone Text Message Forwarding), parses each debit (amount / merchant / time),
auto-suggests a category from the merchant, and creates a row in the Notion
Expenses database via the Notion REST API.

Stdlib only. Debits only (credits are logged to unparsed.log, never added).

Modes:
  (default)        incremental — process messages newer than the saved cursor
  --backfill N     process the last N days, then advance the cursor to now
  --init           set the cursor to the latest message without importing anything
  --dry-run        parse and print; never write to Notion or move the cursor

Cursor + dedup state live in state.json. Unmatched bank SMS go to unparsed.log.
"""

import json
import os
import re
import sys
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT_DB = os.path.expanduser("~/Library/Messages/chat.db")
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
UNPARSED_LOG = os.path.join(HERE, "unparsed.log")

APPLE_EPOCH = 978307200  # seconds between 1970-01-01 and 2001-01-01

# Debit SMS, e.g.:
#   "A/c *6825 debited Rs. 274.00 on 30-05-26 to Swiggy Diner. UPI:588250583875. ..."
DEBIT_RE = re.compile(
    r"A/c\s+\*?(\d+)\s+debited\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s+on\s+"
    r"(\d{2}-\d{2}-\d{2,4})\s+to\s+(.+?)\.\s*UPI[:\s]*([0-9]+)",
    re.IGNORECASE | re.DOTALL,
)


# ── config / state ──────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def log_unparsed(when, sender, body):
    with open(UNPARSED_LOG, "a") as f:
        f.write(f"{when}\t{sender}\t{body}\n")


# ── chat.db reading ─────────────────────────────────────────────────────────
def decode_body(text, attributed_body):
    """Return the message text. Modern macOS leaves `text` empty and stores the
    body in the `attributedBody` typedstream blob, after an 'NSString' marker
    with a length prefix (1 byte, or 0x81+2 LE, or 0x82+4 LE)."""
    if text:
        return text
    if not attributed_body:
        return ""
    blob = attributed_body
    marker = blob.find(b"NSString")
    if marker < 0:
        return ""
    b = blob[marker + 8:]
    # Skip the class/version control bytes up to the 0x2b ('+') separator.
    sep = b.find(b"\x2b")
    p = sep + 1 if 0 <= sep <= 8 else 5
    if p >= len(b):
        return ""
    first = b[p]
    if first == 0x81:
        length = int.from_bytes(b[p + 1:p + 3], "little"); start = p + 3
    elif first == 0x82:
        length = int.from_bytes(b[p + 1:p + 5], "little"); start = p + 5
    else:
        length = first; start = p + 1
    return b[start:start + length].decode("utf-8", "ignore")


def fetch_messages(sender_match, min_rowid=None, since_ns=None):
    """Return [(rowid, date_ns, sender, body)] for bank SMS, ascending by rowid."""
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        sql = (
            "SELECT m.ROWID, m.date, h.id, m.text, m.attributedBody "
            "FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID "
            "WHERE h.id LIKE ? AND m.is_from_me = 0"
        )
        params = [f"%{sender_match}%"]
        if min_rowid is not None:
            sql += " AND m.ROWID > ?"; params.append(min_rowid)
        if since_ns is not None:
            sql += " AND m.date > ?"; params.append(since_ns)
        sql += " ORDER BY m.ROWID ASC"
        out = []
        for rowid, date_ns, sender, text, ab in con.execute(sql, params):
            out.append((rowid, date_ns, sender or "", decode_body(text, ab)))
        return out
    finally:
        con.close()


def current_max_rowid(sender_match):
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT MAX(m.ROWID) FROM message m LEFT JOIN handle h "
            "ON m.handle_id = h.ROWID WHERE h.id LIKE ?",
            [f"%{sender_match}%"],
        ).fetchone()
        return row[0] or 0
    finally:
        con.close()


# ── parsing ─────────────────────────────────────────────────────────────────
def parse_debit(body):
    m = DEBIT_RE.search(body)
    if not m:
        return None
    amount = float(m.group(2).replace(",", ""))
    merchant = " ".join(m.group(4).split())
    ref = m.group(5)
    return {"amount": amount, "merchant": merchant, "ref": ref}


def suggest_category(merchant, cfg):
    low = merchant.lower()
    for cat, words in cfg.get("category_keywords", {}).items():
        if any(w in low for w in words):
            return cat
    return None


def ns_to_local_iso(date_ns):
    secs = date_ns / 1_000_000_000 + APPLE_EPOCH
    return datetime.fromtimestamp(secs).astimezone().isoformat()


# ── Notion ──────────────────────────────────────────────────────────────────
def notion_create(cfg, name, amount, iso_dt, category_id):
    props = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Amount": {"number": amount},
        "Date": {"date": {"start": iso_dt}},
    }
    if category_id:
        props["Category"] = {"relation": [{"id": category_id}]}
    payload = json.dumps({
        "parent": {"database_id": cfg["expenses_database_id"]},
        "properties": props,
    }).encode()
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {cfg['notion_token']}",
            "Notion-Version": cfg.get("notion_version", "2022-06-28"),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


# ── main ────────────────────────────────────────────────────────────────────
def process(rows, cfg, state, dry_run):
    added = skipped = 0
    seen = set(state.get("seen_refs", []))
    for rowid, date_ns, sender, body in rows:
        if not body:
            continue
        deb = parse_debit(body)
        if not deb:
            if re.search(r"debited|credited|UPI", body, re.IGNORECASE):
                log_unparsed(ns_to_local_iso(date_ns), sender, body.replace("\n", " "))
            continue
        if deb["ref"] in seen:
            skipped += 1
            continue
        cat_name = suggest_category(deb["merchant"], cfg)
        cat_id = cfg.get("category_ids", {}).get(cat_name) if cat_name else None
        iso_dt = ns_to_local_iso(date_ns)
        if dry_run:
            print(f"  ₹{deb['amount']:>9,.2f}  {iso_dt[:16]}  "
                  f"{deb['merchant'][:34]:34}  -> {cat_name or '(blank)'}")
        else:
            try:
                notion_create(cfg, deb["merchant"], deb["amount"], iso_dt, cat_id)
            except urllib.error.HTTPError as e:
                print(f"  ! Notion error {e.code} for {deb['merchant']}: "
                      f"{e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
                continue
        seen.add(deb["ref"])
        added += 1
    state["seen_refs"] = list(seen)[-5000:]  # bound growth
    return added, skipped


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    do_init = "--init" in args
    backfill_days = None
    if "--backfill" in args:
        i = args.index("--backfill")
        backfill_days = int(args[i + 1])

    cfg = load_config()
    sender = cfg.get("sender_match", "INDBNK")
    state = load_state() or {"last_rowid": 0, "seen_refs": []}

    if do_init:
        state["last_rowid"] = current_max_rowid(sender)
        save_state(state)
        print(f"Initialized cursor at ROWID {state['last_rowid']}. "
              f"New transactions from now on will be captured.")
        return

    if backfill_days is not None:
        cutoff = datetime.now() - timedelta(days=backfill_days)
        since_ns = int((cutoff.timestamp() - APPLE_EPOCH) * 1_000_000_000)
        rows = fetch_messages(sender, since_ns=since_ns)
        print(f"Backfill: {len(rows)} bank SMS in the last {backfill_days} days"
              + (" (dry-run)" if dry_run else "") + ":")
        added, skipped = process(rows, cfg, state, dry_run)
        if not dry_run:
            state["last_rowid"] = current_max_rowid(sender)
            save_state(state)
        print(f"Done. added={added} skipped(dupes)={skipped}")
        return

    # First normal run with no state: don't flood — just set the cursor.
    if not os.path.exists(STATE_PATH):
        state["last_rowid"] = current_max_rowid(sender)
        save_state(state)
        print(f"First run — initialized cursor at ROWID {state['last_rowid']}. "
              f"Run with --backfill N to import history.")
        return

    rows = fetch_messages(sender, min_rowid=state["last_rowid"])
    added, skipped = process(rows, cfg, state, dry_run)
    if not dry_run and rows:
        state["last_rowid"] = max(r[0] for r in rows)
        save_state(state)
    if added or skipped:
        print(f"added={added} skipped(dupes)={skipped}")


if __name__ == "__main__":
    main()
