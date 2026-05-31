# Expense Sync — setup & go-live

Auto-adds Indian Bank UPI **debits** from your Mac's Messages app to the Notion
Expenses database, auto-suggesting a category from the merchant. Debits only;
credits are ignored. Built and parser-verified on 2026-05-31.

## Status
- [x] Script, config, launchd job written
- [x] Parsing verified on real SMS (33 debits / 14 days)
- [x] Notion write validated (test row created — delete the one named "ZZ — expense-sync test")
- [ ] **You:** add Notion token (step 1)
- [ ] **You:** grant Full Disk Access (step 2)
- [ ] **You:** go live (step 3)

## Step 1 — Notion integration token
1. Go to https://www.notion.so/my-integrations → **New integration** (internal).
   Name it e.g. "Expense Sync". Copy the **Internal Integration Secret**
   (starts with `ntn_` or `secret_`).
2. Open your **Expense Tracker** page in Notion → top-right `•••` → **Connections**
   → **Connect to** → pick "Expense Sync". (This shares the Expenses + Category
   DBs with the integration.)
3. Paste the token into `config.json` → `"notion_token"`.

## Step 2 — Full Disk Access (so the background job can read Messages)
System Settings → Privacy & Security → **Full Disk Access** → **+** →
add **`/usr/bin/python3`**  (Cmd-Shift-G in the file picker, paste the path).
Toggle it on. This lets the scheduled job read `~/Library/Messages/chat.db`.

## Step 3 — Go live
```sh
cd ~/Desktop/expense-sync

# import history (optional; pick how many days). Omit to start fresh from now.
/usr/bin/python3 expense_sync.py --backfill 14

# install + start the 15-minute background job
cp com.hmshuu.expense-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hmshuu.expense-sync.plist
```
After this, new UPI debits appear in Notion within ~15 min while your Mac is awake.

## Handy commands
```sh
/usr/bin/python3 expense_sync.py --dry-run        # parse + print, no writes
/usr/bin/python3 expense_sync.py --backfill 30    # import last 30 days
tail -f run.log run.err.log                       # watch the background job
cat unparsed.log                                  # bank SMS the parser skipped
launchctl unload ~/Library/LaunchAgents/com.hmshuu.expense-sync.plist  # stop it
```

## Tuning categories
Edit `config.json` → `category_keywords`. Merchant names in SMS are truncated to
~12 chars, so use short keywords (e.g. `"village hyp"`, not `"village hyper market"`).
Unmatched merchants (mostly person-to-person transfers) are left blank for you to set.

## Files
- `expense_sync.py` — the sync script (stdlib only)
- `config.json` — token + DB IDs + category map (chmod 600)
- `state.json` — cursor + dedup (created on first real run)
- `unparsed.log` — bank SMS that didn't match a known format
- `com.hmshuu.expense-sync.plist` — the launchd schedule
