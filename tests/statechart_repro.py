"""Statechart Lab repro checks for x-post-queue. Synthetic drafts only.

Run: TELLS_GATE_URL=http://127.0.0.1:9/check python3 tests/statechart_repro.py
"""

import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import server  # noqa: E402

import tempfile  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="xpq-repro-"))
TMP.mkdir(parents=True, exist_ok=True)
server.DATA = TMP
server.QUEUE_PATH = TMP / "queue.json"
server.PID_PATH = TMP / "x-post-queue.pid"
if server.QUEUE_PATH.exists():
    server.QUEUE_PATH.unlink()

HOST = "127.0.0.1"
PORT = 4357
httpd = ThreadingHTTPServer((HOST, PORT), server.Handler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()

import urllib.request  # noqa: E402


def post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


def get_items() -> list[dict]:
    with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/items") as res:
        return json.loads(res.read().decode())["items"]


LIVE = "2026-11-04T09:00"

results = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


status, created = post("/api/items", {"text": "synthetic draft one", "go_live_at_et": LIVE})
item_id = created["item"]["id"]
check("create draft", status == 201 and created["item"]["status"] == "draft", f"{status} {created['item']['status']}")

status, approved = post(f"/api/items/{item_id}/approve", {})
check("approve", status == 200 and approved["item"]["status"] == "approved", f"{status} {approved['item']['status']}")

status, posted = post(f"/api/items/{item_id}/posted", {})
check(
    "posted from approved is refused",
    status == 400 and posted.get("item", {}).get("status", "approved") != "posted",
    f"{status} {posted}",
)

# Reach posted the legal way (cue, then posted) before checking skip.
post(f"/api/items/{item_id}/cue", {})
status, _ = post(f"/api/items/{item_id}/posted", {})
check("posted from cued is accepted", status == 200, f"{status}")
status, skipped = post(f"/api/items/{item_id}/skip", {})
stored = next(it for it in get_items() if it["id"] == item_id)
check(
    "skip after posted is refused",
    status == 400 and stored["status"] == "posted",
    f"{status} stored={stored['status']}",
)

# Control: a draft cannot be marked posted in one request.
status, other = post("/api/items", {"text": "synthetic draft two", "go_live_at_et": LIVE})
other_id = other["item"]["id"]
status, refused = post(f"/api/items/{other_id}/posted", {})
stored_other = next(it for it in get_items() if it["id"] == other_id)
check(
    "draft to posted is refused",
    status == 400 and stored_other["status"] == "draft",
    f"{status} stored={stored_other['status']} body={refused}",
)

httpd.shutdown()

# Lost update: mark-posted saves after an edit has cleared approval.
server.QUEUE_PATH.unlink(missing_ok=True)
held = threading.Event()
release = threading.Event()
orig_save = server.save_queue


def blocking_save(q: dict) -> None:
    if any(it.get("status") == "posted" for it in q.get("items", [])):
        held.set()
        release.wait(5)
    orig_save(q)


server.save_queue = blocking_save
httpd2 = ThreadingHTTPServer((HOST, 4358), server.Handler)
threading.Thread(target=httpd2.serve_forever, daemon=True).start()


def post2(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://{HOST}:4358{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


status, created = post2("/api/items", {"text": "synthetic draft race", "go_live_at_et": LIVE})
race_id = created["item"]["id"]
post2(f"/api/items/{race_id}/approve", {})
post2(f"/api/items/{race_id}/cue", {})

posted_box: dict = {}


def do_posted() -> None:
    posted_box["result"] = post2(f"/api/items/{race_id}/posted", {})


worker = threading.Thread(target=do_posted)
worker.start()
assert held.wait(3), "posted save did not reach the lock gap"
edit_box: dict = {}


def do_edit() -> None:
    edit_box["result"] = post2(f"/api/items/{race_id}", {"text": "synthetic draft edited"})


editor = threading.Thread(target=do_edit)
editor.start()
editor.join(0.5)  # with the fix the edit waits on the lock; without it, it lands in the gap
release.set()
worker.join(5)
editor.join(5)
status, edited = edit_box.get("result", (0, {}))
httpd2.shutdown()
final = next(it for it in json.loads(server.QUEUE_PATH.read_text())["items"] if it["id"] == race_id)
check(
    "edit that cleared approval is not overwritten by an older mark-posted",
    final["status"] == "draft" and final["text"] == "synthetic draft edited",
    f"status={final['status']} text={final['text']} edit={status} {edited.get('item', {}).get('status')} posted={posted_box.get('result', ('?', {}))[0]}",
)

failed = [row for row in results if not row[1]]
print("---")
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
