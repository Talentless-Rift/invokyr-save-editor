# Items and upgrades

Ids come from the game's addressables bundles (`Data-Dice_*`, `Data-GameMasterMask`, …), not
from `global-metadata.dat`. The editor writes an id only if the build actually contains it:
`dice_explosion` is deliberately excluded because it is the boom dice's VFX id, not a pickup.

## Dice (31)

| id | id |
|---|---|
| `dice_antigravity` | `dice_lightning` |
| `dice_boom` | `dice_lightning_hard` |
| `dice_boom_hard` | `dice_pelote` |
| `dice_boomcocoon` | `dice_playcube` |
| `dice_boomheal` | `dice_push` |
| `dice_chaos` | `dice_resurrect` |
| `dice_chaos2` | `dice_revive` |
| `dice_cocoon` | `dice_revive_hard` |
| `dice_cocoon_hard` | `dice_revivetp` |
| `dice_cocoonfire` | `dice_scanner` |
| `dice_cubedanger` | `dice_smoke` |
| `dice_fire` | `dice_tp` |
| `dice_fire_hard` | `dice_freeze` |
| `dice_fireworks` | `dice_gather` |
| `dice_heal` | `dice_heal_hard` |
| `dice_light` | |

## Gadgets (12)

| id | battery | note |
|---|---|---|
| `g_camera` | -1 | |
| `g_compass` | -1 | |
| `g_fire` | 15 | flamethrower |
| `g_guitar` | -1 | |
| `g_hgun` | 5 | |
| `g_mask` | 30 | |
| `g_megaphone` | -1 | |
| `g_mobradar` | 120 | |
| `g_screambox` | -1 | |
| `g_skull` | -1 | |
| `g_xray` | -1 | |
| `lantern` | -1 | no battery |

Battery `-1.0` means "no battery", i.e. unlimited. The four non-infinite gadgets are the
only ones with a real charge value, and the editor uses exactly those numbers.

## Upgrade types

`upgrades.<character>Upgrades` holds `{x, y}` pairs: `x` is the upgrade type, `y` the level
bought. Caps come from the `CharacterUpgrade_*` assets in the build; the `x` mapping was
confirmed against four characters in real saves with zero contradictions.

| x | type | asset | asset id | max level | per-level values |
|---|---|---|---|---|---|
| 1 | Health | `CharacterUpgrade_MaxHealth` | `up_health` | 8 | 20, 40, 60, 80, 100, 120, 140, 160 |
| 2 | Stamina | `CharacterUpgrade_MaxStamina` | `up_stamina` | 7 | 15 … 105 |
| 3 | Speed | `CharacterUpgrade_RunSpeed` | `up_speed` | 5 | 0.065 … 0.325 |
| 6 | Item slots | `CharacterUpgrade_InventorySlot` | `up_inventory` | 2 | two steps |

Upgrade indexes `0`, `4`, `5` and `7` are unmapped. `CharacterUpgrade_DoubleJump` exists in
the build but carries an empty level table and no runtime code — see
[FORMAT.md](FORMAT.md#things-that-are-not-in-the-file).

A character with no upgrades has an empty list; the editor keeps unknown `x` entries
untouched when it rewrites a list.

## Example: exact counts

The game sums every pool, so an exact loadout means one pool filled and the rest empty:

```
save.playerInventories[0].itemStringIds    = ["g_mask","g_mask",
                                              "g_fire","g_fire","g_fire",
                                              "dice_revive" x5,
                                              "dice_heal"   x5,
                                              "dice_boom"   x5]
save.playerInventories[0].itemBatteryLevels = [30.0, 30.0, 15.0, 15.0, 15.0, -1.0, ...]
save.playerInventories[1..2].itemStringIds  = []
save.gameRoomTools.itemStringIds            = []
```

`presets/tools.json` is the same idea for the tool rack; `tools.json` also rewrites
`playerInventories[0]`, so drop those last two lines if you keep a loadout there.
