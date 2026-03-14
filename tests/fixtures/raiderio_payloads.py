"""
tests/fixtures/raiderio_payloads.py

Realistic Raider.IO API response payloads for use across test modules.
These mirror the actual API shape so normalize_profile() can be tested
against data that looks like what the live endpoint returns.
"""

# ---------------------------------------------------------------------------
# Full happy-path response — Fire Mage, Area 52, US
# ---------------------------------------------------------------------------

MAGE_PROFILE_RAW = {
    "name": "Pyroblastus",
    "realm": "Area 52",
    "region": "us",
    "class": "Mage",
    "active_spec_name": "Fire",
    "race": "Blood Elf",
    "faction": "horde",
    "profile_url": "https://raider.io/characters/us/area-52/Pyroblastus",
    "thumbnail_url": "https://render.worldofwarcraft.com/us/character/area-52/Pyroblastus.jpg",
    "gear": {
        "item_level_equipped": 639,
        "item_level_total": 641,
    },
    "mythic_plus_scores_by_season": [
        {
            "season": "season-tww-2",
            "scores": {
                "all": 2847.3,
                "dps": 2847.3,
                "healer": 0.0,
                "tank": 0.0,
                "spec_0": 1423.6,
                "spec_1": 1204.1,
            },
        }
    ],
    "mythic_plus_highest_level_runs": [
        {
            "dungeon": "Ara-Kara, City of Echoes",
            "mythic_level": 12,
            "num_keystone_upgrades": 1,
            "score": 187.4,
        },
        {
            "dungeon": "The Stonevault",
            "mythic_level": 11,
            "num_keystone_upgrades": 2,
            "score": 175.2,
        },
        {
            "dungeon": "City of Threads",
            "mythic_level": 11,
            "num_keystone_upgrades": 1,
            "score": 172.8,
        },
    ],
    "mythic_plus_best_runs": [
        {
            "dungeon": "Ara-Kara, City of Echoes",
            "mythic_level": 12,
            "num_keystone_upgrades": 1,
            "score": 187.4,
        },
        {
            "dungeon": "The Stonevault",
            "mythic_level": 11,
            "num_keystone_upgrades": 2,
            "score": 175.2,
        },
        {
            "dungeon": "City of Threads",
            "mythic_level": 11,
            "num_keystone_upgrades": 1,
            "score": 172.8,
        },
        {"dungeon": "Grim Batol", "mythic_level": 10, "num_keystone_upgrades": 3, "score": 165.0},
        {
            "dungeon": "Siege of Boralus",
            "mythic_level": 10,
            "num_keystone_upgrades": 1,
            "score": 154.3,
        },
        {
            "dungeon": "Mists of Tirna Scithe",
            "mythic_level": 9,
            "num_keystone_upgrades": 2,
            "score": 143.7,
        },
    ],
    "raid_progression": {
        "nerub-ar-palace": {
            "summary": "9/8M",
            "total_bosses": 8,
            "normal_bosses_killed": 8,
            "heroic_bosses_killed": 8,
            "mythic_bosses_killed": 8,
        }
    },
}


# ---------------------------------------------------------------------------
# Minimal response — character with zero M+ activity this season
# ---------------------------------------------------------------------------

WARRIOR_NO_PLUS_RAW = {
    "name": "Smashhardus",
    "realm": "Stormrage",
    "region": "us",
    "class": "Warrior",
    "active_spec_name": "Arms",
    "race": "Human",
    "faction": "alliance",
    "profile_url": "https://raider.io/characters/us/stormrage/Smashhardus",
    "thumbnail_url": "",
    "gear": {
        "item_level_equipped": 580,
        "item_level_total": 583,
    },
    "mythic_plus_scores_by_season": [],  # no scores this season
    "mythic_plus_highest_level_runs": [],
    "mythic_plus_best_runs": [],
    "raid_progression": {},
}


# ---------------------------------------------------------------------------
# Response with missing/null optional fields (API schema drift simulation)
# ---------------------------------------------------------------------------

SPARSE_PROFILE_RAW = {
    "name": "Ghostchar",
    "realm": "Illidan",
    "region": "us",
    # class, spec, race, faction absent
    "gear": None,
    "mythic_plus_scores_by_season": None,
    "mythic_plus_highest_level_runs": None,
    "mythic_plus_best_runs": None,
    "raid_progression": None,
}


# ---------------------------------------------------------------------------
# Error response bodies
# ---------------------------------------------------------------------------

CHARACTER_NOT_FOUND_BODY = {
    "statusCode": 400,
    "error": "Bad Request",
    "message": "Could not find character with name/realm/region combination.",
}

SERVER_ERROR_BODY = {
    "statusCode": 500,
    "error": "Internal Server Error",
    "message": "Something went wrong.",
}
