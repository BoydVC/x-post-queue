#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:4340}"
echo "== health =="
curl -sS "$BASE/api/health" | python3 -m json.tool

echo "== create =="
# go-live 2 minutes ago so /api/due picks it up after approve
GO_LIVE="$(python3 - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
print((datetime.now(ET) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M"))
PY
)"
CREATE="$(curl -sS -X POST "$BASE/api/items" -H 'Content-Type: application/json' -d "$(python3 - <<PY
import json
print(json.dumps({
  "kind": "original",
  "text": "SMOKE test post — do not publish " + __import__("uuid").uuid4().hex[:8],
  "go_live_at_et": "$GO_LIVE",
  "source": "tokenmax",
  "notes": "smoke"
}))
PY
)")"
echo "$CREATE" | python3 -m json.tool
ID="$(echo "$CREATE" | python3 -c 'import sys,json; print(json.load(sys.stdin)["item"]["id"])')"
INTENT="$(echo "$CREATE" | python3 -c 'import sys,json; print(json.load(sys.stdin)["item"]["intent_url"])')"
echo "id=$ID"
echo "intent=$INTENT"

echo "== approve =="
curl -sS -X POST "$BASE/api/items/$ID/approve" -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool >/tmp/xq-approve.json
python3 -m json.tool </tmp/xq-approve.json | head -40

echo "== due =="
DUE="$(curl -sS "$BASE/api/due")"
echo "$DUE" | python3 -m json.tool | head -60
python3 - <<PY
import json, sys, urllib.parse
due = json.loads('''$(echo "$DUE" | python3 -c 'import sys,json; print(json.dumps(json.load(sys.stdin)))')''')
ids = [i["id"] for i in due.get("items", [])]
assert "$ID" in ids, f"smoke id not in due: {ids}"
item = next(i for i in due["items"] if i["id"] == "$ID")
u = item["intent_url"]
assert u.startswith("https://x.com/intent/post?text="), u
# well-formed: parseable, text param present
from urllib.parse import urlparse, parse_qs
p = urlparse(u)
qs = parse_qs(p.query)
assert "text" in qs and qs["text"][0], qs
print("SMOKE_OK due_contains_id intent_well_formed")
print("intent_url=", u)
PY

# cleanup smoke item
curl -sS -X POST "$BASE/api/items/$ID/skip" -H 'Content-Type: application/json' -d '{}' >/dev/null
echo "skipped smoke item"
