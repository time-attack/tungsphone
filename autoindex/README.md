# Auto-Indexer 🧭 — make the Mac "already know everything"

Leave it running for a few hours while you're away. It **explores your Mac by itself**,
app by app — reads the accessibility tree of each screen, clicks safe controls to reach
new screens, and learns the path to every feature. Everything it finds is streamed into
**Moss**. Later the floating Sahur queries that index and acts instantly, with no live
exploration — that's the "no-latency" payoff.

**Moss is the only store.** Nothing is written to disk. (If your Moss op-quota is
exhausted the writes say so out loud — no silent local fallback.)

## Run

```bash
./scripts/autoindex.sh --minutes 120                 # default safe app set
./scripts/autoindex.sh --all --minutes 240           # every installed app
./scripts/autoindex.sh --apps "Notes,Music,Maps"     # specific apps
./scripts/autoindex.sh --dry --apps Calculator       # read launch screens only, no clicking
./scripts/autoindex.sh --query "play my rock playlist"   # test the instant lookup
```

Needs **Accessibility** granted to your terminal (to read + click the UI).

## How it explores (and stays safe)

- **Reads everything** on each screen via the macOS Accessibility API.
- **Navigates by clicking** safe controls; resets between branches by relaunching the
  app and replaying the click-path to a frontier (reliable, no fragile "go back").
- **Safety:** never clicks anything whose label looks destructive (delete, send, buy,
  sign out, reset, …), never types, skips toggles/sliders/checkboxes, and skips the menu
  bar by default. Time-budgeted, with a visited-set so it never loops.
- **Moss writes are batched** (one op per ~20 screens) to respect the monthly op quota.

What lands in Moss, per screen:

```
text:     "<App> screen — reached by: <click path>. Controls: <names…>"
metadata: { kind:"screen", app, path:[…clicks…], elements:[{name,role,x,y}…] }
```

So a query like *"open my Instagram DMs"* returns the screen whose path is
`["Direct messages"]` plus the exact element coords — the agent just replays it.

## Files

| File | Role |
| --- | --- |
| `crawler.py` | the autonomous explorer — AX read, safe clicking, screen discovery |
| `moss_index.py` | Moss-only sink — batched `add_docs` + `query` |
| `autoindex.py` | entry point — app selection, time budget, `--query`, `--dry` |

## Heads-up: Moss quota

At build time your Moss project returned:

```
HTTP 429: "Monthly control API operations limit exceeded (1006/1000).
           Upgrade your plan at https://portal.usemoss.dev/…"
```

The crawler + indexing pipeline are verified end-to-end; the **only** thing stopping the
docs from landing is that op cap. Once it's raised / reset, or you point
`MOSS_PROJECT_KEY` (in `sahur-brain/.env`) at a project with quota, the same run fills
the index. You can also set `MOSS_AUTOINDEX` to choose the index name.
