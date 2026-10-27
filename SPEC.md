# X Post Queue — Spec (Boyd / @chrisalexboyd)

## Goal
Schedule/approve interface for staggered X originals and replies. Boyd approves exact text + America/New_York go-live time once; at each slot he gets a clear go-live cue with an intent Post link. He always taps Post. Never auto-post.

## Hard constraints
- X plugin cannot publish or schedule posts. Only `https://x.com/intent/post?text=URLENCODED` (+ `&in_reply_to=POST_ID` for replies).
- No fake X schedule API. No auto-like / RT / DM.
- Drafts go out as Boyd only; he approves exact text.
- Target account: @chrisalexboyd
- Non-KPD → public BoydVC when published; hold merge until Boyd OK if PR.

## Data model (queue item)
- id, created_at_et, status: draft|approved|cued|posted|skipped
- kind: original|reply
- text (exact, max 280 for single; note threads later)
- in_reply_to (optional post id)
- go_live_at_et (ISO local America/New_York)
- intent_url (computed)
- approved_at_et, cued_at_et, posted_at_et (optional)
- source: x-growth|tokenmax|manual
- notes

## UX
1. List queue (upcoming first); filter by status
2. Add/edit draft (text, optional reply-to, go-live datetime ET)
3. Approve → locks text+time, status=approved
4. At/after go_live: show big "Post now" button → opens intent URL; mark cued; Boyd marks posted after tap
5. Skip / edit (edit reverts to draft)

## Delivery
- Local LIVE demo on box port (e.g. :4340) + queue JSON file
- TokenMax routine every 15m (7am–10pm ET, 7 days — personal X): scan approved due items → SendToUser Boyd with intent link + mark cued; also notify X Growth FYI (priority false) optional
- Cloud/BoydVC app: same UX, public repo when Boyd says go

## Out of scope
- Actually posting via API
- Auto-thread expand beyond single intent link
- Likes/RTs/DMs
