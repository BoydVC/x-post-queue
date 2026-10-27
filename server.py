#!/usr/bin/env python3
"""X Post Queue — schedule/approve UI for @chrisalexboyd. Never auto-posts.

Local LIVE on 0.0.0.0:4340. Persists data/queue.json. Intent links only;
Boyd always taps Post on X.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
QUEUE_PATH = DATA / "queue.json"
PID_PATH = ROOT / "x-post-queue.pid"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "4340"))
ET = ZoneInfo("America/New_York")
ACCOUNT = "@chrisalexboyd"
STATUSES = ("draft", "approved", "cued", "posted", "skipped")
KINDS = ("original", "reply")
_lock = threading.RLock()


def now_et() -> datetime:
    return datetime.now(ET)


def fmt_et(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    else:
        dt = dt.astimezone(ET)
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z").replace("+0000", "+0000")  # keep offset


def fmt_et_iso(dt: datetime | None) -> str | None:
    """ISO-ish local ET with numeric offset, e.g. 2026-10-27T15:30:00-0400 → normalize to -04:00."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    else:
        dt = dt.astimezone(ET)
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    # insert colon in offset: -0400 -> -04:00
    return s[:-2] + ":" + s[-2:]


def parse_et(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    # datetime-local from HTML: 2026-10-27T16:00
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s):
        return datetime.fromisoformat(s).replace(tzinfo=ET)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", s):
        return datetime.fromisoformat(s).replace(tzinfo=ET)
    # with offset
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt.astimezone(ET)
    except ValueError:
        return None


def intent_url(text: str, in_reply_to: str | None = None) -> str:
    url = "https://x.com/intent/post?text=" + quote(text or "", safe="")
    if in_reply_to:
        rid = str(in_reply_to).strip()
        if rid:
            url += "&in_reply_to=" + quote(rid, safe="")
    return url


def default_queue() -> dict:
    t = now_et()
    ex1_live = t + timedelta(minutes=5)
    ex2_live = t + timedelta(hours=2)
    items = [
        {
            "id": str(uuid.uuid4()),
            "created_at_et": fmt_et_iso(t),
            "status": "draft",
            "kind": "original",
            "text": "[EXAMPLE] Quick thought: shipping small tools that still require a human Post tap beats fake schedule APIs. — draft for @chrisalexboyd",
            "in_reply_to": None,
            "go_live_at_et": fmt_et_iso(ex1_live),
            "intent_url": "",
            "approved_at_et": None,
            "cued_at_et": None,
            "posted_at_et": None,
            "source": "tokenmax",
            "notes": "EXAMPLE seed — replace or skip before real use",
        },
        {
            "id": str(uuid.uuid4()),
            "created_at_et": fmt_et_iso(t),
            "status": "draft",
            "kind": "reply",
            "text": "[EXAMPLE] Agree — approve once, intent link at the slot, you still hit Post. Never auto-post.",
            "in_reply_to": "1234567890123456789",
            "go_live_at_et": fmt_et_iso(ex2_live),
            "intent_url": "",
            "approved_at_et": None,
            "cued_at_et": None,
            "posted_at_et": None,
            "source": "x-growth",
            "notes": "EXAMPLE seed reply — fake in_reply_to id",
        },
    ]
    for it in items:
        it["intent_url"] = intent_url(it["text"], it.get("in_reply_to"))
    return {
        "account": ACCOUNT,
        "timezone": "America/New_York",
        "updated_at_et": fmt_et_iso(t),
        "items": items,
    }


def load_queue() -> dict:
    with _lock:
        if not QUEUE_PATH.exists():
            q = default_queue()
            save_queue(q)
            return q
        with QUEUE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)


