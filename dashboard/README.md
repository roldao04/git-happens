# Alfred dashboard

The jury dashboard. It plays back one recorded run of a simulated home: the devices, what they do, and each habit Alfred learns, day by day. Nothing here talks to the kit or the MCP; it only reads `recording.json`.

Spec: [`docs/superpowers/specs/2026-11-17-dashboard-design.md`](../docs/superpowers/specs/2026-11-17-dashboard-design.md).

## Run

```
npm install
npm run dev                       # http://localhost:5173
npm test                          # unit and component tests
npm run test:coverage
npm run typecheck
```

For the pitch, use the production build (no dev server, no hot reload):

```
npm run build && npm run preview  # http://localhost:4173
```

`dist/` is static and uses relative paths, so it also works from any static file server.

## Controls

- **Space** plays or pauses. **← / →** step one simulated day.
- **1× / 2× / 4×**: one, two or four simulated days every 10 seconds.
- **⏭** jumps to the end of the run, where each card shows its final days-seen count.
- The slider jumps to a day. Its ticks mark the days a sensor was learned.
- Click a card to open that sensor. **Back** returns to the stage at the same point in time.

## Show a different run

Replace `public/recording.json`, or `dist/recording.json` in a build, with another file in the same shape. The shape is described in the spec under "The recording", and `src/data/recording.ts` checks it on load. If the file is missing or malformed, the page says which field is wrong.

The current file is a 21-day simulated run of the `rosa` household, written by `source/recording.py`. To make a new one, from the repo root:

```
uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace --recording dashboard/public/recording.json
```

Camera motion is left out. `src/data/mock.test.ts` checks this file (21 days, ten sensors, five strong enough for a card), so update it when the run changes.

## Design system

`public/ds/` is a vendored copy of the Alfred design system's tokens, CSS and fonts. `public/ds/SOURCE.md` says where it came from. Dark theme only.
