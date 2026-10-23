# Telegram Runner Tracking Implementation Plan

> **Status:** Implemented. Automated suite and synthetic browser verification passed. Real Telegram device and production webhook checks remain blocked on organizer credentials, phones, and a reachable HTTPS deployment.
>
> **Revision 2026-10-23:** Runner-only track review with 30-day retention was added. Where this plan says no GPS history or that closure clears all coordinates, the revised design spec and README take precedence.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track consenting runners from private Telegram Live Location messages on protected organizer maps and link-only spectator maps.

**Architecture:** One Django application owns event setup, consent/session state, Telegram webhook processing, and read-only snapshots. SQLite stores only current positions; Leaflet and vanilla JavaScript render server-generated pages and poll snapshots. External Telegram requests happen outside short database transactions; consent, closure, and withdrawal are rechecked inside each write transaction.

**Tech Stack:** Python 3.14, a current security-patched Django 5.2 LTS release at least 5.2.8, SQLite, defusedxml, Leaflet 1.9.4, Gunicorn, and an HTTPS/static-file reverse proxy. Django's test runner and standard-library mocks; no frontend build system.

**Spec:** `docs/superpowers/specs/2026-10-21-telegram-runner-tracking-design.md`

## Global Constraints

- "Runners send Live Location privately to the bot, not to the event group."
- "Spectators need only the event link, with no account or access code."
- "Runner consent must explicitly cover this spectator visibility."
- "Maps poll every five seconds. This does not guarantee GPS updates every five seconds."
- "Keep only each runner's latest position, not a history or replay."
- "Closing an event clears its stored runner coordinates."
- "A runner can track in only one open event at a time."
- "GPX files are limited to 2 MiB and 20,000 points."
- "Limit webhook body size to 256 KiB."
- "Live: active sharing has not expired and its latest position is at most 120 seconds old."
- Public links have at least 128 bits of entropy; use independent `secrets.token_urlsafe(24)` tokens for enrollment and spectators.
- Fewer than 100 runners; one organization and one application instance. No queues, Redis, WebSockets, GPS history, rankings, or native app.
- No real credentials, public webhook registration, external accounts, or hosting changes during implementation without user-provided configuration.
- The folder is not a Git repository. Do not initialize Git or make worktrees; record task checkpoints instead. Commit only if a repository is subsequently supplied or initialization is authorized.
- The named `superpowers` execution skills are not currently in the supplied skill catalog. Do not claim to have loaded them or install plugins implicitly; follow the selected execution method using available capabilities and disclose unavailable review facilities.

## Review Focus

1. Identical runner names must remain separate identities and markers — pin with `SnapshotTests.test_duplicate_names_keep_distinct_public_ids` in Task 5.
2. Withdrawal or event closure while Telegram membership verification is in flight must win over a location write — pin with `LocationTests.test_withdrawal_during_membership_check_wins` in Task 5.
3. Telegram HTTP 429, network failures, and token-bearing transport exceptions must not expose credentials or mark positions fresh — pin with `TelegramClientTests.test_transport_failures_are_sanitized` in Task 3 and the membership-failure assertion in Task 5.
4. UTF-16 XML declarations and disconnected GPX segments must not bypass XML safety or create false connecting lines — pin with `GpxTests.test_utf16_dtd_is_rejected` and `test_segments_remain_separate` in Task 2.
5. Token revocation or staff-session expiry during polling must clear visible positions, whereas a temporary network outage must label retained positions old — pin with the browser scenarios in Task 7.

---

## File map and dependency order

Create a conventional small Django project. Do not add generic service/repository layers.

