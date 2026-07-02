"""
Major raid cooldowns tracked for the timeline display.

Spell IDs sourced from WoWAnalyzer and Wowhead. Verify flagged entries (# ?)
on major patches. Icon names resolve via: https://wow.zamimg.com/images/wow/icons/medium/{icon}.jpg
"""

# WoW class colors (official)
CLASS_COLORS = {
    "Death Knight":  "#C41E3A",
    "Demon Hunter":  "#A330C9",
    "Druid":         "#FF7C0A",
    "Evoker":        "#33937F",
    "Hunter":        "#AAD372",
    "Mage":          "#3FC7EB",
    "Monk":          "#00FF98",
    "Paladin":       "#F48CBA",
    "Priest":        "#FFFFFF",
    "Rogue":         "#FFF468",
    "Shaman":        "#0070DD",
    "Warlock":       "#8788EE",
    "Warrior":       "#C69B3A",
}

# category values used for UI filtering:
#   healer_cd  — major healing throughput cooldowns
#   external   — single-target defensive casts on others
#   tank_cd    — tank personal defensives
#   raid_cd    — raid-wide defensive (e.g. AMZ, Darkness, Rallying Cry)
#   dps_cd     — major offensive damage cooldowns
#   utility    — lust, battle rez, movement, etc.

