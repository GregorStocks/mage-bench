/* Progressive enhancement for server-rendered game archive pages. */
window.GameList = {
  init: function (config) {
    var showSeasonFilter = !!(config && config.showSeasonFilter);
    var list = document.getElementById("games-list");
    if (!list) {
      throw new Error("Missing #games-list");
    }

    var seasonFilterEl = document.getElementById("season-filter");
    var formatTabsEl = document.getElementById("format-tabs");
    var modelTabsEl = document.getElementById("model-tabs");
    var filterBarEl = document.getElementById("filter-bar");
    var emptyMessageEl = document.getElementById("games-empty-message");
    var ratingsData = {};

    var FORMAT_DISPLAY = {
      "standard": "Standard",
      "modern": "Modern",
      "legacy": "Legacy",
      "commander": "Commander (Exhibition)",
      "jumpstart": "Jumpstart",
    };

    function clear(el) {
      el.textContent = "";
    }

    function setCountedLabel(el, labelText, count) {
      clear(el);
      el.appendChild(document.createTextNode(labelText + " "));
      var countSpan = document.createElement("span");
      countSpan.className = "tab-count";
      countSpan.textContent = "(" + count + ")";
      el.appendChild(countSpan);
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

    function readModelEntries(card) {
      var raw = card.dataset.modelEntries || "[]";
      var parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        throw new Error("data-model-entries must be a JSON array");
      }
      return parsed.map(function (entry) {
        if (!entry || typeof entry !== "object" || typeof entry.model !== "string") {
          throw new Error("Invalid model entry on game card");
        }
        return {
          model: entry.model,
          effort: typeof entry.effort === "string" ? entry.effort : "",
        };
      });
    }

    var cards = Array.from(list.querySelectorAll(".game-card")).map(function (card) {
      var gameId = card.dataset.gameId;
      var format = card.dataset.format;
      if (!gameId) {
        throw new Error("Game card missing data-game-id");
      }
      if (!format) {
        throw new Error("Game card missing data-format");
      }
      return {
        el: card,
        gameId: gameId,
        season: card.dataset.season || "0",
        format: format,
        modelEntries: readModelEntries(card),
      };
    });

    function renderFilterBar(filters, totalCount, filteredCount) {
      if (!filterBarEl) return;
      var hasFilters = filters.model || filters.format || filters.effort || filters.season;
      filterBarEl.hidden = !hasFilters;
      clear(filterBarEl);
      if (!hasFilters) {
        return;
      }

      var label = document.createElement("span");
      label.className = "filter-label";
      label.textContent = "Filtered:";
      filterBarEl.appendChild(label);

      if (filters.season) {
        var seasonChip = document.createElement("span");
        seasonChip.className = "filter-chip";
        seasonChip.textContent = "Season " + filters.season;
        var seasonBtn = document.createElement("button");
        seasonBtn.className = "filter-chip-remove";
        seasonBtn.textContent = "×";
        seasonBtn.onclick = function () {
          var next = getFilters();
          next.season = "";
          setFilters(next);
          renderAll();
        };
        seasonChip.appendChild(seasonBtn);
        filterBarEl.appendChild(seasonChip);
      }

      if (filters.format) {
        var formatChip = document.createElement("span");
        formatChip.className = "filter-chip";
        formatChip.textContent = FORMAT_DISPLAY[filters.format] || filters.format;
        var formatBtn = document.createElement("button");
        formatBtn.className = "filter-chip-remove";
        formatBtn.textContent = "×";
        formatBtn.onclick = function () {
          var next = getFilters();
          next.format = "";
          setFilters(next);
          renderAll();
        };
        formatChip.appendChild(formatBtn);
        filterBarEl.appendChild(formatChip);
      }

      if (filters.model) {
        var modelChip = document.createElement("span");
        modelChip.className = "filter-chip";
        var displayName = filters.model;
        var slash = displayName.indexOf("/");
        if (slash !== -1) displayName = displayName.substring(slash + 1);
        if (filters.effort) displayName += " (" + filters.effort + ")";
        modelChip.textContent = displayName;
        var modelBtn = document.createElement("button");
        modelBtn.className = "filter-chip-remove";
        modelBtn.textContent = "×";
        modelBtn.onclick = function () {
          var next = getFilters();
          next.model = "";
          next.effort = "";
          setFilters(next);
          renderAll();
        };
        modelChip.appendChild(modelBtn);
        filterBarEl.appendChild(modelChip);
      }

      var activeFilterCount = (filters.model ? 1 : 0) + (filters.format ? 1 : 0) + (filters.season ? 1 : 0);
      if (activeFilterCount > 1) {
        var clearBtn = document.createElement("button");
        clearBtn.className = "filter-clear";
        clearBtn.textContent = "Clear all";
        clearBtn.onclick = function () {
          setFilters({ model: "", format: "", effort: "", season: "" });
          renderAll();
        };
        filterBarEl.appendChild(clearBtn);
      }

      var count = document.createElement("span");
      count.className = "filter-count";
      count.textContent = filteredCount + " of " + totalCount + " games";
      filterBarEl.appendChild(count);
    }

    function renderSeasonFilter() {
      if (!showSeasonFilter || !seasonFilterEl) {
        return;
      }

      var counts = {};
      cards.forEach(function (card) {
        counts[card.season] = (counts[card.season] || 0) + 1;
      });
      var seasons = Object.keys(counts).map(Number).sort().reverse();
      seasonFilterEl.hidden = seasons.length <= 1;
      clear(seasonFilterEl);
      if (seasons.length <= 1) {
        return;
      }

      var filters = getFilters();

      var label = document.createElement("span");
      label.className = "season-filter-label";
      label.textContent = "Season:";
      seasonFilterEl.appendChild(label);

      var allBtn = document.createElement("button");
      allBtn.className = "season-filter-btn" + (!filters.season ? " active" : "");
      allBtn.textContent = "All";
      allBtn.onclick = function () {
        var next = getFilters();
        next.season = "";
        setFilters(next);
        renderAll();
      };
      seasonFilterEl.appendChild(allBtn);

      seasons.forEach(function (season) {
        var btn = document.createElement("button");
        btn.className = "season-filter-btn" + (filters.season === String(season) ? " active" : "");
        btn.dataset.season = String(season);
        setCountedLabel(btn, "Season " + season, counts[season]);
        btn.onclick = function () {
          var next = getFilters();
          next.season = String(season);
          setFilters(next);
          renderAll();
        };
        seasonFilterEl.appendChild(btn);
      });
    }

    function renderFormatTabs() {
      if (!formatTabsEl) return;

      var counts = {};
      cards.forEach(function (card) {
        counts[card.format] = (counts[card.format] || 0) + 1;
      });

      var formats = ["standard", "modern", "legacy", "commander", "jumpstart"];
      var activeFormats = formats.filter(function (format) { return counts[format] > 0; });
      formatTabsEl.hidden = activeFormats.length <= 1;
      clear(formatTabsEl);
      if (activeFormats.length <= 1) {
        return;
      }

      var filters = getFilters();

      var allBtn = document.createElement("button");
      allBtn.className = "format-tab" + (!filters.format ? " active" : "");
      allBtn.textContent = "All";
      allBtn.onclick = function () {
        var next = getFilters();
        next.format = "";
        setFilters(next);
        renderAll();
      };
      formatTabsEl.appendChild(allBtn);

      activeFormats.forEach(function (format) {
        var btn = document.createElement("button");
        btn.className = "format-tab" + (filters.format === format ? " active" : "");
        btn.dataset.format = format;
        setCountedLabel(btn, FORMAT_DISPLAY[format] || format, counts[format]);
        btn.onclick = function () {
          var next = getFilters();
          next.format = format;
          setFilters(next);
          renderAll();
        };
        formatTabsEl.appendChild(btn);
      });
    }

    function renderModelTabs() {
      if (!modelTabsEl) return;

      var filters = getFilters();
      var counts = {};
      var tabMeta = {};

      cards.forEach(function (card) {
        if (filters.format && card.format !== filters.format) {
          return;
        }
        var seen = {};
        card.modelEntries.forEach(function (entry) {
          var key = entry.model + "::" + entry.effort;
          if (seen[key]) return;
          seen[key] = true;
          counts[key] = (counts[key] || 0) + 1;
          if (!tabMeta[key]) {
            tabMeta[key] = entry;
          }
        });
      });

      var keys = Object.keys(counts).sort(function (a, b) {
        return counts[b] - counts[a] || a.localeCompare(b);
      });

      clear(modelTabsEl);
      modelTabsEl.hidden = keys.length === 0;
      if (keys.length === 0) {
        return;
      }

      var label = document.createElement("span");
      label.className = "model-tabs-label";
      label.textContent = "Model:";
      modelTabsEl.appendChild(label);

      keys.forEach(function (key) {
        var meta = tabMeta[key];
        var isActive = filters.model === meta.model && (filters.effort || "") === meta.effort;
        var btn = document.createElement("button");
        btn.className = "model-tab" + (isActive ? " active" : "");
        var displayName = meta.model;
        var slash = displayName.indexOf("/");
        if (slash !== -1) displayName = displayName.substring(slash + 1);
        if (meta.effort) displayName += " (" + meta.effort + ")";
        setCountedLabel(btn, displayName, counts[key]);
        btn.onclick = function () {
          var next = getFilters();
          if (next.model === meta.model && (next.effort || "") === meta.effort) {
            next.model = "";
            next.effort = "";
          } else {
            next.model = meta.model;
            next.effort = meta.effort;
          }
          setFilters(next);
          renderAll();
        };
        modelTabsEl.appendChild(btn);
      });
    }

    function matchesFilters(card, filters) {
      if (filters.season && card.season !== filters.season) {
        return false;
      }
      if (filters.format && card.format !== filters.format) {
        return false;
      }
      if (filters.model) {
        return card.modelEntries.some(function (entry) {
          return entry.model === filters.model && (!filters.effort || entry.effort === filters.effort);
        });
      }
      return true;
    }

    function applyRatings() {
      cards.forEach(function (card) {
        var gameRatings = ratingsData[card.gameId];
        if (!gameRatings || typeof gameRatings !== "object") {
          return;
        }

        card.el.querySelectorAll(".player-cell[data-rating-key]").forEach(function (cell) {
          if (cell.querySelector(".player-rating")) {
            return;
          }

          var ratingKey = cell.dataset.ratingKey;
          if (!ratingKey) {
            return;
          }

          var playerRating = gameRatings[ratingKey];
          if (!playerRating || typeof playerRating.before !== "number" || typeof playerRating.after !== "number") {
            return;
          }

          var ratingEl = document.createElement("div");
          ratingEl.className = "player-rating";
          var delta = playerRating.after - playerRating.before;
          if (delta > 0) ratingEl.classList.add("rating-up");
          else if (delta < 0) ratingEl.classList.add("rating-down");
          ratingEl.textContent = playerRating.before + " -> " + playerRating.after;
          cell.appendChild(ratingEl);
        });
      });
    }

    function renderAll() {
      renderSeasonFilter();
      renderFormatTabs();
      renderModelTabs();

      var filters = getFilters();
      var filteredCount = 0;
      cards.forEach(function (card) {
        var visible = matchesFilters(card, filters);
        card.el.hidden = !visible;
        if (visible) filteredCount += 1;
      });

      if (emptyMessageEl) {
        emptyMessageEl.hidden = filteredCount !== 0;
      }
      renderFilterBar(filters, cards.length, filteredCount);
    }

    renderAll();

    fetch("/data/ratings.json")
      .then(function (response) {
        return response.ok ? response.json() : {};
      })
      .catch(function () {
        return {};
      })
      .then(function (data) {
        ratingsData = data && typeof data === "object" ? data : {};
        applyRatings();
      });
  },
};
