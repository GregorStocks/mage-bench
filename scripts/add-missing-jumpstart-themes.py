#!/usr/bin/env python3
"""Add missing Jumpstart 2020 themes to jumpstart.txt.

The XMage jumpstart.txt only has 31 of 46 official themes.
This script adds the 15 missing themes by resolving card names
to JMP/M21 collector numbers via Scryfall.

Usage:
    uv run python scripts/add-missing-jumpstart-themes.py
"""

from __future__ import annotations

from scripts import scryfall

# fmt: off
# Missing themes with card names and quantities.
# Format: list of (quantity, card_name) tuples per variant.
# Land counts are at the end. Themed basic lands use generic JMP printings.
MISSING_THEMES: dict[str, list[list[tuple[int, str]]]] = {
    # ---- WHITE ----
    "Angels": [
        # Angels (1)
        [
            (1, "Angelic Page"), (1, "Anointed Chorister"), (1, "Celestial Enforcer"),
            (1, "Baneslayer Angel"), (1, "Emancipation Angel"), (1, "Serra Angel"),
            (1, "Voice of the Provinces"), (1, "Angelic Ascension"), (1, "Feat of Resistance"),
            (1, "Angelic Edict"), (1, "Guardian Idol"), (1, "Scroll of Avacyn"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Angels (2)
        [
            (1, "Angelic Arbiter"), (1, "Angelic Page"), (1, "Anointed Chorister"),
            (1, "Celestial Enforcer"), (1, "Emancipation Angel"), (1, "Linvala, Keeper of Silence"),
            (1, "Serra Angel"), (1, "Angelic Ascension"), (1, "Take Heart"),
            (1, "Angelic Edict"), (1, "Guardian Idol"), (1, "Scroll of Avacyn"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Doctor": [
        # Doctor (1)
        [
            (1, "Anointed Chorister"), (1, "Basri's Acolyte"), (1, "Brightmare"),
            (1, "Bulwark Giant"), (1, "Mesa Unicorn"), (1, "Speaker of the Heavens"),
            (1, "Revitalize"), (1, "Swift Response"), (1, "Take Heart"),
            (1, "Faith's Fetters"), (1, "Griffin Aerie"), (1, "Light of Promise"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Doctor (2)
        [
            (1, "Angel of Mercy"), (1, "Anointed Chorister"), (1, "Basri's Acolyte"),
            (1, "Brightmare"), (1, "Speaker of the Heavens"), (1, "Stone Haven Pilgrim"),
            (1, "Moment of Heroism"), (1, "Revitalize"), (1, "Swift Response"),
            (1, "Secure the Scene"), (1, "Griffin Aerie"), (1, "Light of Promise"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Doctor (3)
        [
            (1, "Angel of Mercy"), (1, "Anointed Chorister"), (1, "Basri's Acolyte"),
            (1, "Brightmare"), (1, "Mesa Unicorn"), (1, "Rhox Faithmender"),
            (1, "Moment of Heroism"), (1, "Revitalize"), (1, "Swift Response"),
            (1, "Faith's Fetters"), (1, "Griffin Aerie"), (1, "Light of Promise"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Doctor (4)
        [
            (1, "Alabaster Mage"), (1, "Angel of Mercy"), (1, "Anointed Chorister"),
            (1, "Basri's Acolyte"), (1, "Brightmare"), (1, "Bulwark Giant"),
            (1, "Stone Haven Pilgrim"), (1, "Revitalize"), (1, "Swift Response"),
            (1, "Cradle of Vitality"), (1, "Griffin Aerie"), (1, "Path of Bravery"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Enchanted": [
        # Enchanted (1)
        [
            (1, "Anointed Chorister"), (1, "Blessed Spirits"), (1, "Knight of the Tusk"),
            (1, "Kor Spiritdancer"), (1, "Stone Haven Pilgrim"), (1, "Trusty Retriever"),
            (1, "Dub"), (1, "Face of Divinity"), (1, "Faith's Fetters"),
            (1, "Forced Worship"), (1, "Indomitable Will"), (1, "Knightly Valor"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Enchanted (2)
        [
            (1, "Ajani's Chosen"), (1, "Blessed Spirits"), (1, "Bulwark Giant"),
            (1, "Staunch Shieldmate"), (1, "Stone Haven Pilgrim"), (1, "Trusty Retriever"),
            (1, "Celestial Mantle"), (1, "Face of Divinity"), (1, "Faith's Fetters"),
            (1, "Forced Worship"), (1, "Indomitable Will"), (1, "Knightly Valor"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    "Legion": [
        # Legion (1)
        [
            (1, "Knight of the Tusk"), (1, "Blessed Sanctuary"), (1, "Inspired Charge"),
            (1, "Raise the Alarm"), (1, "Selfless Savior"), (1, "Falconer Adept"),
            (1, "Basri's Solidarity"), (1, "Valorous Steed"), (1, "Daybreak Charger"),
            (1, "Makeshift Battalion"), (1, "Legion's Judgment"),
            (1, "Thriving Heath"), (8, "Plains"),
        ],
        # Legion (2)
        [
            (1, "Inspiring Captain"), (1, "Release the Dogs"), (1, "Glorious Anthem"),
            (1, "Raise the Alarm"), (1, "Siege Striker"), (1, "Faith's Fetters"),
            (1, "Basri's Solidarity"), (1, "Valorous Steed"), (1, "Daybreak Charger"),
            (1, "Makeshift Battalion"), (1, "Staunch Shieldmate"), (1, "Legion's Judgment"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Legion (3)
        [
            (1, "Legion's Judgment"), (1, "Release the Dogs"), (1, "Fortify"),
            (1, "Raise the Alarm"), (1, "Glorious Anthem"), (1, "Siege Striker"),
            (1, "Faith's Fetters"), (1, "Basri's Solidarity"), (1, "Valorous Steed"),
            (1, "Daybreak Charger"), (1, "Makeshift Battalion"), (1, "Staunch Shieldmate"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
        # Legion (4)
        [
            (1, "Lena, Selfless Champion"), (1, "Mentor of the Meek"), (1, "Fortify"),
            (1, "Raise the Alarm"), (1, "Siege Striker"), (1, "Faith's Fetters"),
            (1, "Selfless Savior"), (1, "Basri's Solidarity"), (1, "Valorous Steed"),
            (1, "Daybreak Charger"), (1, "Makeshift Battalion"), (1, "Legion's Judgment"),
            (1, "Thriving Heath"), (7, "Plains"),
        ],
    ],
    # ---- BLUE ----
    "Above the Clouds": [
        # Above the Clouds (1)
        [
            (1, "Inniaz, the Gale Force"), (1, "Keen Glidemaster"), (1, "Mistral Singer"),
            (1, "Roaming Ghostlight"), (1, "Tide Skimmer"), (1, "Wall of Runes"),
            (1, "Warden of Evos Isle"), (1, "Lofty Denial"), (1, "Rain of Revelation"),
            (1, "Rookie Mistake"), (1, "Unsubstantiate"), (1, "Capture Sphere"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        # Above the Clouds (2)
        [
            (1, "Inniaz, the Gale Force"), (1, "Keen Glidemaster"), (1, "Mistral Singer"),
            (1, "Roaming Ghostlight"), (1, "Serendib Efreet"), (1, "Tide Skimmer"),
            (1, "Wall of Runes"), (1, "Lofty Denial"), (1, "Rain of Revelation"),
            (1, "Unsubstantiate"), (1, "Talrand's Invocation"), (1, "Capture Sphere"),
            (1, "Frost Breath"), (1, "Thriving Isle"), (6, "Island"),
        ],
        # Above the Clouds (3)
        [
            (1, "Keen Glidemaster"), (1, "Roaming Ghostlight"), (1, "Tide Skimmer"),
            (1, "Wall of Runes"), (1, "Warden of Evos Isle"), (1, "Windreader Sphinx"),
            (1, "Lofty Denial"), (1, "Rain of Revelation"), (1, "Unsubstantiate"),
            (1, "Frost Breath"), (1, "Capture Sphere"),
            (1, "Thriving Isle"), (8, "Island"),
        ],
        # Above the Clouds (4)
        [
            (1, "Keen Glidemaster"), (1, "Kira, Great Glass-Spinner"), (1, "Mistral Singer"),
            (1, "Roaming Ghostlight"), (1, "Tide Skimmer"), (1, "Wall of Runes"),
            (1, "Windstorm Drake"), (1, "Lofty Denial"), (1, "Rain of Revelation"),
            (1, "Rookie Mistake"), (1, "Unsubstantiate"), (1, "Capture Sphere"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Milling": [
        # Milling (mythic - only 1 variant)
        [
            (1, "Belltower Sphinx"), (1, "Bruvac the Grandiloquent"), (1, "Reckless Scholar"),
            (1, "Selhoff Occultist"), (1, "Towering-Wave Mystic"), (1, "Vedalken Entrancer"),
            (1, "Wall of Lost Thoughts"), (1, "Sweep Away"), (1, "Thought Collapse"),
            (1, "Thought Scour"), (1, "Capture Sphere"), (1, "Teferi's Tutelage"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    "Spirits": [
        # Spirits (1)
        [
            (1, "Battleground Geist"), (1, "Departed Deckhand"), (1, "Murmuring Phantasm"),
            (1, "Nebelgast Herald"), (1, "Roaming Ghostlight"), (1, "Shacklegeist"),
            (1, "Tome Anima"), (1, "Befuddle"), (1, "Essence Flux"), (1, "Frost Breath"),
            (1, "Winged Words"), (1, "Capture Sphere"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
        # Spirits (2)
        [
            (1, "Battleground Geist"), (1, "Departed Deckhand"), (1, "Nebelgast Herald"),
            (1, "Rattlechains"), (1, "Roaming Ghostlight"), (1, "Shacklegeist"),
            (1, "Tome Anima"), (1, "Frost Breath"), (1, "Rewind"), (1, "Rookie Mistake"),
            (1, "Winged Words"), (1, "Capture Sphere"),
            (1, "Thriving Isle"), (7, "Island"),
        ],
    ],
    # ---- BLACK ----
    "Rogues": [
        # Rogues (1)
        [
            (1, "Gonti, Lord of Luxury"), (1, "Lawless Broker"), (1, "Masked Blackguard"),
            (1, "Mausoleum Turnkey"), (1, "Nightshade Stinger"), (1, "Nocturnal Feeder"),
            (1, "Oona's Blackguard"), (1, "Thieves' Guild Enforcer"),
            (1, "Alchemist's Gift"), (1, "Finishing Blow"), (1, "Stab Wound"),
            (1, "Rogue's Gloves"), (1, "Thriving Moor"), (7, "Swamp"),
        ],
        # Rogues (2)
        [
            (1, "Corpse Hauler"), (1, "Corpse Traders"), (1, "Lawless Broker"),
            (1, "Masked Blackguard"), (1, "Nightshade Stinger"), (1, "Nocturnal Feeder"),
            (1, "Oona's Blackguard"), (1, "Thieves' Guild Enforcer"),
            (1, "Alchemist's Gift"), (1, "Last Gasp"), (1, "Stab Wound"),
            (1, "Rogue's Gloves"), (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    "Spooky": [
        # Spooky (1)
        [
            (1, "Bone Picker"), (1, "Caged Zombie"), (1, "Crypt Lurker"),
            (1, "Dutiful Attendant"), (1, "Eternal Taskmaster"), (1, "Fetid Imp"),
            (1, "Gristle Grinner"), (1, "Liliana's Standard Bearer"),
            (1, "Barter in Blood"), (1, "Finishing Blow"), (1, "Malefic Scythe"),
            (1, "Village Rites"), (1, "Thriving Moor"), (7, "Swamp"),
        ],
        # Spooky (2)
        [
            (1, "Bone Picker"), (1, "Caged Zombie"), (1, "Crypt Lurker"),
            (1, "Dutiful Attendant"), (1, "Eternal Taskmaster"), (1, "Gristle Grinner"),
            (1, "Harvester of Souls"), (1, "Sanitarium Skeleton"),
            (1, "Barter in Blood"), (1, "Bone Splinters"), (1, "Finishing Blow"),
            (1, "Malefic Scythe"), (1, "Thriving Moor"), (7, "Swamp"),
        ],
        # Spooky (3)
        [
            (1, "Bone Picker"), (1, "Caged Zombie"), (1, "Crypt Lurker"),
            (1, "Dutiful Attendant"), (1, "Eternal Taskmaster"), (1, "Fetid Imp"),
            (1, "Gristle Grinner"), (1, "Liliana's Devotee"),
            (1, "Finishing Blow"), (1, "Languish"), (1, "Malefic Scythe"),
            (1, "Ogre Slumlord"), (1, "Thriving Moor"), (7, "Swamp"),
        ],
        # Spooky (4)
        [
            (1, "Bone Picker"), (1, "Caged Zombie"), (1, "Crypt Lurker"),
            (1, "Dutiful Attendant"), (1, "Eternal Taskmaster"), (1, "Gristle Grinner"),
            (1, "Liliana's Devotee"), (1, "Plagued Rusalka"), (1, "Sanitarium Skeleton"),
            (1, "Black Market"), (1, "Finishing Blow"), (1, "Malefic Scythe"),
            (1, "Thriving Moor"), (7, "Swamp"),
        ],
    ],
    # ---- RED ----
    "Devilish": [
        # Devilish (1)
        [
            (1, "Chained Brute"), (1, "Forge Devil"), (1, "Havoc Jester"),
            (1, "Hobblefiend"), (1, "Pitchburn Devils"), (1, "Spiteful Prankster"),
            (1, "Torch Fiend"), (1, "Zurzoth, Chaos Rider"),
            (1, "Collateral Damage"), (1, "Dance with Devils"), (1, "Heartfire"),
            (1, "Traitorous Greed"), (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Devilish (2)
        [
            (1, "Chained Brute"), (1, "Havoc Jester"), (1, "Hobblefiend"),
            (1, "Pitchburn Devils"), (1, "Spiteful Prankster"), (1, "Tibalt's Rager"),
            (2, "Torch Fiend"), (1, "Zurzoth, Chaos Rider"),
            (1, "Collateral Damage"), (1, "Heartfire"), (1, "Traitorous Greed"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Devilish (3)
        [
            (1, "Brash Taunter"), (1, "Chained Brute"), (1, "Havoc Jester"),
            (1, "Hobblefiend"), (1, "Lightning-Core Excavator"), (1, "Pitchburn Devils"),
            (1, "Spiteful Prankster"), (1, "Torch Fiend"),
            (1, "Act of Treason"), (1, "Collateral Damage"), (1, "Dance with Devils"),
            (1, "Traitorous Greed"), (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Devilish (4)
        [
            (1, "Chained Brute"), (1, "Havoc Jester"), (1, "Hellrider"),
            (1, "Hobblefiend"), (1, "Lightning-Core Excavator"), (1, "Pitchburn Devils"),
            (1, "Sin Prodder"), (1, "Spiteful Prankster"),
            (1, "Act of Treason"), (1, "Barrage of Expendables"), (1, "Collateral Damage"),
            (1, "Traitorous Greed"), (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Dragons": [
        # Dragons (1)
        [
            (1, "Dragonloft Idol"), (1, "Dragonspeaker Shaman"), (1, "Dragon Hatchling"),
            (1, "Hellkite Punisher"), (1, "Lightning Shrieker"), (1, "Rapacious Dragon"),
            (1, "Terror of the Peaks"), (1, "Draconic Roar"), (1, "Thrill of Possibility"),
            (1, "Bathe in Dragonfire"), (1, "Dragon Fodder"),
            (1, "Thriving Bluff"), (8, "Mountain"),
        ],
        # Dragons (2)
        [
            (1, "Dragonlord's Servant"), (1, "Dragon Hatchling"), (1, "Furnace Whelp"),
            (1, "Gadrak, the Crown-Scourge"), (1, "Hellkite Punisher"),
            (1, "Lathliss, Dragon Queen"), (1, "Lightning Shrieker"), (1, "Rapacious Dragon"),
            (1, "Draconic Roar"), (1, "Sarkhan's Rage"), (1, "Dragon Fodder"),
            (1, "Thriving Bluff"), (8, "Mountain"),
        ],
    ],
    "Goblins": [
        # Goblins (1)
        [
            (1, "Beetleback Chief"), (1, "Boggart Brute"), (1, "Goblin Arsonist"),
            (1, "Goblin Commando"), (1, "Goblin Instigator"), (1, "Goblin Shortcutter"),
            (1, "Muxus, Goblin Grandee"), (1, "Ornery Goblin"), (1, "Volley Veteran"),
            (1, "Outnumber"), (1, "Shock"), (1, "Makeshift Munitions"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Goblins (2)
        [
            (1, "Battle-Rattle Shaman"), (1, "Beetleback Chief"), (1, "Boggart Brute"),
            (1, "Goblin Arsonist"), (1, "Goblin Commando"), (1, "Goblin Instigator"),
            (1, "Goblin Shortcutter"), (1, "Muxus, Goblin Grandee"), (1, "Ornery Goblin"),
            (1, "Burn Bright"), (1, "Outnumber"), (1, "Makeshift Munitions"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Goblins (3)
        [
            (1, "Beetleback Chief"), (1, "Boggart Brute"), (1, "Goblin Arsonist"),
            (1, "Goblin Chieftain"), (1, "Goblin Commando"), (1, "Goblin Goon"),
            (1, "Goblin Instigator"), (1, "Goblin Shortcutter"), (1, "Ornery Goblin"),
            (1, "Volley Veteran"), (1, "Shock"), (1, "Makeshift Munitions"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Goblins (4)
        [
            (1, "Boggart Brute"), (1, "Goblin Arsonist"), (1, "Goblin Commando"),
            (1, "Goblin Instigator"), (1, "Goblin Shortcutter"), (1, "Krenko, Mob Boss"),
            (1, "Ornery Goblin"), (1, "Burn Bright"), (1, "Outnumber"),
            (1, "Goblin Lore"), (1, "Goblin Rally"), (1, "Makeshift Munitions"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Minotaurs": [
        # Minotaurs (1)
        [
            (1, "Bloodrage Brawler"), (1, "Borderland Minotaur"), (1, "Lightning Visionary"),
            (1, "Minotaur Skullcleaver"), (1, "Minotaur Sureshot"),
            (1, "Sethron, Hurloon General"), (1, "Warfire Javelineer"),
            (1, "Soul Sear"), (1, "Sure Strike"), (1, "Flurry of Horns"),
            (1, "Mugging"), (1, "Herald's Horn"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Minotaurs (2)
        [
            (1, "Bloodrage Brawler"), (1, "Borderland Minotaur"), (1, "Lightning Visionary"),
            (1, "Minotaur Skullcleaver"), (1, "Minotaur Sureshot"),
            (1, "Rageblood Shaman"), (1, "Sethron, Hurloon General"),
            (1, "Soul Sear"), (1, "Sure Strike"), (1, "Unleash Fury"),
            (1, "Flurry of Horns"), (1, "Traitorous Greed"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Smashing": [
        # Smashing (1)
        [
            (1, "Bloodrage Brawler"), (1, "Bone Pit Brute"), (1, "Borderland Marauder"),
            (1, "Flametongue Kavu"), (1, "Goblin Warchief"), (1, "Onakke Ogre"),
            (1, "Tectonic Giant"),
            (1, "Burst Lightning"), (1, "Temur Battle Rage"), (1, "Furious Rise"),
            (1, "Molten Birth"),
            (1, "Thriving Bluff"), (8, "Mountain"),
        ],
        # Smashing (2)
        [
            (1, "Bloodrage Brawler"), (1, "Bone Pit Brute"), (1, "Borderland Marauder"),
            (1, "Flametongue Kavu"), (1, "Heartfire Immolator"), (1, "Onakke Ogre"),
            (1, "Tectonic Giant"),
            (1, "Burst Lightning"), (1, "Collision // Colossus"), (1, "Temur Battle Rage"),
            (1, "Furious Rise"), (1, "Molten Birth"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Smashing (3)
        [
            (1, "Hamletback Goliath"), (1, "Bloodrage Brawler"), (1, "Bone Pit Brute"),
            (1, "Borderland Marauder"), (1, "Heartfire Immolator"), (1, "Inferno Hellion"),
            (1, "Onakke Ogre"), (1, "Turret Ogre"),
            (1, "Fling"), (1, "Unleash Fury"), (1, "Furious Rise"),
            (1, "Sarkhan's Unsealing"), (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Smashing (4)
        [
            (1, "Etali, Primal Storm"), (1, "Bloodrage Brawler"), (1, "Bloodshot Trainee"),
            (1, "Bone Pit Brute"), (1, "Borderland Marauder"), (1, "Heartfire Immolator"),
            (1, "Onakke Ogre"), (1, "Turret Ogre"),
            (1, "Fling"), (1, "Hungry Flames"), (1, "Furious Rise"),
            (1, "Furor of the Bitten"), (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    "Spellcasting": [
        # Spellcasting (1)
        [
            (1, "Thermo-Alchemist"), (1, "Living Lightning"), (1, "Lightning Visionary"),
            (1, "Chandra's Pyreling"), (1, "Kinetic Augur"), (1, "Heartfire Immolator"),
            (1, "Blindblast"), (1, "Hungry Flames"), (1, "Shock"),
            (1, "Thrill of Possibility"), (1, "Goblin Wizardry"), (1, "Double Vision"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Spellcasting (2)
        [
            (1, "Young Pyromancer"), (1, "Thermo-Alchemist"), (1, "Kiln Fiend"),
            (1, "Lightning Visionary"), (1, "Chandra's Pyreling"), (1, "Kinetic Augur"),
            (1, "Heartfire Immolator"), (1, "Flame Lash"), (1, "Hungry Flames"),
            (1, "Shock"), (1, "Thrill of Possibility"), (1, "Double Vision"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Spellcasting (3)
        [
            (1, "Charmbreaker Devils"), (1, "Dualcaster Mage"), (1, "Living Lightning"),
            (1, "Thermo-Alchemist"), (1, "Lightning Visionary"), (1, "Chandra's Pyreling"),
            (1, "Kinetic Augur"), (1, "Heartfire Immolator"),
            (1, "Dragon Fodder"), (1, "Hungry Flames"), (1, "Shock"),
            (1, "Thrill of Possibility"), (1, "Thriving Bluff"), (7, "Mountain"),
        ],
        # Spellcasting (4)
        [
            (1, "Thermo-Alchemist"), (1, "Kiln Fiend"), (1, "Lightning Visionary"),
            (1, "Chandra's Pyreling"), (1, "Kinetic Augur"), (1, "Heartfire Immolator"),
            (1, "Crash Through"), (1, "Hungry Flames"), (1, "Shock"),
            (1, "Thrill of Possibility"), (1, "Doublecast"), (1, "Immolating Gyre"),
            (1, "Thriving Bluff"), (7, "Mountain"),
        ],
    ],
    # ---- GREEN ----
    "Plus One": [
        # Plus One (1)
        [
            (1, "Armorcraft Judge"), (1, "Fertilid"), (1, "Ironshell Beetle"),
            (1, "Nessian Hornbeetle"), (1, "Pridemalkin"), (1, "Trufflesnout"),
            (1, "Wildwood Scourge"), (1, "Arbor Armament"), (1, "Invigorating Surge"),
            (1, "Hunter's Edge"), (1, "Primeval Bounty"),
            (1, "Thriving Grove"), (8, "Forest"),
        ],
        # Plus One (2)
        [
            (1, "Armorcraft Judge"), (1, "Fertilid"), (1, "Ironshell Beetle"),
            (1, "Nessian Hornbeetle"), (1, "Pridemalkin"), (1, "Trufflesnout"),
            (1, "Wildwood Scourge"), (1, "Arbor Armament"), (1, "Invigorating Surge"),
            (1, "Hunter's Edge"), (1, "Branching Evolution"),
            (1, "Thriving Grove"), (8, "Forest"),
        ],
        # Plus One (3)
        [
            (1, "Fertilid"), (1, "Nessian Hornbeetle"), (1, "Pridemalkin"),
            (1, "Rishkar, Peema Renegade"), (1, "Scrounging Bandar"), (1, "Trufflesnout"),
            (1, "Wildwood Scourge"), (1, "Arbor Armament"), (1, "Inspiring Call"),
            (1, "Invigorating Surge"), (1, "Hunter's Edge"),
            (1, "Thriving Grove"), (8, "Forest"),
        ],
        # Plus One (4)
        [
            (1, "Champion of Lambholt"), (1, "Nessian Hornbeetle"), (1, "Pridemalkin"),
            (1, "Scrounging Bandar"), (1, "Trufflesnout"), (1, "Wildwood Scourge"),
            (1, "Arbor Armament"), (1, "Invigorating Surge"), (1, "Lifecrafter's Gift"),
            (1, "Hunter's Edge"), (1, "Branching Evolution"),
            (1, "Thriving Grove"), (8, "Forest"),
        ],
    ],
}
# fmt: on

# Basic land names -> preferred JMP collector numbers (use first available)
_BASIC_LAND_DEFAULTS: dict[str, tuple[str, str]] = {
    "Plains": ("JMP", "45"),
    "Island": ("JMP", "50"),
    "Swamp": ("JMP", "57"),
    "Mountain": ("JMP", "64"),
    "Forest": ("JMP", "74"),
}

# Cards that appear in M21 rather than JMP, or need specific set preference
_SET_PREFERENCE = {"JMP", "M21"}


def resolve_all_cards(
    themes: dict[str, list[list[tuple[int, str]]]],
) -> dict[str, tuple[str, str]]:
    """Resolve all unique non-land card names to (set_code, collector_number)."""
    # Collect unique names
    all_names: set[str] = set()
    for variants in themes.values():
        for cards in variants:
            for _qty, name in cards:
                if name not in _BASIC_LAND_DEFAULTS:
                    all_names.add(name)

    print(f"Resolving {len(all_names)} unique card names via Scryfall...")

    # Use Scryfall collection API
    name_list = sorted(all_names)
    resolved: dict[str, tuple[str, str]] = {}

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

    # For cards not found via collection, try individual lookup preferring JMP/M21
    missing = all_names - set(resolved.keys())
    for name in sorted(missing):
        card = scryfall.named(name)
        if card is None and " // " in name:
            # Split cards: try front half name (Scryfall resolves to full card)
            card = scryfall.named(name.split(" // ")[0])
        if card is not None:
            resolved[name] = (card["set"].upper(), card["collector_number"])
        else:
            print(f"  ERROR: card not found at all: {name}")

    # Now try to find JMP/M21 printings for cards resolved to other sets
    recheck: list[str] = []
    for name, (set_code, _) in resolved.items():
        if set_code not in _SET_PREFERENCE:
            recheck.append(name)

    if recheck:
        print(f"Re-resolving {len(recheck)} cards to find JMP/M21 printings...")
        for name in recheck:
            # For split cards, search by front half name
            search_name = name.split(" // ")[0] if " // " in name else name
            # Search for JMP printing first, then M21
            for preferred_set in ["jmp", "m21"]:
                results = scryfall.search(f'!"{search_name}" set:{preferred_set}')
                if results:
                    card = results[0]
                    resolved[name] = (card["set"].upper(), card["collector_number"])
                    break

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
    resolved = resolve_all_cards(MISSING_THEMES)

    # Validate all cards resolved
    all_names: set[str] = set()
    for variants in MISSING_THEMES.values():
        for cards in variants:
            for _qty, name in cards:
                if name not in _BASIC_LAND_DEFAULTS:
                    all_names.add(name)

    missing = all_names - set(resolved.keys())
    assert not missing, f"Unresolved cards: {missing}"

    # Validate card counts
    for theme, variants in MISSING_THEMES.items():
        for i, cards in enumerate(variants):
            total = sum(qty for qty, _ in cards)
            assert total == 20, (
                f"{theme} variant {i + 1} has {total} cards, expected 20"
            )

    # Generate jumpstart.txt entries
    entries: list[str] = []
    for theme in sorted(MISSING_THEMES.keys()):
        variants = MISSING_THEMES[theme]
        for i, cards in enumerate(variants):
            entry = format_theme_entry(theme, i, len(variants), cards, resolved)
            entries.append(entry)

    new_content = "\n\n".join(entries) + "\n"

    # Append to both jumpstart.txt files
    for path in [
        "Mage/src/main/resources/jumpstart/jumpstart.txt",
        "Mage.Client/release/sample-decks/Jumpstart/jumpstart_custom.txt",
    ]:
        with open(path, "a") as f:
            f.write("\n" + new_content)
        print(f"Appended {len(entries)} theme variants to {path}")

    print(f"\nDone! Added {len(MISSING_THEMES)} themes ({len(entries)} variants total)")


if __name__ == "__main__":
    main()
