# Free asset survey (13 Nov 2026)

First pass from web searches only. Nothing here has been downloaded or licence-checked on its own page yet; T-414 does that. The repo is public, so assets live in `H:\BelfastData\assets\` and git holds only a manifest (source URL, author, licence, hash) and credits.

## Licence rule

Use CC0 first, then CC-BY (credit in `CREDITS.md`). Reject NC, ND, "royalty free, no redistribution" (CGTrader, Free3D, TurboSquid style) and anything that forbids repackaging, because the level zip is a repackaged work. Check every licence on the asset's own page.

## What was found

| Need | Finding | Licence | Verdict |
|---|---|---|---|
| Generic buildings, fences, trees | [Kenney City Kit Suburban](https://kenney-assets.itch.io/city-kit-suburban) (glTF) | CC0 | Usable as a base. Not UK terraces, so recolour and retexture |
| UK terraced houses | [Unity UK Terraced Houses Pack Free](https://assetstore.unity.com/packages/3d/environments/urban/uk-terraced-houses-pack-free-63481), [Free3D Regency terraces](https://free3d.com/3d-model/regency-terrace-houses-96.html), [CGTrader Huddersfield row](https://cgtrader.com/free-3d-models/exterior/house/row-of-terrace-houses-in-huddersfield) | Store EULA or royalty free, not CC0 | Probably not redistributable inside a mod. Check terms. Likely better to build our own simple terrace and semi-detached blocks and texture them with CC0 brick |
| UK road signs | [BlendSwap UK street signs](https://whatever.blendswap.com/blend/25807) | CC0 | Sign poles, frames, parts. Faces come from T-409 (DfT sign images) |
| Bollards | [SketchUp 3D Warehouse UK bollards](https://embed-3dwarehouse-classic.sketchup.com/model/9f0da5a2-774a-4624-8760-d7f6f5a4cde1/UK-Traffic-Bollards-V1) | Unchecked | Check licence, otherwise model our own (trivial shape) |
| Telephone kiosk | [Sketchfab London telephone booth](https://sketchfab.com/3d-models/london-telephone-booth-6eae0ad6204747f683cae4012dc3f8ce) (8.7k tris) | CC-BY | Usable with credit, but a K6 is rare in Belfast now. Low priority |
| Pillar and post boxes | Only CGTrader or Fab listings found | Paid or royalty free | Model our own (simple). Note that in Northern Ireland the crown cypher is absent from newer boxes, which is a real Belfast detail |
| Brick, pebbledash, render textures | OpenGameArt (some CC0), ambientCG, Poly Haven, Sharetextures | CC0 on ambientCG and Poly Haven by their site policy. OpenGameArt varies per item | Best source. Red brick for terraces, pebbledash and render for semis |
| Flags | Wikimedia Commons SVGs of the Union Flag, Ulster Banner and Irish tricolour (the Ulster Banner file is marked public domain, but mirrors disagree on the author) | Public domain, verify the current Commons page | Flags are simple geometry, so we can also draw them ourselves and avoid the question |
| Landmarks | No free Belfast City Hall, Titanic Belfast or Samson and Goliath model found. Only paid CGTrader Belfast scenes and HMS Belfast scans (not the city) | Paid or restricted | Build low-poly stand-ins ourselves. They only need to read right from a car at distance |
| Murals | No free 3D model or scan found | n/a | See below |

Sources not searched directly but known to be CC0 and worth checking in T-414: Kenney, Quaternius, KayKit, Poly Haven, ambientCG.

## Murals: why not copy real ones

- UK freedom of panorama (CDPA 1988 s.62) covers buildings and 3D public art, not 2D works such as murals. A photo of a mural used as a texture is a copy of the artist's work, even if the photo itself is CC-BY-SA on Wikimedia Commons.
- Many murals are political. Putting real paramilitary or party imagery into a learner-driver tool is a judgement call for you, not a default.

Recommended: make our own original murals in the Belfast style (large gable-end paintings: shipyard and Titanic, Cave Hill, Giant's Causeway, football and music themes, community scenes), placed on gable ends in the right districts. If you want specific real murals, only use ones with a clear open licence or your own photos of them.

## Flags: how it should work

- East Belfast and Loyalist areas (Newtownards Road, Sandy Row, Shankill, Sydenham): Union Flags and Ulster Banners on lamp posts, houses and bunting, heavy around July.
- West Belfast and Nationalist areas (Falls Road, Andersonstown, Ballymurphy, Short Strand): Irish tricolours, GAA club flags, Palestinian and other flags are also common in some streets.
- Our Hydebank first slice is in south Belfast and mixed, so expect only a few flags there. The effect shows more once City South and Full are built (T-704, T-705).
- Place them from a hand-written zone file with a density per zone and an evidence note (OSM place names, your knowledge, photos you took), not from a guess per street. Keep it sparse and tasteful. No paramilitary flags or emblems unless you decide otherwise (T-415).
