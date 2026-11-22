# herald 4.3.0

herald is a casual discord bot with server tools, automations, long-term memory, and a social personality. he's meant to be useful, but also someone people can hang out with.

## what's new in 4.3.0

- play trivia, riddles, and would-you-rather through /herald game commands.
- server owners and herald superusers can ask herald to change discord role permissions.
- role permission changes are also available through /herald role-permissions set.
- herald publishes the public-facing patch notes in its official server once per release.
- the social chat, automations, and selective memory features from 4.2.0 remain.

## what herald can do

- chat naturally and keep conversation history.
- create automations from plain english requests.
- react to new members, members leaving, and messages containing a phrase.
- assign roles and send messages to channels through approved automations.
- guide server owners through welcome and verification setup.
- manage delegated permissions for commands, automations, and full herald management.
- launch trivia, riddles, and would-you-rather with /herald game.
- let the server owner or a Herald superuser update supported Discord role permissions.
- provide anti spam alerts, inactivity alerts, goodbye messages, and scheduled announcements.
- keep an audit history of important server configuration changes.

## requirements

- python 3.10 or newer.
- a discord bot application and its token.
- a groq api key for chat and natural language automation planning.

install the dependencies:

```sh
python -m pip install -r requirements.txt
```

## environment variables

set these in your hosting provider:

- `DISCORD_BOT_TOKEN`: required discord bot token. `DISCORD_TOKEN` also works as a legacy fallback.
- `GROQ_API_KEY`: required groq api key. you can provide multiple keys as comma separated values.
- `FLASK_SECRET_KEY`: a long, random secret for web interface sessions. keep it persistent in production.
- `PORT`: optional web health check port. defaults to `8080`.

start herald:

```sh
python herald.py
```

## discord setup

in the discord developer portal, enable:

- **server members intent**
- **message content intent**

herald needs these for member join and leave events, chat, message automations, anti spam alerts, and activity tracking.

give the bot only the permissions it needs:

- view channels
- send messages
- read message history
- embed links
- use application commands
- manage roles, only if you want automatic role assignment

if you're using role assignment, move herald's highest role above the roles it needs to assign. discord checks the role hierarchy when an automation runs.

## first time setup

1. invite herald to your server with the permissions above.
2. run `/herald setup`.
3. choose the member role, welcome channel, and verification channel.
4. confirm the setup.
5. run `/herald status` to check its permissions.

you can also just tell herald what you want an automation to do. for example:

> hey herald, when a new member joins, give them the member role and welcome them in #unverified-chat with a link to #verify

herald shows a preview first. nothing gets saved until someone with permission presses **confirm and enable**. you can also use `/herald automate request`.

## permissions

the server owner can grant or revoke delegated access:

- **commands**: use the safe delegated commands under `/herald command`.
- **automation**: create and manage automations.
- **full management**: manage herald's server features.

use:

- `/herald permissions grant`
- `/herald permissions revoke`
- `/herald permissions list`

server administrators can use server management features too, but delegated permissions are controlled by the server owner.

## automation controls

use these commands to manage automations:

- `/herald automations list`
- `/herald automations test`
- `/herald automations edit`
- `/herald automations pause`
- `/herald automations resume`
- `/herald automations delete`
- `/herald audit`

right now, natural language automations support member joins, member leaves, and messages containing a specific phrase. actions can assign an existing role to a joining member or send a message to an existing text channel.

herald doesn't run generated code, mass ban or kick members, delete channels, or perform destructive moderation actions. Role permission editing is restricted to the server owner and configured Herald superusers; Discord still enforces Herald's Manage Roles permission and role hierarchy.

## templates and schedules

ready made templates are available through `/herald template`.

- **anti spam**: warns after a burst of messages. it doesn't automatically delete messages or punish members.
- **inactivity**: posts an alert after the configured number of days without activity. tracking starts when herald sees a member join or send a message.
- **goodbye**: sends a message when a member leaves.

you can disable anti spam and inactivity alerts through the template command.

to schedule an announcement, use `/herald schedule`. the time must use ISO 8601 and include a timezone. for example:

```text
2026-12-01T18:30:00+00:00
```

utc is recommended.

## memory and privacy

herald stores conversations as original exchanges inside its memory channel. categories are private to the person they belong to when herald retrieves memory, while the separate global archive is reserved for broadly useful, non personal information.

a category gets another numbered part when it reaches discord's 2,000 character message limit. herald also has basic secret redaction and can leave an exchange out of memory when asked.

**important:** per user separation controls what herald loads into a conversation. it does not make messages in the discord memory channel individually private. restrict that channel to herald and trusted admins if you want to protect the stored archives from other server members.

## persistent storage

herald stores server automation settings, delegated permissions, activity timestamps, scheduled announcements, and the latest 100 audit entries per server in `herald_data.json`.

keep this file on persistent storage if your host uses an ephemeral filesystem, or those settings may disappear after a restart or redeploy.

existing files like `guild_settings.json`, `banned_users.json`, and `pending_deliveries.json` are separate and are still used by the bot.

## patch notes

check [CHANGELOG.md](CHANGELOG.md) or use `/updatelogs` in discord to see what's changed.
