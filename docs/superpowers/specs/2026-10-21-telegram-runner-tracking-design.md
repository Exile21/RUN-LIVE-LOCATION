# Telegram runner tracking — MVP design

Date: 2026-10-21
Status: Implemented. Automated and synthetic browser verification complete; real Telegram phone verification remains pending.

## 1. Outcome and agreed decisions

Build a Telegram bot and web application for organizers tracking fewer than 100 runners per event. Organizers see runners on the event's running route. Spectators see a read-only version using a shareable event link.

Agreed decisions:

- Runners send Live Location privately to the bot, not to the event group.
- Spectators need only the event link, with no account or access code.
- Runner consent must explicitly cover this spectator visibility.
- Organizers upload GPX route files.
- The application uses Django, SQLite, and Leaflet on one server.
- Maps poll every five seconds. This does not guarantee GPS updates every five seconds.
- Keep only each runner's latest position, not a history or replay.
- `/stop` removes the runner from public tracking and stops accepting their positions.
- Closing an event clears its stored runner coordinates.

Joining a Telegram group cannot automatically grant GPS access. The bot welcomes new members, but runners must open the private bot chat, consent, and manually send Live Location. Existing group members can use the same onboarding link; the application does not enumerate an existing Telegram group roster.

## 2. Scope and operating assumptions

- One organizing organization; active staff accounts can manage all events. No separate organizer tenants or event-specific staff roles.
- Multiple events may exist, but a Telegram group can belong to only one open event.
- A runner can track in only one open event at a time. They must stop their current participation before selecting another event.
- The bot must be a group administrator for reliable membership checks and member-status updates. The bot does not need permission to delete messages or manage members.
- A runner appears on the event roster only after opting in, not merely after joining its group.
- The first deployment uses one application instance and persistent local storage. Fewer than 100 runners does not imply unlimited spectator capacity.

## 3. Architecture and alternatives

Selected: a single Django application serves organizer pages, spectator pages, JSON snapshots, and the Telegram webhook. Django supplies sessions, password authentication, CSRF protection, forms, ORM, migrations, and the administrative interface. SQLite stores application data. Server-rendered templates and small vanilla JavaScript modules render Leaflet maps and refresh snapshots.

Use a supported Django 5.2 LTS patch release. Python 3.14 requires Django 5.2.8 or later; the local Python is 3.14.8. Resolve and pin a current security-patched release during implementation. Leaflet's official quick start currently uses 1.9.4. Use `defusedxml` for untrusted GPX files rather than writing custom XML defenses.

Alternative considered: a managed PostgreSQL database and separately hosted frontend/backend. This adds services and deployment work without a demonstrated need at this scale. Move to PostgreSQL when measured SQLite write contention, multiple application instances, or spectator traffic justify it.

No queue, Redis, WebSockets, native mobile application, or separate frontend build system is required for this MVP.

## 4. User flows

### 4.1 Organizer setup

1. Create a staff account using Django's management command; ship no default credentials.
2. Create an event with name, route, and Telegram group ID.
3. Add the bot to the group as an administrator. A `/groupid` command in the group provides the numeric ID for setup; it does not change event settings.
4. Validate the configured group and bot administrator status before opening the event. Fail closed if Telegram cannot verify setup.
5. Copy the runner onboarding link and the separate spectator link. Organizers can post or pin the onboarding link for existing members.
6. Open the event dashboard. Closing the event disables intake and public tracking, and clears saved coordinates in the same database transaction.

The event lifecycle is draft, open, and closed. A closed event cannot reopen; create a new event for another activity. This prevents old location messages and consents from silently becoming active again.

### 4.2 Runner onboarding

1. When a person joins a bound, open event group, the bot sends a group welcome with an event-specific private-chat deep link.
2. The runner opens the link and presses Start. The bot cannot initiate this private conversation on its own.
3. The bot verifies that the sender belongs to the event group using `getChatMember`. Accept current members, administrators, owners, and restricted members whose `is_member` is true. Reject users who left or were banned; verification errors must not grant access.
4. Show the consent notice and an explicit inline consent button. Record the event, Telegram identity, consent time, and notice version from the authenticated Telegram update, not browser-supplied user IDs. Bind the button to a fresh onboarding challenge; old or repeated button presses must not reactivate withdrawn tracking.
5. Explain how to choose Telegram's attachment menu, Location, and Share Live Location. A `request_location` keyboard button requests current location, not continuous live sharing, so it is not the tracking mechanism.
6. Use the runner's Telegram first name as the initial public display name, show it in the consent notice, and allow staff to edit it. Do not publish usernames, phone numbers, Telegram IDs, or chat IDs.
7. After consent, accept a newly sent private live-location message for this event. Do not activate a location message sent before consent, a forwarded location, or an edit to an unregistered message.