| Files | Responsibility |
| --- | --- |
| `manage.py`, `runlive/__init__.py`, `runlive/settings.py`, `runlive/urls.py`, `runlive/wsgi.py` | Configuration, entry points, authentication/admin URL wiring |
| `requirements.txt`, `.gitignore`, `.env.example` | Pinned dependencies, generated/private file exclusions, documented environment names |
| `tracking/__init__.py`, `tracking/apps.py`, `tracking/models.py`, `tracking/admin.py`, `tracking/migrations/__init__.py`, `tracking/migrations/0001_initial.py` | Models, constraints, read-only model admin, migrations |
| `tracking/state.py` | Event lifecycle, consent/withdrawal/session transitions, freshness rules |
| `tracking/gpx.py`, `tracking/forms.py` | Bounded safe route parsing and staff form validation |
| `tracking/telegram.py`, `tracking/bot.py` | Telegram HTTP transport and incoming-update orchestration |
| `tracking/snapshots.py`, `tracking/views.py`, `tracking/urls.py` | Explicit JSON allowlists, authorized HTTP endpoints, page rendering |
| `tracking/management/__init__.py`, `tracking/management/commands/__init__.py`, `tracking/management/commands/register_webhook.py`, `tracking/management/commands/check_telegram.py` | Explicit operational setup and integration diagnostics |
| `tracking/templates/tracking/base.html`, `event_list.html`, `event_form.html`, `event_map.html` in the same directory | Organizer pages and the shared map layout with distinct staff/public controls |
| `tracking/templates/registration/login.html` | Django session login |
| `tracking/static/tracking/app.css`, `tracking/static/tracking/map.js` | Accessible map-first interface, marker reconciliation, polling/error behavior |
| `tracking/tests/__init__.py`, `test_state.py`, `test_gpx.py`, `test_telegram.py`, `test_bot.py`, `test_views.py`, `test_operations.py` in the same directory | Focused built-in Django tests; shared factories live in `tracking/tests/helpers.py` only if reused |
| `tracking/tests/fixtures/two_segments.gpx` | Small valid route fixture with visibly disconnected segments |
| `README.md`, `deploy/Caddyfile` | Local setup, production proxy/static configuration, limitations and acceptance checklist |

Tasks execute in order. Later tasks extend the named files and interfaces; they do not create parallel implementations.

## Task 1: Persistent event lifecycle and privacy state

**Files:** Create project/configuration files, dependency/environment files, `tracking/models.py`, `tracking/state.py`, `tracking/admin.py`, app/migration initializers, `0001_initial.py`, and `tracking/tests/test_state.py` from the file map. Create the initial URL configuration without unfinished public endpoints.

**Interfaces produced:**
- `Event`: `name` (160 characters), `status` (`draft/open/closed`), nullable `group_id` (BigInteger), `route_segments` (JSON list), `enrollment_token`, `spectator_token`, and creation/open/close timestamps.
- `Participant`: event FK, UUID `public_id`, `telegram_user_id` and `private_chat_id` (BigInteger), `display_name` (160 characters), `pending_nonce`, `pending_at`, `consented_at`, `consent_version`, `withdrawn` (default true), `control_at`, `control_update_id`, `consent_update_id`, `location_update_id`, `location_message_id`, `location_message_at`, `session_ended`, nullable `live_expires_at`, nullable latitude/longitude/accuracy, `position_at`, `received_at`, and creation time. Initialize update cursors to `-1` and absent timestamps/positions to null. A null expiry means indefinite only when a live session exists.
- `open_event(event_id: int, *, now: datetime) -> Event`; caller must first verify the configured bot/group. `close_event(event_id: int, *, now: datetime) -> Event`; `rotate_spectator_token(event_id: int) -> str`.
- `tracking_status(participant: Participant, *, now: datetime) -> str`, returning `waiting/live/stale/expired/stopped`.

- [ ] **Step 1:** Resolve supported package versions from official release metadata, create the virtual environment, and pin Django, defusedxml, Gunicorn and their required transitive packages. Scaffold only the listed Django files. Require a configured secret key; tests use a clearly test-only key. Default production settings fail closed, and local setup explicitly enables debug. Add `.venv/`, `.env`, SQLite files/journals, static collection output and Python caches to `.gitignore`.
- [ ] **Step 2:** Write `LifecycleTests` asserting unique event/user participation, a conditional unique constraint for non-withdrawn consented users, and one open event per group. Assert `close_event` clears all coordinates/accuracy, marks participation withdrawn, invalidates pending consent, and never reopens; repeated closure is harmless. `FreshnessTests` covers the exact boundary and expiry overriding freshness. Set up an aware UTC `now` and participant in each test, then pin these assertions:

