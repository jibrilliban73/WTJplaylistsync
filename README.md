# WTJ ✨ — What's The Juice? Spotify playlist sync

Automatically checks episodes of [What's The Juice?](https://open.spotify.com/show/2WDgGOFL1vkOEEyGc5W78b) against [WTJ ✨](https://open.spotify.com/playlist/1A8zHSvbvWAnK0ayho09nj).

**Status:** Spotify development apps require an active Premium subscription for the app owner and an authorized refresh token. Check GitHub Actions for the latest sync result.

## What the script does

- Fetches all episodes and the playlist's current items (including pagination).
- Adds episodes only if Spotify explicitly reports `resume_point.fully_played` as `false` and the episode is not in the playlist.
- Skips episodes with **unknown playback state**. If Spotify omits `resume_point`, the episode is not automatically added or removed.
- Removes only episodes of this show that Spotify explicitly reports as fully played. Other playlist items are left alone. Adds and removes in batches of at most 100.
- Checks approximately every 15 minutes using GitHub Actions; scheduled jobs may be delayed.

## One-time authorization: run on a computer once

Spotify requires **user authorization**; the Client ID alone is not sufficient. Spotify Development Mode currently requires a Spotify Premium account. You can do this one-time setup on any Mac/Windows/Linux device with Python 3.10+ and a web browser. The device can be shut down after setup; GitHub runs scheduled jobs in the cloud.

1. Download the repository using **Code → Download ZIP** and extract it.
2. Open Terminal (macOS/Linux) or PowerShell (Windows) in the folder containing `wtj_sync.py`.
3. Install dependencies: `python3 -m pip install -r requirements.txt` (Windows: `py -m pip install -r requirements.txt`).
4. Run `python3 wtj_sync.py --authorize` (Windows: `py wtj_sync.py --authorize`). Approve the Spotify scopes in the opened browser. The redirect URI in Spotify app settings must be `http://127.0.0.1:8888/callback`.
5. Run `python3 wtj_sync.py --show-refresh-token` (Windows: `py wtj_sync.py --show-refresh-token`) **locally** and copy the token without sharing it with ChatGPT or committing it to GitHub.
6. Visit repository **Settings → Secrets and variables → Actions → New repository secret**. Name it exactly `SPOTIFY_REFRESH_TOKEN` and paste the refresh token. Save.
7. Open **Actions → WTJ Spotify Playlist Sync → Run workflow** with **dry_run** checked. Review the log counts. If reasonable, run it again with **dry_run** unchecked for the initial one-time catch-up.
8. The scheduled cloud sync will then run while your MacBook is off. See **Actions** for the status of each execution.

## Limitations and troubleshooting

- A new public repo is fine; **never commit credentials**. Only use GitHub Actions encrypted secrets.
- GitHub cannot retrieve your Spotify refresh token without one-time Spotify OAuth approval. The script never asks for a Spotify password or Client Secret.
- Spotify may restrict a new Development Mode app's API endpoints. A `403` in GitHub Actions means the sync has not succeeded and needs troubleshooting.
- Spotify's `fully_played` flag is not a comprehensive, independently audited listening history. Missing status is deliberately skipped to avoid false positives; the user's exact Spotify "unplayed" filter may differ.
- GitHub scheduled jobs can be delayed or disabled on inactive public repositories (GitHub may disable after 60 days of repository inactivity). Re-enable from Actions if needed.
- Spotify refresh tokens expire after 180 days, and can also rotate. Reauthorize locally and update the GitHub secret when required; this workflow does not update its own secret.
- The GitHub Actions scheduled job requires the repository workflow to remain enabled and the secret to remain valid.
