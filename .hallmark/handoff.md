# Runline redesign

## Outcome

Full mobile-first app redesign. Cobalt's cool material is adapted to the user's
explicit slate-minimalist direction: a single sans family, graphite controls,
readable labels, quieter surfaces, and no decorative imagery.

- The event name stays visible above the map on phones.
- The runner roster leads the desktop sidebar; event controls remain available below it.
- Future draft events appear in a native swipeable scroll-snap carousel, ordered by schedule.
- Anime.js 4.5.0 replaces Motion, with a pinned CDN URL and verified SHA-384 integrity.
- Tabs remain usable without Anime.js, respect reduced motion, and restore their URL state.
- Event management remains visible if the UI script fails.
- Offline map indicators do not label cached data as live.

## Verification

- 97 Django tests pass, including the new multiple-upcoming-events regression.
- Six dependency-free JavaScript tests pass: URL restoration, invalid tabs, library failure,
  reduced motion, animation state changes, and keyboard navigation.
- 25 browser responsive checks pass across 320, 390, 900, 1440, and 1920px widths.
- Five axe WCAG 2 A/AA audits report zero violations. Map contrast checks marked incomplete
  by axe were supplemented with computed token contrast and screenshot inspection.
- Browser interactions cover carousel scrolling, tabs, runner search/selection, keyboard
  navigation, URL restoration, reduced motion, missing Anime.js, failed UI scripts, and offline labels.
- Seven rendered Django pages pass Hallmark's static checks: zero Floor failures,
  zero open Reflex findings, and two F4 false positives dismissed after inspection.
- Hallmark's Puppeteer render tier could not run. Captures and interaction checks use the
  installed agent-browser instead. No Puppeteer dependency was installed.
- Fresh-context finish review: five findings, all resolved and checked in recaptures.
- `git diff --check` passes.

Raw Django templates do not resolve their inherited static CSS in Hallmark's source scanner.
The final copy/layout scan therefore uses actual server-rendered HTML with stylesheet URLs
mapped back to the same local source files. The initial raw-source audit's 40 failures and
64 Reflex findings include those import-related false positives and are not directly comparable.

## Preserved

Existing route names, forms, field names, CSRF tokens, map data attributes, snapshot APIs,
rename actions, permission boundaries, safety/privacy text, and destructive-action confirmations
remain intact. No database schema, Telegram ingestion, or production data was changed.

The only backend change makes the existing upcoming-event selection return all matching
future drafts instead of only the first one. No new endpoint was introduced.

## Copy changes

- `RUNLINE` becomes `Runline` in the wordmark.
- `Organizer access` plus `Sign in` becomes the heading `Organizer sign in`.
- The event-list `Race control` eyebrow is removed; the literal `Events` heading remains.
- `Next scheduled event` becomes a section heading, or `Upcoming events` when multiple events exist.
- Upcoming dates now use explicit `Starts` and `Ends` labels instead of an arrow separator.
- Arrow suffixes are removed from `Open event setup` and `Open control room`.
- `No events yet` becomes the empty-state heading; `Create the first event.` joins the existing body.
- Event-form eyebrows become a short explanation of the existing fields; `Telegram group` is a heading.
- `Race control` and `Spectator view` move below the persistent event title.
- The initial last-refresh dash becomes `Not yet refreshed`.
- Popup status, fix time, and accuracy become separate lines instead of middle-dot strings.
- An offline map chip reads `Offline` rather than presenting a live count.

All safety, consent, privacy, validation, and irreversible-action wording remains unchanged.
Counts, schedules, and runner names come from the existing application data, never invented claims.
Screenshot fixtures are explicitly synthetic and are never added to the production database.

## Files and material

Existing templates, app styles, UI/map presentation, the upcoming-events query, README, and tests
are edited. Shared `tokens.css`, `base.css`, `design.md`, and `scripts/ui.test.cjs` are added.
No product file, component tree, or route is deleted.

There are no photo slots or replacement images to supply. The actual course map is the visual.
Optional future material: an official Runline SVG wordmark could replace the text logo.

Comparison: `.hallmark/compare.html`. Screenshots: `.hallmark/before/` and `.hallmark/shots/`.
Detailed browser results: `.hallmark/verification.json`.

## Remaining limits

- Real-phone touch gestures and live Telegram delivery were not tested in this redesign.
- Google Fonts falls back to the system font if unavailable. Core workflows do not depend on Anime.js.
- The existing development server uses `scripts/devserver.py` with `--noreload`; restart it to
  load the updated upcoming-events query. That pre-existing server was not stopped or reconfigured.
- A failed snapshot load keeps the existing last-known data and warning behavior; no new
  retry, skeleton, undo, or server-loading subsystem was introduced.

Final Picks: full app redesign; slate-adapted Cobalt; existing account bar and mobile tabs;
actual map and roster signature; native upcoming carousel; Floor 0 FAIL; Reflex 0 open;
F4 false positives dismissed; finish review 5/5 resolved.
