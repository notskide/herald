# Herald 4.0.0

Herald is a short, casual Discord chatbot with server-aware automation tools.

## What's new in 4.0.0

- Shorter, more natural conversation by default.
- Plain-English automation requests for member joins, member leaves, and matching message phrases.
- A confirmation preview before a new automation is saved.
- Guided welcome and verification setup with role, welcome-channel, and verification-channel selectors.
- Owner-controlled delegated permissions for commands, automation management, and full Herald management.
- Automation list, test, pause, resume, and delete controls.
- Anti-spam warning alerts, inactivity alerts, goodbye-message templates, and scheduled announcements.
- Server status checks for Herald's available permissions.

## Requirements

- Python 3.10 or newer.
- A Discord bot application and token.
- A Groq API key for chat and natural-language automation planning.

Install the dependencies:

```sh
python -m pip install -r requirements.txt
```

## Environment variables

Set these in your hosting provider:

- `DISCORD_BOT_TOKEN`: required Discord bot token. `DISCORD_TOKEN` is accepted as a legacy fallback.
- `GROQ_API_KEY`: required Groq API key. Multiple keys can be supplied as comma-separated values.
- `FLASK_SECRET_KEY`: a long, random secret used to sign web-interface sessions. Set this to a persistent value in production.
- `PORT`: optional web health-check port; defaults to `8080`.

Start Herald:

```sh
python herald.py
```

## Discord Developer Portal setup

Enable the **Server Members Intent** and **Message Content Intent** for the bot. Herald uses member events for join/leave automations and message content for chat, message-triggered automations, anti-spam alerts, and activity tracking.

Give the bot only the permissions it needs:

- View Channels
- Send Messages
- Read Message History
- Embed Links
- Use Application Commands
- Manage Roles, only if automatic role assignment is needed

For role assignment, move Herald's highest role above every role it needs to assign. Herald checks the role hierarchy before saving a rule and Discord checks it again when a member joins.

## First-time server setup

1. Invite Herald with the permissions above.
2. Run `/herald setup` to open the welcome and verification wizard.
3. Choose the member role, welcome channel, and verification channel.
4. Confirm the setup.
5. Use `/herald status` to check Herald's permissions.

You can also describe an automation directly to Herald in a server channel, for example:

> hey herald, when a new member joins, give them the member role and welcome them in #unverified-chat with a link to #verify

Herald will create a preview. Nothing is saved until an authorized person presses **Confirm and enable**. The slash-command alternative is `/herald automate request`.

## Permissions

Only the server owner can grant or revoke delegated access:

- **Commands**: use the safe delegated commands under `/herald command`.
- **Automation**: create and manage automations.
- **Full management**: manage Herald's server features.

Use `/herald permissions grant`, `/herald permissions revoke`, and `/herald permissions list`. The owner can also ask Herald in natural language to let a mentioned member use delegated commands.

Server administrators can use server-management features, but only the owner can grant or revoke delegated permissions.

## Automation controls

- `/herald automations list`
- `/herald automations test`
- `/herald automations pause`
- `/herald automations resume`
- `/herald automations delete`

Supported natural-language triggers are member joins, member leaves, and messages containing a specified phrase. Supported actions are assigning an existing role to a joining member and sending a message to an existing text channel. Herald does not execute generated code, mass-ban, mass-kick, delete channels, or perform destructive moderation actions.

Ready-made templates are available with `/herald template`:

- Anti-spam: warns after a burst of messages. It does not automatically delete messages or punish members.
- Inactivity: posts an alert after the configured number of days without activity. Tracking begins when Herald sees a member join or message.
- Goodbye: posts a message when a member leaves.

Schedule an announcement with `/herald schedule`. The time must be ISO 8601 and include a timezone, for example `2026-12-01T18:30:00+00:00`. UTC is recommended.

## Persistent storage

Herald stores its server automation settings, delegated permissions, activity timestamps, and scheduled announcements in `herald_data.json`. Keep this file on persistent storage when deploying to a host that uses ephemeral filesystems; otherwise settings may be lost on restart or redeploy.

Existing Herald files such as `guild_settings.json`, `banned_users.json`, and `pending_deliveries.json` remain separate and continue to be used by the existing bot features.

## Patch notes

See [CHANGELOG.md](CHANGELOG.md), or use `/updatelogs` in Discord.
