# SYNAPSE · team GitHappens

<img src="assets/githappens-logo.jpeg" alt="GitHappens logo" width="120">

**The semantic layer for the connected home:** it turns raw device data into meaning, learns routines, proposes rules, verifies what it does and remembers what worked.
Aveiro Tech City Hackathon 2026, Altice Labs Desafio #3 ("Connected Home, Reimagined").

## Docs
| Doc | What it's for | Read when |
|---|---|---|
| [`docs/synapse.md`](docs/synapse.md) | **The concept:** story, semantic layer, rules, verification, memories, demo, build plan | First |
| [`docs/mcp-capabilities.md`](docs/mcp-capabilities.md) | What the Altice MCP can see, change and can't do; device IDs | Before writing code |
| [`docs/altice-hackathon-brief.md`](docs/altice-hackathon-brief.md) | Challenge, schedule, judging criteria, rules | Before the pitch or report |
| [`deliverables/README.md`](deliverables/README.md) | What we hand in, limits and deadlines | Before each deadline |
| [`docs/reference/hardware-list.md`](docs/reference/hardware-list.md) | Kit hardware: models, radios, how each device reaches the MCP, **safety rules** | Before touching the physical kit |
| [`docs/reference/`](docs/reference/) | Organiser PDFs and strategy research | When you need the source |

## Key dates
- **Wed 18 Nov, afternoon:** mentor checkpoint (online, mandatory)
- **Wed 18 Nov, 19:00:** approach report due (max 2 pages)
- **Fri 20 Nov, 17:30:** executive summary (PDF, max 2 pages) + slides + repo link
- **Fri 20 Nov, 18:00:** pitches (3 min + 1 min Q&A; finalists 5 min + 3 min Q&A)

## Setup: connect to the Altice MCP
1. `cp .mcp.example.json .mcp.json`, then put in the team API key and the absolute path to `tools/connected_home_auth.py`.
2. `python3 tools/connected_home_auth.py login` (one-time browser sign-in with the team account).
3. Start Claude Code in this folder and check `/mcp` shows `connected-home` connected.

## Repo structure
```
README.md               you are here
.mcp.example.json       MCP config template (the real .mcp.json is gitignored)
docs/
  synapse.md            the concept
  mcp-capabilities.md   MCP reference
  altice-hackathon-brief.md
  reference/            organiser material + research (read-only)
deliverables/           report, executive summary, slides (+ organiser templates)
assets/                 logo and images
tools/                  connected_home_auth.py (OAuth helper used by .mcp.json)
```
Planned, created when the code starts: `synapse/` (event bridge, semantic layer, rules, verifier, memory, agent, MCP server) · `simulator/` (digital twin + replayed "Rosa" weeks) · `apps/` (Ana's app, Rosa's tablet).

## Conventions
- **Never commit secrets:** `.mcp.json`, `.env` and tokens stay local (see `.gitignore`).
- Docs in English, file names in kebab-case.
- Scenarios we create on the Altice platform are named `SY · <name> · v<n>`. They can't be deleted, so create them only after the format is confirmed.
- `docs/reference/` holds source material (organiser docs, kit notes): summarise it in `docs/` rather than copying it.
- Physical kit: only a small lamp on the smart plug, warn people before testing the smoke alarm, never arm the alarm (see the hardware list).
