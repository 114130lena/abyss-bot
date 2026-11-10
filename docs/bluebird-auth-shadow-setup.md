# Bluebird Twscraper: local opt-in only (no paid API)

The authenticated X collector is **not** connected to Discord. It is a
single-run benchmark in an isolated Bluebird Docker container. The existing
CHZZK, Cafe, YouTube, banner and X alerts remain unchanged.

## Important limitations

- Twscrape is a third-party, non-official X automation library. X's October 2026
  Terms of Service prohibit scraping without prior written permission. The
  account may be limited or suspended. Only use credentials on your own account
  after understanding this risk. Never try to bypass a restriction.
- The probe does not create or buy an official paid X API subscription.
- Use a separate X account rather than your personal primary account.
- Do not paste credentials, cookies, or tokens into ChatGPT, GitHub issues,
  commits, pull requests, shared screenshots, Discord, or terminal commands
  saved in your shell history.

## Secure cookie setup, performed by the account owner on Ubuntu

Use an interactive SSH session as the same Ubuntu user who owns
`/home/ubuntu/bluebird-shadow`. This stores the cookie in a local
`0600` file rather than GitHub Actions or the public repository.

```bash
umask 077
mkdir -p /home/ubuntu/bluebird-shadow/private
chmod 700 /home/ubuntu/bluebird-shadow/private
read -r -s -p 'Paste your own X Cookie header (auth_token=...; ct0=...): ' X_COOKIE_HEADER
printf '\n'
printf '%s' "$X_COOKIE_HEADER" > /home/ubuntu/bluebird-shadow/private/x.cookies
unset X_COOKIE_HEADER
chmod 600 /home/ubuntu/bluebird-shadow/private/x.cookies
stat -c 'Cookie file permissions: %a (expected 600)' /home/ubuntu/bluebird-shadow/private/x.cookies
```

To collect the required `auth_token` and `ct0`, sign into your **own**
X account in your browser and use its browser development tools; the cookie
header is sensitive and grants account access. Do not screenshot or share it.

After installing the cookie file, open an issue in the repository with the
**exact** title `[abyss-test] bluebird-auth-shadow`. Only the repo owner's
issue will activate the workflow. It validates the local file permissions and
runs one isolated authenticated collection cycle against HanayoHiyori and
SakihaneHuya (no Discord sends, no X posting or reactions). Look for
`AUTH_SHADOW_RESULT` and `AUTH_REPOST_META` in the workflow logs.

If the file is not configured, the workflow exits without any X login attempt.
The comparison results are kept at
`/home/ubuntu/bluebird-shadow/data/auth_samples.jsonl`, while the session
database (containing sensitive cookies) is restricted to
`/home/ubuntu/bluebird-shadow/private/twscraper.db`.
No credentials or tweet bodies are uploaded to GitHub.

To revoke access or remove local credentials, terminate any active
`abyss-bluebird-auth-shadow` test container first, then remove
`private/x.cookies` and `private/twscraper.db`. Also sign out of that
browser session through X's account session settings.
