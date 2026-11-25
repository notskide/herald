# Changelog

## 4.4.0 — Media understanding and superuser controls

### Added
- Added a priority operator mode that recognizes only the configured Herald superuser IDs for elevated instruction handling.
- Added superuser-only /imagine image generation using a public Pollinations endpoint without a key.
- Added photo understanding through the configured Groq vision model, with up to three images per message.
- Removed the unverified key-required video-analysis provider; video analysis remains unavailable until a suitable public endpoint is verified.
- Updated media provider configuration and usage guidance to the README.

### Safety and reliability
- Superuser priority applies only to implemented and supported features; it does not execute arbitrary code, disclose secrets, or bypass Discord permissions, platform rules, or safety requirements.
- Video contents are treated as media to analyze, not instructions for Herald to follow.
- Image generation is restricted to configured superusers to protect the shared provider quota.

## 4.3.0 — Games and role permissions

### Member-facing updates
- Added /herald game trivia, /herald game riddle, and /herald game would-you-rather with interactive Discord buttons.
- Server owners and configured Herald superusers can update Discord role permissions through direct natural-language requests or /herald role-permissions set.
- Added direct handling for requests such as enabling Manage Messages for a specified role.
- Public patch notes are published automatically to Herald's official server once per version, with duplicate detection.

### Internal changes
- Patch notes describe member-facing features only; internal memory and bug-fix details are excluded from the public embed.

## 4.2.0 — Social presence and server control

### Member-facing updates
- More natural casual conversations and community hangouts.
- Chat games including trivia, riddles, 20 Questions, word games, and Would You Rather.
- Better continuity across recent channel conversations and ongoing games.
- Superuser-directed role assignment and Herald access management.
- Automatic publication of version notes in Herald's official Discord server, with duplicate-version detection.

### Internal changes
- Selective long-term memory retrieval and background indexing.
- Preserve all system instructions and memory context when preparing model requests.
- Avoid scanning the full memory archive on every reply.

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
