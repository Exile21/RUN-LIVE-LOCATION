# Design: Runline

Runline is a mobile-first tool for managing small running events and following
runner positions. Keep the real map and runner roster central, not decorative imagery.

## System
- Genre: modern-minimal.
- Scope: existing app screens; keep routes, forms, permissions, and backend behavior.
- Theme: slate minimalist, explicitly requested; derived from Cobalt's cool surfaces.
- Palette: light slate canvas, near-white surfaces, graphite text and primary actions.
- Accent: slate selection; green, amber, and red only communicate labeled states.
- Typography: IBM Plex Sans at 400, 500, and 600 with a system fallback.
- Scale: fixed app sizes, 16px body, 14px supporting labels, tabular figures.
- Geometry: 8px controls, 12px independent surfaces, rules rather than nested cards.

## Tokens
`tracking/static/tracking/tokens.css` is the source of truth. Existing short token
names remain aliases so map and template styles share the same semantic palette.
`tracking/static/tracking/base.css` supplies accessibility and browser defaults.

## Screens
- Header: compact wordmark and existing account destinations, with 44px minimum targets.
  Phones add a context eyebrow and an account avatar menu; the bottom app nav is fixed.
- Tracking: map first on phones, Map/Runners/Info tabs with safe-area padding.
- Desktop tracking: existing left control panel and right map, centered and capped.
- Events: search and status-filter chips above one native scroll-snap carousel.
  Each tall card uses the real GPX route outline as its cover, with status, start-date
  badge, and an overlay caption. Group, segment count, and creation date remain
  available in native event details.
- Runner account: signed-in runners see current and past events, elapsed time,
  personal laps, and a Telegram connect link. Past events can open a private
  track replay with the event route and a time slider. The event Info panel can
  post the sign-up page to the Telegram group.
- Forms: visible labels, native inputs, one primary action, inline validation.
  New events can repeat daily, weekly, every 2 weeks, or monthly as a bounded
  draft series. Closed events can create a fresh draft with a new date.
- Preserve all privacy and safety wording and irreversible-action confirmations.
- Content remains available when UI motion or JavaScript fails; maps require JavaScript.

## Controls and motion
- Touch targets: 48px on phones, at least 44px elsewhere.
- Primary: graphite fill, pale text; secondary: quiet surface or text.
- Focus: instant visible outline; selection never relies on color alone.
- Anime.js 4.5.0 replaces Motion, with no frontend build step.
- Motion only explains tab changes and selection, using transform and opacity.
- No entrance choreography, looping pulses, or animation under reduced motion.
- Optional animation-library failure must not break tabs or core workflows.

## Verification
- Django suite: `python manage.py test` with the environment described in README.md.
- Dependency-free JavaScript checks: `node --test scripts/ui.test.cjs`.
- Responsive captures and verification details live in `.hallmark/`.

## Exports
Shared CSS tokens and Django templates; no framework migration or new asset service.