TRACKED_SPELLS = {

    # ── HEALER COOLDOWNS ────────────────────────────────────────────────────

    # Discipline Priest
    62618:  {"name": "Power Word: Barrier", "class": "Priest",       "spec": "Discipline", "category": "healer_cd", "icon": "spell_holy_powerwordbarrier"},
    246287: {"name": "Evangelism",          "class": "Priest",       "spec": "Discipline", "category": "healer_cd", "icon": "spell_holy_divineillumination"},
    421453: {"name": "Ultimate Penitence",  "class": "Priest",       "spec": "Discipline", "category": "healer_cd", "icon": "ability_priest_ascendance"},

    # Holy Priest
    64843:  {"name": "Divine Hymn",         "class": "Priest",       "spec": "Holy",       "category": "healer_cd", "icon": "spell_holy_divinehymn"},
    265339: {"name": "Holy Word: Salvation","class": "Priest",       "spec": "Holy",       "category": "healer_cd", "icon": "spell_holy_restoration"},           # ?
    200183: {"name": "Apotheosis",          "class": "Priest",       "spec": "Holy",       "category": "healer_cd", "icon": "ability_priest_apotheosis"},

    # Restoration Druid
    740:    {"name": "Tranquility",         "class": "Druid",        "spec": "Restoration","category": "healer_cd", "icon": "spell_nature_tranquility"},
    33891:  {"name": "Incarnation: Tree of Life", "class": "Druid",  "spec": "Restoration","category": "healer_cd", "icon": "ability_druid_treeoflife"},
    197721: {"name": "Flourish",            "class": "Druid",        "spec": "Restoration","category": "healer_cd", "icon": "spell_druid_flourish"},
    391528: {"name": "Convoke the Spirits", "class": "Druid",        "spec": "Restoration","category": "healer_cd", "icon": "ability_ardenweald_druid"},

    # Restoration Shaman
    108280: {"name": "Healing Tide Totem",  "class": "Shaman",       "spec": "Restoration","category": "healer_cd", "icon": "ability_shaman_healingtide"},
    98008:  {"name": "Spirit Link Totem",   "class": "Shaman",       "spec": "Restoration","category": "healer_cd", "icon": "spell_shaman_spiritlink"},
    114052: {"name": "Ascendance",          "class": "Shaman",       "spec": "Restoration","category": "healer_cd", "icon": "spell_fire_elementaldevastation"},
    207399: {"name": "Ancestral Protection Totem", "class": "Shaman","spec": "Restoration","category": "healer_cd", "icon": "spell_nature_reincarnation"},

    # Holy Paladin
    31821:  {"name": "Aura Mastery",        "class": "Paladin",      "spec": "Holy",       "category": "healer_cd", "icon": "spell_holy_auramastery"},
    105809: {"name": "Holy Avenger",        "class": "Paladin",      "spec": "Holy",       "category": "healer_cd", "icon": "ability_paladin_holyavenger"},
    304971: {"name": "Divine Toll",         "class": "Paladin",      "spec": "Holy",       "category": "healer_cd", "icon": "ability_bastion_paladin"},           # ? (was Kyrian 304971, talent may differ)

    # Mistweaver Monk
    115310: {"name": "Revival",             "class": "Monk",         "spec": "Mistweaver", "category": "healer_cd", "icon": "spell_monk_revival"},
    322118: {"name": "Invoke Yu'lon",       "class": "Monk",         "spec": "Mistweaver", "category": "healer_cd", "icon": "ability_monk_dragonkick"},           # ? icon
    325197: {"name": "Invoke Chi-Ji",       "class": "Monk",         "spec": "Mistweaver", "category": "healer_cd", "icon": "ability_monk_chiburst"},             # ? icon

    # Preservation Evoker
    363534: {"name": "Rewind",              "class": "Evoker",       "spec": "Preservation","category": "healer_cd","icon": "ability_evoker_rewind"},
    359816: {"name": "Dream Flight",        "class": "Evoker",       "spec": "Preservation","category": "healer_cd","icon": "ability_evoker_dreamflight"},
    370537: {"name": "Stasis",              "class": "Evoker",       "spec": "Preservation","category": "healer_cd","icon": "ability_evoker_stasis"},

    # ── EXTERNALS ────────────────────────────────────────────────────────────

    33206:  {"name": "Pain Suppression",    "class": "Priest",       "spec": "Discipline", "category": "external",  "icon": "spell_holy_painsupression"},
    47788:  {"name": "Guardian Spirit",     "class": "Priest",       "spec": "Holy",       "category": "external",  "icon": "spell_holy_guardianspirit"},
    10060:  {"name": "Power Infusion",      "class": "Priest",       "spec": "Discipline", "category": "external",  "icon": "spell_holy_powerinfusion"},
    102342: {"name": "Ironbark",            "class": "Druid",        "spec": "Restoration","category": "external",  "icon": "spell_druid_ironbark"},
    6940:   {"name": "Blessing of Sacrifice","class": "Paladin",     "spec": "Holy",       "category": "external",  "icon": "spell_holy_sealofsacrifice"},
    1022:   {"name": "Blessing of Protection","class": "Paladin",    "spec": None,         "category": "external",  "icon": "spell_holy_sealofprotection"},
    204018: {"name": "Blessing of Spellwarding","class": "Paladin",  "spec": None,         "category": "external",  "icon": "spell_holy_blessingofprotection"},   # ?

    # ── TANK COOLDOWNS ───────────────────────────────────────────────────────

    # Protection Warrior
    871:    {"name": "Shield Wall",         "class": "Warrior",      "spec": "Protection", "category": "tank_cd",   "icon": "ability_warrior_shieldwall"},
    12975:  {"name": "Last Stand",          "class": "Warrior",      "spec": "Protection", "category": "tank_cd",   "icon": "ability_warrior_laststand"},

    # Protection Paladin
    31850:  {"name": "Ardent Defender",     "class": "Paladin",      "spec": "Protection", "category": "tank_cd",   "icon": "ability_paladin_ardentdefender"},
    86659:  {"name": "Guardian of Ancient Kings","class": "Paladin", "spec": "Protection", "category": "tank_cd",   "icon": "spell_holy_heroism"},
    642:    {"name": "Divine Shield",       "class": "Paladin",      "spec": None,         "category": "tank_cd",   "icon": "spell_holy_divineshield"},

    # Blood Death Knight
    55233:  {"name": "Vampiric Blood",      "class": "Death Knight", "spec": "Blood",      "category": "tank_cd",   "icon": "spell_shadow_lifedrain"},
    49028:  {"name": "Dancing Rune Weapon", "class": "Death Knight", "spec": "Blood",      "category": "tank_cd",   "icon": "inv_sword_07"},
    48792:  {"name": "Icebound Fortitude",  "class": "Death Knight", "spec": "Blood",      "category": "tank_cd",   "icon": "spell_deathknight_iceboundfortitude"},

    # Guardian Druid
    61336:  {"name": "Survival Instincts",  "class": "Druid",        "spec": "Guardian",   "category": "tank_cd",   "icon": "ability_druid_tigersroar"},
    102558: {"name": "Incarnation: Guardian of Ursoc","class": "Druid","spec": "Guardian", "category": "tank_cd",   "icon": "spell_druid_incarnation"},

    # Brewmaster Monk
    115203: {"name": "Fortifying Brew",     "class": "Monk",         "spec": "Brewmaster", "category": "tank_cd",   "icon": "ability_monk_fortifyingale_new"},
    115176: {"name": "Zen Meditation",      "class": "Monk",         "spec": "Brewmaster", "category": "tank_cd",   "icon": "ability_monk_zenmeditation"},

    # Vengeance Demon Hunter
    187827: {"name": "Metamorphosis",       "class": "Demon Hunter", "spec": "Vengeance",  "category": "tank_cd",   "icon": "ability_demonhunter_metamorphasistank"},
    204021: {"name": "Fiery Brand",         "class": "Demon Hunter", "spec": "Vengeance",  "category": "tank_cd",   "icon": "ability_demonhunter_fierybrand"},

    # ── RAID-WIDE DEFENSIVES ─────────────────────────────────────────────────

    97462:  {"name": "Rallying Cry",        "class": "Warrior",      "spec": "Protection", "category": "raid_cd",   "icon": "ability_warrior_rallyingcry"},
    51052:  {"name": "Anti-Magic Zone",     "class": "Death Knight", "spec": "Blood",      "category": "raid_cd",   "icon": "spell_deathknight_antimagiczone"},
    196718: {"name": "Darkness",            "class": "Demon Hunter", "spec": "Havoc",      "category": "raid_cd",   "icon": "ability_demonhunter_darkness"},
    106898: {"name": "Stampeding Roar",     "class": "Druid",        "spec": None,         "category": "raid_cd",   "icon": "spell_druid_stampedingroar_cat"},
    192077: {"name": "Wind Rush Totem",     "class": "Shaman",       "spec": "Restoration","category": "raid_cd",   "icon": "ability_shaman_windwalktotem"},
    132578: {"name": "Invoke Niuzao",       "class": "Monk",         "spec": "Brewmaster", "category": "raid_cd",   "icon": "spell_monk_brewmaster_spec"},

    # ── OFFENSIVE DPS COOLDOWNS ──────────────────────────────────────────────

    # Death Knight
    42650:  {"name": "Army of the Dead",    "class": "Death Knight", "spec": "Unholy",     "category": "dps_cd",    "icon": "spell_deathknight_armyofthedead"},

    # Havoc Demon Hunter
    162264: {"name": "Metamorphosis",       "class": "Demon Hunter", "spec": "Havoc",      "category": "dps_cd",    "icon": "ability_demonhunter_metamorphasis"},

    # Devourer Demon Hunter
    471306: {"name": "Void Metamorphosis",  "class": "Demon Hunter", "spec": "Devourer",   "category": "dps_cd",    "icon": "inv_112_ability_demonhunter_metamorphasisvoid"},

    # Druid
    194223: {"name": "Celestial Alignment", "class": "Druid",        "spec": "Balance",    "category": "dps_cd",    "icon": "ability_druid_celestialalignment"},
    102560: {"name": "Incarnation: Chosen of Elune","class": "Druid","spec": "Balance",    "category": "dps_cd",    "icon": "spell_druid_incarnation"},
    106951: {"name": "Berserk",             "class": "Druid",        "spec": "Feral",      "category": "dps_cd",    "icon": "ability_druid_berserk"},

    # Hunter
    359844: {"name": "Call of the Wild",    "class": "Hunter",       "spec": "Beast Mastery","category": "dps_cd",  "icon": "ability_hunter_callofthewild"},
    288613: {"name": "Trueshot",            "class": "Hunter",       "spec": "Marksmanship","category": "dps_cd",   "icon": "ability_hunter_markedfordeathshot"},
    360952: {"name": "Coordinated Assault", "class": "Hunter",       "spec": "Survival",   "category": "dps_cd",    "icon": "ability_hunter_coordinatedassault"},

    # Mage
    190319: {"name": "Combustion",          "class": "Mage",         "spec": "Fire",       "category": "dps_cd",    "icon": "spell_fire_sealoffire"},
    12472:  {"name": "Icy Veins",           "class": "Mage",         "spec": "Frost",      "category": "dps_cd",    "icon": "spell_frost_coldhearted"},
    365350: {"name": "Arcane Surge",        "class": "Mage",         "spec": "Arcane",     "category": "dps_cd",    "icon": "spell_arcane_arcane03"},              # ?

    # Windwalker Monk
    137639: {"name": "Storm, Earth, and Fire","class": "Monk",       "spec": "Windwalker", "category": "dps_cd",    "icon": "ability_monk_stormearth_and_fire"},
    152173: {"name": "Serenity",            "class": "Monk",         "spec": "Windwalker", "category": "dps_cd",    "icon": "ability_monk_serenity"},

    # Retribution Paladin
    31884:  {"name": "Avenging Wrath",      "class": "Paladin",      "spec": "Retribution","category": "dps_cd",    "icon": "spell_holy_avenginewrath"},
    343721: {"name": "Final Reckoning",     "class": "Paladin",      "spec": "Retribution","category": "dps_cd",    "icon": "spell_paladin_finalreckoning"},

    # Shadow Priest
    228361: {"name": "Void Eruption",       "class": "Priest",       "spec": "Shadow",     "category": "dps_cd",    "icon": "spell_priest_void-blast"},
    391109: {"name": "Dark Ascension",      "class": "Priest",       "spec": "Shadow",     "category": "dps_cd",    "icon": "spell_shadow_shadowwordpain"},        # ?

    # Rogue
    13750:  {"name": "Adrenaline Rush",     "class": "Rogue",        "spec": "Outlaw",     "category": "dps_cd",    "icon": "ability_rogue_adrenalinrush"},
    121471: {"name": "Shadow Blades",       "class": "Rogue",        "spec": "Subtlety",   "category": "dps_cd",    "icon": "inv_knife_1h_grimbatolraid_d_03"},
    360194: {"name": "Deathmark",           "class": "Rogue",        "spec": "Assassination","category": "dps_cd",  "icon": "ability_rogue_deathmark"},            # ?

    # Shaman
    114050: {"name": "Ascendance",          "class": "Shaman",       "spec": "Elemental",  "category": "dps_cd",    "icon": "spell_fire_elementaldevastation"},
    114051: {"name": "Ascendance",          "class": "Shaman",       "spec": "Enhancement","category": "dps_cd",    "icon": "spell_fire_elementaldevastation"},

    # Warlock
    1122:   {"name": "Summon Infernal",     "class": "Warlock",      "spec": "Destruction","category": "dps_cd",    "icon": "spell_shadow_summoninfernal"},
    267217: {"name": "Nether Portal",       "class": "Warlock",      "spec": "Demonology", "category": "dps_cd",    "icon": "warlock_spelldrain"},                 # ?
    113860: {"name": "Dark Soul: Misery",   "class": "Warlock",      "spec": "Affliction", "category": "dps_cd",    "icon": "spell_shadow_twilight"},              # ?

    # Warrior
    1719:   {"name": "Recklessness",        "class": "Warrior",      "spec": None,         "category": "dps_cd",    "icon": "ability_warrior_innerrage"},
    107574: {"name": "Avatar",              "class": "Warrior",      "spec": None,         "category": "dps_cd",    "icon": "warrior_talent_icon_avatar"},

    # Devastation / Augmentation Evoker
    375087: {"name": "Dragonrage",          "class": "Evoker",       "spec": "Devastation","category": "dps_cd",    "icon": "ability_evoker_dragonrage"},
    403631: {"name": "Breath of Eons",      "class": "Evoker",       "spec": "Augmentation","category": "dps_cd",   "icon": "ability_evoker_breathofeons"},        # ?

    # ── UTILITY ──────────────────────────────────────────────────────────────

    # Bloodlust / Heroism variants
    2825:   {"name": "Bloodlust",           "class": "Shaman",       "spec": None,         "category": "utility",   "icon": "spell_nature_bloodlust"},
    32182:  {"name": "Heroism",             "class": "Shaman",       "spec": None,         "category": "utility",   "icon": "ability_shaman_heroism"},
    80353:  {"name": "Time Warp",           "class": "Mage",         "spec": None,         "category": "utility",   "icon": "ability_mage_timewarp"},
    390386: {"name": "Fury of the Aspects", "class": "Evoker",       "spec": None,         "category": "utility",   "icon": "ability_evoker_furyoftheaspects"},
    264667: {"name": "Primal Rage",         "class": "Hunter",       "spec": "Beast Mastery","category": "utility",  "icon": "ability_hunter_beasttaming"},        # ? (hunter pet lust)

    # Battle resurrection
    20484:  {"name": "Rebirth",             "class": "Druid",        "spec": None,         "category": "utility",   "icon": "spell_nature_reincarnation"},
    20707:  {"name": "Soulstone",           "class": "Warlock",      "spec": None,         "category": "utility",   "icon": "spell_shadow_soulgem"},
    61999:  {"name": "Raise Ally",          "class": "Death Knight", "spec": None,         "category": "utility",   "icon": "spell_shadow_deadofnight"},
}


