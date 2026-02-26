/**
 * game-renderer.js — shared rendering module for replay + live visualizer.
 *
 * In the browser this attaches to window.GameRenderer.
 * In Node/Vitest it is importable as a module.
 */
(function (root) {
  "use strict";

  // ── Data normalisation (live API camelCase → internal snake_case) ──

  function normalizeCard(c) {
    if (!c || typeof c === "string") return c;
    return {
      name: c.name,
      tapped: !!c.tapped,
      power: c.power,
      toughness: c.toughness,
      mana_cost: c.manaCost || c.mana_cost,
      typeLine: c.typeLine,
      rules: c.rules,
      imageUrl: c.imageUrl,
      damage: c.damage,
      loyalty: c.loyalty,
      defense: c.defense,
      layout: c.layout || null,
      owner: c.owner,
      targets: c.targets,
      original_card: c.originalCard || c.original_card,
      back_face: c.back_face || c.backFace,
      copy: c.copy,
    };
  }

  function normalizeLivePlayer(p) {
    return {
      name: p.name,
      life: p.life,
      library_size: p.libraryCount,
      hand_count: p.handCount,
      is_active: p.isActive,
      has_priority: p.hasPriority,
      has_left: p.hasLeft,
      counters: p.counters || [],
      commanders: (p.commanders || []).map(normalizeCard),
      battlefield: (p.battlefield || []).map(normalizeCard),
      hand: (p.hand || []).map(normalizeCard),
      graveyard: (p.graveyard || []).map(normalizeCard),
      exile: (p.exile || []).map(normalizeCard),
      timerActive: p.timerActive,
      priorityTimeLeftSecs: p.priorityTimeLeftSecs,
    };
  }

  function normalizeLiveState(apiState) {
    if (!apiState) return apiState;
    return {
      status: apiState.status,
      turn: apiState.turn,
      phase: apiState.phase,
      step: apiState.step,
      active_player: apiState.activePlayer,
      priority_player: apiState.priorityPlayer,
      stack: (apiState.stack || []).map(normalizeCard),
      players: (apiState.players || []).map(normalizeLivePlayer),
      layout: apiState.layout || null,
    };
  }

  // ── Card classification helpers ──

  function isTokenCard(card) {
    if (!card || typeof card === "string") return false;
    var name = card.name || "";
    return name.indexOf(" Token") !== -1 || name.indexOf(" token") !== -1;
  }

  function hasPT(card) {
    return card && (card.power != null || card.toughness != null);
  }

  function formatPT(card) {
    var p = card.power != null ? card.power : "?";
    var t = card.toughness != null ? card.toughness : "?";
    return p + "/" + t;
  }

  function isLikelyLand(card) {
    if (!card || typeof card === "string") return false;
    // Use typeLine when available (live mode + new snapshots)
    var tl = card.typeLine || card.type_line;
    if (tl) {
      return /\bLand\b/.test(tl);
    }
    // Fallback for old snapshots without typeLine:
    // Creatures have P/T, planeswalkers have loyalty, battles have defense
    if (hasPT(card) || card.loyalty || card.defense) return false;
    // Tokens are not lands
    if (isTokenCard(card)) return false;
    return true;
  }

  // ── Card image resolution ──

  // Cache for token image lookups (cardName -> imageUrl or null)
  var _tokenImageCache = {};

  function resolveCardImage(cardName, cardObj, cardImages, version) {
    version = version || "small";
    var isBackFace = cardObj && cardObj.back_face;
    // Priority 1: explicit imageUrl on the card (live mode)
    if (cardObj && cardObj.imageUrl) {
      return cardObj.imageUrl
        .replace("version=normal", "version=" + version)
        .replace("version=small", "version=" + version);
    }
    // Priority 2: cardImages lookup map (replay mode)
    if (cardImages && cardImages[cardName]) {
      return cardImages[cardName].replace("version=small", "version=" + version);
    }
    // Priority 2b: MDFC/transform back face — look up the front face and request back
    if (isBackFace && cardImages && cardObj.original_card && cardImages[cardObj.original_card]) {
      return cardImages[cardObj.original_card].replace("version=small", "version=" + version) + "&face=back";
    }
    // Priority 3: cached token image
    if (_tokenImageCache[cardName]) {
      return _tokenImageCache[cardName].replace("version=small", "version=" + version);
    }
    // Priority 4: Scryfall name-based fallback
    var url =
      "https://api.scryfall.com/cards/named?exact=" +
      encodeURIComponent(cardName) +
      "&format=image&version=" + version;
    if (isBackFace) {
      url += "&face=back";
    }
    return url;
  }

  /**
   * Try to fetch a token image from Scryfall search API.
   * If found, caches the result and calls onFound(imageUrl).
   */
  function fetchTokenImage(cardName, onFound) {
    if (_tokenImageCache[cardName] !== undefined) {
      if (_tokenImageCache[cardName]) onFound(_tokenImageCache[cardName]);
      return;
    }
    // Strip " Token" suffix for search
    var baseName = cardName.replace(/ Token$/i, "").trim();
    var searchUrl = "https://api.scryfall.com/cards/search?q=" +
      encodeURIComponent('!"' + baseName + '" t:token') +
      "&unique=art&order=released&dir=desc";
    fetch(searchUrl)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.data && data.data.length > 0) {
          var card = data.data[0];
          var url = (card.image_uris && card.image_uris.small) || null;
          _tokenImageCache[cardName] = url;
          if (url) onFound(url);
        } else {
          _tokenImageCache[cardName] = null;
        }
      })
      .catch(function () { _tokenImageCache[cardName] = null; });
  }

  // ── Mana symbol rendering ──

  function renderManaCost(manaCostStr) {
    var frag = document.createDocumentFragment();
    if (!manaCostStr) return frag;
    var re = /\{([^}]+)\}/g;
    var match;
    while ((match = re.exec(manaCostStr)) !== null) {
      var symbol = match[1].replace(/\//g, "");
      var img = document.createElement("img");
      img.className = "mana-icon";
      img.src = "https://svgs.scryfall.io/card-symbols/" + encodeURIComponent(symbol) + ".svg";
      img.alt = match[0];
      img.title = match[0];
      frag.appendChild(img);
    }
    if (!frag.hasChildNodes()) {
      frag.appendChild(document.createTextNode(manaCostStr));
    }
    return frag;
  }

  /**
   * Render text with inline mana symbols and italic reminder text.
   * {2}{U} → mana icons, (reminder text) → <i>
   */
  function renderTextWithMana(text) {
    var frag = document.createDocumentFragment();
    if (!text) return frag;
    // Match mana symbols {X} or reminder text (...)
    var re = /\{([^}]+)\}|(\([^)]+\))/g;
    var last = 0;
    var match;
    while ((match = re.exec(text)) !== null) {
      if (match.index > last) {
        frag.appendChild(document.createTextNode(text.substring(last, match.index)));
      }
      if (match[1] != null) {
        // Mana symbol
        var symbol = match[1].replace(/\//g, "");
        var img = document.createElement("img");
        img.className = "mana-icon";
        img.src = "https://svgs.scryfall.io/card-symbols/" + encodeURIComponent(symbol) + ".svg";
        img.alt = match[0];
        img.title = match[0];
        frag.appendChild(img);
      } else {
        // Reminder text — render italic with mana symbols inside
        var em = document.createElement("i");
        em.style.color = "#888";
        // Render mana symbols inside the reminder text (use renderManaCostInline for just {X})
        var inner = match[2];
        var manaRe = /\{([^}]+)\}/g;
        var mLast = 0;
        var mMatch;
        while ((mMatch = manaRe.exec(inner)) !== null) {
          if (mMatch.index > mLast) em.appendChild(document.createTextNode(inner.substring(mLast, mMatch.index)));
          var mSym = mMatch[1].replace(/\//g, "");
          var mImg = document.createElement("img");
          mImg.className = "mana-icon";
          mImg.src = "https://svgs.scryfall.io/card-symbols/" + encodeURIComponent(mSym) + ".svg";
          mImg.alt = mMatch[0];
          mImg.title = mMatch[0];
          em.appendChild(mImg);
          mLast = manaRe.lastIndex;
        }
        if (mLast < inner.length) em.appendChild(document.createTextNode(inner.substring(mLast)));
        frag.appendChild(em);
      }
      last = re.lastIndex;
    }
    if (last < text.length) {
      frag.appendChild(document.createTextNode(text.substring(last)));
    }
    return frag;
  }

  // ── Card preview ──

  // Cache for Scryfall card data fetched on hover (cardName -> { typeLine, rules, mana_cost } or null)
  var _scryfallCardCache = {};

  function _applyScryfallData(data, els, expectedName) {
    // Only apply if preview is still showing the same card
    if (!els || els.name.textContent !== expectedName) return;
    if (data.mana_cost && els.cost && !els.cost.hasChildNodes()) {
      els.cost.appendChild(renderManaCost(data.mana_cost));
    }
    if (data.type_line && !els.type.textContent) {
      els.type.textContent = data.type_line;
    }
    if (data.oracle_text && !els.rules.textContent) {
      els.rules.textContent = "";
      els.rules.appendChild(renderTextWithMana(data.oracle_text));
    }
    if (!els.stats.textContent) {
      var parts = [];
      if (data.power != null && data.toughness != null) {
        parts.push(data.power + "/" + data.toughness);
      }
      if (data.loyalty) parts.push("Loyalty " + data.loyalty);
      if (data.defense) parts.push("Defense " + data.defense);
      if (parts.length > 0) els.stats.textContent = parts.join(" | ");
    }
  }

  function _fetchScryfallCard(cardName, cardImages, els) {
    if (_scryfallCardCache[cardName] !== undefined) {
      if (_scryfallCardCache[cardName]) {
        _applyScryfallData(_scryfallCardCache[cardName], els, cardName);
      }
      return;
    }
    _scryfallCardCache[cardName] = null; // mark as in-flight
    // Try to derive API URL from cardImages map (strip image format params)
    var apiUrl = null;
    if (cardImages && cardImages[cardName]) {
      apiUrl = cardImages[cardName].replace(/\?.*$/, "");
    }
    if (!apiUrl) {
      apiUrl = "https://api.scryfall.com/cards/named?exact=" + encodeURIComponent(cardName);
    }
    fetch(apiUrl)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data) {
          _scryfallCardCache[cardName] = data;
          _applyScryfallData(data, els, cardName);
        }
      })
      .catch(function () {});
  }

  function showPreview(cardName, cardObj, cardImages, els) {
    if (!els || !els.name) return;
    els.name.textContent = cardName;
    if (els.cost) els.cost.textContent = "";
    els.type.textContent = "";
    els.stats.textContent = "";
    els.rules.textContent = "";

    if (cardObj && cardObj.original_card) {
      els.type.textContent = "(copy of " + cardObj.original_card + ")";
    }

    if (cardObj) {
      if (hasPT(cardObj)) {
        els.stats.textContent = formatPT(cardObj);
      }
      if (cardObj.loyalty) {
        els.stats.textContent = (els.stats.textContent ? els.stats.textContent + " | " : "") + "Loyalty " + cardObj.loyalty;
      }
      if (cardObj.defense) {
        els.stats.textContent = (els.stats.textContent ? els.stats.textContent + " | " : "") + "Defense " + cardObj.defense;
      }
      if (cardObj.damage && Number(cardObj.damage) > 0) {
        els.stats.textContent = (els.stats.textContent ? els.stats.textContent + " | " : "") + "Damage " + cardObj.damage;
      }
      if (cardObj.mana_cost && els.cost) {
        els.cost.appendChild(renderManaCost(cardObj.mana_cost));
      }
      var typeLine = cardObj.typeLine || cardObj.type_line;
      if (typeLine) {
        els.type.textContent = typeLine;
      }
      if (cardObj.rules) {
        var rulesText = Array.isArray(cardObj.rules) ? cardObj.rules.join("\n") : cardObj.rules;
        els.rules.appendChild(renderTextWithMana(rulesText));
      }
    }

    // Fetch card details from Scryfall (oracle text, type line, mana cost)
    _fetchScryfallCard(cardName, cardImages, els);

    var imgUrl = resolveCardImage(cardName, cardObj, cardImages, "normal");
    els.image.src = imgUrl;
    els.image.alt = cardName;
    els.container.classList.remove("hidden");
  }

  function hidePreview(els) {
    if (els && els.container) {
      els.container.classList.add("hidden");
    }
  }

  // ── Card chip ──

  function makeCardChip(cardName, cardObj, cardImages, isTapped, previewEls) {
    var chip = document.createElement("span");
    chip.className = "card-chip" + (isTapped ? " tapped" : "");

    if (cardObj && cardObj.owner) {
      var ownerSpan = document.createElement("span");
      ownerSpan.className = "chip-owner";
      ownerSpan.textContent = cardObj.owner + " \u2192 ";
      chip.appendChild(ownerSpan);
    }

    if (cardObj && hasPT(cardObj)) {
      chip.appendChild(document.createTextNode(cardName + " "));
      var pt = document.createElement("span");
      pt.className = "pt";
      pt.textContent = formatPT(cardObj);
      chip.appendChild(pt);
    } else {
      chip.appendChild(document.createTextNode(cardName));
    }

    chip.addEventListener("mouseenter", function () {
      showPreview(cardName, cardObj, cardImages, previewEls);
    });
    chip.addEventListener("mouseleave", function () {
      hidePreview(previewEls);
    });
    return chip;
  }

  // ── Card thumbnail (battlefield + zones) ──

  function makeCardThumbnail(cardName, cardObj, cardImages, isTapped, previewEls) {
    var wrapper = document.createElement("div");
    wrapper.className = "card-thumb" + (isTapped ? " tapped" : "");
    var isToken = isTokenCard(cardObj || { name: cardName });

    var img = document.createElement("img");
    img.src = resolveCardImage(cardName, cardObj, cardImages, "small");
    img.alt = cardName;
    img.loading = "lazy";
    img.draggable = false;

    img.addEventListener("error", function () {
      // For tokens, try fetching from Scryfall search before showing fallback
      if (isToken) {
        fetchTokenImage(cardName, function (url) {
          img.src = url;
          img.style.opacity = "";
          var fb = wrapper.querySelector(".card-thumb-fallback");
          if (fb) fb.remove();
        });
      }
      img.style.opacity = "0";
      var fallback = document.createElement("div");
      fallback.className = "card-thumb-fallback" + (isToken ? " token-fallback" : "");

      if (isToken) {
        var label = document.createElement("div");
        label.className = "token-label";
        label.textContent = "TOKEN";
        fallback.appendChild(label);

        var nameEl = document.createElement("div");
        nameEl.className = "token-name";
        nameEl.textContent = cardName.replace(/ Token$/i, "");
        fallback.appendChild(nameEl);

        if (cardObj && hasPT(cardObj)) {
          var ptEl = document.createElement("div");
          ptEl.className = "token-pt";
          ptEl.textContent = formatPT(cardObj);
          fallback.appendChild(ptEl);
        }
      } else {
        fallback.textContent = cardName;
      }

      wrapper.appendChild(fallback);
    });

    wrapper.appendChild(img);

    if (cardObj && hasPT(cardObj)) {
      var pt = document.createElement("span");
      pt.className = "card-thumb-pt";
      pt.textContent = formatPT(cardObj);
      wrapper.appendChild(pt);
    }

    wrapper.addEventListener("mouseenter", function () {
      showPreview(cardName, cardObj, cardImages, previewEls);
    });
    wrapper.addEventListener("mouseleave", function () {
      hidePreview(previewEls);
    });

    return wrapper;
  }

  // ── Zone rendering ──

  function makeZone(title, cards, opts) {
    // opts: { cardImages, countOverride, useThumbnails, diffInfo, previewEls, smallThumbs }
    opts = opts || {};
    var cardImages = opts.cardImages || {};
    var countOverride = opts.countOverride;
    var useThumbnails = opts.useThumbnails || false;
    var smallThumbs = opts.smallThumbs || false;
    var diffInfo = opts.diffInfo || null;
    var previewEls = opts.previewEls;

    var zone = document.createElement("div");
    zone.className = "zone";

    var titleEl = document.createElement("div");
    titleEl.className = "zone-title";
    var count = countOverride != null ? countOverride : (cards ? cards.length : 0);
    titleEl.textContent = title + " (" + count + ")";
    zone.appendChild(titleEl);

    var row = document.createElement("div");
    row.className = useThumbnails ? "cards-row cards-grid" : "cards-row";
    if (smallThumbs) row.classList.add("cards-grid-sm");
    zone.appendChild(row);

    if (!cards || cards.length === 0) {
      if (count > 0) {
        var hidden = document.createElement("span");
        hidden.className = "zone-empty";
        hidden.textContent = count + " card" + (count !== 1 ? "s" : "");
        row.appendChild(hidden);
      }
      // Render ghost cards even if current list is empty
      if (diffInfo && diffInfo.ghostCards) {
        _renderGhosts(row, diffInfo.ghostCards, cardImages, useThumbnails, previewEls);
      }
      return zone;
    }

    var enteredBag = diffInfo ? diffInfo.enteredNames.slice() : [];
    var tapChangedSet = diffInfo ? diffInfo.tapChangedNames : [];

    cards.forEach(function (card) {
      var name, obj, tapped;
      if (typeof card === "string") {
        name = card; obj = null; tapped = false;
      } else {
        name = card.name || "Unknown"; obj = card; tapped = !!card.tapped;
      }
      var el;
      if (useThumbnails) {
        el = makeCardThumbnail(name, obj, cardImages, tapped, previewEls);
        if (smallThumbs) el.classList.add("card-thumb-sm");
      } else {
        el = makeCardChip(name, obj, cardImages, tapped, previewEls);
      }

      if (diffInfo) {
        var enteredIdx = enteredBag.indexOf(name);
        if (enteredIdx !== -1) {
          el.classList.add("card-entered");
          enteredBag.splice(enteredIdx, 1);
        }
        if (tapChangedSet.indexOf(name) !== -1) {
          el.classList.add("card-tap-changed");
        }
      }

      row.appendChild(el);
    });

    if (diffInfo && diffInfo.ghostCards) {
      _renderGhosts(row, diffInfo.ghostCards, cardImages, useThumbnails, previewEls);
    }

    return zone;
  }

  // ── Battlefield zone with land/nonland split ──

  function _groupLands(lands) {
    // Group lands by name+tapped → { key: { name, tapped, count, card } }
    var groups = {};
    lands.forEach(function (card) {
      var name = card.name || "Unknown";
      var tapped = !!card.tapped;
      var key = name + (tapped ? "|T" : "|U");
      if (!groups[key]) {
        groups[key] = { name: name, tapped: tapped, count: 0, card: card };
      }
      groups[key].count++;
    });
    return Object.keys(groups).map(function (k) { return groups[k]; });
  }

  function makeBattlefieldZone(cards, opts) {
    // opts: { cardImages, diffInfo, previewEls }
    opts = opts || {};
    var cardImages = opts.cardImages || {};
    var diffInfo = opts.diffInfo || null;
    var previewEls = opts.previewEls;

    // Split into non-lands (top) and lands (bottom)
    var nonLands = [];
    var lands = [];
    (cards || []).forEach(function (card) {
      if (typeof card === "string") {
        nonLands.push(card);
      } else if (isLikelyLand(card)) {
        lands.push(card);
      } else {
        nonLands.push(card);
      }
    });

    var zone = document.createElement("div");
    zone.className = "zone battlefield-zone";

    var totalCount = (cards || []).length;
    var titleEl = document.createElement("div");
    titleEl.className = "zone-title";
    titleEl.textContent = "Battlefield (" + totalCount + ")";
    zone.appendChild(titleEl);

    // Non-lands section (creatures, artifacts, etc.) - full thumbnails
    if (nonLands.length > 0) {
      var creaturesRow = document.createElement("div");
      creaturesRow.className = "cards-row cards-grid";
      zone.appendChild(creaturesRow);

      var enteredBag = diffInfo ? diffInfo.enteredNames.slice() : [];
      var tapChangedSet = diffInfo ? diffInfo.tapChangedNames : [];

      nonLands.forEach(function (card) {
        var name, obj, tapped;
        if (typeof card === "string") {
          name = card; obj = null; tapped = false;
        } else {
          name = card.name || "Unknown"; obj = card; tapped = !!card.tapped;
        }
        var el = makeCardThumbnail(name, obj, cardImages, tapped, previewEls);
        if (diffInfo) {
          var enteredIdx = enteredBag.indexOf(name);
          if (enteredIdx !== -1) {
            el.classList.add("card-entered");
            enteredBag.splice(enteredIdx, 1);
          }
          if (tapChangedSet.indexOf(name) !== -1) {
            el.classList.add("card-tap-changed");
          }
        }
        creaturesRow.appendChild(el);
      });

      // Ghost non-lands
      if (diffInfo && diffInfo.ghostCards) {
        diffInfo.ghostCards.forEach(function (ghost) {
          var gObj = typeof ghost === "string" ? { name: ghost } : ghost;
          if (!isLikelyLand(gObj)) {
            var gName = gObj.name || "Unknown";
            var gTapped = !!gObj.tapped;
            var el = makeCardThumbnail(gName, gObj, cardImages, gTapped, previewEls);
            el.classList.add("card-ghost");
            creaturesRow.appendChild(el);
          }
        });
      }
    }

    // Lands section - stacked with count badges
    if (lands.length > 0 || (diffInfo && diffInfo.ghostCards && diffInfo.ghostCards.some(function (g) {
      var go = typeof g === "string" ? { name: g } : g;
      return isLikelyLand(go);
    }))) {
      var landsRow = document.createElement("div");
      landsRow.className = "cards-row cards-grid land-row";
      zone.appendChild(landsRow);

      var landGroups = _groupLands(lands);
      var landEnteredBag = diffInfo ? diffInfo.enteredNames.slice() : [];
      var landTapChangedSet = diffInfo ? diffInfo.tapChangedNames : [];

      landGroups.forEach(function (group) {
        var el = makeCardThumbnail(group.name, group.card, cardImages, group.tapped, previewEls);
        el.classList.add("land-stack");

        if (group.count > 1) {
          var badge = document.createElement("span");
          badge.className = "land-count-badge";
          badge.textContent = "x" + group.count;
          el.appendChild(badge);
        }

        // Check diff info for entered lands
        if (diffInfo) {
          for (var i = 0; i < group.count; i++) {
            var enteredIdx = landEnteredBag.indexOf(group.name);
            if (enteredIdx !== -1) {
              el.classList.add("card-entered");
              landEnteredBag.splice(enteredIdx, 1);
            }
          }
          if (landTapChangedSet.indexOf(group.name) !== -1) {
            el.classList.add("card-tap-changed");
          }
        }

        landsRow.appendChild(el);
      });

      // Ghost lands
      if (diffInfo && diffInfo.ghostCards) {
        diffInfo.ghostCards.forEach(function (ghost) {
          var gObj = typeof ghost === "string" ? { name: ghost } : ghost;
          if (isLikelyLand(gObj)) {
            var gName = gObj.name || "Unknown";
            var gTapped = !!gObj.tapped;
            var el = makeCardThumbnail(gName, gObj, cardImages, gTapped, previewEls);
            el.classList.add("card-ghost");
            landsRow.appendChild(el);
          }
        });
      }
    }

    // Empty state
    if (totalCount === 0 && !(diffInfo && diffInfo.ghostCards && diffInfo.ghostCards.length > 0)) {
      // no content needed
    }

    return zone;
  }

  function _renderGhosts(row, ghostCards, cardImages, useThumbnails, previewEls) {
    ghostCards.forEach(function (ghost) {
      var gName = typeof ghost === "string" ? ghost : (ghost.name || "Unknown");
      var gObj = typeof ghost === "string" ? null : ghost;
      var gTapped = gObj ? !!gObj.tapped : false;
      var el;
      if (useThumbnails) {
        el = makeCardThumbnail(gName, gObj, cardImages, gTapped, previewEls);
      } else {
        el = makeCardChip(gName, gObj, cardImages, gTapped, previewEls);
      }
      el.classList.add("card-ghost");
      row.appendChild(el);
    });
  }

  // ── Player rendering ──

  var PLAYER_COLORS = ["player-0", "player-1", "player-2", "player-3"];

  function renderPlayers(container, players, opts) {
    // opts: { cardImages, playerColorMap, diffs, previewEls, showTimer, showThumbnails, playerMeta, priorityPlayerName }
    opts = opts || {};
    var cardImages = opts.cardImages || {};
    var playerColorMap = opts.playerColorMap || {};
    var diffs = opts.diffs || null;
    var previewEls = opts.previewEls;
    var showTimer = opts.showTimer || false;
    var playerMeta = opts.playerMeta || {};
    var priorityPlayerName = opts.priorityPlayerName || "";

    container.innerHTML = "";
    if (!players || players.length === 0) return;

    // In 1v1 (2 players), stack vertically instead of side-by-side
    container.classList.toggle("players-1v1", players.length === 2);

    players.forEach(function (player) {
      var playerDiff = diffs ? diffs[player.name] : null;
      var meta = playerMeta[player.name] || {};

      var card = document.createElement("article");
      card.className = "player-card";
      if (player.has_left) card.classList.add("eliminated");
      var pColorIdx = playerColorMap[player.name];
      if (pColorIdx != null) card.classList.add(PLAYER_COLORS[pColorIdx]);
      if (player.is_active) card.classList.add("active-turn");

      // Header
      var header = document.createElement("div");
      header.className = "player-header";

      var nameEl = document.createElement("div");
      nameEl.className = "player-name";
      if (pColorIdx != null) nameEl.classList.add(PLAYER_COLORS[pColorIdx]);
      if (player.name === priorityPlayerName) nameEl.classList.add("has-priority");
      nameEl.textContent = player.name || "?";
      header.appendChild(nameEl);

      // Model + cost badges
      if (meta.model || meta.totalCostUsd != null) {
        var badgeRow = document.createElement("div");
        badgeRow.className = "player-badges";
        if (meta.model) {
          var modelBadge = document.createElement("span");
          modelBadge.className = "player-model";
          // Strip provider prefix (e.g. "google/gemini-2.5-flash" -> "gemini-2.5-flash")
          var modelName = meta.model;
          var slashIdx = modelName.indexOf("/");
          if (slashIdx !== -1) modelName = modelName.substring(slashIdx + 1);
          modelBadge.textContent = modelName;
          badgeRow.appendChild(modelBadge);
        }
        if (meta.totalCostUsd != null) {
          var costBadge = document.createElement("span");
          costBadge.className = "player-cost";
          costBadge.textContent = "$" + meta.totalCostUsd.toFixed(2);
          badgeRow.appendChild(costBadge);
        }
        header.appendChild(badgeRow);
      }

      var lifeEl = document.createElement("div");
      lifeEl.className = "player-life";
      var libCount = player.library_size;
      var lifeText = "Life " + (player.life != null ? player.life : "?") +
                     " | Library " + (libCount != null ? libCount : "?");
      if (showTimer && (player.priorityTimeLeftSecs > 0 || player.timerActive)) {
        var secs = player.priorityTimeLeftSecs || 0;
        var m = Math.floor(secs / 60);
        var s = secs % 60;
        lifeText += " | Clock " + m + ":" + String(s).padStart(2, "0");
      }
      lifeEl.textContent = lifeText;

      if (playerDiff && playerDiff.lifeChange !== 0) {
        var lifeSpan = document.createElement("span");
        lifeSpan.className = playerDiff.lifeChange > 0 ? "life-up" : "life-down";
        lifeSpan.textContent = " (" + (playerDiff.lifeChange > 0 ? "+" : "") + playerDiff.lifeChange + ")";
        lifeEl.appendChild(lifeSpan);
        lifeEl.classList.add(playerDiff.lifeChange > 0 ? "life-changed-up" : "life-changed-down");
      }
      header.appendChild(lifeEl);

      card.appendChild(header);

      // Counters — v2 server format uses {name: count} dict, live/spectator uses [{name, count}] array
      var rawCounters = player.counters || [];
      if (!Array.isArray(rawCounters)) {
        rawCounters = Object.keys(rawCounters).map(function (k) { return { name: k, count: rawCounters[k] }; });
      }
      var counters = rawCounters.filter(function (c) { return c && c.count > 0; });
      if (counters.length > 0) {
        var countersEl = document.createElement("div");
        countersEl.className = "player-counters";
        countersEl.textContent = counters.map(function (c) { return c.name + ": " + c.count; }).join(" | ");
        card.appendChild(countersEl);
      }

      // Zones
      var zoneOpts = { cardImages: cardImages, previewEls: previewEls };
      var isCommander = opts.isCommander || false;

      var bfDiff = playerDiff ? {
        enteredNames: (playerDiff.battlefield.entered || []).slice(),
        tapChangedNames: playerDiff.battlefield.tapChanged || [],
        ghostCards: playerDiff.battlefield.left || [],
      } : null;

      // Side zones (commander/graveyard/exile) + battlefield in a horizontal layout
      var bodyRow = document.createElement("div");
      bodyRow.className = "player-body";

      // Side zones column (left)
      var commanders = player.commanders || [];
      if (commanders.length === 0 && player.commander) {
        commanders = [typeof player.commander === "string" ? player.commander : player.commander];
      }
      var hasAnySideZone = isCommander || player.graveyard.length > 0 || (player.exile && player.exile.length > 0);
      if (hasAnySideZone) {
        var sideCol = document.createElement("div");
        sideCol.className = "side-zones";

        if (isCommander) {
          var cmdZone = makeZone("Commander", commanders, {
            cardImages: cardImages, previewEls: previewEls,
            useThumbnails: commanders.length > 0, smallThumbs: true,
          });
          cmdZone.classList.add("commander-zone");
          sideCol.appendChild(cmdZone);
        }

        var gyDiff = playerDiff ? {
          enteredNames: (playerDiff.graveyard.entered || []).slice(),
          tapChangedNames: [],
          ghostCards: [],
        } : null;
        var gyZone = makeZone("Graveyard", player.graveyard, {
          cardImages: cardImages, diffInfo: gyDiff, previewEls: previewEls,
          useThumbnails: player.graveyard.length > 0, smallThumbs: true,
        });
        gyZone.classList.add("graveyard-zone");
        sideCol.appendChild(gyZone);

        if (player.exile && player.exile.length > 0) {
          var exDiff = playerDiff ? {
            enteredNames: (playerDiff.exile.entered || []).slice(),
            tapChangedNames: [],
            ghostCards: [],
          } : null;
          var exZone = makeZone("Exile", player.exile, {
            cardImages: cardImages, diffInfo: exDiff, previewEls: previewEls,
            useThumbnails: true, smallThumbs: true,
          });
          exZone.classList.add("exile-zone");
          sideCol.appendChild(exZone);
        }

        bodyRow.appendChild(sideCol);

        // Fit graveyard cards with aggressive overlap (show only card names)
        _fitOverlappingCards(gyZone, 300, 70, true);
      }

      // Main column (battlefield + hand)
      var mainCol = document.createElement("div");
      mainCol.className = "main-zones";

      mainCol.appendChild(makeBattlefieldZone(player.battlefield, {
        cardImages: cardImages, diffInfo: bfDiff, previewEls: previewEls,
      }));

      var handDiff = playerDiff ? {
        enteredNames: (playerDiff.hand.entered || []).slice(),
        tapChangedNames: [],
        ghostCards: [],
      } : null;
      var handZone = makeZone("Hand", player.hand, {
        cardImages: cardImages, countOverride: player.hand_count, diffInfo: handDiff, previewEls: previewEls,
        useThumbnails: player.hand.length > 0, smallThumbs: true,
      });
      handZone.classList.add("hand-zone");
      mainCol.appendChild(handZone);

      bodyRow.appendChild(mainCol);
      card.appendChild(bodyRow);

      container.appendChild(card);
    });
  }

  // ── Overlapping card fit (graveyard/exile) ──

  function _fitOverlappingCards(zoneEl, maxHeight, baseWidth, alwaysOverlap) {
    var grid = zoneEl.querySelector(".cards-grid-sm");
    if (!grid) return;
    var cards = grid.querySelectorAll(".card-thumb-sm");
    var N = cards.length;
    if (N <= 1) return;

    var cardH = Math.round(baseWidth * 204 / 146);

    // Force aggressive overlap: show only card name for cards below the top.
    // Dynamically shrink the visible slice so the zone stays within maxHeight.
    if (alwaysOverlap) {
      var visibleSlice = Math.min(20, Math.floor((maxHeight - cardH) / (N - 1)));
      if (visibleSlice < 1) visibleSlice = 1;
      var forceOverlap = cardH - visibleSlice;
      for (var i = 1; i < cards.length; i++) {
        cards[i].style.marginTop = "-" + forceOverlap + "px";
      }
      return;
    }

    var totalNatural = N * cardH;
    if (totalNatural <= maxHeight) return; // fits without overlap

    var minVisible = 16;
    var overlap = Math.ceil((totalNatural - maxHeight) / (N - 1));
    var visible = cardH - overlap;

    if (visible < minVisible) {
      // Shrink cards to fit
      var newH = Math.max(minVisible * 2, maxHeight - (N - 1) * minVisible);
      var newW = Math.round(newH * 146 / 204);
      overlap = Math.max(0, newH - minVisible);
      for (var i = 0; i < cards.length; i++) {
        cards[i].style.width = newW + "px";
        if (i > 0) cards[i].style.marginTop = "-" + overlap + "px";
      }
    } else {
      for (var i = 1; i < cards.length; i++) {
        cards[i].style.marginTop = "-" + overlap + "px";
      }
    }
  }

  // ── Stack rendering ──

  function renderStack(container, cardsContainer, stack, cardImages, previewEls) {
    if (!container) return;
    container.classList.remove("hidden");
    cardsContainer.innerHTML = "";
    if (stack && stack.length > 0) {
      stack.forEach(function (item) {
        var name = typeof item === "string" ? item : (item.name || "");
        if (!name) return; // skip empty-named stack items (legacy StackAbilityView bug)
        var obj = typeof item === "string" ? null : item;
        var wrapper = document.createElement("div");
        wrapper.className = "stack-item";
        wrapper.appendChild(makeCardThumbnail(name, obj, cardImages, false, previewEls));
        if (obj && obj.targets && obj.targets.length > 0) {
          var targetEl = document.createElement("div");
          targetEl.className = "stack-target";
          targetEl.textContent = "\u2192 " + obj.targets.map(function (t) {
            return typeof t === "string" ? t : t.name;
          }).join(", ");
          wrapper.appendChild(targetEl);
        }
        cardsContainer.appendChild(wrapper);
      });
    }
  }

  // ── Phase/step formatting ──

  var STEP_LABELS = {
    PRECOMBAT_MAIN: "Main Phase 1",
    POSTCOMBAT_MAIN: "Main Phase 2",
    BEGIN_COMBAT: "Begin Combat",
    DECLARE_ATTACKERS: "Declare Attackers",
    DECLARE_BLOCKERS: "Declare Blockers",
    COMBAT_DAMAGE: "Combat Damage",
    FIRST_COMBAT_DAMAGE: "Combat Damage",
    END_COMBAT: "End Combat",
    END_TURN: "End Step",
    CLEANUP: "Cleanup",
    UPKEEP: "Upkeep",
    DRAW: "Draw Step",
    UNTAP: "Untap",
  };

  function formatPhaseStep(phase, step) {
    var key = step || phase || "";
    if (STEP_LABELS[key]) return STEP_LABELS[key];
    if (!key) return "";
    // Fallback: title-case with underscores replaced
    return key.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  // ── Status line ──

  function renderStatusLine(el, snap) {
    if (!el || !snap) return;
    var turn = snap.turn != null ? "Turn " + snap.turn : "Turn ?";
    var phase = snap.phase || "?";
    var step = snap.step || "?";
    var phaseDisplay = step && step !== phase ? phase + " / " + step : phase;
    var active = snap.active_player || "?";
    var priority = snap.priority_player || "?";
    el.textContent = turn + " | " + phaseDisplay + " | Active: " + active + " | Priority: " + priority;
  }

  // ── Diff computation ──

  function diffStringBag(prevList, currList) {
    var prevBag = {};
    var currBag = {};
    prevList.forEach(function (n) { prevBag[n] = (prevBag[n] || 0) + 1; });
    currList.forEach(function (n) { currBag[n] = (currBag[n] || 0) + 1; });
    var entered = [];
    var left = [];
    var allNames = {};
    Object.keys(prevBag).forEach(function (n) { allNames[n] = true; });
    Object.keys(currBag).forEach(function (n) { allNames[n] = true; });
    Object.keys(allNames).forEach(function (name) {
      var diff = (currBag[name] || 0) - (prevBag[name] || 0);
      for (var i = 0; i < Math.abs(diff); i++) {
        if (diff > 0) entered.push(name);
        else left.push(name);
      }
    });
    return { entered: entered, left: left };
  }

  function diffBattlefield(prevCards, currCards) {
    var prevBag = {};
    var currBag = {};
    var prevTapped = {};
    var currTapped = {};
    prevCards.forEach(function (c) {
      var n = c.name || "Unknown";
      prevBag[n] = (prevBag[n] || 0) + 1;
      if (!prevTapped[n]) prevTapped[n] = [];
      prevTapped[n].push(!!c.tapped);
    });
    currCards.forEach(function (c) {
      var n = c.name || "Unknown";
      currBag[n] = (currBag[n] || 0) + 1;
      if (!currTapped[n]) currTapped[n] = [];
      currTapped[n].push(!!c.tapped);
    });
    var entered = [];
    var left = [];
    var tapChanged = [];
    var allNames = {};
    Object.keys(prevBag).forEach(function (n) { allNames[n] = true; });
    Object.keys(currBag).forEach(function (n) { allNames[n] = true; });
    Object.keys(allNames).forEach(function (name) {
      var pc = prevBag[name] || 0;
      var cc = currBag[name] || 0;
      var diff = cc - pc;
      if (diff > 0) {
        for (var i = 0; i < diff; i++) entered.push(name);
      } else if (diff < 0) {
        for (var i = 0; i < -diff; i++) {
          var cardObj = prevCards.find(function (c) { return (c.name || "Unknown") === name; });
          left.push(cardObj || { name: name, tapped: false });
        }
      }
      if (pc > 0 && cc > 0) {
        var minCount = Math.min(pc, cc);
        var pt = (prevTapped[name] || []).slice(0, minCount);
        var ct = (currTapped[name] || []).slice(0, minCount);
        for (var i = 0; i < minCount; i++) {
          if (pt[i] !== ct[i]) {
            tapChanged.push(name);
            break;
          }
        }
      }
    });
    return { entered: entered, left: left, tapChanged: tapChanged };
  }

  function computeDiff(prevSnap, currSnap) {
    if (!prevSnap || !currSnap) return null;
    var diffs = {};
    var prevPlayers = {};
    (prevSnap.players || []).forEach(function (p) { prevPlayers[p.name] = p; });
    (currSnap.players || []).forEach(function (curr) {
      var prev = prevPlayers[curr.name];
      if (!prev) return;
      var prevHandNames = (prev.hand || []).map(function (c) {
        return typeof c === "string" ? c : (c.name || "Unknown");
      });
      var currHandNames = (curr.hand || []).map(function (c) {
        return typeof c === "string" ? c : (c.name || "Unknown");
      });
      var prevGraveyardNames = (prev.graveyard || []).map(function (c) {
        return typeof c === "string" ? c : (c.name || "Unknown");
      });
      var currGraveyardNames = (curr.graveyard || []).map(function (c) {
        return typeof c === "string" ? c : (c.name || "Unknown");
      });
      var prevExileNames = (prev.exile || []).map(function (c) {
        return typeof c === "string" ? c : (c.name || "Unknown");
      });
      var currExileNames = (curr.exile || []).map(function (c) {
        return typeof c === "string" ? c : (c.name || "Unknown");
      });
      diffs[curr.name] = {
        lifeChange: (curr.life || 0) - (prev.life || 0),
        handCountChange: (curr.hand_count || 0) - (prev.hand_count || 0),
        battlefield: diffBattlefield(prev.battlefield || [], curr.battlefield || []),
        graveyard: diffStringBag(prevGraveyardNames, currGraveyardNames),
        exile: diffStringBag(prevExileNames, currExileNames),
        hand: diffStringBag(prevHandNames, currHandNames),
      };
    });
    return diffs;
  }

  // ── Positioned mode (OBS) ──

  function collectPositionCards(state) {
    var out = [];
    var zoneList = ["commanders", "battlefield", "hand", "graveyard", "exile"];
    (state.players || []).forEach(function (player) {
      zoneList.forEach(function (zone) {
        (player[zone] || []).forEach(function (card) {
          if (card && card.layout) {
            out.push({ card: card, playerId: player.id || player.name, zone: zone, layout: card.layout });
          }
        });
      });
    });
    (state.stack || []).forEach(function (card) {
      if (card && card.layout) {
        out.push({ card: card, playerId: "global", zone: "stack", layout: card.layout });
      }
    });
    return out;
  }

  function computeCardFontSize(width, height) {
    if (width < 42 || height < 16) return 0;
    return Math.max(6, Math.min(11, Math.round(width / 9.5)));
  }

  function renderPositionLayer(positionLayer, state, containerEl, previewEls) {
    if (!positionLayer) return false;

    var sourceWidth = Number(state && state.layout && state.layout.sourceWidth || 0);
    var sourceHeight = Number(state && state.layout && state.layout.sourceHeight || 0);
    if (sourceWidth <= 0 || sourceHeight <= 0) return false;

    var entries = collectPositionCards(state);
    if (entries.length === 0) return false;

    // Enable positioned mode before measuring
    if (containerEl) containerEl.classList.add("positioned-mode");
    positionLayer.classList.remove("hidden");

    positionLayer.innerHTML = "";

    var layerRect = positionLayer.getBoundingClientRect();
    var layerWidth = layerRect.width > 0 ? layerRect.width : (typeof window !== "undefined" ? window.innerWidth : 0);
    var layerHeight = layerRect.height > 0 ? layerRect.height : (typeof window !== "undefined" ? window.innerHeight : 0);
    if (layerWidth <= 0 || layerHeight <= 0) return false;

    var scaleX = layerWidth / sourceWidth;
    var scaleY = layerHeight / sourceHeight;

    entries.forEach(function (entry) {
      var layout = entry.layout || {};
      var x = Math.round(Number(layout.x || 0) * scaleX);
      var y = Math.round(Number(layout.y || 0) * scaleY);
      var width = Math.max(4, Math.round(Number(layout.width || 0) * scaleX));
      var height = Math.max(4, Math.round(Number(layout.height || 0) * scaleY));

      if (width < 2 || height < 2) return;

      var hotspot = document.createElement("div");
      hotspot.className = "position-card" + (entry.card.tapped ? " tapped" : "");
      var fontSize = computeCardFontSize(width, height);
      if (fontSize === 0) {
        hotspot.classList.add("small");
      } else {
        hotspot.style.fontSize = fontSize + "px";
        hotspot.textContent = entry.card.name || "";
        var linesAvailable = Math.floor((height - 4) / (fontSize * 1.15));
        if (width < 80 && linesAvailable >= 2) {
          hotspot.classList.add("wrap-name");
          hotspot.style.webkitLineClamp = Math.min(linesAvailable, 3);
        }
      }

      hotspot.style.left = x + "px";
      hotspot.style.top = y + "px";
      hotspot.style.width = width + "px";
      hotspot.style.height = height + "px";

      if (previewEls) {
        hotspot.addEventListener("mouseenter", function () {
          showPreview(entry.card.name, entry.card, {}, previewEls);
        });
        hotspot.addEventListener("mouseleave", function () {
          hidePreview(previewEls);
        });
      }

      positionLayer.appendChild(hotspot);
    });

    return true;
  }

  // ── Mouse-following card preview ──

  function setupMousePreview(container) {
    if (typeof document === "undefined") return;
    document.addEventListener("mousemove", function (e) {
      if (!container || container.classList.contains("hidden")) return;
      var x = e.clientX + 20;
      var y = e.clientY - 20;
      var vw = window.innerWidth;
      var vh = window.innerHeight;
      var w = container.offsetWidth;
      var h = container.offsetHeight;
      if (x + w > vw) x = e.clientX - w - 20;
      if (y + h > vh) y = vh - h - 8;
      if (y < 8) y = 8;
      container.style.left = x + "px";
      container.style.top = y + "px";
    });
  }

  // ── Public API ──

  var GameRenderer = {
    // Normalisation
    normalizeLiveState: normalizeLiveState,
    normalizeCard: normalizeCard,
    // Classification
    isTokenCard: isTokenCard,
    isLikelyLand: isLikelyLand,
    hasPT: hasPT,
    formatPT: formatPT,
    // Images
    resolveCardImage: resolveCardImage,
    fetchTokenImage: fetchTokenImage,
    renderManaCost: renderManaCost,
    // Preview
    showPreview: showPreview,
    hidePreview: hidePreview,
    // Elements
    makeCardChip: makeCardChip,
    makeCardThumbnail: makeCardThumbnail,
    makeZone: makeZone,
    makeBattlefieldZone: makeBattlefieldZone,
    // Rendering
    renderPlayers: renderPlayers,
    renderStack: renderStack,
    renderStatusLine: renderStatusLine,
    setupMousePreview: setupMousePreview,
    // Diffs
    computeDiff: computeDiff,
    diffStringBag: diffStringBag,
    diffBattlefield: diffBattlefield,
    // Positioned mode
    renderPositionLayer: renderPositionLayer,
    collectPositionCards: collectPositionCards,
    computeCardFontSize: computeCardFontSize,
    // Phase formatting
    formatPhaseStep: formatPhaseStep,
    // Constants
    PLAYER_COLORS: PLAYER_COLORS,
  };

  // Browser: attach to window
  if (typeof root !== "undefined" && root !== null) {
    root.GameRenderer = GameRenderer;
  }

  // Module: export for Vitest / Node
  if (typeof module !== "undefined" && module.exports) {
    module.exports = GameRenderer;
  }

})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