Consent notice meaning: "Your display name and latest location will be visible to anyone with this event's link, including people outside the group. Use /stop to remove yourself. We keep no location history and clear saved coordinates when the event closes. Tracking may be delayed or inaccurate."

### 4.3 Location updates and stopping

- Accept only direct private-chat `message` and `edited_message` updates associated with the authenticated Telegram sender and their consented event.
- A new live-location message starts or replaces the runner's active sharing session. Save its private chat ID, message ID, original message time, and live-period information.
- Only edits to the active session can update its position. Edits from old sessions, duplicates, and out-of-order positions must not overwrite newer state or revive stopped tracking.
- Reject a one-time location pin as tracking input and explain that Live Location is required. Ignore unrelated private and group messages.
- Check current group membership before accepting a new position. A temporary Telegram verification failure leaves the previous position unchanged; it must not mark the runner freshly updated. Member-removal updates immediately withdraw the affected runner and clear their position.
- `/stop` withdraws participation, clears coordinates, and prevents subsequent updates from the existing sharing message. It does not claim to stop Telegram's user-owned message. Tell the runner how to stop sharing in Telegram too.
- Starting again requires fresh consent and a newly sent live-location message. A closed event or a removed group member cannot resume tracking.
- If an edit to a known live message indicates it is no longer active, end that session. Do not assume Telegram always sends an immediate stop notification; finite expiry and stale status provide fallback behavior.

## 5. Dashboard and spectator experience

### Organizer dashboard

- Protected event list, event setup, GPX upload, open/close controls, and spectator-link rotation.
- A map showing the running route, numbered runner markers, and a searchable runner list.
- Selecting a runner highlights their marker and shows display name, tracking status, last received position time, and accuracy when provided.
- An event summary shows consented participants, currently live runners, and stale runners. Do not represent the roster as all Telegram group members.
- Show connection errors and a last-successful-refresh time. A failed fetch must not display a misleading connected status.

### Spectator page

- A separate, read-only map with event title, route, participating display names, eligible current positions, and freshness status.
- The link is an unlisted capability, not authenticated or confidential access. Anyone can forward it or copy visible positions.
- No event directory or sequential-ID lookup. Use a cryptographically random token with at least 128 bits of entropy, distinct from the onboarding token.
- Rotating the spectator token invalidates both the old page and its JSON endpoint. Closing the event returns an ended state with no runner positions.
- The public payload uses event-scoped runner identifiers and an explicit field allowlist. It never exposes internal Telegram identifiers, consent records, or staff controls.

### Location state and map behavior

- Waiting: consent exists, but no active position has arrived.
- Live: active sharing has not expired and its latest position is at most 120 seconds old.
- Stale: active sharing has not expired, but its latest position is more than 120 seconds old. Retain the last marker with a visible stale label; do not present it as current.
- Expired: the finite live-sharing period has ended. Show status without a position. Treat Telegram's indefinite live-period sentinel as indefinite, not an ordinary finite duration.
- Stopped or removed: exclude the participant from spectator responses and clear coordinates. Staff may retain the non-location participation record.

Calculate freshness from the position's Telegram timestamp, not the time a dashboard fetched it. Store server receipt time separately. Validate timestamps and never let an implausible future timestamp extend live status. Offline browser state is separate from runner freshness.

Refresh every five seconds without overlapping requests. Reconcile markers in place, preserve the user's map position, and fit the route only on initial load or an explicit control. Use accessible controls, visible focus states, status text rather than color alone, and a usable mobile layout. The visual design is map-first and original, not a reproduction of UTMB branding.

## 6. Data and interface boundaries

Use a small set of models:

- **Event:** name, lifecycle status, bound group ID, parsed route geometry, onboarding token, spectator token, created/opened/closed times.
- **Participant:** event, Telegram user/private-chat identity, public display name, pending consent challenge, consent version/time, withdrawn state, active sharing message metadata, latest coordinates/accuracy, Telegram position time, receipt time, and update-order metadata.
- **Staff account:** Django's existing user/session models.

Enforce unique participation per event/user and only one active tracking participation per user. Keep only the latest coordinates. Do not store raw Telegram payloads, original GPX uploads after parsing, or historical position rows.

Logical interfaces:

- Staff-only event pages and mutation endpoints, plus staff-only event snapshot JSON.
- Token-addressed spectator page and read-only snapshot JSON.
- One POST-only Telegram webhook authenticated by `X-Telegram-Bot-Api-Secret-Token`.
- Small application functions for GPX parsing, Telegram update handling, and snapshot serialization. No generic service or repository framework.
- Management commands for webhook registration and setup diagnostics. Register `message`, `edited_message`, `callback_query`, and `chat_member` update types explicitly; consent buttons require callback-query delivery.

The webhook adapter validates input and Telegram identity; tracking logic enforces consent and session rules; the snapshot serializer is the public-data boundary. Keep database writes transactional and short. Do not use a single global update-ID cutoff: updates for different runners can arrive out of order. Location and lifecycle writes must be idempotent under Telegram retries.

## 7. Validation, privacy, and security

- Require active staff authentication for every organizer page and API, not merely hidden buttons. Use Django's session and CSRF protections for staff mutations. All staff manage the same organization.
- Exempt only the authenticated Telegram webhook from browser CSRF checks. Check its secret in constant time before parsing or processing. Invalid secrets receive an error without changing state.
- Limit webhook body size to 256 KiB. Validate JSON structure, integer identities, finite coordinates, latitude/longitude ranges, optional accuracy, and sensible timestamps. Never trust an event or user ID supplied by a spectator.
- GPX files are limited to 2 MiB and 20,000 points. Reject malformed XML, entities/DTDs, non-finite or out-of-range coordinates, and files without at least two usable points in a track or route. Preserve separate route segments rather than drawing false lines between them. Escape all uploaded names and runner text.
- Keep the bot token, webhook secret, and Django secret key server-side in environment variables. Do not include credentials or precise positions in application logs or error pages.
- Use HTTPS in production, secure session/CSRF cookies, explicit allowed hosts/origins, and `DEBUG=False`. Do not serve SQLite files, uploaded files, or environment files publicly.
- Apply `no-store` to position responses and private pages. Add `noindex` to spectator pages. These controls reduce accidental retention/discovery, but cannot prevent spectators taking screenshots or copying locations.
- Use a referrer policy that sends only the origin to third-party map assets/tiles, not the event token. Keep required OpenStreetMap attribution visible and follow its tile usage policy. No offline or bulk tile downloading.
- Event closure, withdrawal, and observed membership removal clear application-held coordinates. Browser views discard old positions on their next successful refresh. Revocation cannot erase copies already obtained by spectators.
- Clearing application coordinates does not delete the runner's Telegram messages or change Telegram's own retention. Explain this distinction in setup and runner help.
- The application keeps no GPS history. Any operator-created database backups can retain old coordinates until those backups expire; deployment instructions must disclose this and must not promise deletion from backups.

## 8. Failure behavior

- Telegram API unavailable: show a clear retry message when possible; do not grant consent eligibility without membership verification or mark rejected updates as fresh.
- Transient webhook processing failure: return a retryable error and keep writes idempotent. Return success for valid but irrelevant or already-applied updates, without leaking internal errors.
- Invalid GPX: retain the existing route and display a form error; never partially overwrite a working route.
- Network or tile failure: show an explicit error while keeping the roster usable. Display the last successful snapshot as old, not live.
- Event closed, token invalid, or link rotated: no runner-coordinate payload is returned, including from JSON URLs.
- Bot removed or demoted: setup diagnostics flag the problem, and ingestion rechecks bot administrator status alongside runner membership and fails closed. The dashboard must not imply reliable group tracking.
- GPS coordinates are runner-supplied. The MVP does not prove physical presence, prevent GPS spoofing, calculate official rankings, or guarantee emergency monitoring.

## 9. Deployment and setup