def get_spell(spell_id: int) -> dict | None:
    """Return spell info dict for a given ID, or None if not tracked."""
    return TRACKED_SPELLS.get(spell_id)


def get_icon_url(icon_name: str) -> str:
    """Return Wowhead CDN URL for a spell icon."""
    return f"https://wow.zamimg.com/images/wow/icons/medium/{icon_name}.jpg"


# Pre-built lookup: (class, spec) -> [spell_id, ...]
# spec=None entries (e.g. Bloodlust) appear under every spec for that class.
def _build_class_spec_index() -> dict[tuple[str, str | None], list[int]]:
    index: dict[tuple[str, str | None], list[int]] = {}
    for spell_id, info in TRACKED_SPELLS.items():
        key = (info["class"], info["spec"])
        index.setdefault(key, []).append(spell_id)
    return index

_CLASS_SPEC_INDEX = _build_class_spec_index()


def spells_for(class_name: str, spec: str | None = None) -> list[int]:
    """
    Return tracked spell IDs for a class/spec combination.

    - spells_for("Shaman", "Restoration") → Resto-specific + spec=None Shaman spells
    - spells_for("Shaman")                → all Shaman spells regardless of spec
    - spells_for("Priest", "Discipline")  → Disc + spec=None Priest spells
    """
    if spec is None:
        # All spells for the class, every spec
        return [
            sid for (cls, _), sids in _CLASS_SPEC_INDEX.items()
            if cls == class_name
            for sid in sids
        ]

    # Spec-specific + cross-spec (spec=None) entries for this class
    specific = _CLASS_SPEC_INDEX.get((class_name, spec), [])
    cross_spec = _CLASS_SPEC_INDEX.get((class_name, None), [])
    return specific + cross_spec


def spells_for_category(*categories: str) -> list[int]:
    """
    Return tracked spell IDs matching any of the given categories.

    Categories: healer_cd, external, tank_cd, raid_cd, dps_cd, utility
    Example: spells_for_category("healer_cd", "external")
    """
    cat_set = set(categories)
    return [sid for sid, info in TRACKED_SPELLS.items() if info["category"] in cat_set]
