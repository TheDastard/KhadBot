# Game art

Where the dashboard's game images and spell tooltips come from, what each source allows, and what the server does
and doesn't do with them. The decision behind it is in `docs/decisions.md` (game art); the web rules
(`.claude/rules/web.md`) name the one third-party script the browser loads.

| Image | Source | Where it is drawn |
|---|---|---|
| Spell tooltip and icon | Wowhead's tooltip script, in the browser | Every ability link |
| Ability icon name | WCL's `ReportAbility.icon` (`GET /v1/reports/{code}/abilities`) | Anywhere that wants an icon without the script |
| Boss portrait | The game client's journal art, exported by hand and served by the KhadBot API (planned); a fallback until then | Pull heading, search results |
| Boss thumbnail | The boss's Mythic kill achievement icon, from Blizzard's Game Data API (planned); a neutral square until then | Rail, night overview, loading tiles |
| Raid image, class icon | Blizzard's Game Data API (planned); nothing, and class colours, until then | Raid headings; player names |
| Faction mark | Drawn by KhadBot | Guild results |
| Logo | KhadBot's own asset | Header |

Every component that shows an image has a fallback, and the web app passes its tests with every image missing.

## Spell tooltips and icons

Ability names are links to Wowhead, and Wowhead's own script, running in the visitor's browser, adds the tooltip and
icon on hover, as on Warcraft Logs. The KhadBot server fetches and stores nothing from Wowhead.

Source: https://www.wowhead.com/tooltips, which documents including the script and linking to Wowhead pages (links
"will now sport a tooltip and an icon") and names Warcraft Logs and Raider.IO among the sites that do so. It
documents no data endpoint, no server-side use and no rate limit.

### Embed config

Before any link renders, in the page head:

```html
<script>const whTooltips = {colorLinks: true, iconizeLinks: true, renameLinks: false, iconSize: 'small'};</script>
<script src="https://wow.zamimg.com/js/tooltips.js"></script>
```

- `colorLinks` colours a link by quality; `iconizeLinks` adds the spell icon.
- `renameLinks` replaces the link text with Wowhead's name. It stays off: the dashboard shows its own ability names.
  `data-wh-rename-link="true"` renames one link.
- `iconSize` is `tiny` (the default), `small`, `medium` or `large`; `data-wh-icon-size` overrides it per link.
- The script also reads the same object under the name `wowhead_tooltips` (from its code).

### Links

- An ability name is `<a href="https://www.wowhead.com/spell=<spellId>">`, opened in a new tab with
  `rel="noopener"`. The spell ID is the game ID in WCL events and in `GET /v1/reports/{code}/abilities`.
- `www` is the live game. Other versions use another domain (`ptr`, `classic`, a locale), set with
  `data-wowhead="domain=<d>"`; the dashboard doesn't need one.
- A link that must not go to Wowhead can still get a tooltip: `<a href="#" data-wowhead="spell=<id>">`.

### Client-rendered pages

The script scans for links when it loads. The dashboard renders after load, so it asks the script to scan again after
each render with `WH.Tooltips.refreshLinks(element)`. That function is in the script's code (`tooltips.js`), not on
the tooltips page, so the web app keeps the call in one component.

### Content-Security-Policy

The hosts the script talks to, read from `tooltips.js`:

- `script-src https://wow.zamimg.com`: the script itself.
- `connect-src https://nether.wowhead.com`: tooltip data, `GET /tooltip/spell/<id>?dataEnv=1&locale=0` (CORS `*`).
- `img-src https://wow.zamimg.com`: icons, `/images/wow/icons/<size>/<name>.jpg` (`tiny` is a gif).
- `style-src https://wow.zamimg.com`: it adds `/css/universal.css`. The script also sets inline styles (icons are
  background images) and inserts style rules, so `style-src` needs `'unsafe-inline'` or a nonce.

Other hosts in the script (`use.typekit.net` fonts) serve Diablo titles only; WoW tooltips need no `font-src`
addition. These come from the script's code, not documentation, so Wowhead may change them.

### Privacy and terms

- The tooltip request goes from the visitor's browser to Wowhead, so the visitor's IP address and the spell ID reach
  Wowhead; the dashboard's privacy notice must say so.
- Wowhead's site terms (https://corp.fanbyte.com/legal/terms) grant a personal, non-commercial licence and bar
  downloading its content with any tool other than a general-purpose browser or Wowhead's own, and mirroring the
  service. That is why the server fetches and caches nothing and the script runs in the browser, the way Wowhead
  documents. Questions go to feedback@wowhead.com, the address the tooltips page gives.

## WCL ability icons

