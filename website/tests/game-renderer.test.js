import { describe, it, expect, beforeEach } from "vitest";

// game-renderer.js uses `window` and `module.exports`; load it in happy-dom
const R = await import("../public/game-renderer.js");

// ── normalizeLiveState ──────────────────────────────────────────

describe("normalizeLiveState", () => {
  it("converts camelCase API state to snake_case", () => {
    const input = {
      status: "live",
      turn: 5,
      phase: "MAIN",
      step: "POSTCOMBAT_MAIN",
      activePlayer: "Alice",
      priorityPlayer: "Bob",
      stack: [],
      players: [
        {
          name: "Alice",
          life: 20,
          libraryCount: 40,
          handCount: 7,
          isActive: true,
          hasLeft: false,
          timerActive: true,
          priorityTimeLeftSecs: 900,
          counters: [{ name: "poison", count: 3 }],
          commanders: [],
          battlefield: [],
          hand: [],
          graveyard: [],
          exile: [],
        },
      ],
      layout: { sourceWidth: 1920, sourceHeight: 1080 },
    };
    const result = R.normalizeLiveState(input);

    expect(result.turn).toBe(5);
    expect(result.active_player).toBe("Alice");
    expect(result.priority_player).toBe("Bob");
    expect(result.layout.sourceWidth).toBe(1920);

    const p = result.players[0];
    expect(p.library_size).toBe(40);
    expect(p.hand_count).toBe(7);
    expect(p.is_active).toBe(true);
    expect(p.has_left).toBe(false);
    expect(p.timerActive).toBe(true);
    expect(p.priorityTimeLeftSecs).toBe(900);
    expect(p.counters).toEqual([{ name: "poison", count: 3 }]);
  });

  it("normalizes hasPriority to has_priority", () => {
    const input = {
      turn: 1,
      activePlayer: "A",
      priorityPlayer: "B",
      players: [
        { name: "A", life: 20, libraryCount: 30, handCount: 5, isActive: true, hasPriority: false, hasLeft: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
        { name: "B", life: 20, libraryCount: 30, handCount: 5, isActive: false, hasPriority: true, hasLeft: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
      ],
      stack: [],
    };
    const result = R.normalizeLiveState(input);
    expect(result.players[0].has_priority).toBe(false);
    expect(result.players[1].has_priority).toBe(true);
  });

  it("normalizes cards with camelCase fields", () => {
    const input = {
      turn: 1,
      phase: "MAIN",
      step: "MAIN",
      activePlayer: "A",
      priorityPlayer: "A",
      stack: [{ name: "Bolt", manaCost: "{R}", typeLine: "Instant" }],
      players: [
        {
          name: "A",
          life: 20,
          libraryCount: 30,
          handCount: 1,
          isActive: true,
          hasLeft: false,
          counters: [],
          commanders: [],
          battlefield: [
            {
              name: "Sol Ring",
              manaCost: "{1}",
              typeLine: "Artifact",
              imageUrl: "https://scryfall.com/sol-ring",
              tapped: true,
            },
          ],
          hand: [],
          graveyard: [],
          exile: [],
        },
      ],
    };
    const result = R.normalizeLiveState(input);
    const bf = result.players[0].battlefield[0];
    expect(bf.mana_cost).toBe("{1}");
    expect(bf.imageUrl).toBe("https://scryfall.com/sol-ring");
    expect(bf.tapped).toBe(true);

    const stackCard = result.stack[0];
    expect(stackCard.mana_cost).toBe("{R}");
  });

  it("handles null/undefined input", () => {
    expect(R.normalizeLiveState(null)).toBe(null);
    expect(R.normalizeLiveState(undefined)).toBe(undefined);
  });

  it("handles empty players array", () => {
    const result = R.normalizeLiveState({
      turn: 1,
      activePlayer: "A",
      priorityPlayer: "A",
      players: [],
      stack: [],
    });
    expect(result.players).toEqual([]);
  });

  it("handles missing optional fields", () => {
    const result = R.normalizeLiveState({
      turn: 1,
      activePlayer: "A",
      players: [
        {
          name: "A",
          life: 20,
          libraryCount: 30,
          handCount: 5,
          isActive: true,
          hasLeft: false,
        },
      ],
    });
    const p = result.players[0];
    expect(p.commanders).toEqual([]);
    expect(p.battlefield).toEqual([]);
    expect(p.hand).toEqual([]);
    expect(p.graveyard).toEqual([]);
    expect(p.exile).toEqual([]);
    expect(result.stack).toEqual([]);
  });
});

// ── resolveCardImage ────────────────────────────────────────────

describe("resolveCardImage", () => {
  it("returns cardObj.imageUrl when present (live mode)", () => {
    const url = R.resolveCardImage(
      "Sol Ring",
      { imageUrl: "https://scryfall.com/cards/m21/123?format=image&version=normal" },
      {},
      "small"
    );
    expect(url).toContain("version=small");
    expect(url).toContain("scryfall.com/cards/m21/123");
  });

  it("falls back to cardImages map (replay mode)", () => {
    const cardImages = {
      "Sol Ring": "https://api.scryfall.com/cards/m21/123?format=image&version=small",
    };
    const url = R.resolveCardImage("Sol Ring", null, cardImages, "normal");
    expect(url).toContain("version=normal");
  });

  it("falls back to Scryfall name-based URL", () => {
    const url = R.resolveCardImage("Lightning Bolt", null, {}, "small");
    expect(url).toBe(
      "https://api.scryfall.com/cards/named?exact=Lightning%20Bolt&format=image&version=small"
    );
  });

  it("defaults version to small", () => {
    const url = R.resolveCardImage("Island", null, {});
    expect(url).toContain("version=small");
  });

  it("uses original_card with face=back for MDFC back face (cardImages)", () => {
    const cardImages = {
      "Boggart Trawler": "https://api.scryfall.com/cards/dsk/75?format=image&version=small",
    };
    const cardObj = { back_face: true, original_card: "Boggart Trawler" };
    const url = R.resolveCardImage("Boggart Bog", cardObj, cardImages, "small");
    expect(url).toContain("dsk/75");
    expect(url).toContain("face=back");
  });

  it("appends face=back for MDFC back face (Scryfall name fallback)", () => {
    const cardObj = { back_face: true, original_card: "Boggart Trawler" };
    const url = R.resolveCardImage("Boggart Bog", cardObj, {}, "small");
    expect(url).toContain("exact=Boggart%20Bog");
    expect(url).toContain("face=back");
  });

  it("does not add face=back when back_face is not set", () => {
    const cardObj = { original_card: "Boggart Trawler" };
    const url = R.resolveCardImage("Boggart Bog", cardObj, {}, "small");
    expect(url).not.toContain("face=back");
  });
});

// ── diffStringBag ───────────────────────────────────────────────

describe("diffStringBag", () => {
  it("detects entered cards", () => {
    const result = R.diffStringBag(["Mountain"], ["Mountain", "Forest"]);
    expect(result.entered).toEqual(["Forest"]);
    expect(result.left).toEqual([]);
  });

  it("detects left cards", () => {
    const result = R.diffStringBag(["Mountain", "Forest"], ["Mountain"]);
    expect(result.entered).toEqual([]);
    expect(result.left).toEqual(["Forest"]);
  });

  it("handles duplicates correctly", () => {
    const result = R.diffStringBag(
      ["Mountain", "Mountain"],
      ["Mountain", "Mountain", "Mountain"]
    );
    expect(result.entered).toEqual(["Mountain"]);
    expect(result.left).toEqual([]);
  });

  it("handles removing duplicates", () => {
    const result = R.diffStringBag(
      ["Mountain", "Mountain", "Mountain"],
      ["Mountain"]
    );
    expect(result.left).toEqual(["Mountain", "Mountain"]);
    expect(result.entered).toEqual([]);
  });

  it("handles empty lists", () => {
    expect(R.diffStringBag([], [])).toEqual({ entered: [], left: [] });
    expect(R.diffStringBag([], ["A"])).toEqual({ entered: ["A"], left: [] });
    expect(R.diffStringBag(["A"], [])).toEqual({ entered: [], left: ["A"] });
  });

  it("handles no change", () => {
    const result = R.diffStringBag(["A", "B"], ["A", "B"]);
    expect(result.entered).toEqual([]);
    expect(result.left).toEqual([]);
  });
});

// ── diffBattlefield ─────────────────────────────────────────────

describe("diffBattlefield", () => {
  it("detects entered permanents", () => {
    const prev = [{ name: "Sol Ring", tapped: false }];
    const curr = [
      { name: "Sol Ring", tapped: false },
      { name: "Mountain", tapped: false },
    ];
    const result = R.diffBattlefield(prev, curr);
    expect(result.entered).toEqual(["Mountain"]);
    expect(result.left).toEqual([]);
    expect(result.tapChanged).toEqual([]);
  });

  it("detects left permanents", () => {
    const prev = [
      { name: "Sol Ring", tapped: false },
      { name: "Mountain", tapped: false },
    ];
    const curr = [{ name: "Sol Ring", tapped: false }];
    const result = R.diffBattlefield(prev, curr);
    expect(result.entered).toEqual([]);
    expect(result.left.length).toBe(1);
    expect(result.left[0].name).toBe("Mountain");
  });

  it("detects tap state changes", () => {
    const prev = [{ name: "Sol Ring", tapped: false }];
    const curr = [{ name: "Sol Ring", tapped: true }];
    const result = R.diffBattlefield(prev, curr);
    expect(result.entered).toEqual([]);
    expect(result.left).toEqual([]);
    expect(result.tapChanged).toEqual(["Sol Ring"]);
  });

  it("handles duplicate card names", () => {
    const prev = [
      { name: "Mountain", tapped: false },
      { name: "Mountain", tapped: false },
    ];
    const curr = [
      { name: "Mountain", tapped: false },
      { name: "Mountain", tapped: false },
      { name: "Mountain", tapped: false },
    ];
    const result = R.diffBattlefield(prev, curr);
    expect(result.entered).toEqual(["Mountain"]);
    expect(result.left).toEqual([]);
  });

  it("handles empty arrays", () => {
    const result = R.diffBattlefield([], []);
    expect(result).toEqual({ entered: [], left: [], tapChanged: [] });
  });
});

// ── computeDiff ─────────────────────────────────────────────────

describe("computeDiff", () => {
  it("returns null for null inputs", () => {
    expect(R.computeDiff(null, null)).toBe(null);
    expect(R.computeDiff(null, { players: [] })).toBe(null);
    expect(R.computeDiff({ players: [] }, null)).toBe(null);
  });

  it("computes life changes", () => {
    const prev = { players: [{ name: "A", life: 20, battlefield: [], hand: [], graveyard: [], exile: [] }] };
    const curr = { players: [{ name: "A", life: 17, battlefield: [], hand: [], graveyard: [], exile: [] }] };
    const diffs = R.computeDiff(prev, curr);
    expect(diffs["A"].lifeChange).toBe(-3);
  });

  it("computes battlefield diffs", () => {
    const prev = {
      players: [{
        name: "A",
        life: 20,
        battlefield: [{ name: "Sol Ring", tapped: false }],
        hand: [],
        graveyard: [],
        exile: [],
      }],
    };
    const curr = {
      players: [{
        name: "A",
        life: 20,
        battlefield: [
          { name: "Sol Ring", tapped: true },
          { name: "Mountain", tapped: false },
        ],
        hand: [],
        graveyard: [],
        exile: [],
      }],
    };
    const diffs = R.computeDiff(prev, curr);
    expect(diffs["A"].battlefield.entered).toEqual(["Mountain"]);
    expect(diffs["A"].battlefield.tapChanged).toEqual(["Sol Ring"]);
  });

  it("skips players not in both snapshots", () => {
    const prev = { players: [{ name: "A", life: 20, battlefield: [], hand: [], graveyard: [], exile: [] }] };
    const curr = { players: [{ name: "B", life: 20, battlefield: [], hand: [], graveyard: [], exile: [] }] };
    const diffs = R.computeDiff(prev, curr);
    expect(diffs["A"]).toBeUndefined();
    expect(diffs["B"]).toBeUndefined();
  });
});

// ── computePlayerTurnNumbers ─────────────────────────────────────

describe("computePlayerTurnNumbers", () => {
  it("computes per-player turn numbers for sequential 2p game", () => {
    var snaps = [
      { turn: 1, active_player: null },
      { turn: 1, active_player: "Alice" },
      { turn: 2, active_player: "Bob" },
      { turn: 3, active_player: "Alice" },
      { turn: 4, active_player: "Bob" },
      { turn: 5, active_player: "Alice" },
    ];
    var result = R.computePlayerTurnNumbers(snaps);
    expect(result).toEqual([null, 1, 1, 2, 2, 3]);
  });

  it("computes per-player turn numbers for round-based 4p game", () => {
    var snaps = [
      { turn: 1, active_player: "A" },
      { turn: 1, active_player: "B" },
      { turn: 1, active_player: "C" },
      { turn: 1, active_player: "D" },
      { turn: 2, active_player: "A" },
      { turn: 2, active_player: "B" },
    ];
    var result = R.computePlayerTurnNumbers(snaps);
    expect(result).toEqual([1, 1, 1, 1, 2, 2]);
  });
});

// ── renderStatusLine ────────────────────────────────────────────

describe("renderStatusLine", () => {
  let el;

  beforeEach(() => {
    el = document.createElement("div");
  });

  it("renders turn/phase/active/priority", () => {
    R.renderStatusLine(el, {
      turn: 5,
      phase: "COMBAT",
      step: "DECLARE_ATTACKERS",
      active_player: "Alice",
      priority_player: "Bob",
    }, 3);
    expect(el.textContent).toContain("Alice's Turn 3");
    expect(el.textContent).toContain("COMBAT / DECLARE_ATTACKERS");
    expect(el.textContent).toContain("Priority: Bob");
  });

  it("shows Pregame when no active player and no player turn", () => {
    R.renderStatusLine(el, { turn: 1, phase: null, step: null });
    expect(el.textContent).toContain("Pregame");
  });

  it("skips step when same as phase", () => {
    R.renderStatusLine(el, {
      turn: 1,
      phase: "MAIN",
      step: "MAIN",
      active_player: "A",
      priority_player: "A",
    });
    // Should show just "MAIN" not "MAIN / MAIN"
    expect(el.textContent).not.toContain("MAIN / MAIN");
    expect(el.textContent).toContain("MAIN");
  });
});

// ── makeCardChip ────────────────────────────────────────────────

describe("makeCardChip", () => {
  const mockPreviewEls = {
    container: document.createElement("div"),
    image: document.createElement("img"),
    name: document.createElement("div"),
    cost: document.createElement("div"),
    type: document.createElement("div"),
    stats: document.createElement("div"),
    rules: document.createElement("pre"),
  };

  it("creates a span with card-chip class", () => {
    const chip = R.makeCardChip("Sol Ring", null, {}, false, mockPreviewEls);
    expect(chip.tagName).toBe("SPAN");
    expect(chip.className).toContain("card-chip");
    expect(chip.textContent).toBe("Sol Ring");
  });

  it("adds tapped class when tapped", () => {
    const chip = R.makeCardChip("Sol Ring", null, {}, true, mockPreviewEls);
    expect(chip.className).toContain("tapped");
  });

  it("does not add tapped class when untapped", () => {
    const chip = R.makeCardChip("Sol Ring", null, {}, false, mockPreviewEls);
    expect(chip.className).not.toContain("tapped");
  });

  it("shows power/toughness for creatures", () => {
    const chip = R.makeCardChip(
      "Grizzly Bears",
      { power: "2", toughness: "2" },
      {},
      false,
      mockPreviewEls
    );
    expect(chip.textContent).toContain("2/2");
    const pt = chip.querySelector(".pt");
    expect(pt).not.toBeNull();
    expect(pt.textContent).toBe("2/2");
  });
});

// ── makeZone ────────────────────────────────────────────────────

describe("makeZone", () => {
  const mockPreviewEls = {
    container: document.createElement("div"),
    image: document.createElement("img"),
    name: document.createElement("div"),
    cost: document.createElement("div"),
    type: document.createElement("div"),
    stats: document.createElement("div"),
    rules: document.createElement("pre"),
  };

  it("renders zone title with count", () => {
    const zone = R.makeZone("Battlefield", [{ name: "Sol Ring" }, { name: "Mountain" }], {
      previewEls: mockPreviewEls,
    });
    const title = zone.querySelector(".zone-title");
    expect(title.textContent).toBe("Battlefield (2)");
  });

  it("uses count override for hidden zones", () => {
    const zone = R.makeZone("Hand", [], {
      countOverride: 5,
      previewEls: mockPreviewEls,
    });
    const title = zone.querySelector(".zone-title");
    expect(title.textContent).toBe("Hand (5)");
    const empty = zone.querySelector(".zone-empty");
    expect(empty.textContent).toBe("5 cards");
  });

  it("renders card chips", () => {
    const zone = R.makeZone("Graveyard", [{ name: "Bolt" }, { name: "Ponder" }], {
      previewEls: mockPreviewEls,
    });
    const chips = zone.querySelectorAll(".card-chip");
    expect(chips.length).toBe(2);
  });

  it("marks entered cards with diff info", () => {
    const zone = R.makeZone("Battlefield", [{ name: "Sol Ring" }, { name: "Mountain" }], {
      diffInfo: {
        enteredNames: ["Mountain"],
        tapChangedNames: [],
        ghostCards: [],
      },
      previewEls: mockPreviewEls,
    });
    const chips = zone.querySelectorAll(".card-chip");
    // Sol Ring should not be entered, Mountain should
    expect(chips[0].classList.contains("card-entered")).toBe(false);
    expect(chips[1].classList.contains("card-entered")).toBe(true);
  });

  it("renders ghost cards for left cards", () => {
    const zone = R.makeZone("Battlefield", [{ name: "Sol Ring" }], {
      diffInfo: {
        enteredNames: [],
        tapChangedNames: [],
        ghostCards: [{ name: "Mountain", tapped: false }],
      },
      previewEls: mockPreviewEls,
    });
    const ghosts = zone.querySelectorAll(".card-ghost");
    expect(ghosts.length).toBe(1);
  });
});

// ── renderPlayers ──────────────────────────────────────────────

describe("renderPlayers", () => {
  const mockPreviewEls = {
    container: document.createElement("div"),
    image: document.createElement("img"),
    name: document.createElement("div"),
    cost: document.createElement("div"),
    type: document.createElement("div"),
    stats: document.createElement("div"),
    rules: document.createElement("pre"),
  };

  it("adds active-turn class to the active player card", () => {
    const container = document.createElement("div");
    const players = [
      { name: "Alice", life: 20, library_size: 30, hand_count: 5, is_active: true, has_left: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
      { name: "Bob", life: 20, library_size: 30, hand_count: 5, is_active: false, has_left: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
    ];
    R.renderPlayers(container, players, {
      playerColorMap: { Alice: 0, Bob: 1 },
      priorityPlayerName: "Bob",
      previewEls: mockPreviewEls,
    });
    const cards = container.querySelectorAll(".player-card");
    expect(cards[0].classList.contains("active-turn")).toBe(true);
    expect(cards[1].classList.contains("active-turn")).toBe(false);
  });

  it("adds has-priority class to the priority player name", () => {
    const container = document.createElement("div");
    const players = [
      { name: "Alice", life: 20, library_size: 30, hand_count: 5, is_active: true, has_left: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
      { name: "Bob", life: 20, library_size: 30, hand_count: 5, is_active: false, has_left: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
    ];
    R.renderPlayers(container, players, {
      playerColorMap: { Alice: 0, Bob: 1 },
      priorityPlayerName: "Bob",
      previewEls: mockPreviewEls,
    });
    const names = container.querySelectorAll(".player-name");
    expect(names[0].classList.contains("has-priority")).toBe(false);
    expect(names[1].classList.contains("has-priority")).toBe(true);
  });

  it("active-turn and has-priority can be on the same player", () => {
    const container = document.createElement("div");
    const players = [
      { name: "Alice", life: 20, library_size: 30, hand_count: 5, is_active: true, has_left: false, counters: [], commanders: [], battlefield: [], hand: [], graveyard: [], exile: [] },
    ];
    R.renderPlayers(container, players, {
      playerColorMap: { Alice: 0 },
      priorityPlayerName: "Alice",
      previewEls: mockPreviewEls,
    });
    const card = container.querySelector(".player-card");
    const name = container.querySelector(".player-name");
    expect(card.classList.contains("active-turn")).toBe(true);
    expect(name.classList.contains("has-priority")).toBe(true);
  });
});

// ── computeCardFontSize ─────────────────────────────────────────

describe("computeCardFontSize", () => {
  it("returns 0 for very small cards", () => {
    expect(R.computeCardFontSize(30, 10)).toBe(0);
  });

  it("returns reasonable size for normal cards", () => {
    const size = R.computeCardFontSize(100, 70);
    expect(size).toBeGreaterThanOrEqual(6);
    expect(size).toBeLessThanOrEqual(11);
  });
});

// ── preloadCardData ─────────────────────────────────────────────

describe("preloadCardData", () => {
  it("pre-populates the Scryfall card cache", () => {
    const cardData = {
      "Lightning Bolt": {
        mana_cost: "{R}",
        type_line: "Instant",
        oracle_text: "Lightning Bolt deals 3 damage to any target.",
      },
    };
    R.preloadCardData(cardData);
    // Verify by calling showPreview — if the cache is populated,
    // it will use the cached data instead of fetching
    const previewEls = {
      container: document.createElement("div"),
      image: document.createElement("img"),
      name: document.createElement("div"),
      cost: document.createElement("div"),
      type: document.createElement("div"),
      stats: document.createElement("div"),
      rules: document.createElement("pre"),
    };
    R.showPreview("Lightning Bolt", null, {}, previewEls);
    // The type line should be populated from the cache
    expect(previewEls.type.textContent).toBe("Instant");
  });

  it("handles null/undefined cardData gracefully", () => {
    // Should not throw
    R.preloadCardData(null);
    R.preloadCardData(undefined);
    R.preloadCardData({});
  });

  it("does not overwrite existing cache entries", () => {
    // First preload
    R.preloadCardData({
      "Test Card": { type_line: "First" },
    });
    // Second preload with different data for same card
    R.preloadCardData({
      "Test Card": { type_line: "Second" },
    });
    const previewEls = {
      container: document.createElement("div"),
      image: document.createElement("img"),
      name: document.createElement("div"),
      cost: document.createElement("div"),
      type: document.createElement("div"),
      stats: document.createElement("div"),
      rules: document.createElement("pre"),
    };
    R.showPreview("Test Card", null, {}, previewEls);
    expect(previewEls.type.textContent).toBe("First");
  });
});

// ── resolveCardImage with token in cardImages ────────────────────

describe("resolveCardImage with token in cardImages", () => {
  it("returns preloaded URL for a token in cardImages", () => {
    const cardImages = {
      "Goblin Token": "https://cards.scryfall.io/small/front/token/goblin.jpg",
    };
    const url = R.resolveCardImage("Goblin Token", null, cardImages, "small");
    // Token is in cardImages, so resolveCardImage should find it at Priority 2
    expect(url).toContain("goblin");
  });
});

// ── normalizeCard ───────────────────────────────────────────────

describe("normalizeCard", () => {
  it("passes through strings unchanged", () => {
    expect(R.normalizeCard("Lightning Bolt")).toBe("Lightning Bolt");
  });

  it("passes through null", () => {
    expect(R.normalizeCard(null)).toBe(null);
  });

  it("converts manaCost to mana_cost", () => {
    const card = R.normalizeCard({ name: "Bolt", manaCost: "{R}" });
    expect(card.mana_cost).toBe("{R}");
  });

  it("preserves mana_cost if already snake_case", () => {
    const card = R.normalizeCard({ name: "Bolt", mana_cost: "{R}" });
    expect(card.mana_cost).toBe("{R}");
  });

  it("normalizes back_face and backFace", () => {
    const card1 = R.normalizeCard({ name: "Bog", back_face: true });
    expect(card1.back_face).toBe(true);

    const card2 = R.normalizeCard({ name: "Bog", backFace: true });
    expect(card2.back_face).toBe(true);
  });
});

// ── hasPT / formatPT ──────────────────────────────────────────

describe("hasPT", () => {
  it("returns true for normal creatures", () => {
    expect(R.hasPT({ power: 2, toughness: 3 })).toBe(true);
  });

  it("returns true when power is 0", () => {
    expect(R.hasPT({ power: 0, toughness: 1 })).toBe(true);
  });

  it("returns true when toughness is 0", () => {
    expect(R.hasPT({ power: 1, toughness: 0 })).toBe(true);
  });

  it("returns true for string power/toughness", () => {
    expect(R.hasPT({ power: "2", toughness: "2" })).toBe(true);
  });

  it("returns false when neither is present", () => {
    expect(R.hasPT({ name: "Mountain" })).toBe(false);
  });

  it("returns falsy for null/undefined card", () => {
    expect(R.hasPT(null)).toBeFalsy();
    expect(R.hasPT(undefined)).toBeFalsy();
  });
});

describe("formatPT", () => {
  it("formats normal power/toughness", () => {
    expect(R.formatPT({ power: 2, toughness: 3 })).toBe("2/3");
  });

  it("formats zero power correctly", () => {
    expect(R.formatPT({ power: 0, toughness: 1 })).toBe("0/1");
  });

  it("formats zero toughness correctly", () => {
    expect(R.formatPT({ power: 1, toughness: 0 })).toBe("1/0");
  });

  it("formats string values", () => {
    expect(R.formatPT({ power: "*", toughness: "*" })).toBe("*/*");
  });

  it("uses ? for missing power", () => {
    expect(R.formatPT({ toughness: 3 })).toBe("?/3");
  });

  it("uses ? for missing toughness", () => {
    expect(R.formatPT({ power: 2 })).toBe("2/?");
  });
});

// ── isLikelyLand with type_line ──────────────────────────────

describe("isLikelyLand with type_line", () => {
  it("recognizes land from type_line (snake_case export format)", () => {
    expect(R.isLikelyLand({ name: "Forest", type_line: "Basic Land — Forest" })).toBe(true);
  });

  it("recognizes non-land creature from type_line", () => {
    expect(R.isLikelyLand({ name: "Eldrazi Spawn Token", type_line: "Creature — Eldrazi Spawn", power: 0, toughness: 1 })).toBe(false);
  });

  it("prefers typeLine over type_line", () => {
    expect(R.isLikelyLand({ typeLine: "Land", type_line: "Creature" })).toBe(true);
  });
});

// ── makeCardChip with zero power ────────────────────────────

describe("makeCardChip with zero power", () => {
  const mockPreviewEls = {
    container: document.createElement("div"),
    image: document.createElement("img"),
    name: document.createElement("div"),
    cost: document.createElement("div"),
    type: document.createElement("div"),
    stats: document.createElement("div"),
    rules: document.createElement("pre"),
  };

  it("shows 0/1 for Eldrazi Spawn tokens", () => {
    const chip = R.makeCardChip(
      "Eldrazi Spawn Token",
      { power: 0, toughness: 1 },
      {},
      false,
      mockPreviewEls
    );
    const pt = chip.querySelector(".pt");
    expect(pt).not.toBeNull();
    expect(pt.textContent).toBe("0/1");
  });
});

// ── makeCardThumbnail data-card-id ──────────────────────────

describe("makeCardThumbnail data-card-id", () => {
  const mockPreviewEls = {
    container: document.createElement("div"),
    image: document.createElement("img"),
    name: document.createElement("div"),
    cost: document.createElement("div"),
    type: document.createElement("div"),
    stats: document.createElement("div"),
    rules: document.createElement("pre"),
  };

  it("sets data-card-id when cardObj has id", () => {
    const thumb = R.makeCardThumbnail("Lightning Bolt", { id: "p26", name: "Lightning Bolt" }, {}, false, mockPreviewEls);
    expect(thumb.getAttribute("data-card-id")).toBe("p26");
  });

  it("omits data-card-id when cardObj has no id", () => {
    const thumb = R.makeCardThumbnail("Lightning Bolt", { name: "Lightning Bolt" }, {}, false, mockPreviewEls);
    expect(thumb.getAttribute("data-card-id")).toBeNull();
  });

  it("omits data-card-id when cardObj is null", () => {
    const thumb = R.makeCardThumbnail("Lightning Bolt", null, {}, false, mockPreviewEls);
    expect(thumb.getAttribute("data-card-id")).toBeNull();
  });
});

// ── drawTargetArrows ────────────────────────────────────────

describe("drawTargetArrows", () => {
  function makeGameLeft() {
    const gameLeft = document.createElement("div");
    gameLeft.style.position = "relative";
    document.body.appendChild(gameLeft);
    return gameLeft;
  }

  function cleanup(el) {
    if (el.parentNode) el.parentNode.removeChild(el);
  }

  it("does not create SVG when no stack items with targets", () => {
    const gameLeft = makeGameLeft();
    const stackItem = document.createElement("div");
    stackItem.className = "stack-item";
    gameLeft.appendChild(stackItem);

    R.drawTargetArrows(gameLeft);
    expect(gameLeft.querySelector(".target-arrows-svg")).toBeNull();
    cleanup(gameLeft);
  });

  it("removes previous SVG overlay on re-call", () => {
    const gameLeft = makeGameLeft();

    // Create a fake previous SVG
    const oldSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    oldSvg.setAttribute("class", "target-arrows-svg");
    oldSvg.id = "old-svg";
    gameLeft.appendChild(oldSvg);

    R.drawTargetArrows(gameLeft);
    expect(gameLeft.querySelector("#old-svg")).toBeNull();
    cleanup(gameLeft);
  });

  it("creates SVG with path when target element exists", () => {
    const gameLeft = makeGameLeft();

    // Stack item targeting a card
    const stackItem = document.createElement("div");
    stackItem.className = "stack-item";
    stackItem.setAttribute("data-target-ids", "p10");
    stackItem.setAttribute("data-target-names", "Dark Depths");
    const thumb = document.createElement("div");
    thumb.className = "card-thumb";
    stackItem.appendChild(thumb);
    gameLeft.appendChild(stackItem);

    // Target card on battlefield
    const target = document.createElement("div");
    target.className = "card-thumb";
    target.setAttribute("data-card-id", "p10");
    gameLeft.appendChild(target);

    R.drawTargetArrows(gameLeft);
    const svg = gameLeft.querySelector(".target-arrows-svg");
    expect(svg).not.toBeNull();
    // 2 paths per arrow: glow layer + main line
    expect(svg.querySelectorAll("path").length).toBe(2);
    cleanup(gameLeft);
  });

  it("matches player targets by data-player-name", () => {
    const gameLeft = makeGameLeft();

    // Stack item targeting a player
    const stackItem = document.createElement("div");
    stackItem.className = "stack-item";
    stackItem.setAttribute("data-target-ids", "p1");
    stackItem.setAttribute("data-target-names", "Opponent");
    const thumb = document.createElement("div");
    thumb.className = "card-thumb";
    stackItem.appendChild(thumb);
    gameLeft.appendChild(stackItem);

    // Player header
    const header = document.createElement("div");
    header.className = "player-header";
    header.setAttribute("data-player-name", "Opponent");
    gameLeft.appendChild(header);

    R.drawTargetArrows(gameLeft);
    const svg = gameLeft.querySelector(".target-arrows-svg");
    expect(svg).not.toBeNull();
    // 2 paths per arrow: glow layer + main line
    expect(svg.querySelectorAll("path").length).toBe(2);
    cleanup(gameLeft);
  });

  it("draws no SVG when target not found in DOM", () => {
    const gameLeft = makeGameLeft();

    const stackItem = document.createElement("div");
    stackItem.className = "stack-item";
    stackItem.setAttribute("data-target-ids", "p99");
    stackItem.setAttribute("data-target-names", "Ghost");
    const thumb = document.createElement("div");
    thumb.className = "card-thumb";
    stackItem.appendChild(thumb);
    gameLeft.appendChild(stackItem);

    R.drawTargetArrows(gameLeft);
    // No matching target, so no paths drawn, so no SVG appended
    expect(gameLeft.querySelector(".target-arrows-svg")).toBeNull();
    cleanup(gameLeft);
  });
});