```python
participant.position_at = now - timedelta(seconds=120)
self.assertEqual(tracking_status(participant, now=now), "live")
participant.position_at = now - timedelta(seconds=121)
self.assertEqual(tracking_status(participant, now=now), "stale")
```

- [ ] **Step 3:** Run `python manage.py test tracking.tests.test_state -v 2`; expect missing models/state behavior before implementation.
- [ ] **Step 4:** Implement models, migrations, and the specified functions using short atomic writes. Recheck lifecycle in each write. Use SQLite `OPTIONS={"timeout": 20, "transaction_mode": "IMMEDIATE"}` and no `ATOMIC_REQUESTS`; do not rely on `select_for_update()` for SQLite locking. Make Event/Participant read-only in Django admin to avoid lifecycle bypass; preserve normal user administration. Define `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DATABASE_PATH`, `PUBLIC_BASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and `TELEGRAM_WEBHOOK_SECRET` in the example environment. Load environment through the shell, not a new dotenv dependency.
- [ ] **Step 5:** With local/test environment loaded, run `python manage.py migrate`, `python manage.py check`, and `python manage.py test tracking.tests.test_state -v 2`; expect success. Run `python manage.py makemigrations --check --dry-run`; expect no missing migrations.
- [ ] **Step 6:** Record checkpoint `feat: add event lifecycle and current-position state`; no Git commit while the folder has no repository.

## Task 2: Bounded GPX import and event forms

**Files:** Create `tracking/gpx.py`, `tracking/forms.py`, `tracking/tests/test_gpx.py`, and `tracking/tests/fixtures/two_segments.gpx`.

**Interfaces:** Consumes `Event`. Produces `parse_gpx(data: bytes) -> list[list[list[float]]]`, with each point `[latitude, longitude]`, and `EventForm(data=None, files=None, *, instance=None)` with fields `name`, `group_id`, and optional-on-edit `route_file`. Parsing raises Django `ValidationError`; cleaned form data carries validated `route_segments` without saving the original upload.

- [ ] **Step 1:** Write `GpxTests` asserting two separate two-point segments remain separate, GPX track and route files work, at least one segment contains two points, and non-numeric/non-finite/out-of-range coordinates fail. Assert rejection at 2 MiB + 1 byte and 20,001 points, malformed XML, and UTF-8/UTF-16 DTD/entity declarations. Assert failed form validation leaves an existing route unchanged. The fixture contains segments beginning at `[1.0, 2.0]` and `[3.0, 4.0]`:

```python
segments = parse_gpx(fixture_path.read_bytes())
self.assertEqual([len(segment) for segment in segments], [2, 2])
self.assertEqual([segment[0] for segment in segments], [[1.0, 2.0], [3.0, 4.0]])
```

- [ ] **Step 2:** Run `python manage.py test tracking.tests.test_gpx -v 2`; expect missing parser/form failures.
- [ ] **Step 3:** Implement `parse_gpx` with `defusedxml.ElementTree.fromstring(..., forbid_dtd=True, forbid_entities=True, forbid_external=True)`. Accept GPX 1.0/1.1 track segments and route points; discard unusable singleton segments, reject invalid point values rather than silently changing a route, and enforce byte/point limits. Read uploads at most `2 * 1024 * 1024 + 1` bytes. `EventForm` requires a route on creation and preserves the existing route when no replacement is supplied. Do not save to a media directory.
- [ ] **Step 4:** Run `python manage.py test tracking.tests.test_gpx -v 2`; expect all cases pass, including preservation of disconnected segments and the previous route on failure.
- [ ] **Step 5:** Record checkpoint `feat: import validated GPX event routes`.

## Task 3: Telegram transport with safe failure semantics

**Files:** Create `tracking/telegram.py` and `tracking/tests/test_telegram.py`.

**Interfaces:** `telegram_call(method: str, payload: dict) -> dict | list | bool` returns Telegram's `result`; `TelegramError` is sanitized. `verify_group(group_id: int) -> None` verifies group/supergroup and the bot's administrator status. `verify_member(group_id: int, user_id: int) -> bool` rechecks bot administration, then accepts member/administrator/creator or restricted with `is_member=True`. Methods use configured credentials, not arguments from an HTTP viewer. Missing configuration raises a safe configuration error.

- [ ] **Step 1:** Write `TelegramClientTests` using `unittest.mock` at the `urllib.request.urlopen` boundary: assert JSON POST, a five-second timeout, correct `result` extraction, membership status handling, and false/non-JSON/error responses. In `test_transport_failures_are_sanitized`, feed HTTP 429 and exceptions whose text includes the test token URL; assert neither token nor raw response escapes. Verify demoted bots and network failures cannot grant membership. With `urlopen` mocked to raise a token-bearing exception:

```python
with self.assertRaises(TelegramError) as raised:
    telegram_call("getMe", {})
