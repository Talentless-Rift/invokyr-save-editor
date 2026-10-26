"""
Build a synthetic Invokyr save from scratch - no real save required.

Used by the test suite and by anyone who wants to try the editor without
touching their own save:

    python tests/make_save.py demo.sav
    python invokyr_gui.py --selftest demo.sav
    python invokyr_save.py check demo.sav

The values are made up; the field set is the one documented in docs/FORMAT.md.
Integers are encoded as int64 here because there is no original width to copy,
which is fine for tests but is not what the game itself writes.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from invokyr_save import node_from, pack, write_sav  # noqa: E402

CHARS = ["red", "specs", "tank", "ziggy", "carl", "bobby", "susan", "lily"]


def run_state(visits, score, play_time):
    return {
        "version": 15,
        "bank": {"currentTotalScore": score},
        "shop": {
            "visitCount": visits,
            "completedBoardsAnchorIndexes": [0, 1],
            "completedBoardsConfigurations": [
                {"biome": 0, "difficulty": 1, "featuredMonster": 8},
                {"biome": 3, "difficulty": 2, "featuredMonster": 15},
            ],
            "completedMonsterRounds": [[8, 0], [15, 1]],
            "boardOffersSeed": 123456789,
            "boardOffersSolo": False,
            "shopItemsUIDs": [],
            "shopItemInfos": [],
        },
        "upgrades": {"%sUpgrades" % ch: [] for ch in CHARS},
        "furnitures": {"furnitures": []},
        "playerInventories": [
            {"itemStringIds": [], "itemBatteryLevels": []} for _ in range(3)
        ],
        "playerColors": (
            [{"playerCharacter": i, "_color": 0} for i in range(8)]
            + [{"playerCharacter": -1, "_color": 0}]
        ),
        "gameRoomSkinIndex": 0,
        "gameRoomTools": {"itemStringIds": [], "itemBatteryLevels": []},
        "playTimeSeconds": float(play_time),
        "unlockedKeyDoors": [],
    }


def entries():
    return [
        ("meta", {
            "savedAtUtcTicks": 639286014621687531,
            "roundIndex": 2,
            "endlessRank": 1,
            "playTimeSeconds": 7200,
            "score": 1000,
            "playerCount": 3,
            "characterOwners": [0, 76561190000000000, 76561190000000001,
                                0, 0, 0, 0, 0],
        }),
        ("save", run_state(2, 1000, 3600)),
        ("endless", run_state(2, 1000, 7200)),
        ("collection", {
            "bestEndlessRank": 1,
            "monsterFigurines": 0,
            "completedBoardKeys": [],
            "completedHouseRuleIds": [],
            "bestBoardDifficulties": [],
        }),
    ]


def build(path):
    write_sav(path, [(name, pack(node_from(value))) for name, value in entries()])
    return path


def main(argv):
    out = argv[1] if len(argv) > 1 else "demo.sav"
    build(out)
    print("wrote %s" % os.path.abspath(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