`ReportAbility.icon` (https://www.warcraftlogs.com/v2-api-docs/warcraft/reportability.doc.html: "An icon to use for
the ability", `String`). The report metadata query asks for it (`masterData.abilities.icon`, `docs/wcl/report-data.md`)
and `GET /v1/reports/{code}/abilities` serves it, lowercased and without a file extension, beside each ability's game
ID and name. The recorded fixtures' metadata predates the field, so it is null for them. While Wowhead's script draws
the icons nothing in the dashboard needs it; it is there for a place that wants an icon name without the script.

## Boss portraits: the game client's journal art

The Dungeon Journal's boss button images, the portraits the design canvas draws. No API serves them: the Game Data
API has no journal-encounter media, and a forum reply in 2021 said an image missing from the media endpoints "is not
provided by the API" (https://us.forums.blizzard.com/en/blizzard/t/journal-encounter-media/16364).

### Getting them

1. Launch the game with `-console` (Battle.net app → Game Settings → Additional command line arguments).
2. At character select, open the console (`` ` ``) and run `exportInterfaceFiles art`, the client's own export for
   addon authors. It writes the interface art under `_retail_\BlizzardInterfaceArt\Interface\`.
3. The portraits are `ENCOUNTERJOURNAL\UI-EJ-BOSS-<name>.blp`: 128×64, RGBA with a transparent background, the only
   size. Pillow reads BLP and writes PNG.
4. Re-export after a patch that adds bosses; the new files are the last ones the export writes.

Never extract with third-party tools (CASC readers, wago.tools): the EULA bars unauthorised tools and data mining.

The design canvas's portraits are these files, equal to within 7/255 per channel (BLP decoding).

### Names

The file name is an internal name (a creature type, a nickname), often not the boss's, so a person matches each file
to its boss in the Adventure Guide and the encounter file records it. A boss may have no portrait of its own in the
export; it then uses the fallback.

### Serving them (planned)

The converted PNGs go into the hosted bucket, which is private (it also holds raw WCL pages), and an API route
returns each one with long-lived cache headers. The browser loads them from the API's own origin, so the web rules
and the Content-Security-Policy gain no host. Local mode reads them from `KHADBOT_DATA_DIR` through the same blob
store.

Size: about 10 KB a portrait and about 12 MB for a whole export, inside R2's free storage, and browsers cache each
image, so the cost is nil at this scale (https://developers.cloudflare.com/r2/pricing/, as of 2026-11). A public
bucket would need a custom domain on Cloudflare (`r2.dev` is rate-limited and not for production,
https://developers.cloudflare.com/r2/buckets/public-buckets/); the route can return such a URL later instead.

### Terms

No Blizzard policy grants showing client art on a website: the EULA keeps the artwork Blizzard's, and the trademark
guidelines (https://www.blizzard.com/legal/38fd0408-8431-469a-99bc-2cd9eb9462c8) cover marks, not art. Warcraft
Logs, Wowhead, Raider.IO and warcraft.wiki.gg show the same images. KhadBot shows them on these terms of its own:

- Free and non-commercial; never part of a paid feature.
- Served by KhadBot, never hotlinked from Wowhead's or Warcraft Logs' copies (Wowhead's terms bar
  mirroring, and the 2021 forum reply calls using Wowhead's CDN unauthorised).
- Kept out of git.
- Pages that show them name Blizzard as the source, and they come down if Blizzard asks.

## Boss thumbnails, raid and class images: Blizzard's Game Data API

Nothing calls the API from KhadBot yet (`docs/follow-ups.md`). The facts marked "probed" come from live calls
against namespace `static-12.1.5_70077-us`.

Sources: the endpoint list at https://develop.battle.net/documentation/world-of-warcraft/game-data-apis, the guides
at https://community.developer.battle.net/documentation/guides/getting-started and
https://community.developer.battle.net/documentation/world-of-warcraft/guides/namespaces, and the API terms at
https://www.blizzard.com/en-us/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use.

### Access

- Hosts: `{region}.api.blizzard.com` for `us`, `eu`, `kr` and `tw`; `gateway.battlenet.com.cn` for China.
- Every WoW request carries a namespace, as `?namespace=` or the `Battlenet-Namespace` header. Static game data
  (journal, media, spells, achievements, classes) is `static-{region}`.
- Auth is the OAuth client credentials flow (`POST https://oauth.battle.net/token`,
  `grant_type=client_credentials`), which gives a bearer token for these public endpoints.
- **The client is the maintainer's:** a Battle.net account with an authenticator, the API terms accepted, and a
  client registered in the developer portal. Its ID and secret will be the settings `BLIZZARD_CLIENT_ID` and
  `BLIZZARD_CLIENT_SECRET` (planned, `docs/follow-ups.md`); hosted mode takes them as secrets. A token lasts 24 hours
  (probed).
- Limits: 36,000 requests an hour per client and 100 a second. Over the hourly quota service slows; over the
  per-second limit the API answers 429 until the next second. KhadBot's lookups are a few hundred, once.

### Terms

- Allowed: use the key in a registered application, and distribute the Data to end users for their personal use
  through it.
- **At most 30 days.** Nothing obtained through the APIs may be kept longer than 30 days. KhadBot keeps resolved
  image URLs, never images, and refreshes them before 30 days.
- **No premium features.** Applications may not offer paid versions or charge for features that use the API, so
  anything that shows Blizzard Data stays outside any future paid tier.
- Name Blizzard as the source of the Data, without implying endorsement; no Blizzard trademark in the application's
  title or URL; post a privacy policy; no marketing or advertising use; don't sell or transfer the Data; content
  suits a "T for Teen" audience.
- Scraping Blizzard sites is barred except through the APIs, so the API is how media URLs are learned. The terms say
  nothing specific about hotlinking the image URLs it returns, and Blizzard may change or revoke access at any time.

### Endpoints

| Image | Endpoint (namespace `static-{region}`) | Notes |
|---|---|---|
| Boss thumbnail | `GET /data/wow/media/achievement/{achievementId}` of the boss's `Mythic: <boss>` achievement | `icon` asset, 56×56 JPG, `/us/icons/56/<fileId>.jpg`, the only size (probed) |
| Raid image | `GET /data/wow/media/journal-instance/{journalInstanceId}` | `tile` asset, `/us/zones/<raid-slug>-small.jpg` (probed) |
| Class icon | `GET /data/wow/media/playable-class/{playableClassId}`; specs: `GET /data/wow/media/playable-specialization/{specId}` | Class IDs from `GET /data/wow/playable-class/index`; about 13 calls |
| Boss render | `GET /data/wow/journal-encounter/{id}` lists `creatures`, each with `creature_display.id`; `GET /data/wow/media/creature-display/{id}` gives a `zoom` render | 600×600 JPG of the whole model, small, on flat dark grey (probed). Not the journal portrait; unused |
| Spell icon | `GET /data/wow/media/spell/{spellId}` | Not used: Wowhead's script draws spell icons |

- **Encounter mapping.** A journal encounter ID is not WCL's encounter ID. WCL's `Encounter` type has `journalID`
  (`docs/wcl/search.md`, "Fields"), which gives the mapping; it goes into the encounter files, `null` with
  `verified: false` when unknown. `GET /data/wow/journal-instance/index` lists the raids, and
  `GET /data/wow/journal-instance/{id}` lists each raid's encounters (probed). A raid may be split across several
  journal instances.
- **Achievement mapping.** Nothing links an encounter to its achievement: the achievement's `criteria` carry only a
  description. `GET /data/wow/achievement-category/index` names one "<expansion> Raid" category per expansion, and that
  category lists a `Mythic: <journal encounter name>` achievement for each boss (probed), so a script proposes each ID
  by name and a person confirms it into the encounter file.
- **Media URLs.** The media endpoints return URLs on `render.worldofwarcraft.com`, which need no credential, so the
  browser loads them with `<img src>` once the server has resolved them (probed). The web rules add the host when this
  is built.

### How it will work

1. A refresh job, on the maintainer's client credential, resolves each encounter's achievement icon URL, each raid's
   tile and the class icons into a table with the time each was fetched, refreshes each before 30 days and drops it
   after.
2. The API serves the URL as JSON; it stores and proxies no API image.
3. The browser hotlinks the URL and falls back when there is none.
4. Pages that show Blizzard Data name Blizzard as its source.

### Until then

- Boss portraits and raid images: nothing in their place.
- Class icons: class colours on player names (`GET /v1/reports/{code}/players`).
- Boss thumbnails on the rail and loading tiles: a neutral square.

## Faction mark

No API offers a faction emblem: the guild crest endpoints (`/data/wow/media/guild-crest/border/{id}`,
`/emblem/{id}`) are one guild's customisable crest parts, and `playable-race` names a faction without media. The
dashboard draws its own neutral mark for Alliance and Horde (a coloured shield, or the word), using no Blizzard asset.

## Open questions

- `WH.Tooltips.refreshLinks` and the Content-Security-Policy hosts and inline-style allowance are read from the
  script's code; confirm them in a browser when the web app builds ability links (`docs/follow-ups.md`).
- WCL's `ReportAbility.icon` value is not in any recording; look at it on the first live report.
