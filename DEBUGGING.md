# When Porsche Connect breaks again – the recipe

This fork exists so that an outage can be fixed in minutes instead of weeks.
Always go in this order; most cases are clear after step 2.

## 1. What does Home Assistant say?

Settings → System → Logs, filter `porscheconnect`. Typical patterns:

| Message | Meaning | Where to fix |
|---|---|---|
| `RESPONSE_DECODING_ERROR` / `Unable to decompress Zstandard` | Porsche sends an encoding httpx cannot read | `const.py` → `ACCEPT_ENCODING` |
| `LOGIN_BACKOFF: n failed login(s)` | The login brake is active – several logins failed. Do **not** reload the integration, find the cause first | – |
| `requires a captcha` + repair/notification "Re-authenticate" | Auth0 locked the account after too many logins | HA: integration → Re-authenticate → enter captcha |
| `UNAUTHORIZED` (401) on API calls | Token invalid / scopes changed | `const.py` → `SCOPES`, `CLIENT_ID` |
| `NOT_FOUND` (404) on `/connect/v1/...` | Endpoint moved | `account.py`, `vehicle.py` |
| `Could not fetch authorization code` | Auth0 login flow changed (new intermediate page, different form fields) | `oauth2.py` → `login_with_identifier`, `resume_authorization_code_flow` |
| `Wrong credentials` although the password is correct | Usually also a changed login flow (a 400 is interpreted as "wrong password") | `oauth2.py` |

## 2. Test the library in isolation (10 seconds)

```bash
cd ha-porscheconnect
pip3 install httpx beautifulsoup4     # once
python3 tools/check.py                # asks for e-mail + password
python3 tools/check.py --debug        # with full HTTP log
```

- `check.py` succeeds but HA does not → the problem is in HA (version, Python, restart).
- `check.py` fails right away → Porsche changed something; the message says where.
- After the first success a token is stored in `tools/token.json` (git-ignored), so further runs
  do not log in again. This matters because every login counts towards a captcha lockout.

## 3. Capture the official app (only if 1 + 2 are not enough)

If the login flow or an endpoint changed, the official app shows how it works now:

1. Install and start `mitmproxy` / `mitmweb` on a computer
2. Phone: set the Wi-Fi proxy to that computer (port 8080), open `mitm.it`, install the
   certificate and enable full trust for it in the phone's settings
3. Open the My Porsche app, load the vehicle, optionally start climatisation
4. In mitmweb filter for `identity.porsche.com` (login) and `api.ppa.porsche.com` (data)
5. Compare the differences against `const.py` / `oauth2.py` / `vehicle.py` and update them

If the app uses certificate pinning and nothing shows up through the proxy: the Android app
in an emulator is usually more open (the User-Agent in `const.py` is the Android app anyway).

## 4. Roll out a fix

```bash
git commit -am "Fix: <what>"
# bump the version in custom_components/porscheconnect/manifest.json
git tag v0.2.x && git push && git push --tags
```

Marketplace/HACS → Porsche Connect → Refresh/Update → restart HA. Done.

## Why the login brake (backoff) matters

A broken response encoding alone is harmless, but the integration used to log in again
immediately after every failed poll – hundreds of times – which puts the account behind an
Auth0 captcha lock. Since this fork, after a failed login the library waits 1 min, then
2, 4, 8 … up to 1 h before trying again. A manual login through the configuration dialog
resets the brake. Values in `const.py` (`LOGIN_BACKOFF_*`).
