# Kingshot Gift Code Auto-Redeemer

Checks kingshot.net's public gift-code feed twice a day, redeems any new
code automatically for your Kingshot account, and pings you on Discord.

## How it works

1. GitHub Actions runs `main.py` on a schedule (twice daily, see
   `.github/workflows/check-codes.yml`).
2. The script fetches `https://kingshot.net/api/gift-codes`.
3. It compares the result against `seen_codes.json`.
4. Any brand-new code gets redeemed against your FID via CenturyGame's
   redeem API.
5. A Discord message is sent with the outcome (success / already used /
   error).
6. `seen_codes.json` is committed back to the repo so state persists
   between runs.

## Setup

### 1. Create the repo

Push these files to a **private** GitHub repository (private is important —
your FID and webhook shouldn't be public).

### 2. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**
and add:

| Secret name           | Value                                                             |
|------------------------|--------------------------------------------------------------------|
| `PLAYER_FID`           | `15245465`                                                        |
| `DISCORD_WEBHOOK_URL`  | Your Discord channel's webhook URL (see below)                    |
| `REDEEM_SALT`          | *(optional)* only needed if the default salt turns out to be wrong |

### 3. Get a Discord webhook URL

In Discord: **Server Settings → Integrations → Webhooks → New Webhook** →
pick a channel → **Copy Webhook URL**. Paste that as the
`DISCORD_WEBHOOK_URL` secret.

### 4. Verify the redeem endpoint/salt (important!)

The script currently uses:

- Endpoint: `https://kingshot-giftcode.centurygame.com/api/gift_code`
- Salt: `tB87#kPtkxqOS2` (this is the salt known to work for *Whiteout
  Survival*, the sister game on the same publisher's platform — it **may
  or may not** be identical for Kingshot)

**Before relying on this fully**, do one manual test:

1. Trigger the workflow manually once (Actions tab → "Kingshot Gift Code
   Check" → "Run workflow"), using a currently active real code (temporarily
   edit `seen_codes.json` or just watch the log/Discord message).
2. Check the Discord notification / Action logs for the response.
   - If it says "Redeemed successfully" or "Already redeemed" → salt and
     endpoint are correct, you're done.
   - If it says "Sign/auth error" → the salt or endpoint is wrong for
     Kingshot specifically. In that case, open the official Kingshot
     redeem page in a desktop browser, open DevTools → Network, redeem a
     code manually, and note the exact request URL. Search the page's JS
     for the salt string used to build the `sign` field, then set it as
     the `REDEEM_SALT` secret.

### 5. Done

From here it runs automatically twice a day. You'll only get a Discord
message when a **new** code shows up — silence the rest of the time.

## Files

- `main.py` — the check/redeem/notify logic
- `requirements.txt` — Python dependencies
- `seen_codes.json` — auto-managed list of already-processed codes
- `.github/workflows/check-codes.yml` — the schedule + CI job

## Adjusting the schedule

Edit the two `cron` lines in `.github/workflows/check-codes.yml`. Times are
in UTC. Current default: 04:00 and 16:00 UTC (~9 AM / 9 PM Pakistan time).

## Notes

- This redeems codes only for the one FID you configure — not a whole
  alliance.
- GitHub free-tier Actions minutes are more than enough for two runs a day.
- If kingshot.net ever changes its feed shape, check the `fetch_current_codes()`
  function in `main.py` — it already handles a couple of common JSON shapes,
  but a real fetch against the live feed hasn't been tested end-to-end.
