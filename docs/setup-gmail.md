# Gmail Setup

This guide walks through connecting ts4k to one or more Gmail accounts.

## Prerequisites

- Python 3.12+ with ts4k installed (`pip install -e .`)
- A Google account
- A web browser (for the OAuth consent flow)

## Step 1: Create a Google Cloud Project

Google requires any app that accesses Gmail to authenticate through a "project" in their Cloud Console. This is how Google controls which apps can read your email — even apps you build for yourself. The project is free, takes a few minutes to set up, and you only do it once.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a project** (top bar) then **New Project**
3. Name it `ts4k`, click **Create**
4. Make sure `ts4k` is selected as the active project

## Step 2: Enable the Gmail API

1. Go to **APIs & Services > Library**
2. Search for "Gmail API"
3. Click **Gmail API**, then **Enable**

## Step 3: Configure the OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External** (unless you have a Google Workspace org), click **Create**
3. Fill in the required fields:
   - App name: `ts4k`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue** through the remaining steps
5. Under **Test users**, add your Gmail address(es)
6. Click **Save and Continue**, then **Back to Dashboard**

> **Important:** While in "Testing" mode, Google expires your refresh token after 7 days — meaning you'd need to re-authenticate weekly. To avoid this, go back to **OAuth consent screen** and click **Publish App**. Google will show a warning about verification, but for a personal-use app with only `gmail.readonly` scope, no verification is actually required. Once published, tokens last indefinitely (until you revoke them).

## Step 4: Create OAuth Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Application type: **Desktop app**
4. Name: `ts4k` (or anything)
5. Click **Create**
6. Click **Download JSON** on the confirmation dialog

Save the downloaded file as:

```
~/.config/ts4k/google/client_secret.json
```

This location is shared across all Gmail accounts. If you need a different OAuth app per account, save it to `~/.config/ts4k/google/{email}/client_secret.json` instead.

## Step 5: Register the Source

```bash
ts4k src add g gmail email=alice@gmail.com
```

This tells ts4k that prefix `g` maps to the Gmail account `alice@gmail.com`. You'll use `g` as shorthand in all commands.

Verify it's registered:

```bash
ts4k src list
```

## Step 6: Authenticate

```bash
ts4k auth gmail alice@gmail.com
```

This opens your browser for Google's OAuth consent flow. Sign in, grant read-only access to Gmail, and the token is saved to:

```
~/.config/ts4k/google/alice@gmail.com/token.json
```

The token refreshes automatically on subsequent uses. You won't need to re-authenticate unless you revoke access or the token expires after extended inactivity.

## Headless / Remote Server

If you're running ts4k on a headless machine (e.g. a NUC over SSH), the browser can't open automatically. ts4k will fall back to a copy-paste flow:

```
$ ts4k auth gmail alice@gmail.com
No browser available. Open this URL in any browser:

  https://accounts.google.com/o/oauth2/auth?client_id=...&scope=...&redirect_uri=http://localhost:8085/&...

After signing in, your browser will try to redirect to a page that won't load.
Copy the FULL URL from your browser's address bar and paste it here:

> http://localhost:8085/?state=abc&code=4/0AQ...&scope=...
Saved new token for alice@gmail.com
```

What's happening:

1. ts4k prints a Google OAuth URL
2. You open that URL in any browser (on any machine — your laptop, phone, etc.)
3. You sign in and grant access
4. Google redirects to `http://localhost:8085/?code=...` — this page won't load (that's expected)
5. Copy the full URL from your browser's address bar and paste it back into the terminal
6. ts4k extracts the authorization code and saves the token

No SSH tunnels, no file transfers, no extra flags needed.

## Step 7: Verify It Works

```bash
ts4k wn --source g
```

You should see your recent Gmail messages in pipe-delimited format. If you get an auth error, run `ts4k auth gmail alice@gmail.com` again.

Check overall status:

```bash
ts4k st
```

## Multiple Gmail Accounts

Use different prefixes for each account:

```bash
ts4k src add g  gmail email=alice@gmail.com        # personal
ts4k src add gw gmail email=alice@company.com      # work

ts4k auth gmail alice@gmail.com
ts4k auth gmail alice@company.com
```

Now you can query them separately or together:

```bash
ts4k wn --source g    # personal only
ts4k wn --source gw   # work only
ts4k wn               # both
```

Each account gets its own token at `~/.config/ts4k/google/{email}/token.json`. They can share the same `client_secret.json` or use separate ones.

## Scope

ts4k requests `gmail.readonly` — it can read messages but cannot send, delete, or modify anything.

## Troubleshooting

**"Access blocked: ts4k has not completed the Google verification process"**
This is normal for apps in Testing mode. Click **Continue** (or **Advanced > Go to ts4k**). Only test users you added in Step 3 can authorize.

**"Token has been expired or revoked"**
Run `ts4k auth gmail alice@gmail.com` to re-authenticate.

**"File not found: client_secret.json"**
Make sure the file is at `~/.config/ts4k/google/client_secret.json` or `~/.config/ts4k/google/{email}/client_secret.json`.

**Rate limits**
The Gmail API has generous quotas for personal use. If you hit limits during preload, use `--throttle` to add delays between pages:

```bash
ts4k preload --source g --throttle 1.0
```
