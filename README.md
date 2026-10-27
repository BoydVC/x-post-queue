# X Post Queue

Local schedule/approve UI for **@chrisalexboyd** X posts and replies.

**Never auto-posts.** At each go-live slot you get an [X intent Post link](https://x.com/intent/post); you tap **Post** on X yourself.

## LIVE

| | |
|---|---|
| URL | `http://0.0.0.0:4340/` (on box: `http://127.0.0.1:4340/`) |
| Queue file | `data/queue.json` |
| PID file | `x-post-queue.pid` |
| Timezone | America/New_York (all times labeled) |

## Start / stop

```bash
cd /workspace/tokenmax/x-post-queue
./run.sh          # starts in background if not already up
./run.sh stop     # stops via PID file
./run.sh status   # health + PID
```

Or foreground:

```bash
python3 server.py
```

Env overrides: `HOST` (default `0.0.0.0`), `PORT` (default `4340`).

## How to use

1. Open the UI → add a draft (exact text, optional reply-to post id, go-live datetime ET).
2. **Approve** locks text + time (`status=approved`).
3. When `go_live_at_et <= now` (ET), the item appears in **Due now** / `GET /api/due`.
4. Click **Post now** → opens `https://x.com/intent/post?text=…` (+ `&in_reply_to=` for replies). Marks **cued**.
5. After you post on X, click **Mark posted**. Or **Skip**.

Edit always reverts to `draft`.

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | service + `now_et` |
| GET | `/api/items` | list (`?status=` filter) |
| POST | `/api/items` | create draft JSON |
| POST | `/api/items/:id` | update → draft |
| POST | `/api/items/:id/approve` | draft → approved |
| POST | `/api/items/:id/skip` | → skipped |
| POST | `/api/items/:id/cue` | approved → cued |
| POST | `/api/items/:id/posted` | → posted |
| GET | `/api/due` | approved, go_live ≤ now ET, not yet cued |

Statuses: `draft` \| `approved` \| `cued` \| `posted` \| `skipped`.

Intent URL is computed server-side; never invents an X schedule API.

## Seed

Two **EXAMPLE** drafts ship in `data/queue.json` on first boot (clearly marked `[EXAMPLE]` / notes). Replace or skip before real use.

## Constraints

- No auto-like / RT / DM / publish via API.
- X Growth / TokenMax may create drafts and ping when due; Boyd always approves exact text and taps Post.
- Spec: `SPEC.md`.

## Smoke

```bash
./smoke.sh
```