self.assertNotIn("test-token", str(raised.exception))
self.assertNotIn("https://api.telegram.org", str(raised.exception))
```

- [ ] **Step 2:** Run `python manage.py test tracking.tests.test_telegram -v 2`; expect missing transport functions.
- [ ] **Step 3:** Implement the functions using standard-library `urllib.request` and `json`. Bound the read of API responses to 1 MiB. Raise a generic `TelegramError` without chained URL-bearing exception text; never log request bodies, token-bearing URLs, or positions. Do not sleep/retry inside database transactions or cache successful membership indefinitely. Retryable failures propagate to the webhook layer; no paid/broadcast options.
- [ ] **Step 4:** Run `python manage.py test tracking.tests.test_telegram -v 2`; expect all transport, demotion and sanitization checks pass without real Telegram calls.
- [ ] **Step 5:** Record checkpoint `feat: add safe Telegram API transport`.

## Task 4: Group onboarding, consent, and withdrawal webhook

**Files:** Create `tracking/bot.py`, `tracking/views.py`, `tracking/urls.py`, `tracking/tests/test_bot.py`; extend `tracking/state.py` and `runlive/urls.py`.

**Interfaces:** `handle_update(update: dict, *, now: datetime) -> None` orchestrates Telegram updates. State functions: `begin_onboarding(event_id: int, user_id: int, chat_id: int, display_name: str, update_id: int, *, sent_at: datetime, now: datetime) -> Participant`; `grant_consent(public_id: UUID, user_id: int, nonce: str, update_id: int, *, now: datetime) -> bool`; `withdraw_runner(user_id: int, update_id: int, *, sent_at: datetime, now: datetime, event_id: int | None = None) -> None`. All state functions recheck event/participant state atomically. `telegram_webhook(request) -> HttpResponse` is mounted at `/telegram/webhook/`.

- [ ] **Step 1:** Write `OnboardingTests` and `WebhookTests`: new-member welcome contains the correct `https://t.me/<username>?start=e_<enrollment_token>` link; `/groupid` changes no event state; nonmembers cannot consent; sender IDs cannot be swapped; duplicate callbacks do not reset consent; `/stop` clears positions and pending nonce; an old callback cannot undo `/stop`; an old private lifecycle update from event A cannot affect newer consent in event B. Add a fresh `/start` after eight idle days with a lower randomized update ID: it must work. Assert wrong/missing secret gives 403, non-POST gives 405, oversized body 413, invalid JSON/structure 400, irrelevant valid updates 200, and transient Telegram failure 503. After a valid consent-and-stop sequence, replay the old consent callback and pin:

```python
handle_update(old_consent_callback, now=now)
participant.refresh_from_db()
self.assertTrue(participant.withdrawn)
self.assertIsNone(participant.latitude)
```

