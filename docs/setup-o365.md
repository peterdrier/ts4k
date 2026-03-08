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
5. On the overview page, copy the **Application (client) ID** — you'll need it in Part 2.

### Step 2: Add Permissions

The Azure portal's UI for adding permissions is unreliable — permissions often don't appear in the search. The most reliable method is to edit the manifest directly:

1. In your app registration, go to **Manifest** (left sidebar)
2. Find `"requiredResourceAccess"` and replace it with:

```json
"requiredResourceAccess": [
    {
        "resourceAppId": "00000003-0000-0000-c000-000000000000",
        "resourceAccess": [
            {
                "id": "37f7f235-527c-4136-accd-4a02d197296e",
                "type": "Scope"
            },
            {
                "id": "7427e0e9-2fba-42fe-b0c0-848c9e6a8182",
                "type": "Scope"
            },
            {
                "id": "570282fd-fa5c-430d-a7fd-fc8dc98a9dca",
                "type": "Scope"
            },
            {
                "id": "7b9103a5-4610-446b-9670-80643382c1fa",
                "type": "Scope"
            }
        ]
    }
],
```

3. Click **Save** at the top

That adds these delegated permissions:

| GUID | Permission | Purpose |
|------|-----------|---------|
| `37f7f235-...` | `offline_access` | Token refresh |
| `7427e0e9-...` | `openid` | Sign-in |
| `570282fd-...` | `Mail.Read` | Read your mailbox |
| `7b9103a5-...` | `Mail.Read.Shared` | Read shared/delegate mailboxes |

Verify by going to **API permissions** — all four should appear.

> **Note:** ts4k only requests read permissions — it cannot send, delete, or modify anything.

### Step 3: Enable Public Client Flow

1. Go to **Authentication** in your app registration
2. Under **Settings**, set **Allow public client flows** to **Yes**
3. Click **Save**

That's it for the Azure side. You won't need to come back here.

## Part 2: Add a Mailbox

### Step 4: Add the Mailbox to ts4k

```bash
ts4k src add o o365 client_id=YOUR_CLIENT_ID
```

This tells ts4k: "I have a mailbox nicknamed `o`, it's on Microsoft 365, and here are the app credentials to access it."

- `o` is a short nickname you choose — you'll use it to refer to this mailbox (e.g. `ts4k wn --source o`). Can be anything: `o`, `work`, `ms`, etc.
- `client_id` is the value you copied from Azure in Step 1.

> **Note:** ts4k uses `tenant_id=common` by default, which lets Microsoft route you to your home tenant automatically. You don't need to specify a tenant ID unless you have a specific reason to pin to one tenant.

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
You need to enable public client flows (Step 3). Go to Authentication > Settings > Allow public client flows > Yes.

**Device code expired**
The code is valid for about 15 minutes. If it expires, run the auth command again.

**Shared mailbox access denied (403 ErrorAccessDenied)**
Shared mailboxes require two things:
1. Your account must have **delegate access** (Full Access or Read permission) granted by an Exchange admin. Go to [Microsoft 365 admin center > Shared mailboxes](https://admin.microsoft.com/#/SharedMailbox), select the mailbox, and add your account under **Members**.
2. No extra OAuth scope is needed — `Mail.Read` covers shared mailboxes when you have delegate access.

Once delegate access is granted, add the shared mailbox to ts4k:
```bash
ts4k src add oh o365 mailbox=help@example.com
ts4k auth o365 oh
```