Deliver an example environment file without credentials, database migrations, local setup instructions, and a production checklist. Use a Python virtual environment and SQLite on a persistent volume, not an ephemeral or shared network filesystem. Use a production WSGI server behind HTTPS and a static-file server; Django's development server is local-only.

Production setup requires an organizer-provided BotFather token, bot username, group administrator access, staff account, and a publicly reachable HTTPS base URL. Register the webhook only against the explicitly configured base URL. Do not discard Telegram pending updates by default.

Local automated tests do not require Telegram credentials or a public tunnel. End-to-end device checks do. No external hosting account, purchased map service, or production bot configuration is created automatically.

SQLite and standard OpenStreetMap tiles are appropriate starting points, not an unlimited-capacity promise. OpenStreetMap's standard tile service has no SLA; a production event with significant spectator traffic may need a suitable tile provider. Measure application load before relying on it for a real event.

## 10. Verification and acceptance criteria

Use Django's built-in test runner and a small GPX fixture. Mock Telegram HTTP calls, not consent or tracking rules.

Required automated checks:

1. Staff authentication/CSRF protects event changes and staff JSON; public tokens never authorize mutations.
2. Group membership and explicit event consent are required; event A's runner cannot leak into event B.
3. A private live message creates a marker; an edit to that message updates the same participant without storing history.
4. Duplicate, out-of-order, old-session, forwarded, static-location, and pre-consent input cannot corrupt or reactivate tracking. Old consent callbacks cannot undo withdrawal.
5. `/stop`, membership removal, event closure, and expired sharing suppress coordinates; stopped tracking cannot resume from delayed edits.
6. Stale state is deterministic using controlled times, including finite and indefinite live periods and rejected future timestamps.
7. Spectator JSON exposes only approved fields; old tokens stop working after rotation, and closed events expose no coordinates.
8. Valid GPX track/route segments render correctly; malformed, oversized, entity-bearing, and invalid-coordinate files leave existing data unchanged.
9. Invalid webhook secrets and malformed payloads cannot change state. Transient failures and retries do not produce duplicate participation or regress a position.

Browser checks: staff setup and map interactions, mobile spectator layout, keyboard access, runner selection, polling failure/recovery, status/marker removal after `/stop` or closure, and no controls on spectator pages.

Before real-event use, perform a two-phone Telegram check: new-member welcome, private start/consent, live-location updates while moving and with a locked screen, stop/restart, group removal, and spectator viewing. Report device-dependent behavior rather than claiming continuous background delivery. Run a synthetic smoke check with 99 participants; this validates basic operation, not an unlimited spectator load or an availability SLA.

Success means an opted-in runner's latest private Telegram position appears on the correct event route for staff and link holders, while unconsented, withdrawn, expired, or closed-event positions do not leak.

## 11. Explicit non-goals

No automatic or covert GPS access; native mobile app; guaranteed background update interval; location history/replay; offline maps; GPX exports of runners; official race timing/rankings/checkpoint results; SOS dispatch; wearable integration; payments; spectator accounts; multi-tenant permissions; or multi-server deployment.

## 12. Official references checked

- Telegram bot introduction, including the private-conversation limitation: https://core.telegram.org/bots
- Telegram deep links and privacy mode: https://core.telegram.org/bots/features#deep-linking
- Incoming updates: https://core.telegram.org/bots/api/types/update.md
- Message identity, location, and membership fields: https://core.telegram.org/bots/api/types/message.md
- Location/live-period fields: https://core.telegram.org/bots/api/types/location.md
- Current-location keyboard behavior: https://core.telegram.org/bots/api/types/keyboardbutton.md
- Membership-check administrator requirement: https://core.telegram.org/bots/api/methods/getchatmember.md
- Webhook delivery, allowed updates, and secret header: https://core.telegram.org/bots/api/methods/setwebhook.md
- Django Python compatibility and SQLite support: https://docs.djangoproject.com/en/5.2/faq/install/
- Django authentication and permissions: https://docs.djangoproject.com/en/5.2/topics/auth/default/
- Leaflet quick start and attribution: https://leafletjs.com/examples/quick-start/
- OpenStreetMap tile policy: https://operations.osmfoundation.org/policies/tiles/

Official documentation establishes API fields and configuration requirements, not continuous delivery guarantees on a particular phone. Real Telegram live-location stop behavior and background delivery remain device checks for implementation verification.