- [ ] **Step 2:** Run `python manage.py test tracking.tests.test_bot -v 2`; expect missing behavior.
- [ ] **Step 3:** Implement private `/start`, group welcome/ID handling, inline consent callbacks, `/stop`, and removed-member handling. Use a 12-byte random nonce and callback payload `agree:<participant_uuid_hex>:<nonce>` below Telegram's 64-byte limit. Reverify membership on consent; bind callback sender and private chat to its pending challenge. Invalidate challenges on withdrawal/closure. Order private lifecycle messages per user by original Telegram timestamp at second precision, using update ID only to break ties; callback nonces bind their lifecycle epoch. Do not reject a new timestamp merely because Telegram randomized update IDs after a week without updates. Anonymous/sender-chat, bot, non-private onboarding and unrelated inputs cannot enroll. Escape names in any Telegram HTML or send plain text. Include the spec's consent meaning and manual Live Location instructions; answer callbacks with `answerCallbackQuery`.
- [ ] **Step 4:** Implement the webhook secret check with `secrets.compare_digest` before JSON parsing. Validate integer update/user/chat IDs excluding booleans, required containers, content length and actual bounded body length. Exempt only this POST endpoint from CSRF. Return generic errors and make state writes idempotent; send outbound messages outside transactions. Do not claim exactly-once message delivery.
- [ ] **Step 5:** Run `python manage.py test tracking.tests.test_bot tracking.tests.test_state -v 2`; expect consent, revocation and event-switch ordering checks pass without credentials.
- [ ] **Step 6:** Record checkpoint `feat: add consent-based Telegram event onboarding`.

## Task 5: Live message ingestion and privacy-safe snapshots

**Files:** Extend `tracking/bot.py`, `tracking/state.py`, `tracking/tests/test_bot.py`, `tracking/tests/test_state.py`; create `tracking/snapshots.py`.

**Interfaces:** `apply_location(user_id: int, message: dict, update_id: int, *, edited: bool, now: datetime) -> bool` returns whether it changed current position/session state. `event_snapshot(event: Event, *, staff: bool, now: datetime) -> dict` returns the schema below. `handle_update` calls membership verification before location mutation; `apply_location` rechecks consent, withdrawal and event lifecycle inside the write transaction.

Snapshot schema: `event={name,status}`, `route_segments`, `server_time` (ISO UTC), `runners`, and `summary={participants,live,stale}`. Each runner allows only `id` (UUID string), `number` (display label), `name`, `status`, `latitude`, `longitude`, `accuracy`, and `position_at` (ISO UTC/null). Never include raw model dictionaries or Telegram/consent/session fields. Coordinates and accuracy are null unless status is live/stale. Public snapshots omit withdrawn runners; staff may see stopped rows without positions. Closed events return no runners. IDs, not names or marker numbers, key frontend identity.

- [ ] **Step 1:** Add `LocationTests`: new private live message then matching edit yields one participant and only its latest coordinates; duplicates, earlier position times, lower update IDs at equal times, old message IDs, forwarded messages, static pins, and messages sent before consent do not overwrite it. Reject NaN/infinity, booleans, out-of-range positions/accuracy, and timestamps over 30 seconds in the future. Clamp an accepted small positive clock skew to receipt time for freshness. Test finite expiry and `live_period=0x7FFFFFFF`; edits indicating sharing ended cannot resume that message. Verify `/stop` followed by delayed edits stays hidden until new consent and a newly sent message.
- [ ] **Step 2:** Add `test_withdrawal_during_membership_check_wins`, using the mocked Telegram transport's side effect to withdraw or close the event before the write resumes; assert coordinates remain null. On verification error, assert the previous coordinates and `position_at`/`received_at` do not change; a verified membership loss instead withdraws and clears the runner. Add `SnapshotTests.test_duplicate_names_keep_distinct_public_ids`, field-allowlist, event-isolation, waiting/expired/no-coordinate and controlled 120/121-second cases. Add a 99-participant snapshot smoke test, bounded to a fixed small query count rather than N+1 queries. For duplicate names, pin:

```python
rows = event_snapshot(event, staff=False, now=now)["runners"]
self.assertEqual([row["name"] for row in rows], ["Alex", "Alex"])
self.assertEqual(len({row["id"] for row in rows}), 2)
self.assertNotIn("telegram_user_id", rows[0])
```

