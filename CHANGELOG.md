# Changelog

## 4.0.0 — 2026-11-16

### Added
- Plain-English automations with a validated preview and explicit confirmation.
- Member-join, member-leave, and message-contains triggers.
- Role assignment and channel-message actions.
- A guided welcome flow with role, welcome channel, and verification channel selection.
- Owner-controlled delegated permissions for commands, automation management, and full management.
- Automation listing, testing, editing, pausing, resuming, and deletion.
- Controls to disable anti-spam and inactivity alert templates.
- Anti-spam warning alerts, inactivity alerts, goodbye-message templates, and scheduled announcements.
- A server status command showing key bot permissions and enabled features.
- Persistent server configuration and a 100-entry-per-server audit history in `herald_data.json`.
- `/herald audit` for recent changes, including who granted permissions and who created, changed, or tested automations.

### Changed
- Herald defaults to very short, natural replies.
- `/updatelogs` and the web interface now identify version 4.0.0.

### Safety
- New automation plans are validated against real server resources and Herald's role/channel permissions.
- New automation plans require confirmation before activation.
- Automation messages cannot use mass mentions.
- Generated automations cannot execute arbitrary code or perform destructive moderation actions.
- Test runs skip role changes and do not ping members.
