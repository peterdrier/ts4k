# O365 (Microsoft) Setup

This guide walks through connecting ts4k to one or more Microsoft 365 mailboxes.

## Prerequisites

- Python 3.12+ with ts4k installed (`pip install -e .`)
- A Microsoft 365 account (personal, work, or school)
- Access to the Azure Portal (free — included with any Microsoft account)

## Part 1: Register the App (one-time)

Microsoft requires apps to register before they can access mailboxes. You do this once — a single registration works for all your mailboxes.

### Step 1: Create the Registration

1. Go to [Azure Portal > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Fill in:
   - Name: `ts4k`
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
   - Redirect URI: leave blank
4. Click **Register**
5. On the overview page, copy these two values — you'll need them in Part 2:
   - **Application (client) ID**
   - **Directory (tenant) ID** (or use `common` to support any account type)

### Step 2: Add Mail.Read Permission

1. In your app registration, go to **API permissions**
2. Click **Add a permission > Microsoft Graph > Delegated permissions**
3. Search for and add: **Mail.Read**
4. Click **Add permissions**

> **Note:** ts4k only requests `Mail.Read` — it cannot send, delete, or modify anything.

### Step 3: Enable Public Client Flow

1. Go to **Authentication** in your app registration
2. Scroll to **Advanced settings**
3. Set **Allow public client flows** to **Yes**
4. Click **Save**

That's it for the Azure side. You won't need to come back here.

## Part 2: Add a Mailbox

### Step 4: Add the Mailbox to ts4k

```bash
ts4k src add o o365 client_id=YOUR_CLIENT_ID tenant_id=YOUR_TENANT_ID
```

This tells ts4k: "I have a mailbox nicknamed `o`, it's on Microsoft 365, and here are the app credentials to access it."

- `o` is a short nickname you choose — you'll use it to refer to this mailbox (e.g. `ts4k wn --source o`). Can be anything: `o`, `work`, `ms`, etc.
- `client_id` and `tenant_id` are the values you copied from Azure in Step 1.

### Step 5: Sign In

```bash
ts4k auth o365
```

This opens a device code flow — you'll see something like:

```
To sign in, use a web browser to open https://microsoft.com/devicelogin
and enter the code ABCD1234 to authenticate.
```

Open that URL, enter the code, sign in with your Microsoft account, and approve. Done — the token refreshes automatically from here on.

### Step 6: Verify

```bash
ts4k wn --source o
```

You should see your recent messages. Check overall status with `ts4k st`.

## Adding More Mailboxes

You don't need another app registration. ts4k inherits the client_id and tenant_id from your first O365 source, so adding another mailbox is just:

```bash
ts4k src add ow o365 mailbox=peter@work.com    # work account
ts4k auth o365 ow                               # sign in to it
```

Add as many as you need:

```bash
ts4k src add os o365 mailbox=shared@team.com   # shared mailbox
ts4k auth o365 os
```

Query them separately or together:

```bash
ts4k wn --source o    # personal only
ts4k wn --source ow   # work only
ts4k wn               # all sources
```

Not sure which mailboxes are available? Run `ts4k src discover` after authenticating — it queries Microsoft Graph for your primary mailbox, aliases, and proxy addresses.

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