- [ ] **Step 3:** Run `python manage.py test tracking.tests.test_bot tracking.tests.test_state -v 2`; expect the new ingestion/serialization cases fail.
- [ ] **Step 4:** Implement the interfaces, storing message origin time separately from edit time and receipt time. Finite expiry derives from original message time plus current live period; null expiry represents Telegram's indefinite sentinel only. Match edits by sender/private chat/active message and reject ended sessions. Order positions by Telegram position time and then per-participant update ID; newer message IDs replace prior sessions, older ones never do. For a new session, require original message time at or after consent rounded to seconds; only when those times are equal, also require its update ID to exceed the consent update ID. Matching-session edits use position ordering, not a permanent consent-ID barrier, so idle-week update-ID randomization cannot block a later position. Reverify session/event state after external membership checks; verified membership loss withdraws and clears using the current verification time. Preserve session message IDs on withdrawal so old deliveries cannot revive them. Use explicit snapshot construction and stable sorting; no GPS history or raw updates.
- [ ] **Step 5:** Run `python manage.py test tracking.tests.test_bot tracking.tests.test_state -v 2`; expect ordering, race, privacy, freshness and the 99-participant smoke check pass. Do not call this a spectator-load/SLA benchmark.
- [ ] **Step 6:** Record checkpoint `feat: track latest live positions with isolated public snapshots`.

## Task 6: Authorized event setup and link-only web access

**Files:** Extend `tracking/views.py`, `tracking/urls.py`, `runlive/urls.py`, `tracking/forms.py`; create `tracking/tests/test_views.py`, base/list/form/map/login templates from the file map.

**Interfaces:** HTML view functions `event_list`, `event_create`, `event_edit`, `event_detail`, `spectator_detail`; JSON views `staff_snapshot`, `spectator_snapshot`; POST actions `event_open`, `event_close`, `event_rotate_link`, `participant_rename`. Staff JSON returns 401/403 rather than a login HTML page. All views return Django responses and resolve events server-side, never from browser-provided Telegram identity.

Routes: `/` redirects to `/events/`; `/accounts/login/`, POST `/accounts/logout/`, `/admin/`; `/events/`, `/events/new/`, `/events/<int:pk>/`, `/events/<int:pk>/edit/`, `/events/<int:pk>/snapshot/`; POST `/events/<int:pk>/open/`, `close/`, `rotate-link/`, and `runners/<uuid:public_id>/name/`; GET `/e/<str:token>/` and `/e/<str:token>/snapshot/`.

- [ ] **Step 1:** Write `AccessTests` with anonymous, inactive, ordinary authenticated, and active staff users. Use `Client(enforce_csrf_checks=True)` for mutations. Assert every staff page/action/snapshot is guarded, public tokens cannot mutate, draft/unknown tokens yield 404, rotation invalidates old HTML and JSON links, and closed JSON has no runners. `EventFormTests` asserts GPX failures preserve existing data; opening verifies Telegram setup before state changes; a closed event cannot be reopened or edited. Assert no-store headers on snapshots/private pages and noindex on public pages. Include a display name containing `</script><img src=x onerror=alert(1)>` and assert escaped HTML/data handling. After capturing the old token and rotating it, pin:

```python
self.assertEqual(self.client.get(f"/e/{old_token}/").status_code, 404)
self.assertEqual(self.client.get(f"/e/{old_token}/snapshot/").status_code, 404)
self.assertEqual(self.client.get(f"/e/{new_token}/snapshot/").status_code, 200)
```

- [ ] **Step 2:** Run `python manage.py test tracking.tests.test_views -v 2`; expect missing web behavior.
- [ ] **Step 3:** Implement views/forms using active-staff guards, built-in `LoginView`/`LogoutView`, CSRF-protected POST actions, and the earlier state/transport/snapshot interfaces. Never expose draft events or list events publicly. Group ID must be a group/supergroup; reject channels/private chats via setup verification. Validate rename length, event ownership of the participant, and display a clear form error on network/setup failure. Include separate runner/spectator links, close confirmation, and explicit public-visibility/last-position-only copy. Render embedded JSON with Django `json_script` or fetch snapshots; never interpolate names into executable script or `innerHTML`. Staff-only controls are not the server authorization boundary.
- [ ] **Step 4:** Run `python manage.py test tracking.tests.test_views -v 2`; expect access, revocation, CSRF, form-preservation and injection cases pass. Run the entire suite to catch interface regressions.
- [ ] **Step 5:** Record checkpoint `feat: add protected event management and spectator links`.

## Task 7: Interactive map and resilient spectator dashboard

