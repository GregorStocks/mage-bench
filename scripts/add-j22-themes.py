#!/usr/bin/env python3
"""Add Jumpstart 2022 (J22) themes to jumpstart_custom.txt.

Resolves card names to set codes and collector numbers via Scryfall API,
then appends all 46 J22 themes (124 half-deck variants) to the jumpstart
text files used by the deck generation script.

Usage:
    uv run python scripts/add-j22-themes.py
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts import scryfall

# Output files
JUMPSTART_TXT = Path("Mage/src/main/resources/jumpstart/jumpstart.txt")
JUMPSTART_CUSTOM = Path(
    "Mage.Client/release/sample-decks/Jumpstart/jumpstart_custom.txt"
)

# Basic lands have fixed set/collector numbers — no Scryfall lookup needed
_BASIC_LAND_DEFAULTS: dict[str, tuple[str, str]] = {
    "Plains": ("J22", "100"),
    "Island": ("J22", "101"),
    "Snow-Covered Island": ("J22", "833"),
    "Swamp": ("J22", "104"),
    "Mountain": ("J22", "107"),
    "Forest": ("J22", "110"),
    "Wastes": ("J22", "834"),
}

# Preferred sets for card resolution (J22 first, then fallbacks)
_SET_PREFERENCE = {"J22", "JMP", "M21", "M20", "M19"}

# fmt: off
J22_THEMES: dict[str, list[list[tuple[int, str]]]] = {
    # ===== WHITE =====
    "Knights": [
        [
            (1, "Balan, Wandering Knight"), (1, "Danitha Capashen, Paragon"),
            (1, "Kwende, Pride of Femeref"), (1, "Syr Alin, the Lion's Claw"),
            (1, "Benalish Honor Guard"), (1, "Order of the Golden Cricket"),
            (1, "Skyhunter Prowler"), (1, "Chains of Custody"),
            (1, "The Circle of Loyalty"), (1, "Hero's Blade"),
            (1, "Shining Armor"), (1, "Jousting Lance"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Vehicles": [
        [
            (1, "Lita, Mechanical Engineer"), (1, "Hotshot Mechanic"),
            (1, "Aethershield Artificer"), (1, "Kitsune Ace"),
            (1, "Sanctum Gargoyle"), (1, "Giant Ox"),
            (1, "Built to Last"), (1, "Aerial Modification"),
            (1, "Caught in the Brights"), (1, "Peacewalker Colossus"),
            (1, "Imperial Recovery Unit"), (1, "Aradara Express"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Constellation": [
        [
            (1, "Archon of Sun's Grace"), (1, "Blessed Spirits"),
            (1, "Favored of Iroas"), (1, "Glory Bearers"),
            (1, "Transcendent Envoy"), (1, "Captivating Unicorn"),
            (1, "Pious Wayfarer"), (1, "Spirited Companion"),
            (1, "Chains of Custody"), (1, "Sage's Reverie"),
            (1, "Spectral Steel"), (1, "Forced Worship"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Blessed Spirits"), (1, "Alseid of Life's Bounty"),
            (1, "Transcendent Envoy"), (1, "Auramancer"),
            (1, "Glory Bearers"), (1, "Captivating Unicorn"),
            (1, "Spirited Companion"), (1, "Chains of Custody"),
            (1, "Sigil of the Empty Throne"), (1, "Sage's Reverie"),
            (1, "Spectral Steel"), (1, "Dreadful Apathy"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Teamwork": [
        [
            (1, "Agrus Kos, Eternal Soldier"), (1, "Angelic Cub"),
            (1, "Taranika, Akroan Veteran"), (1, "Angelic Protector"),
            (1, "Task Force"), (1, "Infantry Veteran"),
            (1, "Cavalry Drillmaster"), (1, "Combat Professor"),
            (1, "Valorous Stance"), (1, "Moment of Triumph"),
            (1, "Cage of Hands"), (1, "Dragon Blood"),
            (1, "Sandstone Bridge"), (1, "Thriving Heath"), (6, "Plains"),
        ],
        [
            (1, "Agrus Kos, Eternal Soldier"), (1, "Angelic Cub"),
            (1, "Tempered Veteran"), (1, "Angelic Protector"),
            (1, "Task Force"), (1, "Infantry Veteran"),
            (1, "Cavalry Drillmaster"), (1, "Iron Bully"),
            (1, "Combat Professor"), (1, "Valorous Stance"),
            (1, "Light of Hope"), (1, "Weapon Rack"),
            (1, "Sandstone Bridge"), (1, "Thriving Heath"), (6, "Plains"),
        ],
    ],
    "J22 Spirits": [
        [
            (1, "Angel of Flight Alabaster"), (1, "Selfless Spirit"),
            (1, "Eidolon of Rhetoric"), (1, "Mausoleum Guard"),
            (1, "Gallows Warden"), (1, "Doomed Traveler"),
            (1, "Pilgrim of the Ages"), (1, "Apothecary Geist"),
            (1, "Blessed Defiance"), (1, "Divine Verdict"),
            (1, "Not Forgotten"), (1, "Chains of Custody"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Eidolon of Rhetoric"), (1, "Mausoleum Guard"),
            (1, "Gallows Warden"), (1, "Doomed Traveler"),
            (1, "Kami of Ancient Law"), (1, "Pilgrim of the Ages"),
            (1, "Martyr's Soul"), (1, "Devouring Light"),
            (1, "Blessed Defiance"), (1, "Hour of Reckoning"),
            (1, "Triplicate Spirits"), (1, "Chains of Custody"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Blink": [
        [
            (1, "Brightmare"), (1, "Distinguished Conjurer"),
            (1, "Gallant Cavalry"), (1, "Inspiring Overseer"),
            (1, "Preston, the Vanisher"), (1, "Rambunctious Mutt"),
            (1, "Thraben Inspector"), (1, "Wispweaver Angel"),
            (1, "Acrobatic Maneuver"), (1, "Flicker of Fate"),
            (1, "Settle Beyond Reality"), (1, "Faith's Fetters"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Brightmare"), (1, "Daybreak Charger"),
            (1, "Distinguished Conjurer"), (1, "Emiel the Blessed"),
            (1, "Imperial Aerosaur"), (1, "Inspiring Overseer"),
            (1, "Rambunctious Mutt"), (1, "Valorous Steed"),
            (1, "Flicker of Fate"), (1, "Justiciar's Portal"),
            (1, "Settle Beyond Reality"), (1, "Faith's Fetters"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Brightmare"), (1, "Cavalry Drillmaster"),
            (1, "Distinguished Conjurer"), (1, "Inspiring Overseer"),
            (1, "Preston, the Vanisher"), (1, "Rambunctious Mutt"),
            (1, "Restoration Angel"), (1, "Valorous Steed"),
            (1, "Flicker of Fate"), (1, "Settle Beyond Reality"),
            (1, "Faith's Fetters"), (1, "Valor in Akros"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Brightmare"), (1, "Cavalry Drillmaster"),
            (1, "Distinguished Conjurer"), (1, "Inspiring Overseer"),
            (1, "Pierce Strider"), (1, "Rambunctious Mutt"),
            (1, "Seller of Songbirds"), (1, "Wispweaver Angel"),
            (1, "Flicker of Fate"), (1, "Settle Beyond Reality"),
            (1, "Faith's Fetters"), (1, "Panharmonicon"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Holy": [
        [
            (1, "Angelic Cub"), (1, "Emancipation Angel"),
            (1, "Goldnight Commander"), (1, "Angelic Page"),
            (1, "Pilgrim of the Ages"), (1, "Pillardrop Rescuer"),
            (1, "Anointer of Valor"), (1, "Miraculous Recovery"),
            (1, "Light of Hope"), (1, "Divine Arrow"),
            (1, "Chains of Custody"), (1, "Blessed Sanctuary"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Angelic Cub"), (1, "Lyra Dawnbringer"),
            (1, "Emancipation Angel"), (1, "Starnheim Aspirant"),
            (1, "Pilgrim of the Ages"), (1, "Stalwart Valkyrie"),
            (1, "Dawning Angel"), (1, "Anointer of Valor"),
            (1, "Light of Hope"), (1, "Divine Arrow"),
            (1, "Defy Death"), (1, "Chains of Custody"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Valkyrie Harbinger"), (1, "Emancipation Angel"),
            (1, "Serene Steward"), (1, "Inspiring Cleric"),
            (1, "Pilgrim of the Ages"), (1, "Dawning Angel"),
            (1, "Anointer of Valor"), (1, "Light of Hope"),
            (1, "Divine Arrow"), (1, "Chains of Custody"),
            (1, "Dawn of Hope"), (1, "Planar Atlas"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Righteous Valkyrie"), (1, "Emancipation Angel"),
            (1, "Inspiring Cleric"), (1, "Attended Healer"),
            (1, "Daybreak Chaplain"), (1, "Pilgrim of the Ages"),
            (1, "Pillardrop Rescuer"), (1, "Anointer of Valor"),
            (1, "Light of Hope"), (1, "Divine Arrow"),
            (1, "Chains of Custody"), (1, "Faith's Fetters"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Law": [
        [
            (1, "Magnanimous Magistrate"), (1, "Archon of Justice"),
            (1, "Gideon's Lawkeeper"), (1, "Ninth Bridge Patrol"),
            (1, "Kami of Ancient Law"), (1, "Midnight Guard"),
            (1, "Nightguard Patrol"), (1, "Skyhunter Patrol"),
            (1, "Righteousness"), (1, "Valorous Stance"),
            (1, "Divine Verdict"), (1, "Arrest"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Magnanimous Magistrate"), (1, "Law-Rune Enforcer"),
            (1, "Ninth Bridge Patrol"), (1, "Kami of Ancient Law"),
            (1, "Midnight Guard"), (1, "Nightguard Patrol"),
            (1, "Skyhunter Patrol"), (1, "Phalanx Tactics"),
            (1, "Radiant's Judgment"), (1, "Decree of Justice"),
            (1, "Arrest"), (1, "Unquestioned Authority"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Gideon, Champion of Justice"), (1, "Magnanimous Magistrate"),
            (1, "Gideon's Lawkeeper"), (1, "Ninth Bridge Patrol"),
            (1, "Kami of Ancient Law"), (1, "Midnight Guard"),
            (1, "Nightguard Patrol"), (1, "Skyhunter Patrol"),
            (1, "Righteousness"), (1, "Angelic Edict"),
            (1, "Arrest"), (1, "Unquestioned Authority"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Magnanimous Magistrate"), (1, "Isamaru, Hound of Konda"),
            (1, "Michiko Konda, Truth Seeker"), (1, "Ninth Bridge Patrol"),
            (1, "Kami of Ancient Law"), (1, "Midnight Guard"),
            (1, "Nightguard Patrol"), (1, "Skyhunter Patrol"),
            (1, "Bring to Trial"), (1, "Arrest"),
            (1, "Murder Investigation"), (1, "Edifice of Authority"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "J22 Cats": [
        [
            (1, "Ingenious Leonin"), (1, "Regal Caracal"),
            (1, "King of the Pride"), (1, "Savannah Lions"),
            (1, "Leonin Snarecaster"), (1, "Felidar Cub"),
            (1, "Basri's Acolyte"), (1, "Skyhunter Patrol"),
            (1, "Make a Stand"), (1, "Impeccable Timing"),
            (1, "Weight of Conscience"), (1, "Leonin Scimitar"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Ingenious Leonin"), (1, "Trove Warden"),
            (1, "King of the Pride"), (1, "Savannah Lions"),
            (1, "Steppe Lynx"), (1, "Mesa Lynx"),
            (1, "Leonin Snarecaster"), (1, "Prowling Felidar"),
            (1, "Make a Stand"), (1, "Impeccable Timing"),
            (1, "Felidar Retreat"), (1, "Weight of Conscience"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Ajani, Strength of the Pride"), (1, "Ingenious Leonin"),
            (1, "King of the Pride"), (1, "Ajani's Pridemate"),
            (1, "Trained Caracal"), (1, "Leonin Snarecaster"),
            (1, "Savannah Sage"), (1, "Basri's Acolyte"),
            (1, "Make a Stand"), (1, "Impeccable Timing"),
            (1, "Weight of Conscience"), (1, "Leonin Scimitar"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        [
            (1, "Ingenious Leonin"), (1, "Leonin Warleader"),
            (1, "King of the Pride"), (1, "Savannah Lions"),
            (1, "Leonin Snarecaster"), (1, "Pouncing Lynx"),
            (1, "Skyhunter Prowler"), (1, "Basri's Acolyte"),
            (1, "Make a Stand"), (1, "Impeccable Timing"),
            (1, "Weight of Conscience"), (1, "Leonin Scimitar"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    # ===== BLUE =====
    "Shapeshifters": [
        [
            (1, "Pirated Copy"), (1, "Gigantoplasm"),
            (1, "Mirror Image"), (1, "Bloodline Pretender"),
            (1, "Universal Automaton"), (1, "Amoeboid Changeling"),
            (1, "Mistwalker"), (1, "Littjara Kinseekers"),
            (1, "Lookout's Dispersal"), (1, "Chilling Trap"),
            (1, "Crashing Tide"), (1, "Heirloom Blade"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Snow": [
        [
            (1, "Isu the Abominable"), (1, "Avalanche Caller"),
            (1, "Berg Strider"), (1, "Chillerpillar"),
            (1, "Frostpeak Yeti"), (1, "Phyrexian Ironfoot"),
            (1, "Pilfering Hawk"), (1, "Crippling Chill"),
            (1, "Coldsteel Heart"), (1, "Icebind Pillar"),
            (1, "Marit Lage's Slumber"), (1, "Winter's Rest"),
            (1, "Shimmerdrift Vale"), (7, "Snow-Covered Island"),
        ],
    ],
    "Faeries": [
        [
            (1, "Spellstutter Sprite"), (1, "Brineborn Cutthroat"),
            (1, "Vendilion Clique"), (1, "Wavebreak Hippocamp"),
            (1, "Pestermite"), (1, "Glen Elendra Pranksters"),
            (1, "Sentinels of Glen Elendra"), (1, "Undersea Invader"),
            (1, "Soul Read"), (1, "Synchronized Eviction"),
            (1, "Fleeting Distraction"), (1, "Instruments of War"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Spellstutter Sprite"), (1, "Faerie Seer"),
            (1, "Stinging Lionfish"), (1, "Pestermite"),
            (1, "Glen Elendra Pranksters"), (1, "Sentinels of Glen Elendra"),
            (1, "Undersea Invader"), (1, "Soul Read"),
            (1, "Synchronized Eviction"), (1, "Stolen by the Fae"),
            (1, "So Tiny"), (1, "Instruments of War"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Go to School": [
        [
            (1, "Kasmina, Enigmatic Mentor"), (1, "Biblioplex Kraken"),
            (1, "Merfolk Pupil"), (1, "Barrin, Tolarian Archmage"),
            (1, "Overwhelmed Apprentice"), (1, "Elite Instructor"),
            (1, "Tolarian Sentinel"), (1, "Academy Journeymage"),
            (1, "Wizard Mentor"), (1, "Dismiss"),
            (1, "Tragic Lesson"), (1, "Bury in Books"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Kasmina, Enigmatic Mentor"), (1, "Biblioplex Kraken"),
            (1, "Merfolk Pupil"), (1, "Overwhelmed Apprentice"),
            (1, "Campus Guide"), (1, "Teferi's Protege"),
            (1, "Elite Instructor"), (1, "Academy Journeymage"),
            (1, "Dismiss"), (1, "Bury in Books"),
            (1, "Multiple Choice"), (1, "Rhystic Study"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Scrying": [
        [
            (1, "Kenessos, Priest of Thassa"), (1, "Biblioplex Kraken"),
            (1, "Cryptic Serpent"), (1, "Faerie Seer"),
            (1, "Sage's Row Savant"), (1, "Moonfolk Puzzlemaker"),
            (1, "Octoprophet"), (1, "Perilous Voyage"),
            (1, "No Escape"), (1, "Interpret the Signs"),
            (1, "Preordain"), (1, "Raiders' Karve"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Kenessos, Priest of Thassa"), (1, "Biblioplex Kraken"),
            (1, "Djinn of Wishes"), (1, "Cryptic Serpent"),
            (1, "Faerie Seer"), (1, "Augury Owl"),
            (1, "Moonfolk Puzzlemaker"), (1, "Octoprophet"),
            (1, "Condescend"), (1, "Preordain"),
            (1, "Anchor to the Aether"), (1, "Explorer's Scope"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Think Again": [
        [
            (1, "Jace, Arcane Strategist"), (1, "Spectral Sailor"),
            (1, "Faerie Vandal"), (1, "Merfolk Pupil"),
            (1, "Eyekite"), (1, "Mystic Skyfish"),
            (1, "Teferi's Protege"), (1, "Tome Anima"),
            (1, "Steelgaze Griffin"), (1, "Soul Read"),
            (1, "Serum Visions"), (1, "Drag Under"),
            (1, "Memorial to Genius"), (1, "Thriving Isle"), (6, "Island"),
        ],
        [
            (1, "Alandra, Sky Dreamer"), (1, "Spectral Sailor"),
            (1, "Faerie Vandal"), (1, "Merfolk Pupil"),
            (1, "Mystic Skyfish"), (1, "Teferi's Protege"),
            (1, "Tome Anima"), (1, "Steelgaze Griffin"),
            (1, "Soul Read"), (1, "Opt"),
            (1, "Eternity Snare"), (1, "Hedron Archive"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Alandra, Sky Dreamer"), (1, "Spectral Sailor"),
            (1, "Faerie Vandal"), (1, "Merfolk Pupil"),
            (1, "Mystic Skyfish"), (1, "Teferi's Protege"),
            (1, "Tome Anima"), (1, "Steelgaze Griffin"),
            (1, "Neutralize"), (1, "Startling Development"),
            (1, "Hieroglyphic Illumination"), (1, "Lay Claim"),
            (1, "Desert of the Mindful"), (1, "Thriving Isle"), (6, "Island"),
        ],
        [
            (1, "Faerie Formation"), (1, "Spectral Sailor"),
            (1, "Faerie Vandal"), (1, "Merfolk Pupil"),
            (1, "Mystic Skyfish"), (1, "Teferi's Protege"),
            (1, "Tome Anima"), (1, "Steelgaze Griffin"),
            (1, "Soul Read"), (1, "Leave in the Dust"),
            (1, "Military Intelligence"), (1, "Mantle of Tides"),
            (1, "Memorial to Genius"), (1, "Thriving Isle"), (6, "Island"),
        ],
    ],
    "Detective": [
        [
            (1, "Drownyard Explorers"), (1, "Dutiful Replicator"),
            (1, "Filigree Attendant"), (1, "Floodhound"),
            (1, "Gearseeker Serpent"), (1, "Shambling Suit"),
            (1, "Vedalken Engineer"), (1, "Press for Answers"),
            (1, "Jace's Scrutiny"), (1, "Mechanized Production"),
            (1, "Hold for Questioning"), (1, "Magnifying Glass"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Psychosis Crawler"), (1, "Aeronaut Tinkerer"),
            (1, "Drownyard Explorers"), (1, "Floodhound"),
            (1, "Oneirophage"), (1, "Tolarian Kraken"),
            (1, "Vedalken Engineer"), (1, "Press for Answers"),
            (1, "Jace's Scrutiny"), (1, "Hold for Questioning"),
            (1, "Teferi's Puzzle Box"), (1, "Magnifying Glass"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Shimmer Dragon"), (1, "Drownyard Explorers"),
            (1, "Dutiful Replicator"), (1, "Floodhound"),
            (1, "Lumengrid Sentinel"), (1, "Skilled Animator"),
            (1, "Vedalken Engineer"), (1, "Weldfast Wingsmith"),
            (1, "Press for Answers"), (1, "Jace's Scrutiny"),
            (1, "Hold for Questioning"), (1, "Magnifying Glass"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Tamiyo, the Moon Sage"), (1, "Drownyard Explorers"),
            (1, "Dutiful Replicator"), (1, "Erdwal Illuminator"),
            (1, "Floodhound"), (1, "Syr Elenora, the Discerning"),
            (1, "Vedalken Engineer"), (1, "Press for Answers"),
            (1, "Jace's Scrutiny"), (1, "Hold for Questioning"),
            (1, "Magnifying Glass"), (1, "Tamiyo's Journal"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Inventive": [
        [
            (1, "Tezzeret, Artifice Master"), (1, "Merfolk Pupil"),
            (1, "Whirler Rogue"), (1, "Skilled Animator"),
            (1, "Gearsmith Prodigy"), (1, "Aviation Pioneer"),
            (1, "Gearsmith Guardian"), (1, "Gearseeker Serpent"),
            (1, "Launch Mishap"), (1, "Artificer's Epiphany"),
            (1, "Planar Atlas"), (1, "Aether Spellbomb"),
            (1, "Thriving Isle"), (1, "Seat of the Synod"), (6, "Island"),
        ],
        [
            (1, "Hangarback Walker"), (1, "Whirler Rogue"),
            (1, "Renowned Weaponsmith"), (1, "Juggernaut"),
            (1, "Gearsmith Prodigy"), (1, "Aeronaut Tinkerer"),
            (1, "Aviation Pioneer"), (1, "Gearseeker Serpent"),
            (1, "Launch Mishap"), (1, "Artificer's Epiphany"),
            (1, "Heart-Piercer Bow"), (1, "Vial of Dragonfire"),
            (1, "Thriving Isle"), (1, "Seat of the Synod"), (6, "Island"),
        ],
        [
            (1, "Steel Overseer"), (1, "Whirler Rogue"),
            (1, "Clockwork Hydra"), (1, "Gearsmith Prodigy"),
            (1, "Aviation Pioneer"), (1, "Pilgrim's Eye"),
            (1, "Gearseeker Serpent"), (1, "Launch Mishap"),
            (1, "Artificer's Epiphany"), (1, "Thopter Spy Network"),
            (1, "Whirlermaker"), (1, "Aether Spellbomb"),
            (1, "Thriving Isle"), (1, "Seat of the Synod"), (6, "Island"),
        ],
        [
            (1, "Dutiful Replicator"), (1, "Solemn Simulacrum"),
            (1, "Whirler Rogue"), (1, "Cogwork Assembler"),
            (1, "Gearsmith Prodigy"), (1, "Aviation Pioneer"),
            (1, "Assembly-Worker"), (1, "Self-Assembler"),
            (1, "Gearseeker Serpent"), (1, "Launch Mishap"),
            (1, "Artificer's Epiphany"), (1, "Aether Spellbomb"),
            (1, "Mishra's Factory"), (1, "Thriving Isle"),
            (1, "Seat of the Synod"), (5, "Island"),
        ],
    ],
    "J22 Merfolk": [
        [
            (1, "Seafloor Oracle"), (1, "Merrow Reejerey"),
            (1, "River Sneak"), (1, "Sage of the Falls"),
            (1, "Triton Shorestalker"), (1, "Shaper Apprentice"),
            (1, "Watertrap Weaver"), (1, "Storm Sculptor"),
            (1, "Soul Read"), (1, "Crashing Tide"),
            (1, "Aquatic Incursion"), (1, "One With the Wind"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Merfolk Pupil"), (1, "Svyelun of Sea and Sky"),
            (1, "Merrow Reejerey"), (1, "River Sneak"),
            (1, "Windrider Patrol"), (1, "Triton Shorestalker"),
            (1, "Watertrap Weaver"), (1, "Saltwater Stalwart"),
            (1, "Synchronized Eviction"), (1, "Soul Read"),
            (1, "Crashing Tide"), (1, "One With the Wind"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Harbinger of the Tides"), (1, "Wake Thrasher"),
            (1, "Merrow Reejerey"), (1, "Fallowsage"),
            (1, "Windrider Patrol"), (1, "Triton Shorestalker"),
            (1, "Stonybrook Angler"), (1, "Watertrap Weaver"),
            (1, "Synchronized Eviction"), (1, "Soul Read"),
            (1, "Crashing Tide"), (1, "One With the Wind"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        [
            (1, "Merfolk Sovereign"), (1, "Merrow Reejerey"),
            (1, "River Sneak"), (1, "Windrider Patrol"),
            (1, "Triton Shorestalker"), (1, "Watertrap Weaver"),
            (1, "Library Larcenist"), (1, "Saltwater Stalwart"),
            (1, "Soul Read"), (1, "Crashing Tide"),
            (1, "Aquatic Incursion"), (1, "One With the Wind"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    # ===== BLACK =====
    "Unlucky Thirteen": [
        [
            (1, "Tree of Perdition"), (1, "Disciple of Perdition"),
            (1, "Sling-Gang Lieutenant"), (1, "Blight Keeper"),
            (1, "Gnawing Zombie"), (1, "Skirsdag Supplicant"),
            (1, "Soulcage Fiend"), (1, "Dread Rider"),
            (1, "Tragic Slip"), (1, "Ruthless Disposal"),
            (1, "Exsanguinate"), (1, "Triskaidekaphobia"),
            (1, "Piranha Marsh"), (1, "Thriving Moor"), (6, "Swamp"),
        ],
    ],
    "Rats": [
        [
            (1, "Ashcoat of the Shadow Swarm"), (1, "Burglar Rat"),
            (1, "Chittering Rats"), (1, "Crypt Rats"),
            (1, "Ossuary Rats"), (1, "Typhoid Rats"),
            (1, "Sinuous Vermin"), (1, "Nezumi Bone-Reader"),
            (1, "Ogre Slumlord"), (1, "Ulcerate"),
            (1, "Eviscerate"), (1, "Infiltration Lens"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "Demons": [
        [
            (1, "Conductor of Cacophony"), (1, "Disciple of Perdition"),
            (1, "Kothophed, Soul Hoarder"), (1, "Demon's Disciple"),
            (1, "Renegade Demon"), (1, "Takenuma Bleeder"),
            (1, "Fetid Imp"), (1, "Soulcage Fiend"),
            (1, "Diabolic Edict"), (1, "Demon's Grasp"),
            (1, "Inner Demon"), (1, "Infernal Idol"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Conductor of Cacophony"), (1, "Disciple of Perdition"),
            (1, "Demon of Catastrophes"), (1, "Seizan, Perverter of Truth"),
            (1, "Demon's Disciple"), (1, "Dreadhound"),
            (1, "Doomed Dissenter"), (1, "Takenuma Bleeder"),
            (1, "Diabolic Edict"), (1, "Demonic Gifts"),
            (1, "Demon's Grasp"), (1, "Infernal Idol"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "Boneyard": [
        [
            (1, "Ossuary Rats"), (1, "Suspicious Shambler"),
            (1, "Stitcher's Supplier"), (1, "Revenant"),
            (1, "Wailing Ghoul"), (1, "Pit Keeper"),
            (1, "Spark Reaper"), (1, "Gorging Vulture"),
            (1, "Moodmark Painter"), (1, "Necrotic Wound"),
            (1, "Corpse Churn"), (1, "Oversold Cemetery"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Ossuary Rats"), (1, "Suspicious Shambler"),
            (1, "Shadowborn Demon"), (1, "Graveblade Marauder"),
            (1, "Stitcher's Supplier"), (1, "Liliana's Elite"),
            (1, "Wailing Ghoul"), (1, "Gorging Vulture"),
            (1, "Moodmark Painter"), (1, "Necrotic Wound"),
            (1, "Ghoul's Feast"), (1, "Corpse Churn"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "Morbid": [
        [
            (1, "Disciple of Perdition"), (1, "Suspicious Shambler"),
            (1, "Reaper from the Abyss"), (1, "Morkrut Banshee"),
            (1, "Bone Picker"), (1, "Wakedancer"),
            (1, "Liliana's Steward"), (1, "Durable Coilbug"),
            (1, "Tragic Slip"), (1, "Village Rites"),
            (1, "Reave Soul"), (1, "Spawning Pit"),
            (1, "Mortuary Mire"), (1, "Thriving Moor"), (6, "Swamp"),
        ],
        [
            (1, "Suspicious Shambler"), (1, "Priest of the Blood Rite"),
            (1, "Skirsdag High Priest"), (1, "Morkrut Banshee"),
            (1, "Reassembling Skeleton"), (1, "Bone Picker"),
            (1, "Wakedancer"), (1, "Liliana's Steward"),
            (1, "Tragic Slip"), (1, "Village Rites"),
            (1, "Reave Soul"), (1, "Vampiric Rites"),
            (1, "Mortuary Mire"), (1, "Thriving Moor"), (6, "Swamp"),
        ],
    ],
    "Cruel": [
        [
            (1, "Ob Nixilis, the Hate-Twisted"), (1, "Termination Facilitator"),
            (1, "Ossuary Rats"), (1, "Creeping Bloodsucker"),
            (1, "Syr Konrad, the Grim"), (1, "Tormented Soul"),
            (1, "Alley Strangler"), (1, "Wicked Guardian"),
            (1, "Ob Nixilis's Cruelty"), (1, "Feast on the Fallen"),
            (1, "Infernal Idol"), (1, "Talon of Pain"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Termination Facilitator"), (1, "Creeping Bloodsucker"),
            (1, "Ossuary Rats"), (1, "Plague Spitter"),
            (1, "Nested Ghoul"), (1, "Alley Strangler"),
            (1, "Lurking Deadeye"), (1, "Consign to the Pit"),
            (1, "Feast on the Fallen"), (1, "Mire Blight"),
            (1, "Infernal Idol"), (1, "Talon of Pain"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Conductor of Cacophony"), (1, "Ossuary Rats"),
            (1, "Creeping Bloodsucker"), (1, "Cruel Sadist"),
            (1, "Dread Slaver"), (1, "Hooded Assassin"),
            (1, "Alley Strangler"), (1, "Feast on the Fallen"),
            (1, "Festering Evil"), (1, "Mire Blight"),
            (1, "Infernal Idol"), (1, "Talon of Pain"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Conductor of Cacophony"), (1, "Ossuary Rats"),
            (1, "Creeping Bloodsucker"), (1, "Dread Presence"),
            (1, "Nested Ghoul"), (1, "Tormented Soul"),
            (1, "Hooded Assassin"), (1, "Alley Strangler"),
            (1, "Feast on the Fallen"), (1, "Mire Blight"),
            (1, "Infernal Idol"), (1, "Talon of Pain"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "Fangs": [
        [
            (1, "Rodolf Duskbringer"), (1, "Creeping Bloodsucker"),
            (1, "Oathsworn Vampire"), (1, "Bloodthirsty Aerialist"),
            (1, "Falkenrath Noble"), (1, "Marauding Blight-Priest"),
            (1, "Vampire Envoy"), (1, "Epicure of Blood"),
            (1, "Moment of Craving"), (1, "Feast of Blood"),
            (1, "Blood Price"), (1, "Ill-Gotten Inheritance"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Rodolf Duskbringer"), (1, "Creeping Bloodsucker"),
            (1, "Bloodtracker"), (1, "Oathsworn Vampire"),
            (1, "Bloodthirsty Aerialist"), (1, "Falkenrath Noble"),
            (1, "Marauding Blight-Priest"), (1, "Vampire Envoy"),
            (1, "Moment of Craving"), (1, "Certain Death"),
            (1, "Vampiric Rites"), (1, "Ill-Gotten Inheritance"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Creeping Bloodsucker"), (1, "Tivash, Gloom Summoner"),
            (1, "Oathsworn Vampire"), (1, "Blood Artist"),
            (1, "Bloodbond Vampire"), (1, "Vampire Envoy"),
            (1, "Marauding Blight-Priest"), (1, "Nirkana Assassin"),
            (1, "Moment of Craving"), (1, "Ancient Craving"),
            (1, "Certain Death"), (1, "Ill-Gotten Inheritance"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Creeping Bloodsucker"), (1, "Vito, Thorn of the Dusk Rose"),
            (1, "Oathsworn Vampire"), (1, "Blood Artist"),
            (1, "Bloodbond Vampire"), (1, "Vampire Envoy"),
            (1, "Marauding Blight-Priest"), (1, "Kalastria Nightwatch"),
            (1, "Moment of Craving"), (1, "Feast of Blood"),
            (1, "Blood Price"), (1, "Ill-Gotten Inheritance"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "J22 Zombies": [
        [
            (1, "Suspicious Shambler"), (1, "Karfell Kennel-Master"),
            (1, "Gravecrawler"), (1, "Lord of the Accursed"),
            (1, "Mire Triton"), (1, "Gravedigger"),
            (1, "Crow of Dark Tidings"), (1, "Maalfeld Twins"),
            (1, "Deadly Plot"), (1, "Cemetery Recruitment"),
            (1, "Necromancer's Stockpile"), (1, "Dead Weight"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Liliana, Death's Majesty"), (1, "Stitcher's Supplier"),
            (1, "Suspicious Shambler"), (1, "Lord of the Accursed"),
            (1, "Gravedigger"), (1, "Returned Reveler"),
            (1, "Crow of Dark Tidings"), (1, "Ghoulraiser"),
            (1, "Maalfeld Twins"), (1, "Deadly Plot"),
            (1, "Cemetery Recruitment"), (1, "Dead Weight"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Suspicious Shambler"), (1, "Lord of the Accursed"),
            (1, "Mire Triton"), (1, "Gnawing Zombie"),
            (1, "Crow of Dark Tidings"), (1, "Gavony Unhallowed"),
            (1, "Maalfeld Twins"), (1, "Deadly Plot"),
            (1, "Cemetery Recruitment"), (1, "Endless Ranks of the Dead"),
            (1, "Graf Harvest"), (1, "Dead Weight"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Suspicious Shambler"), (1, "Lord of the Accursed"),
            (1, "Undead Augur"), (1, "Shambling Ghoul"),
            (1, "Crow of Dark Tidings"), (1, "Marauding Boneslasher"),
            (1, "Maalfeld Twins"), (1, "Deadly Plot"),
            (1, "Cemetery Recruitment"), (1, "Liliana's Mastery"),
            (1, "Dead Weight"), (1, "Cellar Door"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "Gross": [
        [
            (1, "Skullslither Worm"), (1, "Massacre Wurm"),
            (1, "Plaguecrafter"), (1, "Yargle, Glutton of Urborg"),
            (1, "Blight Keeper"), (1, "Black Cat"),
            (1, "Durable Coilbug"), (1, "Devouring Swarm"),
            (1, "Grotesque Mutation"), (1, "Strangling Spores"),
            (1, "Eaten Alive"), (1, "Infernal Idol"),
            (1, "Leechridden Swamp"), (1, "Thriving Moor"), (6, "Swamp"),
        ],
        [
            (1, "Skullslither Worm"), (1, "Phyrexian Plaguelord"),
            (1, "Plaguecrafter"), (1, "Blight Keeper"),
            (1, "Durable Coilbug"), (1, "Deathbloom Thallid"),
            (1, "Devouring Swarm"), (1, "Phyrexian Debaser"),
            (1, "Fungal Infection"), (1, "Grotesque Mutation"),
            (1, "Eaten Alive"), (1, "Phyrexian Reclamation"),
            (1, "Blighted Fen"), (1, "Thriving Moor"), (6, "Swamp"),
        ],
        [
            (1, "Skullslither Worm"), (1, "Deathbringer Regent"),
            (1, "Plaguecrafter"), (1, "Kraul Swarm"),
            (1, "Blight Keeper"), (1, "Durable Coilbug"),
            (1, "Dune Beetle"), (1, "Devouring Swarm"),
            (1, "Death Wind"), (1, "Grotesque Mutation"),
            (1, "Infernal Idol"), (1, "Eaten Alive"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
        [
            (1, "Skullslither Worm"), (1, "Ossuary Rats"),
            (1, "Ophiomancer"), (1, "Plaguecrafter"),
            (1, "Swarm of Bloodflies"), (1, "Blight Keeper"),
            (1, "Vermin Gorger"), (1, "Durable Coilbug"),
            (1, "Devouring Swarm"), (1, "Grotesque Mutation"),
            (1, "Eaten Alive"), (1, "Witch's Cauldron"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    # ===== RED =====
    "Speedy": [
        [
            (1, "Kiki-Jiki, Mirror Breaker"), (1, "Ardoz, Cobbler of War"),
            (1, "Bolt Hound"), (1, "Fanatical Firebrand"),
            (1, "Irreverent Revelers"), (1, "Mudbutton Torchrunner"),
            (1, "Markov Warlord"), (1, "Nest Robber"),
            (1, "Warcry Phoenix"), (1, "Fiery Conclusion"),
            (1, "Rush of Adrenaline"), (1, "Swift Kick"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Spicy": [
        [
            (1, "Auntie Blyte, Bad Influence"), (1, "Coalhauler Swine"),
            (1, "Fireslinger"), (1, "Goblin Artillery"),
            (1, "Goblin Psychopath"), (1, "Ravenous Giant"),
            (1, "Spear Spewer"), (1, "Smoldering Efreet"),
            (1, "Aftershock"), (1, "Sarkhan's Rage"),
            (1, "Impending Doom"), (1, "Loxodon Warhammer"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Experimental": [
        [
            (1, "Mizzix, Replica Rider"), (1, "Young Pyromancer"),
            (1, "Grotag Night-Runner"), (1, "Thermo-Alchemist"),
            (1, "Spellgorger Weird"), (1, "Catalyst Elemental"),
            (1, "Goblin Researcher"), (1, "Lightning Axe"),
            (1, "Electric Revelation"), (1, "Ignite the Future"),
            (1, "Seize the Storm"), (1, "Firebolt"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Mizzix, Replica Rider"), (1, "Grotag Night-Runner"),
            (1, "Cyclops Electromancer"), (1, "Thermo-Alchemist"),
            (1, "Sparkmage Apprentice"), (1, "Catalyst Elemental"),
            (1, "Goblin Researcher"), (1, "Electric Revelation"),
            (1, "Electrify"), (1, "Spark of Creativity"),
            (1, "Act on Impulse"), (1, "Firebolt"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "J22 Dragons": [
        [
            (1, "Sarkhan, the Dragonspeaker"), (1, "Plundering Predator"),
            (1, "Sarkhan's Whelp"), (1, "Dragon Egg"),
            (1, "Furnace Whelp"), (1, "Dragon Mage"),
            (1, "Kargan Dragonrider"), (1, "Bogardan Dragonheart"),
            (1, "Scorching Dragonfire"), (1, "Sarkhan's Rage"),
            (1, "Dragon Fodder"), (1, "Tormenting Voice"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Plundering Predator"), (1, "Lathliss, Dragon Queen"),
            (1, "Dragonlord's Servant"), (1, "Dragonspeaker Shaman"),
            (1, "Furnace Whelp"), (1, "Dragon Mage"),
            (1, "Kargan Dragonrider"), (1, "Sparktongue Dragon"),
            (1, "Scorching Dragonfire"), (1, "Sarkhan's Rage"),
            (1, "Tormenting Voice"), (1, "Dragon's Hoard"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Cycling": [
        [
            (1, "Plundering Predator"), (1, "Yidaro, Wandering Monster"),
            (1, "Flameblade Adept"), (1, "Rooting Moloch"),
            (1, "Drannith Stinger"), (1, "Prickly Marmoset"),
            (1, "Quakefoot Cyclops"), (1, "Lava Serpent"),
            (1, "Deem Worthy"), (1, "Shredded Sails"),
            (1, "Go for Blood"), (1, "Reptilian Reflection"),
            (1, "Forgotten Cave"), (1, "Thriving Bluff"), (6, "Mountain"),
        ],
        [
            (1, "Plundering Predator"), (1, "Irencrag Pyromancer"),
            (1, "Mad Ratter"), (1, "Rooting Moloch"),
            (1, "Drannith Stinger"), (1, "Bloodhaze Wolverine"),
            (1, "Prickly Marmoset"), (1, "Starstorm"),
            (1, "Deem Worthy"), (1, "Raking Claws"),
            (1, "Go for Blood"), (1, "Reptilian Reflection"),
            (1, "Forgotten Cave"), (1, "Thriving Bluff"), (6, "Mountain"),
        ],
    ],
    "Treasure": [
        [
            (1, "Dutiful Replicator"), (1, "Captain Lannery Storm"),
            (1, "Sokenzan Smelter"), (1, "Blood Aspirant"),
            (1, "Rapacious Dragon"), (1, "Goldhound"),
            (1, "Brazen Freebooter"), (1, "Ripscale Predator"),
            (1, "Sudden Breakthrough"), (1, "Welding Sparks"),
            (1, "Trove of Temptation"), (1, "Goldvein Pick"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Dutiful Replicator"), (1, "Professional Face-Breaker"),
            (1, "Blood Aspirant"), (1, "Pyre-Sledge Arsonist"),
            (1, "Rapacious Dragon"), (1, "Barrage Ogre"),
            (1, "Goldhound"), (1, "Reckless Fireweaver"),
            (1, "Brazen Freebooter"), (1, "Welding Sparks"),
            (1, "Improvised Weaponry"), (1, "Goldvein Pick"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Dutiful Replicator"), (1, "Goldspan Dragon"),
            (1, "Blood Aspirant"), (1, "Rapacious Dragon"),
            (1, "Kuldotha Flamefiend"), (1, "Goldhound"),
            (1, "Vault Robber"), (1, "Brazen Freebooter"),
            (1, "Welding Sparks"), (1, "Big Score"),
            (1, "Blaze"), (1, "Goldvein Pick"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Dutiful Replicator"), (1, "Gadrak, the Crown-Scourge"),
            (1, "Blood Aspirant"), (1, "Rapacious Dragon"),
            (1, "Kuldotha Flamefiend"), (1, "Goldhound"),
            (1, "Gleaming Barrier"), (1, "Brazen Freebooter"),
            (1, "Welding Sparks"), (1, "Improvised Weaponry"),
            (1, "Trove of Temptation"), (1, "Goldvein Pick"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Fiery": [
        [
            (1, "Chandra, Flame's Fury"), (1, "Chandra's Magmutt"),
            (1, "Chandra's Pyreling"), (1, "Chandra's Spitfire"),
            (1, "Coalborn Entity"), (1, "Goblin Researcher"),
            (1, "Spiteful Prankster"), (1, "Wildfire Elemental"),
            (1, "Fiery Intervention"), (1, "Pillar of Flame"),
            (1, "Chandra's Pyrohelix"), (1, "Thrill of Possibility"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Blisterspit Gremlin"), (1, "Chandra's Pyreling"),
            (1, "Chandra's Spitfire"), (1, "Coalborn Entity"),
            (1, "Goblin Researcher"), (1, "Magmatic Channeler"),
            (1, "Wildfire Elemental"), (1, "Banefire"),
            (1, "Flames of the Firebrand"), (1, "Pillar of Flame"),
            (1, "Fiery Intervention"), (1, "Thrill of Possibility"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Ogre Battlecaster"), (1, "Chandra's Pyreling"),
            (1, "Chandra's Spitfire"), (1, "Coalborn Entity"),
            (1, "Thermo-Alchemist"), (1, "Wildfire Elemental"),
            (1, "Fiery Intervention"), (1, "Pillar of Flame"),
            (1, "Dance with Devils"), (1, "Flame Lash"),
            (1, "Hungry Flames"), (1, "Thrill of Possibility"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Ogre Battlecaster"), (1, "Blisterspit Gremlin"),
            (1, "Chandra's Pyreling"), (1, "Chandra's Spitfire"),
            (1, "Coalborn Entity"), (1, "Viashino Pyromancer"),
            (1, "Wildfire Elemental"), (1, "Cone of Flame"),
            (1, "Fiery Intervention"), (1, "Pillar of Flame"),
            (1, "Searing Spear"), (1, "Thrill of Possibility"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "J22 Goblins": [
        [
            (1, "Goblin Researcher"), (1, "Krenko, Mob Boss"),
            (1, "Volley Veteran"), (1, "Battle Squadron"),
            (1, "Weaselback Redcap"), (1, "Subterranean Scout"),
            (1, "Rummaging Goblin"), (1, "Burn Bright"),
            (1, "Flame Lash"), (1, "Hordeling Outburst"),
            (1, "Goblin Grenade"), (1, "Dragon Fodder"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Goblin Researcher"), (1, "Ib Halfheart, Goblin Tactician"),
            (1, "Goblin Warchief"), (1, "Volley Veteran"),
            (1, "Frenzied Goblin"), (1, "Goblin Trailblazer"),
            (1, "Rummaging Goblin"), (1, "Burn Bright"),
            (1, "Flame Lash"), (1, "Goblin Rally"),
            (1, "Goblin Grenade"), (1, "Dragon Fodder"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Goblin Researcher"), (1, "Muxus, Goblin Grandee"),
            (1, "Arms Dealer"), (1, "Volley Veteran"),
            (1, "Fanatical Firebrand"), (1, "Goblin Trailblazer"),
            (1, "Rummaging Goblin"), (1, "Burn Bright"),
            (1, "Goblin Grenade"), (1, "Dragon Fodder"),
            (1, "Destructive Tampering"), (1, "Instruments of War"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Ardoz, Cobbler of War"), (1, "Goblin Researcher"),
            (1, "Goblin Rabblemaster"), (1, "Volley Veteran"),
            (1, "Torch Courier"), (1, "Rummaging Goblin"),
            (1, "Outnumber"), (1, "Burn Bright"),
            (1, "Hordeling Outburst"), (1, "Goblin Rally"),
            (1, "Goblin Grenade"), (1, "Dragon Fodder"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Raid": [
        [
            (1, "Kari Zev, Skyship Raider"), (1, "Keldon Raider"),
            (1, "Lavastep Raider"), (1, "Plundering Predator"),
            (1, "Rigging Runner"), (1, "Storm Fleet Pyromancer"),
            (1, "Swaggering Corsair"), (1, "War-Name Aspirant"),
            (1, "Firecannon Blast"), (1, "Raze the Effigy"),
            (1, "Daring Piracy"), (1, "Goblin Oriflamme"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Glint-Horn Buccaneer"), (1, "Immersturm Raider"),
            (1, "Keldon Raider"), (1, "Plundering Predator"),
            (1, "Rigging Runner"), (1, "Storm Fleet Pyromancer"),
            (1, "Swaggering Corsair"), (1, "War-Name Aspirant"),
            (1, "Firecannon Blast"), (1, "Lightning Axe"),
            (1, "Bag of Holding"), (1, "Daring Piracy"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Axgard Cavalry"), (1, "Keldon Raider"),
            (1, "Mardu Heart-Piercer"), (1, "Plundering Predator"),
            (1, "Rigging Runner"), (1, "Storm Fleet Pyromancer"),
            (1, "Swaggering Corsair"), (1, "War-Name Aspirant"),
            (1, "Fervent Strike"), (1, "Firecannon Blast"),
            (1, "Brazen Cannonade"), (1, "Daring Piracy"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        [
            (1, "Borderland Marauder"), (1, "Brazen Wolves"),
            (1, "Keldon Raider"), (1, "Plundering Predator"),
            (1, "Rigging Runner"), (1, "Storm Fleet Pyromancer"),
            (1, "Swaggering Corsair"), (1, "War-Name Aspirant"),
            (1, "Firecannon Blast"), (1, "Brazen Cannonade"),
            (1, "Daring Piracy"), (1, "Ordeal of Purphoros"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    # ===== GREEN =====
    "Eldrazi": [
        [
            (1, "World Breaker"), (1, "Endbringer"),
            (1, "Brood Monitor"), (1, "Blisterpod"),
            (1, "Stalking Drone"), (1, "Scion Summoner"),
            (1, "Warden of Geometries"), (1, "Titan's Presence"),
            (1, "Unnatural Aggression"), (1, "Ruin in Their Wake"),
            (1, "Ancient Stirrings"), (1, "Planar Atlas"),
            (1, "Warped Landscape"), (1, "Wastes"),
            (1, "Ash Barrens"), (5, "Forest"),
        ],
    ],
    "Primates": [
        [
            (1, "Kibo, Uktabi Prince"), (1, "Hooting Mandrills"),
            (1, "Scrounging Bandar"), (1, "Silverback Shaman"),
            (1, "Simian Brawler"), (1, "Towering Gibbon"),
            (1, "Uktabi Orangutan"), (1, "Wily Bandar"),
            (1, "Cultivate"), (1, "Declare Dominance"),
            (1, "Ram Through"), (1, "Monkey Cage"),
            (1, "Treetop Village"), (1, "Thriving Grove"), (6, "Forest"),
        ],
    ],
    "Landfall": [
        [
            (1, "Rampaging Baloths"), (1, "Courser of Kruphix"),
            (1, "Turntimber Basilisk"), (1, "Primeval Herald"),
            (1, "Oashra Cultivator"), (1, "Snapping Gnarlid"),
            (1, "Canopy Baloth"), (1, "Rampaging Growth"),
            (1, "Master's Rebuke"), (1, "Zendikar's Roil"),
            (1, "Khalni Heart Expedition"), (1, "Adventuring Gear"),
            (1, "Evolving Wilds"), (1, "Thriving Grove"), (6, "Forest"),
        ],
        [
            (1, "Avenger of Zendikar"), (1, "Turntimber Basilisk"),
            (1, "Primeval Herald"), (1, "Baloth Woodcrasher"),
            (1, "Oashra Cultivator"), (1, "Snapping Gnarlid"),
            (1, "Fertilid"), (1, "Canopy Baloth"),
            (1, "Rampaging Growth"), (1, "Groundswell"),
            (1, "Broken Bond"), (1, "Khalni Heart Expedition"),
            (1, "Evolving Wilds"), (1, "Thriving Grove"), (6, "Forest"),
        ],
    ],
    "Multi-Headed": [
        [
            (1, "Benevolent Hydra"), (1, "Domesticated Hydra"),
            (1, "Clockwork Hydra"), (1, "Feral Hydra"),
            (1, "Servant of the Scale"), (1, "Ilysian Caryatid"),
            (1, "Fertilid"), (1, "Adventurous Impulse"),
            (1, "Scale the Heights"), (1, "Hunter's Edge"),
            (1, "Soul's Might"), (1, "Hydra's Growth"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Benevolent Hydra"), (1, "Primordial Hydra"),
            (1, "Domesticated Hydra"), (1, "Clockwork Hydra"),
            (1, "Servant of the Scale"), (1, "Kujar Seedsculptor"),
            (1, "Ilysian Caryatid"), (1, "Pridemalkin"),
            (1, "Titanic Brawl"), (1, "Hunter's Edge"),
            (1, "Ordeal of Nylea"), (1, "Hydra's Growth"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
    ],
    "Gigantic": [
        [
            (1, "Runadi, Behemoth Caller"), (1, "Towering Gibbon"),
            (1, "Thrashing Brontodon"), (1, "Woodborn Behemoth"),
            (1, "Naga Vitalist"), (1, "Ondu Giant"),
            (1, "Colossal Dreadmaw"), (1, "Havenwood Wurm"),
            (1, "Pounce"), (1, "Enlarge"),
            (1, "Gift of the Gargantuan"), (1, "Overgrowth"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Runadi, Behemoth Caller"), (1, "Towering Gibbon"),
            (1, "Engulfing Slagwurm"), (1, "Paradise Druid"),
            (1, "Thrashing Brontodon"), (1, "Colossal Dreadmaw"),
            (1, "Ondu Giant"), (1, "Mammoth Spider"),
            (1, "Enlarge"), (1, "Prey Upon"),
            (1, "Gift of the Gargantuan"), (1, "Overgrowth"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
    ],
    "J22 Elves": [
        [
            (1, "Primeval Herald"), (1, "Elvish Warmaster"),
            (1, "Dwynen's Elite"), (1, "Reclamation Sage"),
            (1, "Reckless Amplimancer"), (1, "Elvish Rejuvenator"),
            (1, "Ivy Lane Denizen"), (1, "Elderleaf Mentor"),
            (1, "Might of the Masses"), (1, "Band Together"),
            (1, "Roots of Wisdom"), (1, "Elven Bow"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Primeval Herald"), (1, "Imperious Perfect"),
            (1, "Dwynen's Elite"), (1, "Ghirapur Guide"),
            (1, "Reckless Amplimancer"), (1, "Elvish Rejuvenator"),
            (1, "Ivy Lane Denizen"), (1, "Elderleaf Mentor"),
            (1, "Might of the Masses"), (1, "Band Together"),
            (1, "Roots of Wisdom"), (1, "Elven Bow"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Wolverine Riders"), (1, "Woodland Champion"),
            (1, "Dwynen's Elite"), (1, "Elvish Rejuvenator"),
            (1, "Ivy Lane Denizen"), (1, "Elderleaf Mentor"),
            (1, "Lys Alana Huntmaster"), (1, "Might of the Masses"),
            (1, "Band Together"), (1, "Roots of Wisdom"),
            (1, "Instruments of War"), (1, "Elven Bow"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Dutiful Replicator"), (1, "Wildborn Preserver"),
            (1, "Woodland Champion"), (1, "Dwynen's Elite"),
            (1, "Elvish Rejuvenator"), (1, "Ivy Lane Denizen"),
            (1, "Elderleaf Mentor"), (1, "Band Together"),
            (1, "Overcome"), (1, "Roots of Wisdom"),
            (1, "Presence of Gond"), (1, "Elven Bow"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
    ],
    "Ferocious": [
        [
            (1, "Giant Ladybug"), (1, "Spectral Hunt-Caller"),
            (1, "Rhonas the Indomitable"), (1, "Challenger Troll"),
            (1, "Ilysian Caryatid"), (1, "Drowsing Tyrannodon"),
            (1, "Gaea's Protector"), (1, "Ornery Dilophosaur"),
            (1, "Rampaging Growth"), (1, "Roar of Challenge"),
            (1, "Savage Punch"), (1, "Colossal Majesty"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Giant Ladybug"), (1, "Spectral Hunt-Caller"),
            (1, "Goreclaw, Terror of Qal Sisma"), (1, "Nessian Hornbeetle"),
            (1, "Challenger Troll"), (1, "Ilysian Caryatid"),
            (1, "Drowsing Tyrannodon"), (1, "Gaea's Protector"),
            (1, "Roar of Challenge"), (1, "Savage Punch"),
            (1, "Colossal Majesty"), (1, "Rosethorn Halberd"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Giant Ladybug"), (1, "Spectral Hunt-Caller"),
            (1, "Prowling Serpopard"), (1, "Challenger Troll"),
            (1, "Ilysian Caryatid"), (1, "Drowsing Tyrannodon"),
            (1, "Ornery Dilophosaur"), (1, "Bristling Boar"),
            (1, "Roar of Challenge"), (1, "Savage Punch"),
            (1, "Hammer of Ruin"), (1, "Colossal Majesty"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Towering Gibbon"), (1, "Giant Ladybug"),
            (1, "Spectral Hunt-Caller"), (1, "Creeperhulk"),
            (1, "Challenger Troll"), (1, "Ilysian Caryatid"),
            (1, "Drowsing Tyrannodon"), (1, "Frontier Mastodon"),
            (1, "Roar of Challenge"), (1, "Savage Punch"),
            (1, "Colossal Majesty"), (1, "Kitesail"),
            (1, "Bonders' Enclave"), (1, "Thriving Grove"), (6, "Forest"),
        ],
    ],
    "Insects": [
        [
            (1, "Giant Ladybug"), (1, "Swarm Shambler"),
            (1, "Nessian Hornbeetle"), (1, "Nantuko Cultivator"),
            (1, "Iridescent Hornbeetle"), (1, "Caustic Caterpillar"),
            (1, "Ironshell Beetle"), (1, "Duskshell Crawler"),
            (1, "Moldgraf Millipede"), (1, "Relentless Pursuit"),
            (1, "Hunter's Edge"), (1, "Crawling Sensation"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Zask, Skittering Swarmlord"), (1, "Giant Ladybug"),
            (1, "Kraul Harpooner"), (1, "Circuit Mender"),
            (1, "Nantuko Cultivator"), (1, "Caustic Caterpillar"),
            (1, "Ironshell Beetle"), (1, "Kraul Foragers"),
            (1, "Moldgraf Millipede"), (1, "Relentless Pursuit"),
            (1, "Hunter's Edge"), (1, "Crawling Sensation"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Giant Ladybug"), (1, "Deadbridge Goliath"),
            (1, "Phantom Nantuko"), (1, "Nessian Hornbeetle"),
            (1, "Iridescent Hornbeetle"), (1, "Nantuko Cultivator"),
            (1, "Caustic Caterpillar"), (1, "Drudge Beetle"),
            (1, "Ironshell Beetle"), (1, "Relentless Pursuit"),
            (1, "Hunter's Edge"), (1, "Crawling Sensation"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Zask, Skittering Swarmlord"), (1, "Giant Ladybug"),
            (1, "Nantuko Cultivator"), (1, "Quarry Beetle"),
            (1, "Caustic Caterpillar"), (1, "Ironshell Beetle"),
            (1, "Kraul Warrior"), (1, "Giant Caterpillar"),
            (1, "Rampaging Growth"), (1, "Relentless Pursuit"),
            (1, "Hunter's Edge"), (1, "Crawling Sensation"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
    ],
    "Wolves": [
        [
            (1, "Arlinn, Voice of the Pack"), (1, "Mild-Mannered Librarian"),
            (1, "Spectral Hunt-Caller"), (1, "Wicked Wolf"),
            (1, "Packsong Pup"), (1, "Ferocious Pup"),
            (1, "Bounding Wolf"), (1, "Fierce Witchstalker"),
            (1, "Wolf's Quarry"), (1, "Prey Upon"),
            (1, "Wolfwillow Haven"), (1, "Wolfkin Bond"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Arlinn, Voice of the Pack"), (1, "Mild-Mannered Librarian"),
            (1, "Spectral Hunt-Caller"), (1, "Master of the Wild Hunt"),
            (1, "Howlgeist"), (1, "Young Wolf"),
            (1, "Pestilent Wolf"), (1, "Bounding Wolf"),
            (1, "Ferocious Pup"), (1, "Moonlight Hunt"),
            (1, "Prey Upon"), (1, "Howl of the Hunt"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Arlinn, Voice of the Pack"), (1, "Mild-Mannered Librarian"),
            (1, "Spectral Hunt-Caller"), (1, "Kessig Cagebreakers"),
            (1, "Sporeback Wolf"), (1, "Ferocious Pup"),
            (1, "Bounding Wolf"), (1, "Predator's Howl"),
            (1, "Hunger of the Howlpack"), (1, "Prey Upon"),
            (1, "Feed the Pack"), (1, "Wolfwillow Haven"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
        [
            (1, "Arlinn, Voice of the Pack"), (1, "Mild-Mannered Librarian"),
            (1, "Spectral Hunt-Caller"), (1, "Nightpack Ambusher"),
            (1, "Briarpack Alpha"), (1, "Pestilent Wolf"),
            (1, "Ferocious Pup"), (1, "Bounding Wolf"),
            (1, "Flourishing Hunter"), (1, "Prey Upon"),
            (1, "Wolfwillow Haven"), (1, "Howl of the Hunt"),
            (1, "Thriving Grove"), (7, "Forest"),
        ],
    ],
    # ===== COLORLESS =====
    "Urza's": [
        [
            (1, "Karn Liberated"), (1, "Walking Ballista"),
            (1, "Golem Artisan"), (1, "Meteor Golem"),
            (1, "Locthwain Gargoyle"), (1, "Runed Servitor"),
            (1, "Manakin"), (1, "Thaumaturge's Familiar"),
            (1, "Planar Atlas"), (1, "Universal Solvent"),
            (1, "Alchemist's Vial"), (1, "Expedition Map"),
            (1, "Urza's Factory"), (3, "Urza's Tower"),
            (2, "Urza's Power Plant"), (2, "Urza's Mine"),
        ],
    ],
}
# fmt: on


XMAGE_J22_JAVA = Path("Mage.Sets/src/mage/sets/Jumpstart2022.java")


def _parse_xmage_j22() -> dict[str, str]:
    """Parse XMage's Jumpstart2022.java to get card name -> collector number.

    XMage's collector numbers differ from Scryfall's, so we use XMage's
    own data as the source of truth for J22 cards.
    """
    text = XMAGE_J22_JAVA.read_text()
    card_map: dict[str, str] = {}
    for m in re.finditer(r'new SetCardInfo\("(.+?)",\s*"?(\d+)"?', text):
        name, num = m.group(1), m.group(2)
        if name not in card_map:  # keep first occurrence
            card_map[name] = num
    return card_map


def resolve_all_cards(
    themes: dict[str, list[list[tuple[int, str]]]],
) -> dict[str, tuple[str, str]]:
    """Resolve all unique non-land card names to (set_code, collector_number).

    First checks XMage's own J22 set file for collector numbers (since XMage's
    numbering differs from Scryfall). Falls back to Scryfall for cards not in J22.
    """
    all_names: set[str] = set()
    for variants in themes.values():
        for cards in variants:
            for _qty, name in cards:
                if name not in _BASIC_LAND_DEFAULTS:
                    all_names.add(name)

    # Step 1: Check XMage's J22 set file first (authoritative for J22 collector numbers)
    xmage_j22 = _parse_xmage_j22()
    resolved: dict[str, tuple[str, str]] = {}
    remaining: set[str] = set()
    for name in all_names:
        if name in xmage_j22:
            resolved[name] = ("J22", xmage_j22[name])
        else:
            remaining.add(name)

    print(f"Resolved {len(resolved)} cards from XMage J22 set file")

    if not remaining:
        return resolved

    # Step 2: Use Scryfall for cards not in J22
    print(f"Resolving {len(remaining)} remaining cards via Scryfall...")
    name_list = sorted(remaining)

    for i in range(0, len(name_list), 75):
        batch = name_list[i : i + 75]
        found, not_found = scryfall.collection(batch)
        for card in found:
            name = card["name"]
            set_code = card["set"].upper()
            collector = card["collector_number"]
            resolved[name] = (set_code, collector)
        for nf in not_found:
            print(f"  WARNING: not found in collection: {nf['name']}")

    # For cards not found via collection, try individual lookup
    missing = remaining - set(resolved.keys())
    for name in sorted(missing):
        lookup = scryfall.named(name)
        if lookup is None and " // " in name:
            lookup = scryfall.named(name.split(" // ")[0])
        if lookup is not None:
            resolved[name] = (lookup["set"].upper(), lookup["collector_number"])
        else:
            print(f"  ERROR: card not found at all: {name}")

    return resolved


def format_theme_entry(
    theme: str,
    variant_idx: int,
    num_variants: int,
    cards: list[tuple[int, str]],
    resolved: dict[str, tuple[str, str]],
) -> str:
    """Format a single theme variant in jumpstart.txt format."""
    if num_variants == 1:
        header = f"# {theme}"
    else:
        header = f"# {theme} ({variant_idx + 1})"

    lines = [header]
    for qty, name in cards:
        if name in _BASIC_LAND_DEFAULTS:
            set_code, num = _BASIC_LAND_DEFAULTS[name]
        else:
            assert name in resolved, f"Card not resolved: {name}"
            set_code, num = resolved[name]
        lines.append(f"{qty} {set_code} {num} {name}")

    return "\n".join(lines)


def main() -> None:
    resolved = resolve_all_cards(J22_THEMES)

    # Validate all cards resolved
    all_names: set[str] = set()
    for variants in J22_THEMES.values():
        for cards in variants:
            for _qty, name in cards:
                if name not in _BASIC_LAND_DEFAULTS:
                    all_names.add(name)

    missing = all_names - set(resolved.keys())
    assert not missing, f"Unresolved cards: {missing}"

    # Validate card counts
    for theme, variants in J22_THEMES.items():
        for i, cards in enumerate(variants):
            total = sum(qty for qty, _ in cards)
            assert total == 20, (
                f"{theme} variant {i + 1} has {total} cards, expected 20"
            )

    # Generate jumpstart.txt entries
    entries: list[str] = []
    for theme in sorted(J22_THEMES.keys()):
        variants = J22_THEMES[theme]
        for i, cards in enumerate(variants):
            entry = format_theme_entry(theme, i, len(variants), cards, resolved)
            entries.append(entry)

    text_to_append = "\n\n" + "\n\n".join(entries) + "\n"

    for path in [JUMPSTART_TXT, JUMPSTART_CUSTOM]:
        path.write_text(path.read_text() + text_to_append)
        print(f"Appended {len(entries)} theme variants to {path}")

    print(f"\nDone! Added {len(J22_THEMES)} themes ({len(entries)} variants total)")


if __name__ == "__main__":
    main()
