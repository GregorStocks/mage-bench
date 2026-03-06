/**
 * game-viewer.js — reusable game viewer widget.
 *
 * Creates a full game viewer (transport controls, board with diffs,
 * action log with unified timeline) inside any container element.
 *
 * In the browser this attaches to window.GameViewer.
 * In Node/Vitest it is importable as a module.
 */
(function (root) {
  "use strict";

  var R = (typeof root !== "undefined" && root !== null) ? root.GameRenderer : null;
  if (!R && typeof require !== "undefined") {
    try { R = require("./game-renderer.js"); } catch (e) { /* ok in test */ }
  }

  // ── Helpers ──

  function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function formatToolArgs(args) {
    if (!args || typeof args !== "object") return "";
    var keys = Object.keys(args);
    if (keys.length === 0) return "";
    var parts = [];
    keys.forEach(function (k) {
      var v = args[k];
      if (typeof v === "string" && v.length > 40) v = v.substring(0, 40) + "...";
      else if (typeof v === "object") v = JSON.stringify(v).substring(0, 40) + (JSON.stringify(v).length > 40 ? "..." : "");
      parts.push(k + "=" + v);
    });
    return parts.join(", ");
  }

  function tryFormatJson(str) {
    if (!str || typeof str !== "string") return str || "";
    try {
      return JSON.stringify(JSON.parse(str), null, 2);
    } catch (e) {
      return str;
    }
  }

  function formatTimestamp(ts) {
    if (!ts) return null;
    var match = ts.match(/T(\d{2}:\d{2}:\d{2})(\.\d+)?/);
    if (!match) return null;
    return { display: match[1], full: match[1] + (match[2] || "") };
  }

  function makeTimestampEl(ts) {
    var parsed = formatTimestamp(ts);
    if (!parsed) return null;
    var span = document.createElement("span");
    span.className = "entry-ts";
    span.textContent = parsed.display;
    span.title = parsed.full;
    return span;
  }

  // ── Decision display ──

  /**
   * Convert a canonical decision into human-readable text describing what
   * the player chose.  Pure function — no DOM, no closure state.
   *
   * @param {object} decision  A Decision from game.decisions[]
   * @returns {string}
   */
  function chosenDisplayText(decision) {
    var chosen = decision.chosen;
    var chosenArgs = decision.chosenArgs || {};
    var choices = decision.choices || [];
    var message = decision.message || "";
    var pilotCtx = decision.pilotContext || {};

    // Build ID → choice lookup from choices + incomingAttackers
    var choiceById = {};
    choices.forEach(function (c) {
      if (c && typeof c === "object" && c.id) {
        choiceById[c.id] = c;
      }
    });
    (pilotCtx.incomingAttackers || []).forEach(function (a) {
      if (a && a.id && !choiceById[a.id]) {
        choiceById[a.id] = a;
      }
    });

    function nameOf(id) {
      var c = choiceById[id];
      return c ? (c.name || c.description || id) : id;
    }

    function nameWithStats(id) {
      var c = choiceById[id];
      if (!c) return id;
      var n = c.name || c.description || id;
      if (c.power != null && c.toughness != null) {
        n += " " + c.power + "/" + c.toughness;
      }
      return n;
    }

    // Batch attacks: chosen is null, chosenArgs.attackers exists
    if (chosenArgs.attackers) {
      var attackers = chosenArgs.attackers;
      if (typeof attackers === "string") {
        attackers = attackers.split(",").map(function (s) { return s.trim(); });
      }
      if (attackers.length === 1 && attackers[0] === "all") {
        var allNames = choices.filter(function (c) {
          return c && typeof c === "object" && c.id !== "all";
        }).map(function (c) {
          return nameWithStats(c.id);
        });
        return allNames.length > 0
          ? "Attack with all (" + allNames.join(", ") + ")"
          : "Attack with all creatures";
      }
      var atkNames = attackers.map(function (a) {
        var id = (typeof a === "object" && a.id) ? a.id : String(a);
        return nameWithStats(id);
      });
      return "Attack with " + atkNames.join(", ");
    }

    // Batch blocks: chosen is null, chosenArgs.blockers exists
    if (chosenArgs.blockers) {
      var blockers = chosenArgs.blockers;
      if (typeof blockers === "string") {
        try { blockers = JSON.parse(blockers); } catch (e) {
          blockers = blockers.split(",").map(function (s) { return s.trim(); });
        }
      }
      if (!blockers || blockers.length === 0) return "No blocks";
      var blockParts = [];
      blockers.forEach(function (entry) {
        if (typeof entry === "object" && entry.id) {
          blockParts.push(nameOf(entry.id) + " blocks " + nameOf(entry.blocks));
        } else if (typeof entry === "string" && entry.indexOf(":") !== -1) {
          var pair = entry.split(":", 2);
          blockParts.push(nameOf(pair[0]) + " blocks " + nameOf(pair[1]));
        } else {
          blockParts.push(nameOf(String(entry)));
        }
      });
      return blockParts.join(", ");
    }

    // Boolean response
    if (typeof chosen === "boolean") {
      var msgLower = message.toLowerCase();
      if (msgLower.indexOf("mulligan") !== -1) {
        return chosen ? "Mulligan" : "Keep hand";
      }
      if (!chosen) {
        if (msgLower.indexOf("blocker") !== -1) return "No blocks";
        return "Pass";
      }
      return String(chosen);
    }

    // Null chosen with a choice ID → resolve it
    if (chosen == null && chosenArgs.choice && chosenArgs.choice !== "no") {
      var resolved = choiceById[chosenArgs.choice];
      if (resolved) {
        var rName = resolved.name || resolved.description || chosenArgs.choice;
        var rAction = resolved.action;
        if (rAction === "cast") {
          var lbl = "Cast " + rName;
          if (resolved.mana_cost) lbl += " " + resolved.mana_cost;
          return lbl;
        }
        if (rAction === "land") return "Play " + rName;
        if (rAction === "activate") return "Activate " + rName;
        return rName;
      }
      return chosenArgs.choice;
    }

    // Null chosen, empty args = pass
    if (chosen == null) {
      return "Pass";
    }

    // Index into choices
    if (typeof chosen === "number" && chosen >= 0 && chosen < choices.length) {
      var c = choices[chosen];
      if (c && typeof c === "object") {
        var choiceName = c.name || c.description || String(chosen);
        var action = c.action;
        if (action === "cast") {
          var label = "Cast " + choiceName;
          if (c.mana_cost) label += " " + c.mana_cost;
          return label;
        }
        if (action === "land") return "Play " + choiceName;
        if (action === "activate") return "Activate " + choiceName;
        return choiceName;
      }
      return String(c);
    }

    return String(chosen);
  }

  // ── LLM event processing ──

  function extractSystemMessages(toolCallEvent) {
    if (!toolCallEvent.result) return [];
    try {
      var r = JSON.parse(toolCallEvent.result);
      var chat = r.recent_chat || [];
      var msgs = [];
      chat.forEach(function (msg) {
        if (typeof msg === "string" && msg.indexOf("[System]") !== -1) {
          msgs.push(msg.replace("[System] ", ""));
        }
      });
      return msgs;
    } catch (ex) {
      return [];
    }
  }

  function mergeLlmEvents(events) {
    var merged = [];
    var i = 0;
    while (i < events.length) {
      var e = events[i];
      if (e.type === "llm_response") {
        var toolResults = [];
        var j = i + 1;
        while (j < events.length && events[j].type === "tool_call" && events[j].player === e.player) {
          toolResults.push(events[j]);
          j++;
        }
        var mergedSeq = e.gameSeq || (toolResults.length > 0 ? toolResults[0].gameSeq : 0) || 0;
        merged.push({
          type: "llm_merged",
          ts: e.ts,
          gameSeq: mergedSeq,
          player: e.player,
          reasoning: e.reasoning,
          thinking: e.thinking,
          toolCalls: e.toolCalls,
          costUsd: e.costUsd,
          toolResults: toolResults,
        });
        i = j;
      } else if (e.type === "tool_call") {
        merged.push({
          type: "llm_merged",
          ts: e.ts,
          gameSeq: e.gameSeq || 0,
          player: e.player,
          toolResults: [e],
        });
        i++;
      } else {
        merged.push(e);
        i++;
      }
    }
    return merged;
  }

  // ── DOM construction ──

  function buildDOM(container) {
    container.innerHTML = "";

    var html = [
      '<div id="transport">',
      '  <div class="transport-buttons">',
      '    <button id="btn-prev" title="Previous (Left arrow)">&lt;</button>',
      '    <button id="btn-auto" title="Auto-play">Play</button>',
      '    <button id="btn-next" title="Next (Right arrow)">&gt;</button>',
      '  </div>',
      '  <div id="slider-container">',
      '    <input type="range" id="slider" min="0" max="0" value="0" />',
      '  </div>',
      '  <div class="snapshot-position">',
      '    <input type="number" id="snapshot-jump" min="1" max="1" value="1" title="Jump to snapshot #" />',
      '    <span class="snap-divider">/</span>',
      '    <span class="snap-total">1</span>',
      '  </div>',
      '</div>',
      '<div id="game-content">',
      '  <div id="game-left">',
      '    <div id="stack-section">',
      '      <div class="section-title">Stack</div>',
      '      <div id="stack-cards" class="cards-row"></div>',
      '    </div>',
      '    <div id="players-grid"></div>',
      '  </div>',
      '  <div id="game-right">',
      '    <div id="action-log">',
      '      <div class="action-log-header">',
      '        <span class="section-title">Game Log</span>',
      '        <div class="log-filters">',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-llm" checked /> LLM</label>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-game" checked /> Game</label>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-chat" checked /> Chat</label>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-annotations" checked /> Blunders</label>',
      '        </div>',
      '      </div>',
      '      <div id="action-list"></div>',
      '    </div>',
      '  </div>',
      '</div>',
      '<div id="card-preview" class="hidden">',
      '  <img id="preview-image" alt="" />',
      '  <div class="card-meta">',
      '    <div class="card-name-row">',
      '      <div id="preview-name" class="card-name"></div>',
      '      <div id="preview-cost" class="card-cost"></div>',
      '    </div>',
      '    <div id="preview-type" class="card-type"></div>',
      '    <pre id="preview-rules" class="card-rules"></pre>',
      '    <div id="preview-stats" class="card-stats"></div>',
      '  </div>',
      '</div>',
    ].join("\n");

    container.innerHTML = html;

    return {
      // Transport
      btnPrev: container.querySelector("#btn-prev"),
      btnNext: container.querySelector("#btn-next"),
      btnAuto: container.querySelector("#btn-auto"),
      slider: container.querySelector("#slider"),
      snapshotJump: container.querySelector("#snapshot-jump"),
      snapTotal: container.querySelector(".snap-total"),
      // Display
      playersGrid: container.querySelector("#players-grid"),
      stackSection: container.querySelector("#stack-section"),
      stackCards: container.querySelector("#stack-cards"),
      actionList: container.querySelector("#action-list"),
      gameLeft: container.querySelector("#game-left"),
      // Preview
      previewEls: {
        container: container.querySelector("#card-preview"),
        image: container.querySelector("#preview-image"),
        name: container.querySelector("#preview-name"),
        cost: container.querySelector("#preview-cost"),
        type: container.querySelector("#preview-type"),
        stats: container.querySelector("#preview-stats"),
        rules: container.querySelector("#preview-rules"),
      },
      // Filters
      filterLlmEl: container.querySelector("#filter-llm"),
      filterGameEl: container.querySelector("#filter-game"),
      filterChatEl: container.querySelector("#filter-chat"),
      filterAnnotationsEl: container.querySelector("#filter-annotations"),
      sliderContainer: container.querySelector("#slider-container"),
    };
  }

  // ── Viewer creation ──

  function create(container, game, options) {
    options = options || {};
    var initialSnapshot = options.initialSnapshot || 0;
    var onSnapshotChange = options.onSnapshotChange || null;

    // Build DOM
    var dom = buildDOM(container);

    // State
    var currentIndex = 0;
    var autoPlayInterval = null;
    var playerColorMap = {};
    var playerMeta = {};
    var turnStartIndices = [];
    var phaseTransitions = [];
    var playerTurnNumbers = [];
    var isCommander = false;
    var filterLlm = true;
    var filterGame = true;
    var filterChat = true;
    var filterAnnotations = true;
    var useSeq = false;

    // ── Rendering helpers ──

    function colorizePlayerNames(message) {
      var escaped = escapeHtml(message);
      var names = Object.keys(playerColorMap);
      names.sort(function (a, b) { return b.length - a.length; });
      names.forEach(function (name) {
        var cls = "action-" + R.PLAYER_COLORS[playerColorMap[name]];
        var escapedName = escapeHtml(name);
        escaped = escaped.split(escapedName).join('<span class="' + cls + '">' + escapedName + '</span>');
      });
      return escaped;
    }

    function playerColorClass(playerName) {
      var idx = playerColorMap[playerName];
      return idx != null ? "llm-player-" + idx : "";
    }

    function playerSpan(playerName) {
      var idx = playerColorMap[playerName];
      var cls = idx != null ? "action-" + R.PLAYER_COLORS[idx] : "";
      return '<span class="llm-player ' + cls + '">' + escapeHtml(playerName) + '</span>';
    }

    function renderToolResult(tc) {
      // Look up whether this tool call maps to a canonical decision
      var decision = (tc._origIdx != null) ? llmEventIndexToDecision[tc._origIdx] || null : null;

      // choose_action mapped to a decision: show human-readable summary
      if (decision && tc.tool === "choose_action") {
        var displayText = chosenDisplayText(decision);

        var container = document.createElement("span");
        container.className = "llm-decision-display";

        var badge = document.createElement("span");
        badge.className = "log-badge badge-mcp";
        badge.textContent = "mcp";
        container.appendChild(badge);

        var actionSpan = document.createElement("span");
        actionSpan.className = "decision-action-text";
        actionSpan.textContent = displayText;
        container.appendChild(actionSpan);

        // Raw MCP call hidden behind an inline disclosure triangle
        var rawDetails = document.createElement("details");
        rawDetails.className = "llm-tool-raw";
        var rawSummary = document.createElement("summary");
        rawSummary.textContent = "raw";
        rawDetails.appendChild(rawSummary);
        var rawInner = document.createElement("div");
        var rawArgs = formatToolArgs(tc.args);
        rawInner.innerHTML = '<div class="llm-tool-raw-call">' + escapeHtml(tc.tool) + "(" + escapeHtml(rawArgs) + ")</div>";
        if (tc.result) {
          var rawPre = document.createElement("pre");
          rawPre.textContent = tryFormatJson(tc.result);
          rawInner.appendChild(rawPre);
        }
        rawDetails.appendChild(rawInner);
        container.appendChild(rawDetails);

        return container;
      }

      // get_action_choices mapped to a decision: hide (choose_action shows the summary)
      if (decision && tc.tool === "get_action_choices") {
        return null;
      }

      // send_chat_message: show mcp badge + message text, raw call behind disclosure
      if (tc.tool === "send_chat_message") {
        var chatContainer = document.createElement("span");
        chatContainer.className = "llm-decision-display";

        var chatBadge = document.createElement("span");
        chatBadge.className = "log-badge badge-mcp";
        chatBadge.textContent = "mcp";
        chatContainer.appendChild(chatBadge);

        var chatText = document.createElement("span");
        chatText.className = "decision-action-text";
        chatText.textContent = "send_chat_message";
        chatContainer.appendChild(chatText);

        var chatRaw = document.createElement("details");
        chatRaw.className = "llm-tool-raw";
        var chatRawSummary = document.createElement("summary");
        chatRawSummary.textContent = "raw";
        chatRaw.appendChild(chatRawSummary);
        var chatRawInner = document.createElement("div");
        var chatRawArgs = formatToolArgs(tc.args);
        chatRawInner.innerHTML = '<div class="llm-tool-raw-call">' + escapeHtml(tc.tool) + "(" + escapeHtml(chatRawArgs) + ")</div>";
        if (tc.result) {
          var chatRawPre = document.createElement("pre");
          chatRawPre.textContent = tryFormatJson(tc.result);
          chatRawInner.appendChild(chatRawPre);
        }
        chatRaw.appendChild(chatRawInner);
        chatContainer.appendChild(chatRaw);

        return chatContainer;
      }

      // Default: original rendering for unmapped tool calls
      var wrapper = document.createElement("span");
      wrapper.className = "llm-tool-default";
      var llmBadge = document.createElement("span");
      llmBadge.className = "log-badge badge-llm";
      llmBadge.textContent = "llm";
      wrapper.appendChild(llmBadge);
      var details = document.createElement("details");
      details.className = "llm-tool-detail";
      var summary = document.createElement("summary");
      var argsSummary = formatToolArgs(tc.args);
      summary.innerHTML = escapeHtml(tc.tool) + "(" + escapeHtml(argsSummary) + ")";
      details.appendChild(summary);
      if (tc.result) {
        var pre = document.createElement("pre");
        pre.textContent = tryFormatJson(tc.result);
        details.appendChild(pre);
      }
      wrapper.appendChild(details);
      return wrapper;
    }

    function renderLlmEvent(event) {
      var type = event.type;

      if (type === "llm_merged") {
        var hasReasoning = event.reasoning && event.reasoning.trim();
        var hasThinking = event.thinking && event.thinking.trim();
        var hasToolResults = event.toolResults && event.toolResults.length > 0;

        if (hasReasoning || hasThinking) {
          var div = document.createElement("div");
          div.className = "llm-event llm-thought " + playerColorClass(event.player);

          var header = '<span class="thinking-badge">thinking</span>' + playerSpan(event.player);
          var headerEl = document.createElement("div");
          headerEl.innerHTML = header;
          div.appendChild(headerEl);

          if (hasThinking) {
            var thinkDetails = document.createElement("details");
            thinkDetails.className = "llm-thinking-block";
            var thinkSummary = document.createElement("summary");
            thinkSummary.textContent = "Thinking (" + event.thinking.length + " chars)";
            thinkDetails.appendChild(thinkSummary);
            var thinkPre = document.createElement("pre");
            thinkPre.className = "llm-thinking-text";
            thinkPre.textContent = event.thinking;
            thinkDetails.appendChild(thinkPre);
            div.appendChild(thinkDetails);
          }

          if (hasReasoning) {
            var reasoningEl = document.createElement("div");
            reasoningEl.className = "llm-reasoning";
            reasoningEl.textContent = event.reasoning;
            div.appendChild(reasoningEl);
          }

          if (hasToolResults) {
            event.toolResults.forEach(function (tc) {
              var el = renderToolResult(tc); if (el) div.appendChild(el);
            });
          }

          return div;
        } else if (hasToolResults) {
          var div = document.createElement("div");
          div.className = "llm-event llm-compact";
          var headerHtml = playerSpan(event.player);
          var headerEl = document.createElement("span");
          headerEl.innerHTML = headerHtml;
          div.appendChild(headerEl);
          var hasVisible = false;
          event.toolResults.forEach(function (tc) {
            var el = renderToolResult(tc); if (el) { div.appendChild(el); hasVisible = true; }
          });
          if (!hasVisible) return null;
          return div;
        }
        return null;
      }

      if (type === "context_trim") return null;

      if (type === "system_message") {
        var div = document.createElement("div");
        div.className = "llm-event llm-system-message";
        div.innerHTML = '<span class="log-badge badge-llm">llm</span>' + playerSpan(event.player) + ' <span class="system-message-text">' + escapeHtml(event.message) + '</span>';
        return div;
      }

      var div = document.createElement("div");
      div.className = "llm-event llm-meta";
      var metaText = "";

      if (type === "stall") {
        metaText = event.player + " stalled (" + (event.turnsWithoutProgress || 0) + " turns without progress)";
      } else if (type === "llm_error") {
        metaText = event.player + " error: " + (event.errorType || "") + " " + (event.errorMessage || "");
      } else if (type === "context_reset") {
        metaText = event.player + " context reset: " + (event.reason || "");
      } else if (type === "auto_pilot_mode") {
        metaText = event.player + " switched to auto-pilot: " + (event.reason || "");
      } else {
        metaText = event.player + " " + type;
      }

      div.innerHTML = '<span class="log-badge badge-llm">llm</span>' + escapeHtml(metaText);
      return div;
    }

    function renderAnnotation(ann) {
      var div = document.createElement("div");
      div.className = "annotation-block severity-" + ann.severity;

      var header = document.createElement("div");
      header.className = "annotation-header";
      var badge = document.createElement("span");
      badge.className = "annotation-badge severity-" + ann.severity;
      badge.textContent = ann.severity === "questionable" ? "questionable" : ann.severity + " blunder";
      header.appendChild(badge);
      div.appendChild(header);

      var desc = document.createElement("div");
      desc.className = "annotation-description";
      desc.textContent = ann.description;
      div.appendChild(desc);

      var details = document.createElement("details");
      details.className = "annotation-details";
      var summary = document.createElement("summary");
      summary.textContent = "Analysis";
      details.appendChild(summary);
      var content = document.createElement("div");
      var fieldsHtml =
        '<div class="annotation-field"><strong>Action taken:</strong> ' + escapeHtml(ann.actionTaken) + '</div>' +
        '<div class="annotation-field"><strong>Better line:</strong> ' + escapeHtml(ann.betterLine) + '</div>';
      if (ann.llmReasoning) {
        fieldsHtml += '<div class="annotation-field"><strong>Why the LLM erred:</strong> ' + escapeHtml(ann.llmReasoning) + '</div>';
      }
      content.innerHTML = fieldsHtml;
      details.appendChild(content);
      div.appendChild(details);

      return div;
    }

    function renderAnnotationMarkers(annotations, totalSnapshots) {
      var existing = dom.sliderContainer.querySelector(".annotation-markers");
      if (existing) existing.remove();

      if (!annotations || annotations.length === 0) return;

      var markers = document.createElement("div");
      markers.className = "annotation-markers";
      annotations.forEach(function (ann, annIdx) {
        var dot = document.createElement("div");
        dot.className = "annotation-marker severity-" + ann.severity;
        var decisionSnapIdx = annotationDecisionSnap[annIdx];
        var pct = totalSnapshots > 1 ? (decisionSnapIdx / (totalSnapshots - 1)) * 100 : 50;
        dot.style.left = pct + "%";
        dot.title = ann.player + " (" + ann.severity + "): " + ann.description.substring(0, 80);
        dot.addEventListener("click", function () { goTo(decisionSnapIdx); });
        markers.appendChild(dot);
      });
      dom.sliderContainer.appendChild(markers);
    }

    function renderTurnMarkers(turnStarts, totalSnapshots) {
      var existing = dom.sliderContainer.querySelector(".turn-markers");
      if (existing) existing.remove();

      if (!turnStarts || turnStarts.length <= 1 || totalSnapshots <= 1) return;

      var container = document.createElement("div");
      container.className = "turn-markers";
      // Skip the first turn (index 0) — no need for a marker at the very start
      for (var i = 1; i < turnStarts.length; i++) {
        var tick = document.createElement("div");
        tick.className = "turn-marker";
        var pct = (turnStarts[i].index / (totalSnapshots - 1)) * 100;
        tick.style.left = pct + "%";
        container.appendChild(tick);
      }
      dom.sliderContainer.appendChild(container);
    }

    // ── Core rendering ──

    function renderSnapshot(index) {
      var snap = game.snapshots[index];
      if (!snap) return;

      // Compute diff against previous snapshot
      var prevSnap = index > 0 ? game.snapshots[index - 1] : null;
      var diffs = R.computeDiff(prevSnap, snap);

      // Compute running cost up to current snapshot
      if (game.llmEvents && game.llmEvents.length > 0) {
        var costNextSnap = index < game.snapshots.length - 1 ? game.snapshots[index + 1] : null;
        var costCutoffTs = costNextSnap ? (costNextSnap.ts || "") : "";
        var runningCost = {};
        game.llmEvents.forEach(function (e) {
          if (costCutoffTs && e.ts >= costCutoffTs) return;
          if (e.costUsd && e.player) {
            runningCost[e.player] = (runningCost[e.player] || 0) + e.costUsd;
          }
        });
        (game.players || []).forEach(function (p) {
          if (playerMeta[p.name]) {
            playerMeta[p.name].totalCostUsd = runningCost[p.name] || 0;
          }
        });
      }

      // Players
      R.renderPlayers(dom.playersGrid, snap.players, {
        cardImages: game.cardImages,
        playerColorMap: playerColorMap,
        playerMeta: playerMeta,
        diffs: diffs,
        previewEls: dom.previewEls,
        priorityPlayerName: snap.priority_player,
        isCommander: isCommander,
      });

      // Phase bar between player battlefields
      if (dom.playersGrid.children.length >= 2) {
        var insertIdx = dom.playersGrid.children.length <= 2 ? 1 : 2;
        var phaseBar = document.createElement("div");
        phaseBar.id = "phase-bar";
        var phase = snap.phase || "";
        var step = snap.step || "";
        var phaseDisplay = step && step !== phase ? phase + " / " + step : phase;
        phaseBar.textContent = R.formatTurnLabel(playerTurnNumbers[index], snap.active_player) + " \u2014 " + phaseDisplay;
        dom.playersGrid.insertBefore(phaseBar, dom.playersGrid.children[insertIdx]);
      }

      // Stack
      R.renderStack(dom.stackSection, dom.stackCards, snap.stack, game.cardImages, dom.previewEls);

      // Pending decisions for this snapshot
      R.renderDecisions(dom.stackSection, snapshotDecisionMap[index] || [], playerColorMap);

      // Target arrows from stack items to their targets
      R.drawTargetArrows(dom.gameLeft);

      // Action log: show full accumulated log up to current snapshot
      dom.actionList.innerHTML = "";
      var prevSeq = index > 0 ? game.snapshots[index - 1].seq : 0;
      var curSeq = snap.seq;

      var SPAM_RE = / skip attack$|^Attacker: .+ unblocked$/;

      var allActions = game.actions.filter(function (a) {
        if (a.seq > curSeq) return false;
        if (!a.message && a.type !== "chat") return false;
        if (a.type !== "chat" && SPAM_RE.test(a.message)) return false;
        return true;
      });

      // Extract chat messages from llmEvents
      var chatFromLlm = [];
      if (game.llmEvents) {
        var chatNextSnap = index < game.snapshots.length - 1 ? game.snapshots[index + 1] : null;
        var chatNextSeq = chatNextSnap ? (chatNextSnap.seq || Infinity) : Infinity;
        var chatNextTs = chatNextSnap ? (chatNextSnap.ts || "") : "";
        game.llmEvents.forEach(function (e) {
          if (e.type !== "tool_call" || e.tool !== "send_chat_message") return;
          if (useSeq) {
            if ((e.gameSeq || 0) >= chatNextSeq) return;
          } else {
            if (chatNextTs && e.ts >= chatNextTs) return;
          }
          chatFromLlm.push({
            ts: e.ts || "",
            from: e.player,
            message: (e.args && e.args.message) || "",
            gameSeq: e.gameSeq || 0,
          });
        });
      }

      // All LLM events up to current snapshot
      var hasLlmEvents = game.llmEvents && game.llmEvents.length > 0;
      var relevantLlm = [];
      if (hasLlmEvents) {
        var nextSnap = index < game.snapshots.length - 1 ? game.snapshots[index + 1] : null;
        var nextTs = nextSnap ? (nextSnap.ts || "") : "";
        var nextSeq = nextSnap ? (nextSnap.seq || Infinity) : Infinity;

        // Pre-pass: extract system messages and backdate
        var systemMessages = [];
        var lastPlayerTs = {};
        var lastPlayerSeq = {};
        game.llmEvents.forEach(function (e) {
          if (e.type === "tool_call") {
            var sysmsgs = extractSystemMessages(e);
            if (sysmsgs.length > 0) {
              var backdatedTs = lastPlayerTs[e.player] || e.ts;
              var backdatedSeq = lastPlayerSeq[e.player] || e.gameSeq || 0;
              sysmsgs.forEach(function (msg) {
                systemMessages.push({ type: "system_message", ts: backdatedTs, gameSeq: backdatedSeq, player: e.player, message: msg });
              });
            }
            lastPlayerTs[e.player] = e.ts;
            lastPlayerSeq[e.player] = e.gameSeq || 0;
          }
        });

        relevantLlm = game.llmEvents.filter(function (e) {
          if (useSeq) {
            if ((e.gameSeq || 0) >= nextSeq) return false;
          } else {
            if (nextTs && e.ts >= nextTs) return false;
          }
          return true;
        });

        systemMessages.forEach(function (sm) {
          if (useSeq) {
            if ((sm.gameSeq || 0) >= nextSeq) return;
          } else {
            if (nextTs && sm.ts >= nextTs) return;
          }
          relevantLlm.push(sm);
        });
      }

      var mergedLlm = mergeLlmEvents(relevantLlm);

      // Build unified timeline
      var timeline = [];
      var showGameActions = filterGame;
      var showChatMessages = filterChat;
      var showLlmEvents = filterLlm;

      function itemSeq(item) {
        if (item.kind === "action" || item.kind === "chat") return item.data.seq || item.data.gameSeq || 0;
        if (item.kind === "llm") return item.data.gameSeq || item.data.seq || 0;
        if (item.kind === "turn-sep" || item.kind === "phase-sep") return item.data.seq || 0;
        if (item.kind === "annotation") {
          if (item.seq != null) return item.seq;
          var annSnap = game.snapshots[item.data.snapshotIndex];
          return annSnap ? (annSnap.seq || 0) : 0;
        }
        return 0;
      }

      allActions.forEach(function (a) {
        var isChat = a.type === "chat";
        if (isChat && showChatMessages) {
          timeline.push({ kind: "chat", ts: a.ts || "", data: a });
        } else if (!isChat && showGameActions) {
          timeline.push({ kind: "action", ts: a.ts || "", data: a });
        }
      });
      if (showLlmEvents) {
        mergedLlm.forEach(function (e) {
          timeline.push({ kind: "llm", ts: e.ts || "", data: e });
        });
      }
      if (showChatMessages && chatFromLlm.length > 0) {
        var chatDedup = {};
        allActions.forEach(function (a) {
          if (a.type === "chat") {
            var div = document.createElement("div");
            div.innerHTML = a.message || "";
            var decoded = div.textContent || div.innerText || "";
            chatDedup[a.from + "|" + decoded] = true;
          }
        });
        chatFromLlm.forEach(function (c) {
          if (!chatDedup[c.from + "|" + c.message]) {
            timeline.push({ kind: "chat", ts: c.ts, data: c });
          }
        });
      }

      function timelineSort(a, b) {
        if (useSeq) {
          var aSeq = itemSeq(a);
          var bSeq = itemSeq(b);
          if (aSeq !== bSeq) return aSeq - bSeq;
        } else {
          if (a.ts < b.ts) return -1;
          if (a.ts > b.ts) return 1;
        }
        var order = { "turn-sep": -2, "phase-sep": -1, "annotation": 1 };
        var aOrd = order[a.kind] || 0;
        var bOrd = order[b.kind] || 0;
        return aOrd - bOrd;
      }

      timeline.sort(timelineSort);

      // Add annotations
      var showAnnotationsFlag = filterAnnotations;
      if (showAnnotationsFlag && game.annotations) {
        game.annotations.forEach(function (ann, annIdx) {
          // Show annotation at the pre-decision snapshot so it appears
          // alongside the decision and the board state the player saw.
          var decisionSnapIdx = annotationDecisionSnap[annIdx];
          if (decisionSnapIdx <= index) {
            var decSnap = game.snapshots[decisionSnapIdx] || {};
            if (useSeq) {
              timeline.push({ kind: "annotation", seq: decSnap.seq || 0, data: ann });
            } else {
              timeline.push({ kind: "annotation", ts: decSnap.ts || "", data: ann });
            }
          }
        });
        timeline.sort(timelineSort);
      }

      // Add phase/step transition separators
      if (filterGame) {
        phaseTransitions.forEach(function (pt) {
          if (pt.index <= index) {
            timeline.push({
              kind: pt.turnChanged ? "turn-sep" : "phase-sep",
              ts: pt.ts,
              data: pt,
            });
          }
        });
        timeline.sort(timelineSort);
      }

      // Render interleaved timeline
      timeline.forEach(function (item) {
        var el = null;
        if (item.kind === "chat") {
          var a = item.data;
          el = document.createElement("div");
          el.className = "chat-line";
          var fromIdx = playerColorMap[a.from];
          var fromCls = fromIdx != null ? "action-" + R.PLAYER_COLORS[fromIdx] : "";
          el.innerHTML = '<span class="chat-badge">chat</span><span class="chat-from ' + fromCls + '">' + escapeHtml(a.from || "") + ':</span> ' + escapeHtml(a.message || "");
        } else if (item.kind === "action") {
          var a = item.data;
          el = document.createElement("div");
          el.className = "action-line";
          if (a.seq > prevSeq) {
            el.style.color = "#e0e0f0";
          }
          el.innerHTML = '<span class="log-badge badge-game">game</span>' + colorizePlayerNames(a.message);
        } else if (item.kind === "llm") {
          el = renderLlmEvent(item.data);
        } else if (item.kind === "annotation") {
          el = renderAnnotation(item.data);
        } else if (item.kind === "turn-sep") {
          el = document.createElement("div");
          el.className = "turn-separator";
          el.textContent = "\u2014 " + R.formatTurnLabel(item.data.playerTurn, item.data.active_player) + " \u2014";
        } else if (item.kind === "phase-sep") {
          el = document.createElement("div");
          el.className = "phase-separator";
          el.textContent = "\u2014 " + R.formatPhaseStep(item.data.phase, item.data.step) + " \u2014";
        }
        if (el) {
          dom.actionList.appendChild(el);
        }
      });

      // Show winner at end of game
      if (game.winner && index === game.snapshots.length - 1) {
        var winLine = document.createElement("div");
        winLine.className = "game-result-line";
        var winIdx = playerColorMap[game.winner];
        var winCls = winIdx != null ? "action-" + R.PLAYER_COLORS[winIdx] : "";
        winLine.innerHTML = '<span class="' + winCls + '">' + escapeHtml(game.winner) + '</span> wins the game!';
        dom.actionList.appendChild(winLine);
      }

      // Show timeout losses at end of game
      if (index === game.snapshots.length - 1) {
        (game.players || []).forEach(function (p) {
          if (!p.timedOut) return;
          var toLine = document.createElement("div");
          toLine.className = "game-result-line game-result-timeout";
          var pIdx = playerColorMap[p.name];
          var pCls = pIdx != null ? "action-" + R.PLAYER_COLORS[pIdx] : "";
          toLine.innerHTML = '<span class="' + pCls + '">' + escapeHtml(p.name) + '</span> ran out of time';
          dom.actionList.appendChild(toLine);
        });
      }

      // Sync game log height with left column, then auto-scroll to bottom
      setTimeout(function () {
        var leftHeight = dom.gameLeft.offsetHeight;
        var headerHeight = dom.actionList.parentElement.offsetHeight - dom.actionList.offsetHeight;
        var targetHeight = Math.max(400, leftHeight - headerHeight);
        dom.actionList.style.maxHeight = targetHeight + "px";
        dom.actionList.style.minHeight = targetHeight + "px";
        dom.actionList.scrollTop = dom.actionList.scrollHeight;
      }, 50);

      // Update transport
      dom.slider.value = String(index);
      dom.snapshotJump.value = String(index + 1);
      dom.snapTotal.textContent = String(game.snapshots.length);
    }

    // ── Navigation ──

    function goTo(index) {
      currentIndex = Math.max(0, Math.min(index, game.snapshots.length - 1));
      renderSnapshot(currentIndex);
      if (onSnapshotChange) onSnapshotChange(currentIndex);
    }

    function toggleAutoPlay() {
      if (autoPlayInterval) {
        clearInterval(autoPlayInterval);
        autoPlayInterval = null;
        dom.btnAuto.textContent = "Play";
        dom.btnAuto.classList.remove("active");
      } else {
        dom.btnAuto.textContent = "Pause";
        dom.btnAuto.classList.add("active");
        autoPlayInterval = setInterval(function () {
          if (currentIndex >= game.snapshots.length - 1) {
            toggleAutoPlay();
            return;
          }
          goTo(currentIndex + 1);
        }, 500);
      }
    }

    // ── Initialise game data ──

    // v2 games have seq on everything but no ts on actions/snapshots
    useSeq = game.version != null;

    // Preload baked Scryfall card data (v3 exports) into renderer cache
    if (R.preloadCardData) R.preloadCardData(game.cardData);

    // Fill in missing gameSeq on llmEvents
    if (useSeq && game.llmEvents) {
      var lastSeq = 0;
      game.llmEvents.forEach(function (e) {
        if (e.gameSeq != null) {
          lastSeq = e.gameSeq;
        } else {
          e.gameSeq = lastSeq;
        }
      });
    }

    // Build player color map and meta
    (game.players || []).forEach(function (p, i) {
      playerColorMap[p.name] = i % 4;
      if (p.model || p.totalCostUsd != null) {
        playerMeta[p.name] = { model: p.model, totalCostUsd: 0 };
      }
    });

    // Detect commander format
    isCommander = (game.players || []).some(function (p) { return !!p.commander; });

    // Show filter checkboxes based on content
    var hasLlm = game.llmEvents && game.llmEvents.length > 0;
    var hasChat = (game.actions && game.actions.some(function (a) { return a.type === "chat"; }))
      || (game.llmEvents && game.llmEvents.some(function (e) { return e.tool === "send_chat_message"; }));
    var hasAnnotations = game.annotations && game.annotations.length > 0;
    if (hasLlm) {
      dom.filterLlmEl.parentElement.classList.remove("hidden");
    }
    dom.filterGameEl.parentElement.classList.remove("hidden");
    if (hasChat) {
      dom.filterChatEl.parentElement.classList.remove("hidden");
    }
    if (hasAnnotations) {
      dom.filterAnnotationsEl.parentElement.classList.remove("hidden");
    }

    // Compute per-player turn numbers
    playerTurnNumbers = R.computePlayerTurnNumbers(game.snapshots);

    // Build turn start indices
    var lastTurn = -1;
    game.snapshots.forEach(function (snap, i) {
      if (snap.turn !== lastTurn) {
        turnStartIndices.push({ turn: snap.turn, index: i });
        lastTurn = snap.turn;
      }
    });

    // Build phase/step transition indices
    game.snapshots.forEach(function (snap, i) {
      if (i === 0) return;
      var prev = game.snapshots[i - 1];
      var turnChanged = snap.turn !== prev.turn;
      var phaseChanged = snap.phase !== prev.phase || snap.step !== prev.step;
      if (turnChanged || phaseChanged) {
        phaseTransitions.push({
          index: i,
          ts: snap.ts || "",
          seq: snap.seq || 0,
          turn: snap.turn,
          playerTurn: playerTurnNumbers[i],
          phase: snap.phase,
          step: snap.step,
          turnChanged: turnChanged,
          active_player: snap.active_player,
        });
      }
    });

    // Build snapshot -> decisions map
    var snapshotDecisionMap = {};
    (game.decisions || []).forEach(function (d) {
      var si = d.snapshotIndex;
      if (!snapshotDecisionMap[si]) snapshotDecisionMap[si] = [];
      snapshotDecisionMap[si].push(d);
    });

    // Build llmEvent index -> decision reverse lookup.
    // Stamp each llmEvent with its original index so renderToolResult can
    // look up the decision for any tool_call it receives.
    (game.llmEvents || []).forEach(function (e, i) { e._origIdx = i; });
    var llmEventIndexToDecision = {};
    (game.decisions || []).forEach(function (d) {
      (d.llmEventIndices || []).forEach(function (ei) {
        llmEventIndexToDecision[ei] = d;
      });
    });

    // Map each annotation to its decision's snapshot index so we can show
    // the annotation alongside the decision (pre-decision board state).
    // annotation.snapshotIndex is post-decision; the matching decision is
    // the latest one by the same player with snapshotIndex < ann.snapshotIndex.
    var annotationDecisionSnap = [];
    if (game.annotations) {
      // Build per-player sorted decision snapshot indices
      var playerDecSnaps = {};
      (game.decisions || []).forEach(function (d) {
        if (!playerDecSnaps[d.player]) playerDecSnaps[d.player] = [];
        playerDecSnaps[d.player].push(d.snapshotIndex);
      });
      Object.keys(playerDecSnaps).forEach(function (p) {
        playerDecSnaps[p].sort(function (a, b) { return a - b; });
      });

      game.annotations.forEach(function (ann) {
        var candidates = playerDecSnaps[ann.player] || [];
        var best = Math.max(0, ann.snapshotIndex - 1); // fallback
        for (var i = candidates.length - 1; i >= 0; i--) {
          if (candidates[i] < ann.snapshotIndex) {
            best = candidates[i];
            break;
          }
        }
        annotationDecisionSnap.push(best);
      });
    }


    // Set up transport
    dom.slider.max = String(game.snapshots.length - 1);
    dom.snapshotJump.max = String(game.snapshots.length);

    // ── Event listeners ──

    dom.filterLlmEl.addEventListener("change", function () {
      filterLlm = dom.filterLlmEl.checked;
      renderSnapshot(currentIndex);
    });
    dom.filterGameEl.addEventListener("change", function () {
      filterGame = dom.filterGameEl.checked;
      renderSnapshot(currentIndex);
    });
    dom.filterChatEl.addEventListener("change", function () {
      filterChat = dom.filterChatEl.checked;
      renderSnapshot(currentIndex);
    });
    dom.filterAnnotationsEl.addEventListener("change", function () {
      filterAnnotations = dom.filterAnnotationsEl.checked;
      renderSnapshot(currentIndex);
    });

    dom.snapshotJump.addEventListener("change", function () {
      var val = Number(dom.snapshotJump.value);
      if (val >= 1 && val <= game.snapshots.length) {
        goTo(val - 1);
      }
    });
    dom.snapshotJump.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        dom.snapshotJump.blur();
        var val = Number(dom.snapshotJump.value);
        if (val >= 1 && val <= game.snapshots.length) {
          goTo(val - 1);
        }
      }
    });

    dom.btnPrev.addEventListener("click", function () { goTo(currentIndex - 1); });
    dom.btnNext.addEventListener("click", function () { goTo(currentIndex + 1); });
    dom.btnAuto.addEventListener("click", toggleAutoPlay);
    dom.slider.addEventListener("input", function () { goTo(Number(dom.slider.value)); });

    // Keyboard shortcuts — bound on container so they don't conflict with other UI
    container.setAttribute("tabindex", "0");
    container.style.outline = "none";
    container.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { e.preventDefault(); goTo(currentIndex + 1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); goTo(currentIndex - 1); }
      if (e.key === "Home") { e.preventDefault(); goTo(0); }
      if (e.key === "End") { e.preventDefault(); goTo(game.snapshots.length - 1); }
      if (e.key === " ") { e.preventDefault(); toggleAutoPlay(); }
      if (e.key === "Escape") { R.hidePreview(dom.previewEls); }
      if (e.key === "[") {
        e.preventDefault();
        for (var i = turnStartIndices.length - 1; i >= 0; i--) {
          if (turnStartIndices[i].index < currentIndex) { goTo(turnStartIndices[i].index); break; }
        }
      }
      if (e.key === "]") {
        e.preventDefault();
        for (var i = 0; i < turnStartIndices.length; i++) {
          if (turnStartIndices[i].index > currentIndex) { goTo(turnStartIndices[i].index); break; }
        }
      }
    });

    // Set up mouse-following card preview
    R.setupMousePreview(dom.previewEls.container);

    // Preload card images into browser cache at low priority.
    // Load in small batches during idle time so we don't saturate the
    // browser's connection pool and starve the actually-visible images.
    if (game.cardImages) {
      var _precacheUrls = Object.values(game.cardImages);
      var _precacheIdx = 0;
      var _precacheBatch = 4;
      function _precacheNext() {
        if (_precacheIdx >= _precacheUrls.length) return;
        var end = Math.min(_precacheIdx + _precacheBatch, _precacheUrls.length);
        for (var i = _precacheIdx; i < end; i++) {
          var img = new Image();
          img.src = _precacheUrls[i];
        }
        _precacheIdx = end;
        if (typeof requestIdleCallback !== "undefined") {
          requestIdleCallback(_precacheNext);
        } else {
          setTimeout(_precacheNext, 100);
        }
      }
      // Delay start so the initial render's images get first crack at connections
      if (typeof requestIdleCallback !== "undefined") {
        requestIdleCallback(_precacheNext);
      } else {
        setTimeout(_precacheNext, 200);
      }
    }

    // Render turn boundary markers and annotation markers on slider
    renderTurnMarkers(turnStartIndices, game.snapshots.length);
    if (hasAnnotations) {
      renderAnnotationMarkers(game.annotations, game.snapshots.length);
    }

    // Render initial snapshot
    goTo(initialSnapshot);

    // ── Cleanup ──

    var blurHandler = function () { R.hidePreview(dom.previewEls); };
    root.addEventListener("blur", blurHandler);

    function destroy() {
      if (autoPlayInterval) {
        clearInterval(autoPlayInterval);
        autoPlayInterval = null;
      }
      root.removeEventListener("blur", blurHandler);
      container.innerHTML = "";
    }

    // ── Public API ──

    return {
      goTo: goTo,
      getCurrentIndex: function () { return currentIndex; },
      getSnapshotCount: function () { return game.snapshots.length; },
      getPlayerColorMap: function () { return playerColorMap; },
      destroy: destroy,
    };
  }

  // ── Static utilities ──

  function fetchGameData(basePath, slug) {
    return fetch(basePath + "/games/" + slug + ".json")
      .then(function (r) {
        var ct = (r.headers.get("content-type") || "");
        if (r.ok && ct.indexOf("json") !== -1) return r.json();
        return fetch(basePath + "/games/" + slug + ".json.gz").then(function (r2) {
          if (!r2.ok) throw new Error("Game not found");
          var ds = new DecompressionStream("gzip");
          return new Response(r2.body.pipeThrough(ds)).json();
        });
      });
  }

  // ── Exports ──

  var GameViewer = {
    create: create,
    fetchGameData: fetchGameData,
    // Expose for testing
    mergeLlmEvents: mergeLlmEvents,
    extractSystemMessages: extractSystemMessages,
    chosenDisplayText: chosenDisplayText,
  };

  if (typeof root !== "undefined" && root !== null) {
    root.GameViewer = GameViewer;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = GameViewer;
  }

})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