**Files:** Create `tracking/static/tracking/app.css`, `tracking/static/tracking/map.js`; finish the listed templates. Record browser acceptance results in the final verification report, not a new public debug endpoint.

**Interfaces:** `map.js` reads the snapshot URL and staff/public mode from escaped HTML data attributes; it consumes only Task 5's schema. Maintain `Map<runner UUID, Leaflet marker>`. Poll with completion-scheduled `setTimeout(..., 5000)` and no overlapping fetch. The page has labeled search, runner buttons, route-fit control, status summary, connection state and last-successful-refresh time.

- [ ] **Step 1:** Load applicable frontend design instructions and `agent-browser` before UI implementation/testing. With a temporary local SQLite database, use Django's existing shell/fixtures to seed one event and synthetic live/stale/waiting/expired runners, including duplicate and markup-bearing names. Do not add a persistent demo command, extra test dependency, or diagnostic endpoint. Start the normal dev server; use browser automation to establish missing map/polling behavior before implementing it. Keep this database separate from user/production data.
- [ ] **Step 2:** Build an original map-first layout using Leaflet 1.9.4 with pinned CDN URLs/integrity and visible OpenStreetMap attribution. Draw separate GPX polylines, reconcile markers/list rows by UUID, update via text nodes, support runner selection and case-insensitive search, show accuracy when available, and preserve map pan/zoom across polls. Show textual freshness and connection state; use readable contrast, semantic buttons, focus states, and responsive mobile panels. Do not add progress/ranking/distance claims.
- [ ] **Step 3:** Implement failure behavior: network/5xx retains the last snapshot with a prominent old/offline label and continues aging freshness using the last server-time anchor; successful recovery clears the error. Definitive public 404 or staff 401/403 clears markers and roster and stops polling. A closed-event snapshot clears positions and shows ended state. Label connectivity as the dashboard connection, never as proof Telegram/GPS is healthy; keep the Telegram delivery caveat and per-runner timestamps visible. Tile errors show a map warning while keeping the runner list usable. Fit only on first load or the explicit route control.
- [ ] **Step 4:** Verify using `agent-browser` at desktop and 390-pixel mobile width: route/markers/list agree; selecting one of two identically named runners selects only its UUID marker; keyboard controls work; no script executes from names; no overflow or hidden attribution; no staff controls on spectator page. Trigger `/stop`/closure via authenticated app state and confirm removal after polling. Revoke a link and expire a staff session during polling to confirm coordinates disappear. Interrupt and restore network to confirm old/recovered states, no overlapping requests, and no map recentering. Record console errors and screenshots as evidence; do not infer Telegram delivery from synthetic updates.
- [ ] **Step 5:** Run all Django tests after UI wiring and record checkpoint `feat: add live event maps and spectator dashboard`.

## Task 8: Operational setup and final evidence

**Files:** Create management package initializers, `register_webhook.py`, `check_telegram.py`, `tracking/tests/test_operations.py`, `README.md`, `deploy/Caddyfile`; finalize `.env.example` and production settings.

**Interfaces:** `python manage.py register_webhook` registers only configured `PUBLIC_BASE_URL + '/telegram/webhook/'`; HTTPS is mandatory. Pass `allowed_updates=['message','edited_message','callback_query','chat_member']`, the configured secret, `max_connections=1`, and `drop_pending_updates=False`. `python manage.py check_telegram` reports sanitized bot identity, webhook health and each open event's group/admin setup; it does not change event lifecycle or expose secret URLs.

- [ ] **Step 1:** Write `OperationsTests` mocking only Telegram HTTP. Assert registration rejects HTTP/absent base URLs, missing credentials/secrets and mismatched production hosts without making a request; valid registration includes all four update types and never drops pending updates. Assert diagnostics sanitize tokens, show demotion/setup failures and do not publish positions. With the configured webhook registration call intercepted, pin:

```python
self.assertEqual(payload["allowed_updates"], ["message", "edited_message", "callback_query", "chat_member"])
self.assertEqual(payload["max_connections"], 1)
self.assertIs(payload["drop_pending_updates"], False)
```

