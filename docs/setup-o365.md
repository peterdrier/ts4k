# O365 (Microsoft) Setup

This guide walks through connecting ts4k to one or more Microsoft 365 mailboxes.

## Prerequisites

- Python 3.12+ with ts4k installed (`pip install -e .`)
- A Microsoft 365 account (personal, work, or school)
- Access to the Azure Portal (free — included with any Microsoft account)

## Step 1: Create an Azure App Registration

Microsoft requires any app that accesses mailboxes to register through Azure Active Directory. This is how Microsoft controls which apps can read your email. The registration is free, takes a few minutes, and you only do it once. A single registration works for all your mailboxes.

1. Go to [Azure Portal > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Fill in:
   - Name: `ts4k`
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
   - Redirect URI: leave blank (not needed for device code flow)
4. Click **Register**
5. On the overview page, copy these two values — you'll need them:
   - **Application (client) ID**
   - **Directory (tenant) ID** (or use `common` to support any account type)

## Step 2: Add API Permissions

1. In your app registration, go to **API permissions**
2. Click **Add a permission > Microsoft Graph > Delegated permissions**
3. Search for and add: **Mail.Read**
4. Click **Add permissions**

You should see `Mail.Read` listed under Configured permissions. No admin consent is required for this permission.

> **Note:** ts4k only requests `Mail.Read` — it can read messages but cannot send, delete, or modify anything. Adding `Mail.Send` later for outbound support won't require admin consent either.

## Step 3: Enable Public Client Flow

The device code flow (where you enter a code on a web page instead of a redirect) requires enabling the public client setting:

1. Go to **Authentication** in your app registration
2. Scroll to **Advanced settings**
3. Set **Allow public client flows** to **Yes**
4. Click **Save**

## Step 4: Register the Source

```bash
ts4k src add o o365 client_id=YOUR_CLIENT_ID tenant_id=YOUR_TENANT_ID
```

This tells ts4k that prefix `o` maps to your O365 account. Use `common` as tenant_id if you want to support any Microsoft account type:

```bash
ts4k src add o o365 client_id=abc123-def456 tenant_id=common
```

Verify it's registered:

```bash
ts4k src list
```

## Step 5: Authenticate

```bash
ts4k auth o365 --client-id YOUR_CLIENT_ID --tenant-id YOUR_TENANT_ID
```

This prints a device code to your terminal:

```
To sign in, use a web browser to open https://microsoft.com/devicelogin
and enter the code ABCD1234 to authenticate.
```

Open the URL, enter the code, sign in with your Microsoft account, and grant the permission. The token is cached to:

```
~/.config/ts4k/microsoft/{client_id}/token_cache.json
```

The token refreshes automatically. You won't need to re-authenticate unless you revoke access.

## Step 6: Verify It Works

```bash
ts4k wn --source o
```

You should see your recent O365 messages in pipe-delimited format. Check overall status:

```bash
ts4k st
```

## Multiple Mailboxes

A single app registration can access multiple mailboxes. Use different prefixes with the `mailbox` parameter:

```bash
ts4k src add o  o365 client_id=abc123 tenant_id=common                          # personal (default)
ts4k src add ow o365 client_id=abc123 tenant_id=common mailbox=peter@work.com   # work
ts4k src add os o365 client_id=abc123 tenant_id=common mailbox=shared@team.com  # shared mailbox
```

Query them separately or together:

```bash
ts4k wn --source o    # personal only
ts4k wn --source ow   # work only
ts4k wn               # all sources
```

### Discover Available Mailboxes

If you're not sure which mailboxes are available to your account:

```bash
ts4k src discover
```

This queries Microsoft Graph for your primary mailbox, aliases, and proxy addresses, and suggests which sources to add.

## Troubleshooting

**"AADSTS700016: Application not found"**
Double-check your client_id. Copy it again from **Azure Portal > App registrations > ts4k > Overview**.

**"AADSTS65001: The user or administrator has not consented"**
Make sure `Mail.Read` is listed under API permissions in your app registration (Step 2).

**"AADSTS7000218: The request body must contain ... client_secret"**
You need to enable public client flows (Step 3). Go to Authentication > Advanced settings > Allow public client flows > Yes.

**Device code expired**
The code is valid for about 15 minutes. If it expires, run the auth command again.

**Shared mailbox access denied**
Shared mailboxes require that your account has been granted access by an administrator. ts4k can't bypass mailbox permissions.
