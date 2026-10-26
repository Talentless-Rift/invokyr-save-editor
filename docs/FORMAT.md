# Save format

Everything below was derived from real saves and the game's own asset bundles, and checked
by re-encoding: `python invokyr_save.py check <save>` re-packs all four entries and compares
them to the original bytes, so the encoder is byte-exact for untouched data.

## Container

```
<slot>.sav = ZIP, deflate, no password
    meta        7 fields   - slot header, what the load menu shows
    save       11 keys      - normal run
    endless    11 keys      - endless run
    collection  5 fields    - permanent progression
```

* All four entries are a single MessagePack value, zero trailing bytes.
* `version = 15` inside `save` and `endless`.
* **No checksum, HMAC or signature field exists.** Every byte is accounted for by a named
  field, so there is nowhere for a digest to hide. Whether the game *accepts* an edited file
  is a separate question, answered only by loading it.
* Integers are written in the smallest MessagePack form that fits (`6943` = `d1 1b 1f`,
  `128825` = `ce 00 01 f7 39`), so the declared C# type is not visible from the bytes.
  The editor keeps the original width and signedness per field and widens only when the new
  value does not fit.

## meta — slot header

| field | example | meaning |
|---|---|---|
| `savedAtUtcTicks` | `639286014621687531` | .NET ticks (100 ns since 0001-01-01) → 2026-10-26 15:51 UTC |
| `roundIndex` | `8` | mirrors `endless.shop.visitCount` |
| `endlessRank` | `3` | mirrors `collection.bestEndlessRank` |
| `playTimeSeconds` | `23523` | int of `endless.playTimeSeconds` |
| `score` | `6943` | mirrors `endless.bank.currentTotalScore` |
| `playerCount` | `3` | players in the party |
| `characterOwners` | `[0, <steamid64>, 0, ...]` | 8 slots, SteamID64 per slot, `0` = empty |

`meta` tracks the **endless** run. Editing `endless.*` without fixing `meta.*` leaves the slot
menu showing stale numbers; the editor's consistency check reports it.

## save / endless — run state

Same 11-key schema in both entries.

| field | type | meaning |
|---|---|---|
| `version` | int | save format, currently `15` |
| `bank.currentTotalScore` | int | the run's score — this is the money the shop spends. There is no separate coin field. |
| `shop.visitCount` | int | completed shop rounds |
| `shop.completedBoardsAnchorIndexes` | int[] | `0..n-1` |
| `shop.completedBoardsConfigurations` | map[] | `{biome, difficulty, featuredMonster}` per board |
| `shop.completedMonsterRounds` | `[int,int][]` | `[featuredMonster, index]` per round |
| `shop.boardOffersSeed` | int | RNG seed for the current offers |
| `shop.boardOffersSolo` | bool | offers generated solo |
| `shop.shopItemsUIDs` | int[] | hashed ids of the shop offers |
| `shop.shopItemInfos` | map[] | `{boughtCount}`, same order as the uids |
| `upgrades.<char>Upgrades` | `{x,y}[]` | one entry per upgrade type: `x` = type, `y` = level |
| `furnitures.furnitures` | int[] | owned furniture ids |
| `playerInventories[i]` | `{itemStringIds: str[], itemBatteryLevels: float[]}` | items, parallel arrays |
| `playerColors[i]` | `{playerCharacter, _color}` | 9 rows: characters 0..7 then `-1` |
| `gameRoomSkinIndex` | int | selected room skin |
| `gameRoomTools` | same shape as `playerInventories` | items placed in the game room |
| `playTimeSeconds` | float32 | run playtime |
| `unlockedKeyDoors` | any[] | empty in every sample; element encoding unknown |

Characters: `red`, `specs`, `tank`, `ziggy`, `carl`, `bobby`, `susan`, `lily`.

### Invariants the game maintains

* `completedBoardsAnchorIndexes`, `completedBoardsConfigurations` and
  `completedMonsterRounds` always have the same length.
* `shopItemsUIDs` and `shopItemInfos` always have the same length.
* `itemStringIds` and `itemBatteryLevels` are parallel — same length, same order.
* `meta` mirrors `endless` for score, visits, playtime, and mirrors `collection` for rank.

### Items are one shared pool

The game **sums** `playerInventories[0..2]` and `gameRoomTools` into a single party
inventory. Two copies of a stack in different pools therefore count twice on screen. To hit
an exact total, keep the items in one pool and leave the others empty.

Battery level `-1.0` means no battery (unlimited). Real values: mask 30, flamethrower 15,
handgun 5, mob radar 120.

## collection — permanent progression

| field | type | meaning |
|---|---|---|
| `bestEndlessRank` | int | best endless rank |
| `monsterFigurines` | int (uint32) | bitmask, bit *n* = figurine *n* unlocked |
| `completedBoardKeys` | str[] | `"biome:featuredMonster"`, e.g. `"3:15"` |
| `completedHouseRuleIds` | str[] | e.g. `HouseRules/houserules/desc/kidnapping_2` |
| `bestBoardDifficulties` | `{boardKey, difficulty}[]` | best difficulty per board, 1–5 |

The highest figurine bit seen in a real save is 16; the game has 20 monster assets, so bits
0–19 are plausible and `1048575` (`0xFFFFF`) is what the editor writes for "all".

## Items and upgrades

See [ITEMS.md](ITEMS.md) for the full id list, battery defaults and the upgrade type table.

## Things that are not in the file

* **No player name.** Names come from Steam, joined through `meta.characterOwners`.
* **No slot title field.** The slot name is the file name.
* **No double jump.** `CharacterUpgrade_DoubleJump` exists as an asset with an empty level
  table (level count 0, no values), while `up_health` has 8 levels, `up_stamina` 7,
  `up_speed` 5 and `up_inventory` 2. The il2cpp metadata contains no air-jump counter at all
  (`AirJump`, `MaxJumps`, `jumpCount`, `ExtraJump`, `MultiJump`, `jumpsLeft` — zero hits);
  the jump code is `CanJump`, `JumpForce`, `JumpForceMultiplier`,
  `EntityState_JumpStart`/`JumpLand`, i.e. one jump. Probe saves at the unused upgrade
  indexes (0, 4, 5, 7) load without error and change nothing.