- [ ] **Step 2:** Run `python manage.py test tracking.tests.test_operations -v 2`; expect missing commands/configuration behavior.
- [ ] **Step 3:** Implement commands and a reverse-proxy/static configuration. Bind Gunicorn to localhost, serve only collected static files, and keep the database/environment outside the public static root. Enable HTTPS redirects, secure cookies and explicit hosts/origins with `DEBUG=False`; trust forwarded HTTPS only from the controlled reverse-proxy deployment. Use `strict-origin-when-cross-origin` so tile requests carry origin, not event tokens. Do not enable URL access logging that exposes capability links. Do not use Django's dev server in production.
- [ ] **Step 4:** Write README instructions for virtualenv/install, editing and exporting `.env`, migrations, `createsuperuser`, GPX upload, `/groupid`, bot/group administration, runner onboarding and sharing, public links/rotation, event closure, webhook registration, static collection, persistent-volume deployment and local test commands. Document Telegram/device uncertainty, spoofable GPS, no emergency/official timing guarantee, no OSM tile SLA, spectator capacity limits, and SQLite contention. Explain that `/stop` and closure clear application coordinates, not Telegram messages, spectator copies or old backups. Include the two-phone manual checklist and do not mark it passed without phones and credentials.
- [ ] **Step 5:** Run `python manage.py test -v 2`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python manage.py collectstatic --noinput` with local/test settings; expect success and no uncommitted migration work. Run `python manage.py check --deploy` under explicit production-like settings with dummy non-live secrets, `DEBUG=False`, secure cookies/redirects and explicit hosts/origins; report unresolved deployment warnings rather than suppressing checks. Re-run browser scenarios only if relevant UI/response behavior changed since Task 7. Never register a dummy webhook against Telegram.
- [ ] **Step 6:** Self-review the connected code for public field leaks, lifecycle bypasses, secret logging and timestamp/session ordering. Follow the user's chosen independent-review method if available. Fix findings and repeat only the evidence affected by each fix. Mark each claim below established, limited or blocked. Record checkpoint `docs: add deployment and tracking verification guide`.

## Verification budget and completion report

The selected executor owns establishing each claim. An independent reviewer, if selected/available, challenges the evidence rather than repeating all commands.

| Claim | Minimum evidence | Limit |
| --- | --- | --- |
| Consent, session ordering and withdrawal protect location visibility | State/bot tests with real ORM transactions and mocked Telegram transport | Cannot establish actual phone/Telegram delivery |
| Public links are read-only and event-isolated | CSRF-enforced request tests, explicit JSON allowlist assertions, token rotation tests | Cannot prevent recipients copying visible locations |
| GPX parsing is bounded and segments are faithful | Small GPX fixture, malformed/oversized/UTF-16 hostile-input tests | Does not certify uploaded race-course correctness |
| Dashboard reflects current/old/ended states accessibly | Desktop/mobile browser checks with controlled synthetic data | Device GPS and Internet conditions remain external |
| Single-server app is runnable and configured safely | Migrations/checks/full suite, static collection, production-like deployment check, 99-participant smoke test | Not a spectator-load benchmark, security audit or availability SLA |
| Private Telegram Live Location works on real phones | Two-phone manual checklist with organizer-provided credentials | Blocked until phones, credentials and a reachable HTTPS endpoint exist |

Do not install verification-only dependencies. A stronger real-event claim requires actual phone checks and a separate measured spectator-load rehearsal, not more mocked tests. Temporary browser data remains in a clearly separate ignored database and must not replace a user's database.

## Plan self-review and execution handoff

Coverage: spec sections 1–3 map to Task 1 and global constraints; organizer setup to Tasks 2/6/8; onboarding to Task 4; live sessions to Task 5; dashboards to Tasks 6/7; validation/privacy/failures to Tasks 2–8; deployment to Task 8; acceptance and non-goals to the verification table and every task's scope.

Review the plan before implementation. Recommended method: **Native**, because these eight tasks share model/state/snapshot interfaces and sequential implementation avoids cross-agent integration overhead. Native means this session implements each task, with one fresh independent reviewer at the end if the harness provides one and the user approves that approach. Subagent-driven execution provides a fresh implementer/reviewer per task at greater context and review cost. No implementation begins until the user reviews this plan and selects the method.
