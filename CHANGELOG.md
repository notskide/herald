# Changelog

## 4.2.0 — Intelligence and social presence

### Added
- A consistent social persona designed for casual conversation, community hangouts, and lightweight chat games.
- Recent same-channel conversation context so Herald can follow ongoing discussions and games across turns.
- Selective long-term memory retrieval based on the current message, instead of attaching the full archive to every reply.
- A background memory index built at startup so normal replies no longer scan the memory channel.
- Relevant private and shared memories are retrieved locally without an extra AI classification request during response generation.

### Fixed
- Preserve and merge all system instruction and memory messages before sending a request to the model. Previously, message cleanup silently discarded every system message after the first.
- Avoid loading thousands of Discord memory messages on every incoming conversation.

### Notes
- Herald has a consistent AI character, not literal consciousness or experiences outside the information available to it.
- Existing memory archives remain in Discord and are indexed in the background after startup.

# Changelog

## 4.1.0 — 2026-11-16

### Added
- Full conversation archives stored as original user and Herald exchanges instead of AI-generated summaries.
- Separate private memory categories for each Discord user, organized by conversation topic.
- Automatic continuation into Part 2, Part 3, and later messages when a category reaches Discord's message limit.
- A separate global memory archive for broadly useful, non-personal information that is safe to share across users.
- Per-user memory retrieval so Herald loads only the current user's private categories alongside the shared global archive.
- Basic secret redaction and an option to keep an exchange off the record by asking Herald not to remember or save it.

### Changed
- Herald's memory instructions and the web interface now identify version 4.1.0.
- The `/updatelogs` command now describes the full conversation memory system.

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
