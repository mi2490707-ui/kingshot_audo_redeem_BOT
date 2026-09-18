#!/usr/bin/env python3
"""
Kingshot Gift Code Auto-Redeemer
---------------------------------
Checks kingshot.net's public gift-code feed for new codes, redeems any new
code for a single configured player (FID), and sends a Discord notification
with the result. Designed to run on a schedule via GitHub Actions.

State (which codes have already been processed) is kept in seen_codes.json,
which this script updates in place. The GitHub Actions workflow is
responsible for committing that file back to the repo after each run.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration (read from environment variables / GitHub Secrets)
# ---------------------------------------------------------------------------

PLAYER_FID = os.environ.get("PLAYER_FID", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# The signing salt used by CenturyGame's redeem API. This value was reverse
# engineered by the community for Whiteout Survival (same publisher/pattern)
# and MAY need to be replaced with the Kingshot-specific salt.
#
# HOW TO VERIFY / FIND THE REAL SALT FOR KINGSHOT:
#   1. Open the official Kingshot web redeem page in Chrome.
#   2. Open DevTools -> Network tab, then redeem any code manually once.
#   3. Look at the POST request to *.centurygame.com/api/gift_code (or
#      /api/player) and note the exact host + path.
#   4. The "sign" field can't be read directly, but if this default salt
#      produces a "sign error" response, search the page's JS bundle for the
#      literal salt string (it's usually a short alphanumeric string near
#      code that builds "fid=...&time=..." + salt).
#   5. Put the confirmed salt in the REDEEM_SALT secret to override this
#      default without editing code.
REDEEM_SALT = os.environ.get("REDEEM_SALT", "tB87#kPtkxqOS2").strip()

# Endpoints
CODES_FEED_URL = "https://kingshot.net/api/gift-codes"
REDEEM_BASE_URL = os.environ.get(
    "REDEEM_BASE_URL", "https://kingshot-giftcode.centurygame.com"
).strip()
LOGIN_ENDPOINT = f"{REDEEM_BASE_URL}/api/player"
REDEEM_ENDPOINT = f"{REDEEM_BASE_URL}/api/gift_code"

SEEN_CODES_FILE = Path(__file__).parent / "seen_codes.json"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

REQUEST_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_seen_codes() -> set:
    if not SEEN_CODES_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_CODES_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen", []))
    except (json.JSONDecodeError, OSError):
        log("WARNING: seen_codes.json unreadable, starting fresh.")
        return set()


def save_seen_codes(seen: set) -> None:
    SEEN_CODES_FILE.write_text(
        json.dumps({"seen": sorted(seen)}, indent=2), encoding="utf-8"
    )


def _find_candidate_lists(node) -> list:
    """
    Recursively hunts for lists that plausibly hold gift codes, under any of
    the common key names sites like this use (giftCodes, codes, activeCodes,
    list, items), including nested under a "data" wrapper. Returns a list of
    lists (each a candidate array of code entries).
    """
    found = []
    if isinstance(node, dict):
        for key in ("giftCodes", "activeCodes", "codes", "list", "items"):
            value = node.get(key)
            if isinstance(value, list):
                found.append(value)
        # Recurse into common wrapper keys (e.g. {"data": {...}})
        for key in ("data", "result", "payload"):
            if key in node:
                found.extend(_find_candidate_lists(node[key]))
    elif isinstance(node, list):
        # A bare top-level list is itself a candidate.
        found.append(node)
    return found


def _is_expired_entry(item: dict) -> bool:
    """Best-effort check for an 'expired'/'inactive' flag on a code entry."""
    if "expired" in item:
        return bool(item.get("expired"))
    if "isExpired" in item:
        return bool(item.get("isExpired"))
    status = item.get("status")
    if isinstance(status, str) and status.lower() in ("expired", "inactive", "disabled"):
        return True
    if "active" in item and item.get("active") is False:
        return True
    if "isActive" in item and item.get("isActive") is False:
        return True
    return False


def fetch_current_codes() -> list:
    """Returns a list of currently-ACTIVE code strings from kingshot.net's feed."""
    resp = requests.get(CODES_FEED_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    candidate_lists = _find_candidate_lists(data)

    codes = []
    seen_in_this_fetch = set()
    for raw_list in candidate_lists:
        for item in raw_list:
            code = None
            active = True
            if isinstance(item, str):
                code = item
            elif isinstance(item, dict):
                code = item.get("code") or item.get("name") or item.get("cdk")
                active = not _is_expired_entry(item)
            if code and active and code not in seen_in_this_fetch:
                codes.append(code)
                seen_in_this_fetch.add(code)
    return codes


def make_sign(payload: dict) -> str:
    """CenturyGame-style signing: sort params, urlencode, append salt, MD5."""
    query_string = "&".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
    to_hash = f"{query_string}{REDEEM_SALT}"
    return hashlib.md5(to_hash.encode("utf-8")).hexdigest()


def login_player(fid: str) -> dict:
    """Verifies the FID exists before redeeming (mirrors the site's own flow)."""
    payload = {"fid": fid, "time": str(int(time.time() * 1000))}
    payload["sign"] = make_sign(payload)
    resp = requests.post(
        LOGIN_ENDPOINT, data=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    return {"status_code": resp.status_code, "body": safe_json(resp)}


def redeem_code(fid: str, code: str) -> dict:
    payload = {
        "fid": fid,
        "cdk": code,
        "time": str(int(time.time() * 1000)),
    }
    payload["sign"] = make_sign(payload)
    resp = requests.post(
        REDEEM_ENDPOINT, data=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    return {"status_code": resp.status_code, "body": safe_json(resp)}


def safe_json(resp: requests.Response):
    try:
        return resp.json()
    except ValueError:
        return {"raw_text": resp.text[:500]}


def interpret_result(result: dict) -> str:
    """Turns the raw API response into a short human-readable status."""
    body = result.get("body", {})
    msg = ""
    if isinstance(body, dict):
        msg = str(body.get("msg") or body.get("message") or body.get("err_code") or body)
    else:
        msg = str(body)

    lowered = msg.lower()
    if result.get("status_code") != 200:
        return f"HTTP error {result.get('status_code')}: {msg}"
    if "success" in lowered or "0" == lowered.strip():
        return "Redeemed successfully"
    if "already" in lowered or "received" in lowered:
        return "Already redeemed for this account"
    if "expired" in lowered:
        return "Code expired"
    if "sign" in lowered:
        return f"Sign/auth error (REDEEM_SALT may be wrong): {msg}"
    return f"Response: {msg}"


STATUS_STYLE = {
    "success": ("✅", 0x57F287),   # green
    "already": ("ℹ️", 0x5865F2),   # blurple
    "expired": ("⌛", 0xED4245),   # red
    "sign_error": ("⚠️", 0xFEE75C),  # yellow
    "error": ("❌", 0xED4245),     # red
    "other": ("🎁", 0x5865F2),
}


def classify_status(status: str) -> str:
    lowered = status.lower()
    if "success" in lowered:
        return "success"
    if "already" in lowered:
        return "already"
    if "expired" in lowered:
        return "expired"
    if "sign/auth" in lowered:
        return "sign_error"
    if "http error" in lowered or "request failed" in lowered:
        return "error"
    return "other"


def send_discord_embed(code: str, fid: str, status: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        log("No DISCORD_WEBHOOK_URL configured, skipping notification.")
        return

    kind = classify_status(status)
    emoji, color = STATUS_STYLE[kind]

    embed = {
        "title": f"{emoji}  Kingshot Gift Code — {code}",
        "color": color,
        "fields": [
            {"name": "Player FID", "value": f"`{fid}`", "inline": True},
            {"name": "Result", "value": status, "inline": True},
        ],
        "footer": {"text": "Kingshot Auto-Redeemer"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        log(f"Failed to send Discord notification: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not PLAYER_FID:
        log("ERROR: PLAYER_FID environment variable is not set.")
        return 1

    log("Checking kingshot.net for gift codes...")
    try:
        current_codes = fetch_current_codes()
    except requests.RequestException as e:
        log(f"ERROR: Failed to fetch codes feed: {e}")
        return 1

    log(f"Feed returned {len(current_codes)} code(s): {current_codes}")

    seen = load_seen_codes()
    new_codes = [c for c in current_codes if c not in seen]

    if not new_codes:
        log("No new codes. Nothing to do.")
        return 0

    log(f"New code(s) found: {new_codes}")

    for code in new_codes:
        log(f"Redeeming '{code}' for FID {PLAYER_FID}...")
        try:
            result = redeem_code(PLAYER_FID, code)
        except requests.RequestException as e:
            log(f"ERROR redeeming {code}: {e}")
            send_discord_embed(code, PLAYER_FID, f"Request failed: {e}")
            continue

        status = interpret_result(result)
        log(f"Result for {code}: {status}")

        send_discord_embed(code, PLAYER_FID, status)

        # Mark as seen regardless of outcome so we don't retry a dead code
        # forever; genuine transient failures can be re-added manually by
        # removing the code from seen_codes.json.
        seen.add(code)

    save_seen_codes(seen)
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
