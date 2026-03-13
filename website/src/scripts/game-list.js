/* Shared game list logic for /games and /season/N/games pages.
 *
 * Usage:
 *   GameList.init({
 *     games: [...],
 *     minBlunderVersion: 11,
 *     showSeasonFilter: true,   // /games page only
 *     showSeasonBadge: true,    // /games page only
 *   });
 */
window.GameList = {
  init: function (config) {
    var __games = config.games;
    var MIN_BLUNDER_VERSION = config.minBlunderVersion;
    var showSeasonFilter = config.showSeasonFilter || false;
    var showSeasonBadge = config.showSeasonBadge || false;
    var ratingsData = {};

    var DECK_TYPE_FORMATS = {
      "Constructed - Standard": "standard",
      "Constructed - Modern": "modern",
      "Constructed - Legacy": "legacy",
      "Variant Magic - Freeform Commander": "commander",
      "Variant Magic - Commander": "commander",
      "Limited": "jumpstart",
    };

    var FORMAT_DISPLAY = {
      "standard": "Standard",
      "modern": "Modern",
      "legacy": "Legacy",
      "commander": "Commander (Exhibition)",
      "jumpstart": "Jumpstart",
    };

    function gameSeason(game) {
      return game.season != null ? game.season : 0;
    }

    function getGameFormat(deckType) {
      return DECK_TYPE_FORMATS[deckType] || "commander";
    }

    function getFilters() {
      var params = new URLSearchParams(window.location.search);
      return {
        model: params.get("model") || "",
        format: params.get("format") || "",
        effort: params.get("effort") || "",
        season: params.get("season") || "",
      };
    }

    function setFilters(filters) {
      var params = new URLSearchParams();
      if (filters.model) params.set("model", filters.model);
      if (filters.format) params.set("format", filters.format);
      if (filters.effort) params.set("effort", filters.effort);
      if (filters.season) params.set("season", filters.season);
      var qs = params.toString();
      var url = window.location.pathname + (qs ? "?" + qs : "");
      history.replaceState(null, "", url);
    }

    function filterGames(games, filters) {
      return games.filter(function (game) {
        if (filters.season !== "") {
          if (String(gameSeason(game)) !== filters.season) return false;
        }
        if (filters.format) {
          var fmt = getGameFormat(game.deckType);
          if (fmt !== filters.format) return false;
        }
        if (filters.model) {
          var hasModel = (game.players || []).some(function (p) {
            if (p.model !== filters.model) return false;
            if (filters.effort && (p.reasoningEffort || "") !== filters.effort) return false;
            return true;
          });
          if (!hasModel) return false;
        }
        return true;
      });
    }

    function renderFilterBar(filters, totalCount, filteredCount) {
      var bar = document.getElementById("filter-bar");
      var hasFilters = filters.model || filters.format || filters.effort || filters.season;
      if (!hasFilters) {
        bar.style.display = "none";
        bar.innerHTML = "";
        return;
      }
      bar.style.display = "";
      bar.className = "filter-bar";
      bar.innerHTML = "";

      var label = document.createElement("span");
      label.className = "filter-label";
      label.textContent = "Filtered:";
      bar.appendChild(label);

      if (filters.season) {
        var chip = document.createElement("span");
        chip.className = "filter-chip";
        chip.textContent = "Season " + filters.season;
        var btn = document.createElement("button");
        btn.className = "filter-chip-remove";
        btn.innerHTML = "&times;";
        btn.onclick = function () {
          var f = getFilters();
          f.season = "";
          setFilters(f);
          renderAll();
        };
        chip.appendChild(btn);
        bar.appendChild(chip);
      }

      if (filters.format) {
        const formatChip = document.createElement("span");
        formatChip.className = "filter-chip";
        formatChip.textContent = FORMAT_DISPLAY[filters.format] || filters.format;
        const formatBtn = document.createElement("button");
        formatBtn.className = "filter-chip-remove";
        formatBtn.innerHTML = "&times;";
        formatBtn.onclick = function () {
          var f = getFilters();
          f.format = "";
          setFilters(f);
          renderAll();
        };
        formatChip.appendChild(formatBtn);
        bar.appendChild(formatChip);
      }

      if (filters.model) {
        const modelChip = document.createElement("span");
        modelChip.className = "filter-chip";
        var modelDisplay = filters.model;
        var slash = modelDisplay.indexOf("/");
        if (slash !== -1) modelDisplay = modelDisplay.substring(slash + 1);
        if (filters.effort) modelDisplay += " (" + filters.effort + ")";
        modelChip.textContent = modelDisplay;
        const modelBtn = document.createElement("button");
        modelBtn.className = "filter-chip-remove";
        modelBtn.innerHTML = "&times;";
        modelBtn.onclick = function () {
          var f = getFilters();
          f.model = "";
          f.effort = "";
          setFilters(f);
          renderAll();
        };
        modelChip.appendChild(modelBtn);
        bar.appendChild(modelChip);
      }

      var activeFilterCount = (filters.model ? 1 : 0) + (filters.format ? 1 : 0) + (filters.season ? 1 : 0);
      if (activeFilterCount > 1) {
        var clear = document.createElement("button");
        clear.className = "filter-clear";
        clear.textContent = "Clear all";
        clear.onclick = function () {
          setFilters({ model: "", format: "", effort: "", season: "" });
          renderAll();
        };
        bar.appendChild(clear);
      }

      var count = document.createElement("span");
      count.className = "filter-count";
      count.textContent = filteredCount + " of " + totalCount + " games";
      bar.appendChild(count);
    }

    function renderGameCard(game) {
      var a = document.createElement("a");
      a.href = "/games/" + game.id;
      a.className = "game-card";

      var ts = game.timestamp || "";
      var dateStr = "";
      if (ts.length >= 15) {
        var year = ts.substring(0, 4);
        var month = ts.substring(4, 6);
        var day = ts.substring(6, 8);
        var hour = ts.substring(9, 11);
        var min = ts.substring(11, 13);
        var d = new Date(year + "-" + month + "-" + day + "T" + hour + ":" + min);
        if (!isNaN(d.getTime())) {
          dateStr = d.toLocaleDateString("en-US", {
            year: "numeric", month: "short", day: "numeric"
          }) + " " + d.toLocaleTimeString("en-US", {
            hour: "numeric", minute: "2-digit"
          });
        }
      }

      var header = document.createElement("div");
      header.className = "game-header";

      var dateEl = document.createElement("span");
      dateEl.className = "game-date";
      dateEl.textContent = dateStr || game.id;
      header.appendChild(dateEl);

      var formatKey = getGameFormat(game.deckType);
      var formatLabel = FORMAT_DISPLAY[formatKey] || "Commander";
      a.dataset.format = formatKey;
      var fmtBadge = document.createElement("span");
      fmtBadge.className = "format-badge format-" + formatLabel.toLowerCase();
      fmtBadge.textContent = formatLabel;
      header.appendChild(fmtBadge);

      var turnsEl = document.createElement("span");
      turnsEl.className = "game-turns";
      turnsEl.textContent = game.totalTurns + " turns";
      header.appendChild(turnsEl);

      if (showSeasonBadge && gameSeason(game) === 0) {
        var seasonBadge = document.createElement("span");
        seasonBadge.className = "season-badge season-old";
        seasonBadge.title = "Season 0 game \u2014 not included in Season 1 ratings";
        seasonBadge.textContent = "Season 0";
        header.appendChild(seasonBadge);
      }

      a.appendChild(header);

      var grid = document.createElement("div");
      grid.className = "game-players";

      var totalCost = 0;
      var hasCost = false;
      var isCommander = getGameFormat(game.deckType) === "commander";

      (game.players || []).forEach(function (p) {
        var cell = document.createElement("div");
        cell.className = "player-cell";
        if (isCommander && p.placement != null) {
          cell.classList.add("placement-" + p.placement);
        } else if (game.winner && p.name === game.winner) {
          cell.classList.add("is-winner");
        }

        var nameRow = document.createElement("div");
        nameRow.className = "player-name";

        if (isCommander && p.placement != null) {
          var placeEl = document.createElement("span");
          placeEl.className = "player-placement placement-" + p.placement;
          var ordinals = ["", "1st", "2nd", "3rd", "4th"];
          placeEl.textContent = ordinals[p.placement] || p.placement + "th";
          nameRow.appendChild(placeEl);
        }

        var nameText = document.createTextNode(p.name);
        nameRow.appendChild(nameText);

        if (game.winner && p.name === game.winner) {
          var badge = document.createElement("span");
          badge.className = "winner-badge";
          badge.textContent = "WINNER";
          nameRow.appendChild(badge);
        }

        if (p.timedOut) {
          var toBadge = document.createElement("span");
          toBadge.className = "timeout-badge";
          toBadge.textContent = "TIMEOUT";
          nameRow.appendChild(toBadge);
        }

        if (p.model) {
          var modelName = p.model;
          var slash = modelName.indexOf("/");
          if (slash !== -1) modelName = modelName.substring(slash + 1);
          var modelSpan = document.createElement("span");
          modelSpan.className = "player-model-inline";
          modelSpan.textContent = "(" + modelName + ")";
          nameRow.appendChild(modelSpan);
        }

        cell.appendChild(nameRow);

        var cmdEl = document.createElement("div");
        cmdEl.className = "player-commander";
        cmdEl.textContent = p.deckName || p.commander || "";
        cell.appendChild(cmdEl);

        if (p.totalCostUsd != null) {
          var costEl = document.createElement("div");
          costEl.className = "player-cost";
          costEl.textContent = "$" + p.totalCostUsd.toFixed(2);
          cell.appendChild(costEl);
          totalCost += p.totalCostUsd;
          hasCost = true;
        }

        var playerScore = (game.blunderScoreByPlayer || {})[p.name];
        if (playerScore != null) {
          var scoreEl = document.createElement("div");
          scoreEl.className = "player-blunder-score";
          if (playerScore >= 1.5) scoreEl.classList.add("blunder-high");
          else if (playerScore >= 0.5) scoreEl.classList.add("blunder-med");
          else scoreEl.classList.add("blunder-low");
          var isOldAnalysis = !game.blunderScriptVersion || game.blunderScriptVersion < MIN_BLUNDER_VERSION;
          scoreEl.textContent = "Blunder Index: " + playerScore.toFixed(2);
          if (isOldAnalysis) {
            var oldTag = document.createElement("span");
            oldTag.className = "old-analysis-tag";
            oldTag.textContent = "(older analysis)";
            oldTag.title = "Analyzed with blunder script v" + (game.blunderScriptVersion || 1) + " (min: v" + MIN_BLUNDER_VERSION + ")";
            scoreEl.appendChild(document.createTextNode(" "));
            scoreEl.appendChild(oldTag);
          }
          cell.appendChild(scoreEl);
        }

        var gameRatings = ratingsData[game.id];
        var ratingKey = p.model;
        if (ratingKey && p.reasoningEffort) ratingKey += "::" + p.reasoningEffort;
        var playerRating = gameRatings && ratingKey ? gameRatings[ratingKey] : null;
        if (playerRating) {
          var ratingEl = document.createElement("div");
          ratingEl.className = "player-rating";
          var delta = playerRating.after - playerRating.before;
          if (delta > 0) ratingEl.classList.add("rating-up");
          else if (delta < 0) ratingEl.classList.add("rating-down");
          ratingEl.textContent = playerRating.before + " \u2192 " + playerRating.after;
          cell.appendChild(ratingEl);
        }

        grid.appendChild(cell);
      });

      a.appendChild(grid);

      if (hasCost) {
        var footer = document.createElement("div");
        footer.className = "game-footer";
        footer.textContent = "Total cost: $" + totalCost.toFixed(2);
        a.appendChild(footer);
      }
      if (game.youtubeUrl) {
        var ytLink = document.createElement("a");
        ytLink.href = game.youtubeUrl;
        ytLink.target = "_blank";
        ytLink.rel = "noopener";
        ytLink.className = "yt-link";
        ytLink.textContent = "YouTube";
        ytLink.onclick = function (e) { e.stopPropagation(); };
        var footerEl = a.querySelector(".game-footer");
        if (footerEl) {
          footerEl.appendChild(ytLink);
        } else {
          var ytFooter = document.createElement("div");
          ytFooter.className = "game-footer";
          ytFooter.appendChild(ytLink);
          a.appendChild(ytFooter);
        }
      }

      return a;
    }

    function renderSeasonFilter(games) {
      var el = document.getElementById("season-filter");
      if (!el) return;
      var counts = {};
      games.forEach(function (game) {
        var s = gameSeason(game);
        counts[s] = (counts[s] || 0) + 1;
      });
      var seasons = Object.keys(counts).map(Number).sort().reverse();
      if (seasons.length <= 1) {
        el.style.display = "none";
        return;
      }
      el.innerHTML = "";
      var filters = getFilters();

      var label = document.createElement("span");
      label.className = "season-filter-label";
      label.textContent = "Season:";
      el.appendChild(label);

      var allBtn = document.createElement("button");
      allBtn.className = "season-filter-btn" + (!filters.season ? " active" : "");
      allBtn.textContent = "All";
      allBtn.onclick = function () {
        var f = getFilters(); f.season = ""; setFilters(f); renderAll();
      };
      el.appendChild(allBtn);

      seasons.forEach(function (s) {
        var btn = document.createElement("button");
        btn.className = "season-filter-btn" + (filters.season === String(s) ? " active" : "");
        var seasonLabel = "Season " + s;
        btn.innerHTML = seasonLabel + ' <span class="tab-count">(' + counts[s] + ")</span>";
        btn.dataset.season = String(s);
        btn.onclick = function () {
          var f = getFilters(); f.season = String(s); setFilters(f); renderAll();
        };
        el.appendChild(btn);
      });
    }

    function renderFormatTabs(games) {
      var tabsEl = document.getElementById("format-tabs");
      if (!tabsEl) return;
      var counts = {};
      games.forEach(function (game) {
        var fmt = getGameFormat(game.deckType);
        counts[fmt] = (counts[fmt] || 0) + 1;
      });
      var formats = ["standard", "modern", "legacy", "commander", "jumpstart"];
      var activeFormats = formats.filter(function (f) { return counts[f] > 0; });
      if (activeFormats.length <= 1) {
        tabsEl.style.display = "none";
        return;
      }
      tabsEl.innerHTML = "";
      var filters = getFilters();

      var allBtn = document.createElement("button");
      allBtn.className = "format-tab" + (!filters.format ? " active" : "");
      allBtn.textContent = "All";
      allBtn.onclick = function () {
        var f = getFilters(); f.format = ""; setFilters(f); renderAll();
      };
      tabsEl.appendChild(allBtn);

      activeFormats.forEach(function (fmt) {
        var btn = document.createElement("button");
        btn.className = "format-tab" + (filters.format === fmt ? " active" : "");
        btn.innerHTML = (FORMAT_DISPLAY[fmt] || fmt) + ' <span class="tab-count">(' + counts[fmt] + ")</span>";
        btn.onclick = function () {
          var f = getFilters(); f.format = fmt; setFilters(f); renderAll();
        };
        tabsEl.appendChild(btn);
      });
    }

    function renderModelTabs(games) {
      var tabsEl = document.getElementById("model-tabs");
      if (!tabsEl) return;
      var filters = getFilters();

      var counts = {};
      var tabMeta = {};
      games.forEach(function (game) {
        if (filters.format) {
          var fmt = getGameFormat(game.deckType);
          if (fmt !== filters.format) return;
        }
        var seen = {};
        (game.players || []).forEach(function (p) {
          if (!p.model) return;
          var key = p.model;
          var effort = p.reasoningEffort || "";
          if (effort) key += "::" + effort;
          if (!seen[key]) {
            seen[key] = true;
            counts[key] = (counts[key] || 0) + 1;
            if (!tabMeta[key]) tabMeta[key] = { model: p.model, effort: effort };
          }
        });
      });

      var keys = Object.keys(counts).sort(function (a, b) {
        return counts[b] - counts[a] || a.localeCompare(b);
      });

      if (keys.length === 0) {
        tabsEl.innerHTML = "";
        return;
      }

      tabsEl.innerHTML = "";
      var label = document.createElement("span");
      label.className = "model-tabs-label";
      label.textContent = "Model:";
      tabsEl.appendChild(label);

      keys.forEach(function (key) {
        var meta = tabMeta[key];
        var isActive = filters.model === meta.model && (filters.effort || "") === meta.effort;
        var btn = document.createElement("button");
        btn.className = "model-tab" + (isActive ? " active" : "");
        var displayName = meta.model;
        var slash = displayName.indexOf("/");
        if (slash !== -1) displayName = displayName.substring(slash + 1);
        if (meta.effort) displayName += " (" + meta.effort + ")";
        btn.innerHTML = displayName + ' <span class="tab-count">(' + counts[key] + ")</span>";
        btn.onclick = function () {
          var f = getFilters();
          if (f.model === meta.model && (f.effort || "") === meta.effort) {
            f.model = "";
            f.effort = "";
          } else {
            f.model = meta.model;
            f.effort = meta.effort;
          }
          setFilters(f);
          renderAll();
        };
        tabsEl.appendChild(btn);
      });
    }

    function renderAll() {
      var games = __games;
      var list = document.getElementById("games-list");
      list.innerHTML = "";

      if (!games || games.length === 0) {
        renderFilterBar({ model: "", format: "", effort: "", season: "" }, 0, 0);
        list.innerHTML = '<p class="no-games">No games exported yet.</p>';
        return;
      }

      if (showSeasonFilter) renderSeasonFilter(games);
      renderFormatTabs(games);
      renderModelTabs(games);
      var filters = getFilters();
      var filtered = filterGames(games, filters);
      renderFilterBar(filters, games.length, filtered.length);

      if (filtered.length === 0) {
        list.innerHTML = '<p class="no-games">No games match the current filters.</p>';
        return;
      }

      filtered.forEach(function (game) {
        list.appendChild(renderGameCard(game));
      });
    }

    fetch("/data/ratings.json").then(function (r) {
      return r.ok ? r.json() : {};
    }).catch(function () { return {}; })
      .then(function (data) {
        ratingsData = data;
        renderAll();
      })
      .catch(function () {
        var list = document.getElementById("games-list");
        list.innerHTML = '<p class="no-games">No games exported yet.</p>';
      });
  }
};