def save_queue(q: dict) -> None:
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        q["updated_at_et"] = fmt_et_iso(now_et())
        q["account"] = ACCOUNT
        q["timezone"] = "America/New_York"
        tmp = QUEUE_PATH.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(q, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(QUEUE_PATH)


def find_item(q: dict, item_id: str) -> dict | None:
    for it in q.get("items", []):
        if it.get("id") == item_id:
            return it
    return None


def enrich(it: dict) -> dict:
    it = dict(it)
    it["intent_url"] = intent_url(it.get("text") or "", it.get("in_reply_to"))
    return it


def due_items(q: dict) -> list:
    """Approved, go_live_at_et <= now ET, not yet cued (and not posted/skipped)."""
    now = now_et()
    out = []
    for it in q.get("items", []):
        if it.get("status") != "approved":
            continue
        gl = parse_et(it.get("go_live_at_et"))
        if gl is None:
            continue
        if gl <= now:
            out.append(enrich(it))
    out.sort(key=lambda x: x.get("go_live_at_et") or "")
    return out


def sort_items(items: list) -> list:
    # upcoming first: by go_live, then created
    def key(it):
        gl = it.get("go_live_at_et") or "9999"
        return (gl, it.get("created_at_et") or "")

    return sorted(items, key=key)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>X Post Queue — @chrisalexboyd</title>
<style>
  :root {
    --bg: #0b0f14;
    --panel: #121821;
    --panel2: #1a2330;
    --border: #2a3544;
    --text: #e8eef6;
    --muted: #8b9bb0;
    --accent: #1d9bf0;
    --accent2: #00ba7c;
    --warn: #f4212e;
    --amber: #ffad1f;
    --draft: #8b9bb0;
    --approved: #1d9bf0;
    --cued: #ffad1f;
    --posted: #00ba7c;
    --skipped: #6e7681;
    --radius: 12px;
    --font: "Segoe UI", system-ui, -apple-system, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    font-family: var(--font);
    background: radial-gradient(1200px 600px at 10% -10%, #1a2740 0%, transparent 50%),
                radial-gradient(900px 500px at 100% 0%, #0d2818 0%, transparent 45%),
                var(--bg);
    color: var(--text);
  }
  header {
    display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px;
    padding: 20px 24px; border-bottom: 1px solid var(--border);
    background: rgba(18,24,33,0.85); backdrop-filter: blur(8px);
    position: sticky; top: 0; z-index: 10;
  }
  header h1 { margin: 0; font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em; }
  header .sub { color: var(--muted); font-size: 0.85rem; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
    background: var(--panel2); border: 1px solid var(--border); color: var(--muted);
  }
  .badge.live { color: var(--accent2); border-color: #1a5c40; }
  .badge.live::before {
    content: ""; width: 7px; height: 7px; border-radius: 50%;
    background: var(--accent2); box-shadow: 0 0 8px var(--accent2);
  }
  main { max-width: 1100px; margin: 0 auto; padding: 24px; display: grid; gap: 20px; }
  .grid { display: grid; grid-template-columns: 1fr 340px; gap: 20px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px;
  }
  .panel h2 { margin: 0 0 14px; font-size: 1rem; font-weight: 650; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
  .filters button {
    background: var(--panel2); border: 1px solid var(--border); color: var(--muted);
    padding: 6px 12px; border-radius: 999px; cursor: pointer; font-size: 0.8rem;
  }
  .filters button.active { color: var(--text); border-color: var(--accent); background: #152536; }
  .item {
    border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    margin-bottom: 10px; background: var(--panel2);
  }
  .item.due-now {
    border-color: var(--amber);
    box-shadow: 0 0 0 1px rgba(255,173,31,0.35), 0 8px 24px rgba(255,173,31,0.08);
  }
  .item-top { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
  .status {
    font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 3px 8px; border-radius: 6px;
  }
  .status.draft { background: #2a3140; color: var(--draft); }
  .status.approved { background: #0d2f4a; color: var(--approved); }
  .status.cued { background: #3a2a0a; color: var(--cued); }
  .status.posted { background: #0a3324; color: var(--posted); }
  .status.skipped { background: #22262c; color: var(--skipped); }
  .kind { font-size: 0.75rem; color: var(--muted); }
  .meta { font-size: 0.78rem; color: var(--muted); }
  .text { white-space: pre-wrap; line-height: 1.45; margin: 8px 0 12px; font-size: 0.95rem; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  button, .btn {
    font: inherit; cursor: pointer; border-radius: 8px; border: 1px solid var(--border);
    padding: 8px 12px; background: var(--panel); color: var(--text); font-size: 0.85rem;
    text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
  }
  button.primary, .btn.primary {
    background: var(--accent); border-color: #1a8cd8; color: #fff; font-weight: 650;
  }
  button.good, .btn.good {
    background: var(--accent2); border-color: #00a06a; color: #04140e; font-weight: 700;
  }
  button.danger { background: #3a1216; border-color: #6b1d24; color: #ff8a93; }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  label { display: block; font-size: 0.78rem; color: var(--muted); margin: 10px 0 4px; }
  input, textarea, select {
    width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 8px; padding: 9px 10px; font: inherit; font-size: 0.9rem;
  }
  textarea { min-height: 110px; resize: vertical; }
  .hint { font-size: 0.75rem; color: var(--muted); margin-top: 4px; }
  .char { font-size: 0.75rem; color: var(--muted); text-align: right; }
  .char.over { color: var(--warn); }
  .banner {
    background: #1a1508; border: 1px solid #5a4410; color: #ffd27a;
    padding: 12px 14px; border-radius: 10px; font-size: 0.88rem; margin-bottom: 14px;
  }
  .empty { color: var(--muted); font-size: 0.9rem; padding: 20px 0; text-align: center; }
  footer {
    max-width: 1100px; margin: 0 auto; padding: 8px 24px 32px;
    color: var(--muted); font-size: 0.75rem;
  }
  code { background: #1a2330; padding: 1px 5px; border-radius: 4px; font-size: 0.85em; }
</style>
</head>
<body>
<header>
  <div>
    <h1>X Post Queue</h1>
    <div class="sub">@chrisalexboyd · America/New_York · intent links only · never auto-post</div>
  </div>
  <span class="badge live">LIVE :4340</span>
  <span class="badge" id="clock">—</span>
  <span class="badge" id="dueCount">0 due</span>
</header>
<main>
  <div class="banner">
    Approve exact text + go-live time once. When a slot is due, open <strong>Post now</strong>
    (X intent URL). You still tap Post on X. Mark <em>Posted</em> after. No API publish.
  </div>
  <div class="grid">
    <section class="panel">
      <h2>Queue</h2>
      <div class="filters" id="filters">
        <button data-f="all" class="active">All</button>
        <button data-f="draft">Draft</button>
        <button data-f="approved">Approved</button>
        <button data-f="cued">Cued</button>
        <button data-f="posted">Posted</button>
        <button data-f="skipped">Skipped</button>
        <button data-f="due">Due now</button>
      </div>
      <div id="list"></div>
    </section>
    <aside class="panel">
      <h2 id="formTitle">New draft</h2>
      <form id="form">
        <input type="hidden" id="editId" value="" />
        <label for="kind">Kind</label>
        <select id="kind">
          <option value="original">Original</option>
          <option value="reply">Reply</option>
        </select>
        <label for="text">Exact post text</label>
        <textarea id="text" maxlength="500" required placeholder="What Boyd will post…"></textarea>
        <div class="char" id="charCount">0 / 280</div>
        <label for="reply">In reply to (post id, optional)</label>
        <input id="reply" placeholder="e.g. 1234567890123456789" />
        <label for="goLive">Go live (America/New_York)</label>
        <input id="goLive" type="datetime-local" required />
        <label for="source">Source</label>
        <select id="source">
          <option value="manual">manual</option>
          <option value="x-growth">x-growth</option>
          <option value="tokenmax">tokenmax</option>
        </select>
        <label for="notes">Notes</label>
        <input id="notes" placeholder="optional" />
        <div class="actions" style="margin-top:14px">
          <button type="submit" class="primary" id="saveBtn">Save draft</button>
          <button type="button" id="resetBtn">Clear</button>
        </div>
        <p class="hint">Edit of an approved/cued item reverts it to <code>draft</code>.</p>
      </form>
    </aside>
  </div>
</main>
<footer>
  Persist: <code>data/queue.json</code> · APIs: <code>/api/items</code>
  <code>/api/due</code> <code>/api/items/:id/approve|skip|posted|cue</code>
  · No auto-post · Times labeled America/New_York
</footer>
<script>
const $ = (s) => document.querySelector(s);
let items = [];
let filter = "all";
let dueIds = new Set();

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[c]));
}

function fmtLabel(iso) {
  if (!iso) return "—";
  // show as stored + zone label
  try {
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso) + " America/New_York";
    const opts = { timeZone: "America/New_York", year:"numeric", month:"short", day:"2-digit",
      hour:"numeric", minute:"2-digit", hour12:true };
    return d.toLocaleString("en-US", opts) + " America/New_York";
  } catch { return esc(iso) + " America/New_York"; }
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({
    headers: { "Content-Type": "application/json" }
  }, opts || {}));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function refresh() {
  const [q, due] = await Promise.all([
    api("/api/items"),
    api("/api/due")
  ]);
  items = q.items || [];
  dueIds = new Set((due.items || []).map(i => i.id));
  $("#dueCount").textContent = dueIds.size + " due";
  render();
}

function render() {
  const list = $("#list");
  let view = items.slice();
  if (filter === "due") view = view.filter(i => dueIds.has(i.id));
  else if (filter !== "all") view = view.filter(i => i.status === filter);
  if (!view.length) {
    list.innerHTML = '<div class="empty">No items in this filter.</div>';
    return;
  }
  list.innerHTML = view.map(it => {
    const due = dueIds.has(it.id) || (it.status === "approved" && dueIds.has(it.id));
    const isDue = dueIds.has(it.id);
    const canApprove = it.status === "draft";
    const canCue = it.status === "approved" || it.status === "cued";
    const canPosted = it.status === "cued" || it.status === "approved";
    const canSkip = it.status === "draft" || it.status === "approved" || it.status === "cued";
    return `<article class="item ${isDue ? "due-now" : ""}" data-id="${esc(it.id)}">
      <div class="item-top">
        <span class="status ${esc(it.status)}">${esc(it.status)}</span>
        <span class="kind">${esc(it.kind)}${it.in_reply_to ? " · reply-to " + esc(it.in_reply_to) : ""}</span>
        <span class="meta">go-live ${fmtLabel(it.go_live_at_et)}</span>
        ${isDue ? '<span class="status cued">DUE NOW</span>' : ""}
        ${String(it.notes||"").includes("EXAMPLE") || String(it.text||"").includes("[EXAMPLE]")
          ? '<span class="status skipped">EXAMPLE</span>' : ""}
      </div>
      <div class="text">${esc(it.text)}</div>
      <div class="meta">source ${esc(it.source||"—")} · created ${fmtLabel(it.created_at_et)}</div>
      <div class="actions" style="margin-top:10px">
        ${canApprove ? `<button class="primary" data-act="approve">Approve</button>` : ""}
        ${(isDue || it.status === "approved" || it.status === "cued")
          ? `<a class="btn good" data-act="cue" href="${esc(it.intent_url)}" target="_blank" rel="noopener">Post now ↗</a>` : ""}
        ${canPosted ? `<button data-act="posted">Mark posted</button>` : ""}
        ${canSkip ? `<button class="danger" data-act="skip">Skip</button>` : ""}
        <button data-act="edit">Edit</button>
        <button data-act="copy">Copy intent URL</button>
      </div>
    </article>`;
  }).join("");
}

$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-f]");
  if (!b) return;
  filter = b.dataset.f;
  [...$("#filters").children].forEach(x => x.classList.toggle("active", x === b));
  render();
});

$("#list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const art = btn.closest(".item");
  const id = art.dataset.id;
  const act = btn.dataset.act;
  const it = items.find(x => x.id === id);
  try {
    if (act === "edit") {
      $("#editId").value = it.id;
      $("#kind").value = it.kind || "original";
      $("#text").value = it.text || "";
      $("#reply").value = it.in_reply_to || "";
      const gl = (it.go_live_at_et || "").slice(0, 16);
      $("#goLive").value = gl;
      $("#source").value = it.source || "manual";
      $("#notes").value = it.notes || "";
      $("#formTitle").textContent = "Edit draft";
      $("#saveBtn").textContent = "Save (→ draft)";
      updateChar();
      return;
    }
    if (act === "copy") {
      await navigator.clipboard.writeText(it.intent_url || "");
      btn.textContent = "Copied";
      setTimeout(() => btn.textContent = "Copy intent URL", 1200);
      return;
    }
    if (act === "cue") {
      // link also navigates; mark cued
      e.preventDefault();
      window.open(it.intent_url, "_blank", "noopener");
      await api("/api/items/" + id + "/cue", { method: "POST", body: "{}" });
      await refresh();
      return;
    }
    if (act === "approve" || act === "skip" || act === "posted") {
      await api("/api/items/" + id + "/" + act, { method: "POST", body: "{}" });
      await refresh();
    }
  } catch (err) {
    alert(err.message || String(err));
  }
});

function updateChar() {
  const n = $("#text").value.length;
  const el = $("#charCount");
  el.textContent = n + " / 280";
  el.classList.toggle("over", n > 280);
}

$("#text").addEventListener("input", updateChar);

function resetForm() {
  $("#editId").value = "";
  $("#form").reset();
  $("#formTitle").textContent = "New draft";
  $("#saveBtn").textContent = "Save draft";
  updateChar();
  // default go-live = now + 30m ET via server clock approx local
  const d = new Date();
  d.setMinutes(d.getMinutes() + 30);
  const pad = n => String(n).padStart(2,"0");
  // use America/New_York wall via Intl
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false
  }).formatToParts(d);
  const get = t => parts.find(p => p.type === t).value;
  let hour = get("hour");
  if (hour === "24") hour = "00";
  $("#goLive").value = `${get("year")}-${get("month")}-${get("day")}T${hour}:${get("minute")}`;
}

$("#resetBtn").addEventListener("click", resetForm);

$("#form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    kind: $("#kind").value,
    text: $("#text").value,
    in_reply_to: $("#reply").value.trim() || null,
    go_live_at_et: $("#goLive").value,
    source: $("#source").value,
    notes: $("#notes").value
  };
  const id = $("#editId").value;
  try {
    if (id) {
      await api("/api/items/" + id, { method: "POST", body: JSON.stringify(body) });
    } else {
      await api("/api/items", { method: "POST", body: JSON.stringify(body) });
    }
    resetForm();
    await refresh();
  } catch (err) {
    alert(err.message || String(err));
  }
});

function tickClock() {
  const d = new Date();
  const s = d.toLocaleString("en-US", {
    timeZone: "America/New_York",
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true
  });
  $("#clock").textContent = s + " America/New_York";
}

resetForm();
tickClock();
setInterval(tickClock, 1000);
refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "XPostQueue/1.0"

    def log_message(self, fmt, *args):
        print(f"[x-post-queue] {self.address_string()} {fmt % args}", flush=True)

    def _cors(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/index.html"):
            return self._html(200, HTML)

        if path == "/api/health":
            return self._json(200, {
                "ok": True,
                "service": "x-post-queue",
                "account": ACCOUNT,
                "timezone": "America/New_York",
                "port": PORT,
                "now_et": fmt_et_iso(now_et()),
                "auto_post": False,
            })

        if path == "/api/items":
            q = load_queue()
            items = [enrich(it) for it in sort_items(q.get("items", []))]
            status = parse_qs(parsed.query).get("status", [None])[0]
            if status:
                items = [i for i in items if i.get("status") == status]
            return self._json(200, {
                "account": ACCOUNT,
                "timezone": "America/New_York",
                "now_et": fmt_et_iso(now_et()),
                "updated_at_et": q.get("updated_at_et"),
                "count": len(items),
                "items": items,
            })

        if path == "/api/due":
            q = load_queue()
            items = due_items(q)
            return self._json(200, {
                "account": ACCOUNT,
                "timezone": "America/New_York",
                "now_et": fmt_et_iso(now_et()),
                "count": len(items),
                "items": items,
            })

        return self._json(404, {"error": "not found", "path": path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        body = self._read_json()

        # POST /api/items — create
        if path == "/api/items":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "text required"})
            kind = body.get("kind") or "original"
            if kind not in KINDS:
                return self._json(400, {"error": "kind must be original|reply"})
            gl = parse_et(body.get("go_live_at_et"))
            if gl is None:
                return self._json(400, {"error": "go_live_at_et required (America/New_York)"})
            in_reply = body.get("in_reply_to") or None
            if in_reply is not None:
                in_reply = str(in_reply).strip() or None
            if kind == "reply" and not in_reply:
                # allow but note
                pass
            item = {
                "id": str(uuid.uuid4()),
                "created_at_et": fmt_et_iso(now_et()),
                "status": "draft",
                "kind": kind,
                "text": text,
                "in_reply_to": in_reply,
                "go_live_at_et": fmt_et_iso(gl),
                "intent_url": intent_url(text, in_reply),
                "approved_at_et": None,
                "cued_at_et": None,
                "posted_at_et": None,
                "source": body.get("source") or "manual",
                "notes": body.get("notes") or "",
            }
            q = load_queue()
            q.setdefault("items", []).append(item)
            save_queue(q)
            return self._json(201, {"ok": True, "item": enrich(item)})

        # POST /api/items/:id — update (reverts to draft)
        m = re.fullmatch(r"/api/items/([0-9a-fA-F-]{36})", path)
        if m:
            item_id = m.group(1)
            q = load_queue()
            it = find_item(q, item_id)
            if not it:
                return self._json(404, {"error": "item not found"})
            if "text" in body:
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "text required"})
                it["text"] = text
            if "kind" in body:
                if body["kind"] not in KINDS:
                    return self._json(400, {"error": "kind must be original|reply"})
                it["kind"] = body["kind"]
            if "in_reply_to" in body:
                v = body.get("in_reply_to")
                it["in_reply_to"] = (str(v).strip() if v else None) or None
            if "go_live_at_et" in body:
                gl = parse_et(body.get("go_live_at_et"))
                if gl is None:
                    return self._json(400, {"error": "invalid go_live_at_et"})
                it["go_live_at_et"] = fmt_et_iso(gl)
            if "source" in body:
                it["source"] = body.get("source") or "manual"
            if "notes" in body:
                it["notes"] = body.get("notes") or ""
            # edit reverts to draft
            it["status"] = "draft"
            it["approved_at_et"] = None
            it["cued_at_et"] = None
            it["posted_at_et"] = None
            it["intent_url"] = intent_url(it["text"], it.get("in_reply_to"))
            save_queue(q)
            return self._json(200, {"ok": True, "item": enrich(it)})

        # actions: approve | skip | posted | cue
        m = re.fullmatch(r"/api/items/([0-9a-fA-F-]{36})/(approve|skip|posted|cue)", path)
        if m:
            item_id, action = m.group(1), m.group(2)
            q = load_queue()
            it = find_item(q, item_id)
            if not it:
                return self._json(404, {"error": "item not found"})
            t = fmt_et_iso(now_et())
            if action == "approve":
                if it["status"] not in ("draft", "approved"):
                    return self._json(400, {"error": f"cannot approve from status={it['status']}"})
                it["status"] = "approved"
                it["approved_at_et"] = t
                it["intent_url"] = intent_url(it["text"], it.get("in_reply_to"))
            elif action == "skip":
                it["status"] = "skipped"
            elif action == "cue":
                if it["status"] not in ("approved", "cued"):
                    return self._json(400, {"error": f"cannot cue from status={it['status']}"})
                it["status"] = "cued"
                it["cued_at_et"] = t
                it["intent_url"] = intent_url(it["text"], it.get("in_reply_to"))
            elif action == "posted":
                if it["status"] not in ("approved", "cued", "posted"):
                    return self._json(400, {"error": f"cannot mark posted from status={it['status']}"})
                it["status"] = "posted"
                it["posted_at_et"] = t
            save_queue(q)
            return self._json(200, {"ok": True, "item": enrich(it)})

        return self._json(404, {"error": "not found", "path": path})


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    load_queue()  # seed if missing
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    PID_PATH.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    print(
        f"[x-post-queue] LIVE http://{HOST}:{PORT}/  account={ACCOUNT}  "
        f"tz=America/New_York  pid={os.getpid()}  queue={QUEUE_PATH}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    finally:
        try:
            if PID_PATH.exists() and PID_PATH.read_text().strip() == str(os.getpid()):
                PID_PATH.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
