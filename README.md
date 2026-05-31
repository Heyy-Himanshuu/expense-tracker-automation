# Expense Tracker Automation

Automatically log your bank/UPI **debits** into a [Notion](https://notion.so) expense
tracker — no manual data entry. Reads transaction SMS from the macOS Messages database
(deposited there by iPhone **Text Message Forwarding**), parses each debit, auto-suggests
a category from the merchant, and creates a row in your Notion Expenses database.

> Built for **Indian Bank** UPI SMS, but the patterns are easy to adapt to other banks.

## How it works

```
iPhone bank SMS ──(Text Message Forwarding)──► macOS Messages (chat.db)
        │
        ▼
  expense_sync.py  ── parses debit (amount / merchant / time)
        │           ── auto-suggests category from merchant keywords
        ▼
   Notion Expenses database  (Name, Amount ₹, Date, Category)
        ▲
        └── runs every 15 min via launchd
```

- **Debits only.** Credits and non-transaction SMS are ignored (logged to `unparsed.log`).
- **Auto-categorize.** Merchant keywords map to your Notion categories; unknown merchants
  (mostly person-to-person transfers) are left blank for you to set.
- **No dependencies.** Pure Python standard library.
- **No data leaves your Mac** except the Notion API calls you configure.

## Setup

1. **Enable iPhone → Mac SMS forwarding**: iPhone Settings → Messages → Text Message
   Forwarding → enable your Mac. Bank SMS will now appear in the Messages app.
2. **Notion integration**: create one at <https://www.notion.so/my-integrations>, then open
   your expense-tracker page → `•••` → Connections → connect the integration.
3. **Configure**: copy `config.example.json` to `config.json` and fill in your token,
   database ID, and category page IDs. Then `chmod 600 config.json`.
4. **Full Disk Access**: System Settings → Privacy & Security → Full Disk Access → add
   `/usr/bin/python3` (so the background job can read Messages).
5. **Run it**: see [`SETUP.md`](./SETUP.md) for the exact backfill + launchd commands.

## Usage

```sh
python3 expense_sync.py --dry-run        # parse + print, no writes
python3 expense_sync.py --backfill 14    # import the last 14 days
python3 expense_sync.py                   # incremental (used by the scheduler)
```

## Adapting to another bank

- Set `sender_match` in `config.json` to your bank's SMS sender substring.
- Adjust `DEBIT_RE` in `expense_sync.py` to match your bank's debit SMS wording.
- Tune `category_keywords` — keep keywords short, since banks often truncate merchant
  names to ~12 characters.

## Limitations

- Only captures transactions that arrive as forwarded SMS while the Mac is awake.
- Indian Bank UPI debits only out of the box — not credits, cards, other banks, or
  wallet-internal payments.
- `attributedBody` decoding is heuristic (robust for these SMS; misses are logged).

## License

MIT
