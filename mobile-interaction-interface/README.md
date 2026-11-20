# Alfred on the phone

The phone app. It chats with Alfred by text or voice, and its "Right now" card shows what the home is doing and what Alfred has planned, with Skip. It also has device controls and earlier chats.

## Run
```
npm install
npm run demo        # the demo house, simulated in the app: no kit, no .env
npm run home        # your real home (the kit) and the real Alfred, look-only: nothing is sent
npm run home:live   # the same, and changes really happen (lamp and plug; never the alarm)
npm test && npm run typecheck && npm run build
```

| | House | Alfred's answers | Switching |
|---|---|---|---|
| `demo` | the demo house: three plugs, thermostat, two phones | scripted (understands English and Portuguese, answers in English) | simulated |
| `home` | the kit, through the loop (`python -m source --look-only --api`) | the LLM (needs `OPENROUTER_API_KEY`) | look-only: a change that isn't sent says so, and nothing moves |
| `home:live` | the same (`python -m source --api`) | the LLM | real |

- **The app says its mode** under the text box and in Settings.
- **The microphone is the same in every mode:** this laptop's voice model. Without it (setup said no), there's no microphone, and typing works.
- **`npm run setup`** runs by itself the first time:
  - It makes HTTPS certificates this laptop signs for itself, in `.certs/`.
  - It asks once whether to install the voice model (about 650 MB, runs on this computer). Change your answer with `npm run setup -- --voice` or `--no-voice`.
- **On the phone**, on the same Wi-Fi:
  1. Once: open `http://<laptop IP>:5175`, download the certificate, and install it as shown there (Android or iPhone).
  2. Then open `https://<laptop IP>:5174/`. It's the same address in every mode.
- **HTTPS is only needed for the microphone.** Phones only allow it on HTTPS or `localhost`.
- **Your real home** is read with Claude Code's sign-in to the kit (`python3 tools/connected_home_auth.py login`, once). Look-only still asks the LLM on every wake, as live does: the same cost, only nothing is sent.
- **Your real home learns by itself.** The loop runs the learners every night, so the plan, Skip and suggestions appear after a few days of watching.
- **`npm run dev`** is plain `http://localhost:5174` on the demo house, for working on the app. **`dev/responsive.html`** shows the app at 320 to 1440 px in every state and theme, and flags anything that sticks out sideways (the voice state needs `npm run demo`).

## How it fits
- **One interface, two implementations.** The app only uses `HomeApi` (`src/api/client.ts`). `MockHomeApi` keeps the demo house in memory with the guard's rules (`src/api/mock/guard.ts`, mirroring `source/guard.py`) and a scripted chat. `HttpHomeApi` calls the loop's API, described in [`docs/phone-api.md`](../docs/phone-api.md).
- **Voice runs only on this home's computer:** Parakeet TDT 0.6B v3 (int8, `source/voice.py`).
  - Recording stops by itself after about 1.2 s of quiet.
  - The model recognises Portuguese and English by itself. The Language setting (Auto by default) only chooses which language Alfred answers and speaks in.
- **Styles.** Light by default; dark in Settings. The design system is vendored in `public/ds/`, as in the dashboard.
- **Chats and settings** stay on the phone (`localStorage`).
