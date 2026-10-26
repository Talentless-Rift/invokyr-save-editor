"""
invokyr_gui.py - point-and-click editor for Invokyr .sav files.

Runs on top of invokyr_save.py (same folder): the decode/encode logic, the
byte-exact re-encoder and the type-width preservation all come from there.

    python invokyr_gui.py [optional.sav]

Tabs:

    Quick       money / score, save-slot name, playtime, rank, visits, and the
                one-click buttons (max upgrades, everything, max figurines)
    Upgrades    per-character upgrade levels, capped at the real asset maximums
                (Health 8, Stamina 7, Speed 5, Item slots 2)
    Items       the shared party item pool, per entry and per slot, with the
                full 43-id catalogue and default battery levels
    Collection  monster figurine bitmask, completed boards + difficulties,
                house rules, furniture, room skin
    Advanced    the raw tree - double-click any row to edit it as JSON

Notes that matter:

  * Money is `bank.currentTotalScore` (there is no separate coin field). The
    slot menu shows `meta.score`, which mirrors the endless bank, so both are
    written together.
  * Items are one shared pool: the game sums playerInventories[*] and
    gameRoomTools, so spreading a stack over two slots doubles it.
  * The save-slot name *is* the file name; the game's rename button renames the
    file. Player names come from Steam and are not in the save at all.
  * Nothing here overwrites a file unless you pick it and confirm; a copy of
    the previous file is taken before any overwrite.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import invokyr_save as core

HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(HERE, "presets")
SAVE_DIR = os.path.join(os.environ.get("USERPROFILE", ""),
                        "AppData", "LocalLow", "Ludogram", "Invokyr", "saves")
DEFAULT_SAV = os.path.join(SAVE_DIR, "save 1.sav")

CHARS = ["red", "specs", "tank", "ziggy", "carl", "bobby", "susan", "lily"]

# x = upgrade type in the save, cap = max level in the CharacterUpgrade assets
UPGRADES = [(1, "Health", 8), (2, "Stamina", 7), (3, "Speed", 5), (6, "Item slots", 2)]
UPGRADE_CAP = {x: cap for x, _, cap in UPGRADES}
UPGRADE_NAME = {x: name for x, name, _ in UPGRADES}

GADGETS = ["g_camera", "g_compass", "g_fire", "g_guitar", "g_hgun", "g_mask",
           "g_megaphone", "g_mobradar", "g_screambox", "g_skull", "g_xray", "lantern"]
DICE = ["dice_antigravity", "dice_boom", "dice_boom_hard", "dice_boomcocoon",
        "dice_boomheal", "dice_chaos", "dice_chaos2", "dice_cocoon",
        "dice_cocoon_hard", "dice_cocoonfire", "dice_cubedanger", "dice_fire",
        "dice_fire_hard", "dice_fireworks", "dice_freeze", "dice_gather",
        "dice_heal", "dice_heal_hard", "dice_light", "dice_lightning",
        "dice_lightning_hard", "dice_pelote", "dice_playcube", "dice_push",
        "dice_resurrect", "dice_revive", "dice_revive_hard", "dice_revivetp",
        "dice_scanner", "dice_smoke", "dice_tp"]
ALL_ITEMS = DICE + GADGETS

# battery level (charge / durability) the game uses for each item; -1 = unlimited
BATTERY = {"g_mask": 30.0, "g_fire": 15.0, "g_hgun": 5.0,
           "g_mobradar": 120.0, "lantern": -1.0}

# the counts asked for originally: (id, battery, count)
LOADOUT = [("g_mask", 30.0, 2), ("g_fire", 15.0, 3), ("dice_revive", -1.0, 5),
           ("dice_heal", -1.0, 5), ("dice_boom", -1.0, 5)]

FIGURINE_BITS = 20


def battery_for(item_id):
    return BATTERY.get(item_id, -1.0)


def pretty(item_id):
    s = item_id
    for pre in ("dice_", "g_"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.replace("_", " ").title()


def as_int(text):
    return int(str(text).replace(",", "").replace("_", "").strip())


# --------------------------------------------------------------------- widgets

def preview(node):
    k = node.kind
    if k == "map":
        return "{ %d fields }" % len(node.value)
    if k == "arr":
        return "[ %d items ]" % len(node.value)
    if k == "str":
        s = node.value
        return '"%s"' % (s if len(s) <= 60 else s[:57] + "...")
    if k == "bin":
        return "<%d bytes>" % len(node.value)
    if k == "float":
        return repr(node.value)
    if k == "bool":
        return "true" if node.value else "false"
    if k == "nil":
        return "null"
    return str(node.value)


def fmt_name(node):
    return {"map": "map", "arr": "list"}.get(node.kind, node.kind)


class EditDialog(tk.Toplevel):
    """One JSON text box; works for a single scalar and for a whole subtree."""

    def __init__(self, master, path, node, on_ok):
        super().__init__(master)
        self.title("Edit " + path)
        self.on_ok = on_ok
        self.path = path
        self.orig = node
        self.transient(master)
        self.resizable(True, True)

        head = ttk.Label(self, text=path, font=("Consolas", 9, "bold"))
        head.pack(anchor="w", padx=10, pady=(10, 4))

        hint = {
            "map": "JSON object", "arr": "JSON array", "str": "JSON string in quotes",
            "int": "integer", "float": "number", "bool": "true / false", "nil": "null",
        }.get(node.kind, node.kind)
        ttk.Label(self, text="expects " + hint).pack(anchor="w", padx=10)

        text = json.dumps(core.plain(node), indent=2, ensure_ascii=False)
        self.box = tk.Text(self, width=78, height=min(24, max(3, text.count("\n") + 2)),
                           font=("Consolas", 10), wrap="none", undo=True)
        self.box.insert("1.0", text)
        self.box.pack(fill="both", expand=True, padx=10, pady=6)
        self.box.focus_set()

        self.err = ttk.Label(self, text="", foreground="#b00020")
        self.err.pack(anchor="w", padx=10)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="OK", command=self.commit).pack(side="right", padx=6)
        ttk.Label(bar, text="Ctrl+Enter = OK").pack(side="left")

        self.bind("<Control-Return>", lambda e: self.commit())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def commit(self):
        raw = self.box.get("1.0", "end").strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        kind = self.orig.kind
        if kind == "map" and not isinstance(value, dict):
            return self.fail("this field is a JSON object; edit the braces, not the text")
        if kind == "arr" and not isinstance(value, list):
            return self.fail("this field is a JSON array; edit the brackets, not the text")
        if kind == "str" and not isinstance(value, str):
            return self.fail("this field is a string; wrap the value in double quotes")
        if kind == "bool" and not isinstance(value, bool):
            return self.fail("this field is true or false")
        if kind in ("int", "float", "bool", "str") and isinstance(value, (dict, list)):
            return self.fail("this field holds a single %s value, not a JSON object or array" % kind)
        if kind == "int":
            if isinstance(value, bool):
                value = int(value)
            elif isinstance(value, float):
                value = int(value)
            elif isinstance(value, str) and value.lstrip("+-").isdigit():
                value = int(value)
            elif not isinstance(value, int):
                return self.fail("this field is a whole number")
        if kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except ValueError:
                        return self.fail("this field is a number")
                else:
                    return self.fail("this field is a number")
            value = float(value)
        if kind == "nil" and not isinstance(value, (dict, list)):
            pass
        try:
            self.on_ok(self.path, value)
        except Exception as exc:  # type width overflow etc.
            return self.fail(str(exc))
        self.destroy()

    def fail(self, msg):
        self.err.configure(text=msg)


# ------------------------------------------------------------------------- app

class App:
    def __init__(self, root, path):
        self.root = root
        self.src = None
        self.nodes = {}
        self.order = []
        self.dirty = set()
        self.quiet = False

        root.title("Invokyr save editor")
        root.geometry("1080x780")
        root.minsize(860, 600)

        self._build_toolbar()
        self._build_status_bar()
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self._build_quick()
        self._build_upgrades()
        self._build_items()
        self._build_collection()
        self._build_advanced()

        if path and os.path.exists(path):
            self.load(path)
        else:
            self.set_status("No file loaded. Use 'Open .sav' (default: %s)" % DEFAULT_SAV)

    # ------------------------------------------------------------------ chrome

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open .sav", command=self.open_dialog).pack(side="left")
        ttk.Button(bar, text="Save as...", command=self.save_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Save into game folder", command=self.save_into_game).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Revert", command=self.reload).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Backup saves folder", command=self.backup).pack(side="left", padx=(18, 0))

        ttk.Label(bar, text="   money is int16 in this save; bigger values are written as int32",
                  foreground="#666").pack(side="right")

    def _build_status_bar(self):
        self.status = ttk.Label(self.root, text="", padding=(10, 6), relief="sunken", anchor="w")
        self.status.pack(fill="x", side="bottom")

    # ------------------------------------------------------------- quick tab

    def _build_quick(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Quick")
        self.money_wide = tk.BooleanVar(value=False)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        money = ttk.LabelFrame(f, text="Money / score", padding=8)
        money.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Label(money, text="current run (save.bank.currentTotalScore):").grid(row=0, column=0, sticky="w")
        self.money_save = ttk.Entry(money, width=14)
        self.money_save.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(money, text="endless run + slot menu (endless.bank / meta.score):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.money_end = ttk.Entry(money, width=14)
        self.money_end.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        row = ttk.Frame(money)
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(row, text="Apply money", command=lambda: self.guard(self.apply_money)).pack(side="left")
        for v in (9999, 32767, 999999):
            ttk.Button(row, text="%d" % v, width=8,
                       command=lambda v=v: self.guard(self.set_all_money, v)).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(money, text="allow values above 32767 (the field is written as int32)",
                        variable=self.money_wide).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(money, text="there is no separate coin field; this is the score the shop spends. "
                              "The game stores small values as int16 - if a slot refuses to load, "
                              "keep money at 32767 or less.",
                  foreground="#666", wraplength=520, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

        slot = ttk.LabelFrame(f, text="Save slot", padding=8)
        slot.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(slot, text="slot name (the file name):").grid(row=0, column=0, sticky="w")
        self.slot_name = ttk.Entry(slot, width=24)
        self.slot_name.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(slot, text="playtime hours:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.play_hours = ttk.Entry(slot, width=10)
        self.play_hours.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(slot, text="set playtime", command=lambda: self.guard(self.apply_playtime)).grid(row=1, column=2, sticky="w")
        ttk.Label(slot, text="endless rank:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.rank_var = ttk.Entry(slot, width=10)
        self.rank_var.grid(row=2, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(slot, text="set rank", command=lambda: self.guard(self.apply_rank)).grid(row=2, column=2, sticky="w")
        ttk.Label(slot, text="shop visits:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.visit_var = ttk.Entry(slot, width=10)
        self.visit_var.grid(row=3, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(slot, text="set visits", command=lambda: self.guard(self.apply_visits)).grid(row=3, column=2, sticky="w")
        ttk.Label(slot, text="character owners (8 SteamIDs, 0 = empty):").grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.owners_var = ttk.Entry(slot, width=44)
        self.owners_var.grid(row=4, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(slot, text="set owners", command=lambda: self.guard(self.apply_owners)).grid(
            row=4, column=2, sticky="w")
        ttk.Label(slot, text="player names come from Steam, not from the save",
                  foreground="#666").grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        acts = ttk.LabelFrame(f, text="One click", padding=8)
        acts.grid(row=0, column=1, rowspan=2, sticky="nsew")
        buttons = [
            ("Max upgrades - all 8 characters", self.max_all_upgrades),
            ("My loadout - 2 mask / 3 fire / 5+5+5 dice", self.give_loadout),
            ("Everything - one of all 43 items", self.give_everything),
            ("Clear every inventory", self.clear_items),
            ("Max figurines", self.max_figurines),
            ("Save into game folder", self.save_into_game),
        ]
        for i, (text, fn) in enumerate(buttons):
            ttk.Button(acts, text=text, width=44, command=lambda fn=fn: self.guard(fn)).grid(
                row=i, column=0, sticky="ew", pady=2)
        acts.columnconfigure(0, weight=1)

        note = ("Items are one shared party pool - the game adds up all three player "
                "slots and the rack. Editing here writes both the save and the endless "
                "entry so either run shows the same thing.")
        ttk.Label(f, text=note, foreground="#666", wraplength=980,
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w")
        f.rowconfigure(2, weight=1)

    # ---------------------------------------------------------- upgrades tab

    def _build_upgrades(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Upgrades")
        self.up_vars = {}
        for col, (x, name, cap) in enumerate(UPGRADES):
            ttk.Label(f, text="%s (max %d)" % (name, cap),
                      font=("Segoe UI", 9, "bold")).grid(row=0, column=col + 1, padx=8, pady=(0, 6))
        for row, ch in enumerate(CHARS, start=1):
            ttk.Label(f, text=ch.capitalize()).grid(row=row, column=0, sticky="w")
            for col, (x, name, cap) in enumerate(UPGRADES):
                var = tk.StringVar(value="0")
                self.up_vars[(ch, x)] = var
                sb = ttk.Spinbox(f, from_=0, to=cap, width=6, textvariable=var,
                                 command=lambda ch=ch, x=x: self.guard(self.apply_upgrade, ch, x))
                sb.grid(row=row, column=col + 1, padx=8, pady=2)
                sb.bind("<FocusOut>", lambda e, ch=ch, x=x: self.guard(self.apply_upgrade, ch, x))
                sb.bind("<Return>", lambda e, ch=ch, x=x: self.guard(self.apply_upgrade, ch, x))
        bar = ttk.Frame(f)
        bar.grid(row=len(CHARS) + 1, column=0, columnspan=5, sticky="w", pady=(12, 0))
        ttk.Button(bar, text="Set all max", command=lambda: self.guard(self.max_all_upgrades)).pack(side="left")
        ttk.Button(bar, text="Set all to 0", command=lambda: self.guard(self.zero_all_upgrades)).pack(side="left", padx=6)
        ttk.Label(f, text="written to both the save and the endless run, other upgrade "
                          "types in the file are kept", foreground="#666").grid(
            row=len(CHARS) + 2, column=0, columnspan=5, sticky="w", pady=(8, 0))

    # ------------------------------------------------------------- items tab

    def _build_items(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Items")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="write to:").pack(side="left")
        self.item_target = ttk.Combobox(top, width=10, state="readonly",
                                        values=["both", "save", "endless"])
        self.item_target.set("both")
        self.item_target.pack(side="left", padx=(4, 14))
        ttk.Label(top, text="pool:").pack(side="left")
        self.item_pool = ttk.Combobox(top, width=12, state="readonly",
                                      values=["Player 1", "Player 2", "Player 3", "Rack"])
        self.item_pool.set("Player 1")
        self.item_pool.pack(side="left", padx=(4, 0))
        for box in (self.item_target, self.item_pool):
            box.bind("<<ComboboxSelected>>", lambda e: self.refresh_items())

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.item_list = tk.Listbox(body, height=14, font=("Consolas", 9), activestyle="none")
        self.item_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.item_list.yview)
        sb.pack(side="left", fill="y")
        self.item_list.configure(yscrollcommand=sb.set)

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        ttk.Label(side, text="add item").pack(anchor="w")
        self.item_pick = ttk.Combobox(side, width=28, values=ALL_ITEMS, state="readonly")
        self.item_pick.pack(anchor="w", pady=(2, 0))
        self.item_pick.set("dice_heal")
        self.item_pick.bind("<<ComboboxSelected>>", lambda e: self.item_bat.set(
            "%g" % battery_for(self.item_pick.get())))
        row = ttk.Frame(side)
        row.pack(anchor="w", pady=(4, 0))
        ttk.Label(row, text="count").pack(side="left")
        self.item_count = ttk.Spinbox(row, from_=1, to=99, width=5)
        self.item_count.set("1")
        self.item_count.pack(side="left", padx=(4, 8))
        ttk.Label(row, text="battery").pack(side="left")
        self.item_bat = ttk.Entry(row, width=7)
        self.item_bat.insert(0, "-1")
        self.item_bat.pack(side="left", padx=(4, 0))

        ttk.Button(side, text="Add to pool", command=lambda: self.guard(self.add_item)).pack(
            anchor="w", pady=(8, 0))
        ttk.Button(side, text="Remove selected", command=lambda: self.guard(self.remove_item)).pack(
            anchor="w", pady=(4, 0))
        ttk.Button(side, text="Reset batteries to defaults",
                   command=lambda: self.guard(self.reset_batteries)).pack(anchor="w", pady=(4, 0))
        ttk.Button(side, text="Clear this pool", command=lambda: self.guard(self.clear_pool)).pack(
            anchor="w", pady=(4, 0))

        self.item_info = ttk.Label(f, text="", foreground="#666")
        self.item_info.pack(anchor="w", pady=(6, 0))

    # -------------------------------------------------------- collection tab

    def _build_collection(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Collection")

        fig = ttk.LabelFrame(f, text="Monster figurines (bitmask)", padding=8)
        fig.grid(row=0, column=0, sticky="nw", padx=(0, 12), pady=(0, 8))
        self.fig_vars = []
        for bit in range(FIGURINE_BITS):
            var = tk.BooleanVar(value=False)
            self.fig_vars.append(var)
            ttk.Checkbutton(fig, text="%d" % bit, variable=var).grid(
                row=bit // 10, column=bit % 10, sticky="w", padx=2, pady=1)
        row = ttk.Frame(fig)
        row.grid(row=2, column=0, columnspan=10, sticky="w", pady=(8, 0))
        ttk.Label(row, text="raw").pack(side="left")
        self.fig_raw = ttk.Entry(row, width=12)
        self.fig_raw.pack(side="left", padx=6)
        ttk.Button(row, text="Apply boxes", command=lambda: self.guard(self.apply_figurines)).pack(side="left")
        ttk.Button(row, text="Apply raw", command=lambda: self.guard(self.apply_figurines_raw)).pack(side="left", padx=6)
        ttk.Button(row, text="All", command=lambda: self.guard(self.max_figurines)).pack(side="left")

        boards = ttk.LabelFrame(f, text="Completed boards  (boardKey difficulty)", padding=8)
        boards.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        self.board_text = tk.Text(boards, width=32, height=9, font=("Consolas", 9))
        self.board_text.pack(fill="both", expand=True)
        ttk.Button(boards, text="Apply boards", command=lambda: self.guard(self.apply_boards_ui)).pack(
            anchor="w", pady=(6, 0))
        ttk.Label(boards, text="one per line, e.g.  0:8 5", foreground="#666").pack(anchor="w")

        rules = ttk.LabelFrame(f, text="House rules completed (one id per line)", padding=8)
        rules.grid(row=1, column=1, sticky="nsew")
        self.rule_text = tk.Text(rules, width=52, height=9, font=("Consolas", 9))
        self.rule_text.pack(fill="both", expand=True)
        ttk.Button(rules, text="Apply rules", command=lambda: self.guard(self.apply_rules_ui)).pack(
            anchor="w", pady=(6, 0))

        misc = ttk.LabelFrame(f, text="Room", padding=8)
        misc.grid(row=0, column=1, sticky="new")
        ttk.Label(misc, text="furniture ids").grid(row=0, column=0, sticky="w")
        self.furn_var = ttk.Entry(misc, width=22)
        self.furn_var.grid(row=0, column=1, padx=6)
        ttk.Button(misc, text="apply", command=lambda: self.guard(self.apply_furniture)).grid(row=0, column=2)
        ttk.Label(misc, text="room skin index").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.skin_var = ttk.Entry(misc, width=8)
        self.skin_var.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(misc, text="apply", command=lambda: self.guard(self.apply_skin)).grid(row=1, column=2)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)

    # ---------------------------------------------------------- advanced tab

    def _build_advanced(self):
        f = ttk.Frame(self.nb, padding=(8, 6))
        self.nb.add(f, text="Advanced")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Label(bar, text="entry:").pack(side="left")
        self.filter = ttk.Combobox(bar, width=11, state="readonly",
                                   values=["all", "meta", "save", "endless", "collection"])
        self.filter.set("all")
        self.filter.pack(side="left", padx=(4, 14))
        self.filter.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        ttk.Label(bar, text="find:").pack(side="left")
        self.search = ttk.Entry(bar, width=18)
        self.search.pack(side="left", padx=(4, 0))
        self.search.bind("<KeyRelease>", lambda e: self.refresh())

        ttk.Button(bar, text="consistency check", command=self.show_warnings).pack(side="right")
        ttk.Button(bar, text="collapse all", command=lambda: self.set_open(False)).pack(side="right", padx=6)
        ttk.Button(bar, text="expand all", command=lambda: self.set_open(True)).pack(side="right")

        pre = ttk.Frame(f)
        pre.pack(fill="x", pady=(6, 4))
        ttk.Label(pre, text="presets:").pack(side="left")
        for label, fn in (("score 999999", "rich.json"),
                          ("all figurines", "collection.json"),
                          ("full tool belt", "tools.json")):
            if os.path.exists(os.path.join(PRESET_DIR, fn)):
                ttk.Button(pre, text=label,
                           command=lambda fn=fn: self.apply_preset(fn)).pack(side="left", padx=(6, 0))

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=("value", "type"), selectmode="browse")
        self.tree.heading("#0", text="field")
        self.tree.heading("value", text="value")
        self.tree.heading("type", text="type")
        self.tree.column("#0", width=340)
        self.tree.column("value", width=480)
        self.tree.column("type", width=70, anchor="center")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree.tag_configure("dirty", background="#fff3bf")
        self.tree.tag_configure("entry", font=("Segoe UI", 9, "bold"))
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Return>", self.on_double)

    # ------------------------------------------------------------------ state

    def set_status(self, msg):
        extra = ""
        if self.dirty:
            extra = "   |   %d unsaved change%s" % (len(self.dirty), "" if len(self.dirty) == 1 else "s")
        if self.src:
            w = len(self.warnings())
            if w:
                extra += "   |   %d consistency warning%s (Advanced > check)" % (w, "" if w == 1 else "s")
        if self.game_running():
            extra += "   |   GAME IS RUNNING - close it before replacing the file"
        self.status.configure(text=msg + extra)

    @staticmethod
    def game_running():
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Invokyr.exe"],
                                 capture_output=True, text=True, timeout=4).stdout
            return "Invokyr.exe" in out
        except Exception:
            return False

    def guard(self, fn, *args, **kwargs):
        """Run an edit callback; report failures in a dialog instead of a traceback."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            messagebox.showerror("Edit failed", str(exc) or type(exc).__name__)
            return None

    def load(self, path):
        try:
            entries = core.read_sav(path)
            nodes = {n: core.Reader(d).read() for n, d in entries}
            for n in nodes:
                core.pack(nodes[n])  # prove it re-encodes before we show it
        except Exception as exc:
            messagebox.showerror("Cannot read save", "%s\n\n%s" % (path, exc))
            return
        self.src = path
        self.order = [n for n, _ in entries]
        self.nodes = nodes
        self.dirty = set()
        self.slot_name.delete(0, "end")
        self.refresh()
        self.refresh_forms()
        self.set_status(os.path.basename(path) + "   |   " + path)

    def reload(self):
        if not self.src:
            return
        if self.dirty and not messagebox.askokcancel("Revert", "Drop %d unsaved change(s)?" % len(self.dirty)):
            return
        self.load(self.src)

    # --------------------------------------------------------------- reading

    def get(self, entry, path, default=None):
        try:
            parent, key = core.walk(self.nodes[entry], path)
            return core.plain(core.child(parent, key))
        except Exception:
            return default

    def set_field(self, entry, sub, value):
        if entry not in self.nodes:
            raise KeyError("this save has no %r entry" % entry)
        core.set_path(self.nodes[entry], sub, value)
        packed = core.pack(self.nodes[entry])
        self.nodes[entry] = core.Reader(packed).read()  # proves it is valid msgpack
        self.dirty.add("%s.%s" % (entry, sub))

    def after_bulk(self, msg):
        self.refresh()
        self.refresh_forms()
        self.set_status(msg)

    # ------------------------------------------------------------ form state

    def refresh_forms(self):
        """Push current file values into every widget (no callbacks fire)."""
        if not self.src:
            return
        if "save" in self.nodes:
            self.money_save.delete(0, "end")
            self.money_save.insert(0, str(self.get("save", "bank.currentTotalScore", 0)))
        if "endless" in self.nodes:
            self.money_end.delete(0, "end")
            self.money_end.insert(0, str(self.get("endless", "bank.currentTotalScore", 0)))
            secs = float(self.get("endless", "playTimeSeconds", 0) or 0)
            self.play_hours.delete(0, "end")
            self.play_hours.insert(0, "%.2f" % (secs / 3600.0))
            self.visit_var.delete(0, "end")
            self.visit_var.insert(0, str(self.get("endless", "shop.visitCount", 0)))
        if "meta" in self.nodes:
            self.rank_var.delete(0, "end")
            self.rank_var.insert(0, str(self.get("meta", "endlessRank", 0)))
            owners = self.get("meta", "characterOwners", []) or []
            self.owners_var.delete(0, "end")
            self.owners_var.insert(0, ", ".join(str(o) for o in owners))
            if not self.slot_name.get().strip():
                self.slot_name.insert(0, self.default_slot_name())
        for ch in CHARS:
            for x in UPGRADE_CAP:
                self.up_vars[(ch, x)].set(str(self.upgrade_level(ch, x)))
        self.refresh_items()
        self.refresh_collection()

    def default_slot_name(self):
        if not self.src:
            return "save mod"
        base = os.path.splitext(os.path.basename(self.src))[0]
        base = base.replace(" - edited", "").strip()
        # never default to a stock slot name: those are the player's real saves
        if re.match(r"^save \d", base, re.I):
            return "save mod"
        return base or "save mod"

    def refresh_items(self):
        if not self.src:
            return
        self.item_list.delete(0, "end")
        entry = self.item_target.get()
        if entry not in ("save", "endless") or entry not in self.nodes:
            entry = "save" if "save" in self.nodes else self.order[0]
        pool = self.pool_key()
        grouped = self.group(self.pool_pairs(entry, pool))
        for item_id, count, bat in grouped:
            self.item_list.insert("end", "%-22s x%-3d %-8s %s"
                                  % (item_id, count, ("%g" % bat), pretty(item_id)))
        total = sum(c for _, c, _ in grouped)
        self.item_info.configure(
            text="%s / %s: %d entries, %d kinds   |   the game sums every pool, so a stack "
                 "here plus the same stack in another pool counts twice"
                 % (entry, self.pool_label(), total, len(grouped)))

    def refresh_collection(self):
        if not self.src or "collection" not in self.nodes:
            return
        value = int(self.get("collection", "monsterFigurines", 0) or 0)
        self.fig_raw.delete(0, "end")
        self.fig_raw.insert(0, str(value))
        for bit, var in enumerate(self.fig_vars):
            var.set(bool(value & (1 << bit)))
        board = self.get("collection", "bestBoardDifficulties", []) or []
        self.board_text.delete("1.0", "end")
        self.board_text.insert("1.0", "\n".join("%s %s" % (b.get("boardKey"), b.get("difficulty"))
                                                for b in board))
        rules = self.get("collection", "completedHouseRuleIds", []) or []
        self.rule_text.delete("1.0", "end")
        self.rule_text.insert("1.0", "\n".join(rules))
        furn = []
        if "save" in self.nodes:
            furn = (self.get("save", "furnitures.furnitures", []) or [])
        self.furn_var.delete(0, "end")
        self.furn_var.insert(0, ", ".join(str(f) for f in furn))
        self.skin_var.delete(0, "end")
        self.skin_var.insert(0, str(self.get("save", "gameRoomSkinIndex", 0)))

    # ------------------------------------------------------------- money etc.

    def money_value(self, value):
        value = int(value)
        if value < 0:
            raise ValueError("money cannot be negative")
        if value > 32767 and not self.money_wide.get():
            raise ValueError("money above 32767 is stored as a wider integer; "
                             "tick 'allow values above 32767' first")
        return value

    def apply_money(self):
        if not self.src:
            return
        # validate both boxes first so a rejected number cannot half-apply
        save_v = self.money_value(as_int(self.money_save.get())) if "save" in self.nodes else None
        end_v = self.money_value(as_int(self.money_end.get())) if "endless" in self.nodes else None
        if save_v is not None:
            self.set_field("save", "bank.currentTotalScore", save_v)
        if end_v is not None:
            self.set_field("endless", "bank.currentTotalScore", end_v)
            if "meta" in self.nodes:
                self.set_field("meta", "score", end_v)
        self.after_bulk("money applied")

    def set_all_money(self, value):
        if not self.src:
            return
        if value > 32767:
            self.money_wide.set(True)  # the button asked for it explicitly
        value = self.money_value(value)
        for entry in ("save", "endless", "meta"):
            if entry in self.nodes:
                sub = "score" if entry == "meta" else "bank.currentTotalScore"
                self.set_field(entry, sub, value)
        self.after_bulk("money = %d in save, endless and the slot menu" % value)

    def apply_playtime(self):
        if not self.src:
            return
        hours = float(str(self.play_hours.get()).replace(",", "."))
        secs = int(round(hours * 3600))
        if "endless" in self.nodes:
            self.set_field("endless", "playTimeSeconds", float(secs))
        if "meta" in self.nodes:
            self.set_field("meta", "playTimeSeconds", secs)
        self.after_bulk("playtime = %.2f h" % hours)

    def apply_rank(self):
        if not self.src:
            return
        rank = as_int(self.rank_var.get())
        if "meta" in self.nodes:
            self.set_field("meta", "endlessRank", rank)
        if "collection" in self.nodes:
            self.set_field("collection", "bestEndlessRank", rank)
        self.after_bulk("endless rank = %d" % rank)

    def apply_visits(self):
        if not self.src or "endless" not in self.nodes:
            return
        visits = as_int(self.visit_var.get())
        self.set_field("endless", "shop.visitCount", visits)
        if "meta" in self.nodes:
            self.set_field("meta", "roundIndex", visits)
        self.after_bulk("shop visits = %d" % visits)

    def apply_owners(self):
        if not self.src or "meta" not in self.nodes:
            return
        ids = [as_int(p) for p in self.owners_var.get().split(",") if p.strip()]
        if not ids:
            raise ValueError("need at least one SteamID (0 = empty slot)")
        if len(ids) > 8:
            raise ValueError("the array holds 8 characters, got %d values" % len(ids))
        self.set_field("meta", "characterOwners", ids)
        self.after_bulk("character owners written")

    # -------------------------------------------------------------- upgrades

    def upgrade_level(self, ch, x, entry="save"):
        for row in self.get(entry, "upgrades.%sUpgrades" % ch, []) or []:
            if row.get("x") == x:
                return int(row.get("y") or 0)
        return 0

    def write_levels(self, ch, levels):
        keep_by_entry = {}
        for entry in ("save", "endless"):
            if entry not in self.nodes:
                continue
            keep = [r for r in (self.get(entry, "upgrades.%sUpgrades" % ch, []) or [])
                    if r.get("x") not in UPGRADE_CAP]
            keep_by_entry[entry] = keep
            rows = [{"x": x, "y": int(levels[x])} for x in sorted(levels) if levels[x] > 0]
            self.set_field(entry, "upgrades.%sUpgrades" % ch, rows + keep)

    def apply_upgrade(self, ch, x):
        var = self.up_vars[(ch, x)]
        wanted = as_int(var.get() or 0)
        wanted = max(0, min(UPGRADE_CAP[x], wanted))
        levels = {ux: self.upgrade_level(ch, ux) for ux in UPGRADE_CAP}
        levels[x] = wanted
        self.write_levels(ch, levels)
        var.set(str(wanted))
        self.refresh()
        self.set_status("%s %s = %d" % (ch.capitalize(), UPGRADE_NAME[x], wanted))

    def set_character_max(self, ch):
        self.write_levels(ch, {x: cap for x, _, cap in UPGRADES})

    def max_all_upgrades(self):
        if not self.src:
            return
        for ch in CHARS:
            self.set_character_max(ch)
        self.after_bulk("all 8 characters maxed: Health 8, Stamina 7, Speed 5, Slots 2")

    def zero_all_upgrades(self):
        if not self.src:
            return
        for ch in CHARS:
            self.write_levels(ch, {x: 0 for x in UPGRADE_CAP})
        self.after_bulk("all upgrades set to 0")

    # ----------------------------------------------------------------- items

    def pool_key(self):
        label = self.item_pool.get()
        return "rack" if label == "Rack" else int(label.split()[-1]) - 1

    def pool_label(self):
        return "rack" if self.pool_key() == "rack" else "player %d" % (self.pool_key() + 1)

    @staticmethod
    def pool_path(pool):
        return "gameRoomTools" if pool == "rack" else "playerInventories[%d]" % pool

    def pool_pairs(self, entry, pool):
        base = self.pool_path(pool)
        ids = self.get(entry, base + ".itemStringIds", []) or []
        bats = self.get(entry, base + ".itemBatteryLevels", []) or []
        if len(bats) < len(ids):
            bats = list(bats) + [battery_for(i) for i in ids[len(bats):]]
        if len(bats) > len(ids):
            bats = bats[:len(ids)]
        return [(i, float(b)) for i, b in zip(ids, bats)]

    @staticmethod
    def group(pairs):
        """[(id, battery)] -> [(id, count, battery)] keeping first-seen order."""
        counts, first, order = {}, {}, []
        for item_id, bat in pairs:
            if item_id not in counts:
                order.append(item_id)
                first[item_id] = bat
            counts[item_id] = counts.get(item_id, 0) + 1
        return [(i, counts[i], first[i]) for i in order]

    def write_pool(self, entry, pool, pairs):
        base = self.pool_path(pool)
        self.set_field(entry, base + ".itemStringIds", [i for i, _ in pairs])
        self.set_field(entry, base + ".itemBatteryLevels", [b for _, b in pairs])

    def targets(self):
        want = self.item_target.get()
        if want == "both":
            return [e for e in ("save", "endless") if e in self.nodes]
        return [want] if want in self.nodes else []

    def add_item(self):
        if not self.src:
            return
        item_id = self.item_pick.get().strip()
        if not item_id:
            raise ValueError("pick an item first")
        count = int(self.item_count.get() or 1)
        bat = float(str(self.item_bat.get()).replace(",", ".") or battery_for(item_id))
        pool = self.pool_key()
        for entry in self.targets():
            self.write_pool(entry, pool, self.pool_pairs(entry, pool) + [(item_id, bat)] * count)
        self.after_bulk("added %d x %s to %s" % (count, item_id, self.pool_label()))

    def remove_item(self):
        if not self.src:
            return
        sel = self.item_list.curselection()
        if not sel:
            raise ValueError("select a row in the list first")
        line = self.item_list.get(sel[0])
        item_id = line.split()[0]
        pool = self.pool_key()
        for entry in self.targets():
            pairs = [(i, b) for i, b in self.pool_pairs(entry, pool) if i != item_id]
            self.write_pool(entry, pool, pairs)
        self.after_bulk("removed every %s from %s" % (item_id, self.pool_label()))

    def reset_batteries(self):
        if not self.src:
            return
        pool = self.pool_key()
        for entry in self.targets():
            pairs = [(i, battery_for(i)) for i, _ in self.pool_pairs(entry, pool)]
            self.write_pool(entry, pool, pairs)
        self.after_bulk("batteries reset to defaults in %s" % self.pool_label())

    def clear_pool(self):
        if not self.src:
            return
        pool = self.pool_key()
        for entry in self.targets():
            self.write_pool(entry, pool, [])
        self.after_bulk("cleared %s" % self.pool_label())

    def clear_items(self):
        if not self.src:
            return
        for entry in [e for e in ("save", "endless") if e in self.nodes]:
            for pool in (0, 1, 2, "rack"):
                self.write_pool(entry, pool, [])
        self.after_bulk("every inventory pool cleared")

    def fill_items(self, pairs, label):
        """Put exactly `pairs` in player 1, empty the other pools, both entries."""
        if not self.src:
            return
        for entry in [e for e in ("save", "endless") if e in self.nodes]:
            self.write_pool(entry, 0, pairs)
            for pool in (1, 2, "rack"):
                self.write_pool(entry, pool, [])
        self.after_bulk(label)

    def give_everything(self):
        self.fill_items([(i, battery_for(i)) for i in ALL_ITEMS],
                        "gave one of all %d items to player 1; other pools cleared" % len(ALL_ITEMS))

    def give_loadout(self):
        pairs = []
        for item_id, bat, count in LOADOUT:
            pairs += [(item_id, bat)] * count
        self.fill_items(pairs, "gave the 2/3/5/5/5 loadout to player 1; other pools cleared")

    # ------------------------------------------------------------ collection

    def apply_figurines(self):
        value = 0
        for bit, var in enumerate(self.fig_vars):
            if var.get():
                value |= 1 << bit
        self.set_field("collection", "monsterFigurines", value)
        self.after_bulk("figurines = %d" % value)

    def apply_figurines_raw(self):
        value = as_int(self.fig_raw.get())
        self.set_field("collection", "monsterFigurines", value)
        self.after_bulk("figurines = %d" % value)

    def max_figurines(self):
        value = (1 << FIGURINE_BITS) - 1
        self.set_field("collection", "monsterFigurines", value)
        self.after_bulk("figurines = %d (all %d bits)" % (value, FIGURINE_BITS))

    def apply_boards(self, text):
        keys, rows, seen = [], [], set()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            key = parts[0]
            if key in seen:
                continue
            seen.add(key)
            diff = int(parts[1]) if len(parts) > 1 else 5
            diff = max(1, min(5, diff))
            keys.append(key)
            rows.append({"boardKey": key, "difficulty": diff})
        self.set_field("collection", "completedBoardKeys", keys)
        self.set_field("collection", "bestBoardDifficulties", rows)
        return len(keys)

    def apply_boards_ui(self):
        n = self.apply_boards(self.board_text.get("1.0", "end"))
        self.after_bulk("%d completed boards written" % n)

    def apply_rules(self, text):
        ids = [line.strip() for line in text.splitlines() if line.strip()]
        self.set_field("collection", "completedHouseRuleIds", ids)
        return len(ids)

    def apply_rules_ui(self):
        n = self.apply_rules(self.rule_text.get("1.0", "end"))
        self.after_bulk("%d house rules written" % n)

    def apply_furniture(self):
        raw = self.furn_var.get().strip()
        ids = [as_int(p) for p in raw.split(",") if p.strip()]
        for entry in [e for e in ("save", "endless") if e in self.nodes]:
            self.set_field(entry, "furnitures.furnitures", ids)
        self.after_bulk("furniture = %r" % (ids,))

    def apply_skin(self):
        value = as_int(self.skin_var.get())
        for entry in [e for e in ("save", "endless") if e in self.nodes]:
            self.set_field(entry, "gameRoomSkinIndex", value)
        self.after_bulk("room skin = %d" % value)

    # ------------------------------------------------------------------- tree

    def refresh(self):
        if not self.src:
            return
        sel = self.tree.selection()
        keep = sel[0] if sel else None
        self.tree.delete(*self.tree.get_children())
        want = self.filter.get()
        needle = self.search.get().strip().lower()
        for name in self.order:
            if want != "all" and want != name:
                continue
            added = self.add_node(name, name, self.nodes[name], None, needle)
            if added:
                self.tree.item(name, open=True)
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)
        self.set_status(os.path.basename(self.src) if self.src else "empty")

    def add_node(self, entry, path, node, parent, needle):
        """Insert node if it or a descendant matches the filter. Returns rows added."""
        label = path.split(".")[-1] if path != entry else entry
        kids = []
        if node.kind == "map":
            kids = [(k.value, v) for k, v in node.value]
        elif node.kind == "arr":
            kids = [(i, v) for i, v in enumerate(node.value)]

        if needle and needle not in path.lower() and needle not in preview(node).lower():
            if not any(self.matches(sub, v, needle) for sub, v in kids):
                return False

        tags = []
        if path in self.dirty:
            tags.append("dirty")
        if path == entry:
            tags.append("entry")
        self.tree.insert(parent or "", "end", iid=path,
                         text=("  " + label) if parent else label,
                         values=(preview(node), fmt_name(node)), tags=tags)
        for sub, child in kids:
            self.add_node(entry, "%s.%s" % (path, sub) if node.kind == "map" else "%s[%d]" % (path, sub),
                          child, path, needle)
        return True

    def matches(self, sub, node, needle):
        if needle in str(sub).lower() or needle in preview(node).lower():
            return True
        if node.kind == "map":
            return any(self.matches(k.value, v, needle) for k, v in node.value)
        if node.kind == "arr":
            return any(self.matches(i, v, needle) for i, v in enumerate(node.value))
        return False

    def set_open(self, state):
        def walk(iid):
            for child in self.tree.get_children(iid):
                walk(child)
            if iid:
                self.tree.item(iid, open=state)
        walk("")

    def on_double(self, event=None):
        if event is not None:
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
        iid = self.tree.focus()
        if not iid:
            return
        entry, _, sub = iid.partition(".")
        if not sub or entry not in self.nodes:
            return
        try:
            parent, key = core.walk(self.nodes[entry], sub)
            node = core.child(parent, key)
        except Exception:
            return
        EditDialog(self.root, iid, node, self.apply_edit)

    def apply_edit(self, path, value):
        entry, _, sub = path.partition(".")
        self.set_field(entry, sub, value)
        self.refresh()
        self.refresh_forms()
        self.set_status("set " + path)

    def apply_preset(self, filename):
        if not self.src:
            messagebox.showinfo("No save", "Open a .sav first.")
            return
        with open(os.path.join(PRESET_DIR, filename), "r", encoding="utf-8-sig") as f:
            patch = json.load(f)
        changed = []
        for path, value in patch.items():
            entry, _, sub = path.partition(".")
            if entry not in self.nodes or not sub:
                continue
            try:
                self.set_field(entry, sub, value)
                changed.append(path)
            except Exception as exc:
                messagebox.showerror("Preset", "%s\n%s" % (path, exc))
                return
        self.after_bulk("preset %s: %d field(s) changed" % (filename, len(changed)))

    # ---------------------------------------------------------------- sanity

    def warnings(self):
        """Cross-field rules observed in real saves. Returns list of strings."""
        try:
            return self._warnings()
        except Exception as exc:  # never let a sanity check block editing
            return ["consistency check failed: %r" % (exc,)]

    def _warnings(self):
        out = []
        p = {n: core.plain(v) for n, v in self.nodes.items()}
        for entry in ("save", "endless"):
            if entry not in p:
                continue
            run = p[entry]
            for key in ("gameRoomTools",):
                self._pair_check(out, entry, key, run.get(key) or {})
            for i, inv in enumerate(run.get("playerInventories") or []):
                self._pair_check(out, entry, "playerInventories[%d]" % i, inv or {})
            shop = run.get("shop") or {}
            a = shop.get("completedBoardsAnchorIndexes") or []
            b = shop.get("completedBoardsConfigurations") or []
            c = shop.get("completedMonsterRounds") or []
            if len({len(a), len(b), len(c)}) > 1:
                out.append("%s.shop: completed* arrays disagree (%d/%d/%d) - the game fills "
                           "them in lockstep" % (entry, len(a), len(b), len(c)))
            uids = shop.get("shopItemsUIDs") or []
            infos = shop.get("shopItemInfos") or []
            if len(uids) != len(infos):
                out.append("%s.shop: shopItemsUIDs (%d) vs shopItemInfos (%d)"
                           % (entry, len(uids), len(infos)))
        meta, end = p.get("meta"), p.get("endless")
        if meta and end:
            if meta.get("score") != end["bank"].get("currentTotalScore"):
                out.append("meta.score (%s) != endless.bank.currentTotalScore (%s) - "
                           "the slot menu will show the old score"
                           % (meta.get("score"), end["bank"].get("currentTotalScore")))
            if meta.get("roundIndex") != end["shop"].get("visitCount"):
                out.append("meta.roundIndex (%s) != endless.shop.visitCount (%s)"
                           % (meta.get("roundIndex"), end["shop"].get("visitCount")))
            if meta.get("playTimeSeconds") != int(end.get("playTimeSeconds") or 0):
                out.append("meta.playTimeSeconds (%s) != endless.playTimeSeconds (%s)"
                           % (meta.get("playTimeSeconds"), int(end.get("playTimeSeconds") or 0)))
        col = p.get("collection")
        if col and meta and col.get("bestEndlessRank") != meta.get("endlessRank"):
            out.append("collection.bestEndlessRank (%s) != meta.endlessRank (%s)"
                       % (col.get("bestEndlessRank"), meta.get("endlessRank")))
        if meta and "save" in p:
            n = len(p["save"].get("playerInventories") or [])
            if meta.get("playerCount") and n != meta["playerCount"]:
                out.append("meta.playerCount (%s) vs save.playerInventories entries (%d)"
                           % (meta.get("playerCount"), n))
        return out

    @staticmethod
    def _pair_check(out, entry, key, container):
        ids = container.get("itemStringIds")
        lv = container.get("itemBatteryLevels")
        if ids is None or lv is None:
            return
        if len(ids) != len(lv):
            out.append("%s.%s: itemStringIds (%d) vs itemBatteryLevels (%d) - "
                       "parallel arrays must match" % (entry, key, len(ids), len(lv)))

    def show_warnings(self):
        w = self.warnings()
        if not w:
            messagebox.showinfo("Consistency check", "No inconsistencies found.")
            return
        messagebox.showwarning("Consistency check", "\n\n".join("- " + x for x in w))

    # ------------------------------------------------------------------ disk

    def open_dialog(self):
        p = filedialog.askopenfilename(
            title="Open Invokyr save",
            initialdir=os.path.dirname(self.src) if self.src else SAVE_DIR,
            filetypes=[("Invokyr save", "*.sav"), ("All files", "*.*")])
        if p:
            if self.dirty and not messagebox.askokcancel("Open", "Drop unsaved changes?"):
                return
            self.load(p)

    def save_dialog(self):
        if not self.src:
            return
        folder = os.path.dirname(self.src)
        base = os.path.splitext(os.path.basename(self.src))[0]
        p = filedialog.asksaveasfilename(
            title="Save as", initialdir=folder, defaultextension=".sav",
            initialfile=base + " - edited.sav",
            filetypes=[("Invokyr save", "*.sav")])
        if p:
            self.guard(self.write, p)
            if not self.quiet and os.path.exists(p):
                if messagebox.askyesno("Saved", "%s\n\nOpen the containing folder?" % p):
                    os.startfile(os.path.dirname(p))

    def save_into_game(self):
        if not self.src:
            return
        name = re.sub(r'[\\/:*?"<>|]+', "_", (self.slot_name.get() or "").strip()).strip(" .")
        name = name or "save mod"
        dst = os.path.join(SAVE_DIR, name + ".sav")
        if self.game_running():
            messagebox.showerror("Game is running",
                                 "Close Invokyr before writing into its saves folder.")
            return
        if os.path.exists(dst):
            if not messagebox.askyesno("Overwrite?",
                                       "%s\n\nalready exists. A copy of it is kept as a backup. Continue?"
                                       % dst):
                return
            self.backup_file(dst)
        self.guard(self.write, dst)
        self.set_status("wrote " + dst)

    @staticmethod
    def backup_file(path):
        folder = os.path.join(os.path.dirname(path), "_backup_" + time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(path, os.path.join(folder, os.path.basename(path)))

    def write(self, dst):
        data = []
        for name in self.order:
            packed = core.pack(self.nodes[name])
            core.Reader(packed).read()
            data.append((name, packed))
        core.write_sav(dst, data)
        back = {n: core.plain(core.Reader(d).read()) for n, d in core.read_sav(dst)}
        ref = {n: core.plain(self.nodes[n]) for n in self.order}
        if back != ref:
            raise RuntimeError("verification failed: %s does not read back identical" % dst)
        self.dirty = set()
        self.refresh()
        self.set_status("wrote %s  (verified)" % dst)
        self.src_slot_sync(dst)
        return dst

    def src_slot_sync(self, dst):
        """Keep the slot-name field pointing at the file we just wrote."""
        self.slot_name.delete(0, "end")
        self.slot_name.insert(0, os.path.splitext(os.path.basename(dst))[0])

    def backup(self):
        if not self.src:
            return
        src_dir = os.path.dirname(self.src)
        dst = os.path.join(os.path.dirname(src_dir),
                           "saves_backup_" + time.strftime("%Y%m%d_%H%M%S"))
        try:
            shutil.copytree(src_dir, dst)
        except Exception as exc:
            messagebox.showerror("Backup failed", str(exc))
            return
        self.set_status("backup: " + dst)


def edit_via_dialog(root, app, path, new_text):
    entry, _, sub = path.partition(".")
    parent, key = core.walk(app.nodes[entry], sub)
    node = core.child(parent, key)
    dlg = EditDialog(root, path, node, app.apply_edit)
    dlg.box.delete("1.0", "end")
    dlg.box.insert("1.0", new_text)
    dlg.commit()
    return dlg


def dialog_closed(dlg):
    try:
        return not dlg.winfo_exists()
    except tk.TclError:
        return True


def selftest(root, app):
    """Drive the real widgets without opening modal dialogs."""
    assert app.nodes, "nothing loaded"
    rows = app.tree.get_children("")
    assert rows, "tree empty"
    app.tree.item(rows[0], open=True)

    # scalar edit through the API
    app.set_field("save", "bank.currentTotalScore", 424242)
    assert "save.bank.currentTotalScore" in app.dirty
    assert core.plain(app.nodes["save"])["bank"]["currentTotalScore"] == 424242

    # money changer writes save + endless + the slot-menu mirror
    app.money_save.delete(0, "end")
    app.money_save.insert(0, "1234")
    app.money_end.delete(0, "end")
    app.money_end.insert(0, "5678")
    app.apply_money()
    p = {n: core.plain(v) for n, v in app.nodes.items()}
    assert p["save"]["bank"]["currentTotalScore"] == 1234
    assert p["endless"]["bank"]["currentTotalScore"] == 5678
    assert p["meta"]["score"] == 5678, "slot menu mirror not updated"

    app.set_all_money(99999)
    p = {n: core.plain(v) for n, v in app.nodes.items()}
    assert p["save"]["bank"]["currentTotalScore"] == 99999
    assert p["meta"]["score"] == 99999

    app.owners_var.delete(0, "end")
    app.owners_var.insert(0, "0, 76561190000000000, 0, 0, 0, 0, 0, 0")
    app.apply_owners()
    assert core.plain(app.nodes["meta"])["characterOwners"][1] == 76561190000000000

    # money above the int16 range needs the explicit opt-in, and must not half-apply
    app.money_wide.set(False)
    app.money_save.delete(0, "end")
    app.money_save.insert(0, "111")
    app.money_end.delete(0, "end")
    app.money_end.insert(0, "40000")
    try:
        app.apply_money()
    except ValueError as exc:
        assert "32767" in str(exc), exc
    else:
        raise AssertionError("wide money accepted without the checkbox")
    assert core.plain(app.nodes["save"])["bank"]["currentTotalScore"] == 99999, "half-applied"
    app.money_wide.set(True)
    app.apply_money()
    assert core.plain(app.nodes["meta"])["score"] == 40000
    app.set_all_money(99999)

    # upgrades: values, caps, and unknown types preserved
    app.write_levels("red", {1: 8, 2: 7, 3: 5, 6: 2})
    for entry in ("save", "endless"):
        got = {r["x"]: r["y"] for r in core.plain(app.nodes[entry])["upgrades"]["redUpgrades"]}
        assert got == {1: 8, 2: 7, 3: 5, 6: 2}, got
    app.up_vars[("red", 1)].set("99")
    app.apply_upgrade("red", 1)
    assert app.upgrade_level("red", 1) == 8, "cap not enforced"
    app.up_vars[("red", 1)].set("3")
    app.apply_upgrade("red", 1)
    assert app.upgrade_level("red", 1) == 3

    app.max_all_upgrades()
    for ch in CHARS:
        assert {x: app.upgrade_level(ch, x) for x in UPGRADE_CAP} == dict(
            {1: 8, 2: 7, 3: 5, 6: 2}), ch

    # items: add, group, write both entries, batteries stay parallel
    app.item_target.set("both")
    app.item_pool.set("Player 1")
    app.fill_items([], "clear")
    app.item_pick.set("dice_heal")
    app.item_count.set("3")
    app.item_bat.delete(0, "end")
    app.item_bat.insert(0, "-1")
    app.add_item()
    for entry in ("save", "endless"):
        inv = core.plain(app.nodes[entry])["playerInventories"][0]
        assert inv["itemStringIds"] == ["dice_heal"] * 3, inv["itemStringIds"]
        assert len(inv["itemBatteryLevels"]) == 3

    app.give_everything()
    inv = core.plain(app.nodes["save"])["playerInventories"][0]
    assert len(inv["itemStringIds"]) == len(ALL_ITEMS) == 43
    assert core.plain(app.nodes["save"])["gameRoomTools"]["itemStringIds"] == []

    app.give_loadout()
    grouped = {i: c for i, c, _ in app.group(app.pool_pairs("save", 0))}
    assert grouped == {"g_mask": 2, "g_fire": 3, "dice_revive": 5,
                       "dice_heal": 5, "dice_boom": 5}, grouped

    # collection
    app.max_figurines()
    assert core.plain(app.nodes["collection"])["monsterFigurines"] == (1 << 20) - 1
    n = app.apply_boards("0:8 5\n1:4 3\n")
    assert n == 2
    col = core.plain(app.nodes["collection"])
    assert col["completedBoardKeys"] == ["0:8", "1:4"]
    assert col["bestBoardDifficulties"][1]["difficulty"] == 3
    app.apply_rules("HouseRules/houserules/desc/x_1\n")

    # scalar edit through the real dialog
    dlg = edit_via_dialog(root, app, "save.shop.visitCount", "77")
    assert dialog_closed(dlg), "dialog stayed open after a valid edit"
    assert core.plain(app.nodes["save"])["shop"]["visitCount"] == 77

    # container edit (whole array) through the same dialog
    dlg = edit_via_dialog(root, app, "save.gameRoomTools.itemStringIds",
                          '["dice_heal", "lantern"]')
    assert dialog_closed(dlg), "dialog stayed open after a valid list edit"
    assert core.plain(app.nodes["save"])["gameRoomTools"]["itemStringIds"] == ["dice_heal", "lantern"]

    # invalid input must be rejected, not silently written
    before = core.plain(app.nodes["save"])["shop"]["visitCount"]
    dlg = edit_via_dialog(root, app, "save.shop.visitCount", '{"nope": 1}')
    assert not dialog_closed(dlg), "invalid edit was accepted"
    assert dlg.err.cget("text"), "no error shown for invalid edit"
    dlg.destroy()
    assert core.plain(app.nodes["save"])["shop"]["visitCount"] == before

    app.search.insert(0, "score")
    app.refresh()
    found = app.tree.exists("save.bank.currentTotalScore")
    app.search.delete(0, "end")
    app.refresh()
    assert found, "search filter broke the tree"

    # consistency rules: breaking the meta mirror must be flagged, fixing it must clear
    app.set_field("endless", "bank.currentTotalScore", 515151)
    assert any("meta.score" in w for w in app.warnings()), "meta mirror drift not detected"
    app.set_field("meta", "score", 515151)
    assert not any("meta.score" in w for w in app.warnings()), "clean state still flagged"

    # parallel arrays must match length
    app.set_field("save", "gameRoomTools.itemBatteryLevels", [-1, -1])
    assert not any("itemBatteryLevels" in w for w in app.warnings()), "false positive on arrays"
    app.set_field("save", "gameRoomTools.itemBatteryLevels", [-1])
    assert any("itemBatteryLevels" in w for w in app.warnings()), "parallel array drift not caught"
    app.set_field("save", "gameRoomTools.itemBatteryLevels", [-1, -1])

    out = os.path.join(HERE, "work", "_selftest.sav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    app.write(out)
    check = {n: core.plain(core.Reader(d).read()) for n, d in core.read_sav(out)}
    assert check["save"]["bank"]["currentTotalScore"] == 99999, "value did not survive"
    assert check["save"]["gameRoomTools"]["itemStringIds"] == ["dice_heal", "lantern"]
    assert check["meta"]["score"] == 515151
    assert check["endless"]["bank"]["currentTotalScore"] == 515151
    assert check["save"]["upgrades"]["redUpgrades"]

    app.load(out)
    assert not app.dirty, "clean reload should have no pending changes"
    os.remove(out)
    print("selftest ok: load, money, upgrades, items, collection, dialogs, filter, write, reload")


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    path = args[0] if args else DEFAULT_SAV
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    app = App(root, path)
    if "--selftest" in argv:
        app.quiet = True
        try:
            root.update()
            selftest(root, app)
        finally:
            root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
