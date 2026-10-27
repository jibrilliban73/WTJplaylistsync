#!/usr/bin/env python3
"""Sync What's The Juice? unplayed Spotify podcast episodes into WTJ ✨.

Uses Spotify's OAuth PKCE (no client secret). Store SPOTIFY_REFRESH_TOKEN as
an encrypted GitHub Actions repository secret, NEVER in this repository.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

CLIENT_ID = '658f7327c1c7439c9cc8cec9aa94de4f'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SHOW_ID = '2WDgGOFL1vkOEEyGc5W78b'
PLAYLIST_ID = '1A8zHSvbvWAnK0ayho09nj'
SCOPES = 'user-read-playback-position playlist-read-private playlist-modify-private playlist-modify-public'
API_ROOT = 'https://api.spotify.com/v1'
TOKEN_PATH = Path.home() / '.wtj_playlist_sync' / 'tokens.json'


def save_local_token(data):
    TOKEN_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(data), encoding='utf-8')
    TOKEN_PATH.chmod(0o600)


def authorize():
    """Complete PKCE once on your own computer, then put the refresh token in GitHub Secrets."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(24)
    args = {
        'client_id': CLIENT_ID, 'response_type': 'code', 'redirect_uri': REDIRECT_URI,
        'scope': SCOPES, 'code_challenge_method': 'S256',
        'code_challenge': challenge, 'state': state,
    }
    url = 'https://accounts.spotify.com/authorize?' + urllib.parse.urlencode(args)
    received = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path != '/callback' or query.get('state', [''])[0] != state:
                received['error'] = 'Invalid authorization callback or state'
            elif 'error' in query:
                received['error'] = query['error'][0]
            else:
                received['code'] = query.get('code', [''])[0]
            status = 200 if received.get('code') else 400
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Spotify authorization received. Return to Terminal.')

        def log_message(self, *args):
            pass

    print('Authorize in your browser. If it does not open, visit:\n' + url)
    with HTTPServer(('127.0.0.1', 8888), Callback) as server:
        server.timeout = 180
        webbrowser.open(url)
        server.handle_request()
    if not received.get('code'):
        raise RuntimeError(received.get('error', 'No Spotify callback received within 3 minutes'))
    response = requests.post('https://accounts.spotify.com/api/token', data={
        'grant_type': 'authorization_code', 'code': received['code'],
        'redirect_uri': REDIRECT_URI, 'client_id': CLIENT_ID, 'code_verifier': verifier,
    }, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get('refresh_token'):
        raise RuntimeError('Spotify did not return a refresh token')
    data['expires_at'] = time.time() + data.get('expires_in', 3600) - 60
    save_local_token(data)
    print('Authorization saved on this computer. Your Spotify credentials were NOT sent to ChatGPT.')
    print('Next: follow README instructions to copy your refresh token into GitHub Actions Secrets.')


class Spotify:
    def __init__(self):
        self.cloud_refresh_token = os.environ.get('SPOTIFY_REFRESH_TOKEN', '').strip()
        self.local = not bool(self.cloud_refresh_token)
        if self.local:
            if not TOKEN_PATH.is_file():
                raise RuntimeError('Not authorized. Run python wtj_sync.py --authorize first.')
            self.auth = json.loads(TOKEN_PATH.read_text(encoding='utf-8'))
        else:
            self.auth = {'refresh_token': self.cloud_refresh_token, 'expires_at': 0}

    def access_token(self):
        if time.time() >= self.auth.get('expires_at', 0):
            response = requests.post('https://accounts.spotify.com/api/token', data={
                'grant_type': 'refresh_token',
                'refresh_token': self.auth['refresh_token'],
                'client_id': CLIENT_ID,
            }, timeout=30)
            if not response.ok:
                raise RuntimeError('Spotify token refresh failed (HTTP %s). Reauthorize if revoked.' % response.status_code)
            token_data = response.json()
            new_refresh_token = token_data.get('refresh_token')
            # In a cloud job the encrypted repository secret cannot be changed automatically.
            # Stop safely if Spotify rotates the refresh token (rather than pretending future syncs work).
            if new_refresh_token and new_refresh_token != self.auth['refresh_token'] and not self.local:
                raise RuntimeError('Spotify rotated the refresh token. Reauthorize and update GitHub Actions secret.')
            self.auth.update(token_data)
            self.auth['expires_at'] = time.time() + token_data.get('expires_in', 3600) - 60
            if self.local:
                save_local_token(self.auth)
        return self.auth['access_token']

    def request(self, method, path, params=None, data=None):
        for attempt in range(4):
            response = requests.request(
                method, API_ROOT + path, params=params, json=data,
                headers={'Authorization': 'Bearer ' + self.access_token()}, timeout=35,
            )
            if response.status_code == 429 and attempt < 3:
                wait = response.headers.get('Retry-After', '3')
                time.sleep(min(max(int(wait) if wait.isdigit() else 3, 1), 60))
                continue
            if not response.ok:
                raise RuntimeError(f'Spotify API returned HTTP {response.status_code} on {method} {path}; check scopes/app restrictions.')
            return response.json() if response.content else {}
        raise RuntimeError('Spotify rate-limit retry exhausted')

    def iter_items(self, path, extra_params=None):
        offset = 0
        while True:
            page = self.request('GET', path, params={'limit': 50, 'offset': offset, **(extra_params or {})})
            items = page.get('items', [])
            yield from items
            offset += len(items)
            if not items or not page.get('next'):
                return


def classify(episodes, existing):
    """Plan changes only when Spotify explicitly reports the playback status."""
    add = []
    remove = []
    skipped_unknown = 0
    skipped_existing = 0
    for episode in episodes:
        if not episode or not episode.get('uri'):
            continue
        uri = episode['uri']
        point = episode.get('resume_point')
        if not isinstance(point, dict) or not isinstance(point.get('fully_played'), bool):
            skipped_unknown += 1
            continue
        if point['fully_played']:
            if uri in existing:
                remove.append(uri)
            continue
        if episode.get('is_playable') is False:
            continue
        if uri in existing:
            skipped_existing += 1
            continue
        add.append(uri)
    return add, remove, skipped_unknown, skipped_existing

def sync(dry_run=False):
    client = Spotify()
    existing = set()
    for entry in client.iter_items(f'/playlists/{PLAYLIST_ID}/items', extra_params={'additional_types': 'track,episode'}):
        item = entry.get('item') or entry.get('track') or {}
        if item.get('uri'):
            existing.add(item['uri'])
    episodes = client.iter_items(f'/shows/{SHOW_ID}/episodes')
    add, remove, unknown, duplicate = classify(episodes, existing)
    print(f'Unfinished episodes already in playlist: {duplicate}; '
          f'unknown play status excluded: {unknown}; '
          f'to add: {len(add)}; finished to remove: {len(remove)}')
    if dry_run:
        print('DRY RUN: Playlist unchanged.')
        return
    for index in range(0, len(remove), 100):
        chunk = remove[index:index + 100]
        client.request('DELETE', f'/playlists/{PLAYLIST_ID}/items',
                       data={'items': [{'uri': uri} for uri in chunk]})
        print(f'Removed {index + len(chunk)} of {len(remove)}')
    for index in range(0, len(add), 100):
        chunk = add[index:index + 100]
        client.request('POST', f'/playlists/{PLAYLIST_ID}/items', data={'uris': chunk})
        print(f'Added {index + len(chunk)} of {len(add)}')
    print('Sync finished.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync unplayed WTJ episodes into Spotify playlist WTJ ✨')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--authorize', action='store_true', help='Authorize Spotify locally via PKCE')
    group.add_argument('--dry-run', action='store_true', help='Preview eligible episodes without adding')
    group.add_argument('--show-refresh-token', action='store_true', help='Print local refresh token; DO NOT SHARE it')
    args = parser.parse_args()
    try:
        if args.authorize:
            authorize()
        elif args.show_refresh_token:
            if not TOKEN_PATH.exists():
                raise RuntimeError('Authorize locally first')
            print(json.loads(TOKEN_PATH.read_text(encoding='utf-8'))['refresh_token'])
        else:
            sync(dry_run=args.dry_run)
    except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
