import { describe, it, expect } from "vitest";

const gameViewerModule = await import("../src/scripts/game-viewer.js");
const GV = gameViewerModule.default ?? gameViewerModule.GameViewer ?? window.GameViewer;

// ── chosenDisplayText ─────────────────────────────────────────

describe("chosenDisplayText", () => {
  it("renders cast with mana cost", () => {
    expect(GV.chosenDisplayText({
      chosen: 0,
      chosenArgs: { choice: "p3" },
      message: "Play spells and abilities",
      choices: [{ name: "Lightning Bolt", index: 0, action: "cast", id: "p3", mana_cost: "{R}" }],
    })).toBe("Cast Lightning Bolt {R}");
  });

  it("renders cast without mana cost", () => {
    expect(GV.chosenDisplayText({
      chosen: 0,
      chosenArgs: { choice: "p10" },
      message: "Play spells and abilities",
      choices: [{ name: "Evoke Elemental", index: 0, action: "cast", id: "p10" }],
    })).toBe("Cast Evoke Elemental");
  });

  it("renders land drop", () => {
    expect(GV.chosenDisplayText({
      chosen: 0,
      chosenArgs: { choice: "p10" },
      message: "Play spells and abilities",
      choices: [{ name: "Forest", index: 0, action: "land", id: "p10" }],
    })).toBe("Play Forest");
  });

  it("renders activate", () => {
    expect(GV.chosenDisplayText({
      chosen: 0,
      chosenArgs: { choice: "p3" },
      message: "Play spells and abilities",
      choices: [{ name: "Blighted Bat", index: 0, action: "activate", id: "p3" }],
    })).toBe("Activate Blighted Bat");
  });

  it("renders pass via false chosen on play message", () => {
    expect(GV.chosenDisplayText({
      chosen: false,
      chosenArgs: { choice: "no" },
      message: "Play spells and abilities",
      choices: [{ name: "Forest", index: 0, action: "land", id: "p42" }],
    })).toBe("Pass");
  });

  it("renders pass via false chosen on instants message", () => {
    expect(GV.chosenDisplayText({
      chosen: false,
      chosenArgs: { choice: "no" },
      message: "Play instants and activated abilities",
      choices: [],
    })).toBe("Pass");
  });

  it("renders pass via null chosen with empty args", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: {},
      message: "Play spells and abilities",
      choices: [{ name: "Forest", index: 0, action: "land", id: "p42" }],
    })).toBe("Pass");
  });

  it("renders mulligan yes", () => {
    expect(GV.chosenDisplayText({
      chosen: true,
      chosenArgs: { answer: true },
      message: "Mulligan down to 6 cards?",
      choices: [],
    })).toBe("Mulligan");
  });

  it("renders keep hand", () => {
    expect(GV.chosenDisplayText({
      chosen: false,
      chosenArgs: { choice: "no" },
      message: "Mulligan down to 5 cards?",
      choices: [],
    })).toBe("Keep hand");
  });

  it("renders no blocks via false chosen", () => {
    expect(GV.chosenDisplayText({
      chosen: false,
      chosenArgs: { choice: "no" },
      message: "Select blockers",
      choices: [
        { name: "Goblin Token", index: 0, choice_type: "blocker", id: "p28", power: "1", toughness: "1" },
      ],
    })).toBe("No blocks");
  });

  it("renders batch attack with names and stats", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: { attackers: "p12,p30" },
      message: "Select attackers",
      choices: [
        { name: "Feral Prowler", index: 0, choice_type: "attacker", id: "p12", power: "2", toughness: "4" },
        { name: "Pouncing Cheetah", index: 1, choice_type: "attacker", id: "p30", power: "3", toughness: "2" },
        { name: "All attack", index: 2, choice_type: "special", id: "all" },
      ],
    })).toBe("Attack with Feral Prowler 2/4, Pouncing Cheetah 3/2");
  });

  it("renders attack all", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: { attackers: "all" },
      message: "Select attackers",
      choices: [
        { name: "Bear", id: "p1", power: "2", toughness: "2" },
        { name: "All attack", id: "all", choice_type: "special" },
      ],
    })).toBe("Attack with all (Bear 2/2)");
  });

  it("renders blockers with attacker names from pilotContext", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: { blockers: "p28:p12,p29:p30" },
      message: "Select blockers",
      choices: [
        { name: "Goblin Token", id: "p28", power: "1", toughness: "1" },
        { name: "Goblin Token", id: "p29", power: "1", toughness: "1" },
      ],
      pilotContext: {
        incomingAttackers: [
          { name: "Feral Prowler", id: "p12", power: "2", toughness: "4" },
          { name: "Pouncing Cheetah", id: "p30", power: "3", toughness: "2" },
        ],
      },
    })).toBe("Goblin Token blocks Feral Prowler, Goblin Token blocks Pouncing Cheetah");
  });

  it("renders choice by ID when chosen is null", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: { choice: "p42" },
      message: "Play spells and abilities",
      choices: [
        { name: "Orazca Frillback", index: 4, action: "cast", id: "p42", mana_cost: "{2}{G}", power: "4", toughness: "2" },
      ],
    })).toBe("Cast Orazca Frillback {2}{G}");
  });

  it("renders choice by ID for land when chosen is null", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: { choice: "p5" },
      message: "Play spells and abilities",
      choices: [{ name: "Forest", index: 0, action: "land", id: "p5" }],
    })).toBe("Play Forest");
  });

  it("falls back to choice ID when not found in choices", () => {
    expect(GV.chosenDisplayText({
      chosen: null,
      chosenArgs: { choice: "p99" },
      message: "Play spells and abilities",
      choices: [],
    })).toBe("p99");
  });

  it("falls back to description for old exports", () => {
    expect(GV.chosenDisplayText({
      chosen: 1,
      chosenArgs: { index: 1 },
      message: "Play spells and abilities",
      choices: [
        { index: 0, description: "Temple of the False God [Land]" },
        { index: 1, description: "Forest [Land]" },
      ],
    })).toBe("Forest [Land]");
  });

  it("renders boolean true for non-mulligan questions", () => {
    expect(GV.chosenDisplayText({
      chosen: true,
      chosenArgs: { answer: true },
      message: "Choose: yes or no",
      choices: [],
    })).toBe("true");
  });

  it("renders generic choice name without action", () => {
    expect(GV.chosenDisplayText({
      chosen: 1,
      chosenArgs: { choice: "p18" },
      message: "Select up to one creature",
      choices: [
        { name: "Canopy Stalker", index: 0, id: "p17" },
        { name: "Feral Prowler", index: 1, id: "p18" },
      ],
    })).toBe("Feral Prowler");
  });
});
