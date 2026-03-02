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
          if (events[j].tool !== "send_chat_message") {
            toolResults.push(events[j]);
          }
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
        if (e.tool === "send_chat_message") {
          i++;
          continue;
        }
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
      '          <select id="player-filter" class="hidden" title="Filter by player">',
      '            <option value="">All players</option>',
      '          </select>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-llm" checked /> LLM</label>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-game" checked /> Game</label>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-chat" checked /> Chat</label>',
      '          <label class="filter-checkbox hidden"><input type="checkbox" id="filter-annotations" checked /> Blunders</label>',
      '          <label id="llm-toggle-label" class="hidden">',
      '            <input type="checkbox" id="llm-toggle" checked /> Show LLM thinking',
      '          </label>',
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
      llmToggleLabel: container.querySelector("#llm-toggle-label"),
      llmToggle: container.querySelector("#llm-toggle"),
      playerFilter: container.querySelector("#player-filter"),
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
    var showLlm = true;
    var isCommander = false;
    var filterPlayer = "";
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
      return details;
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
              div.appendChild(renderToolResult(tc));
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
          event.toolResults.forEach(function (tc) {
            div.appendChild(renderToolResult(tc));
          });
          return div;
        }
        return null;
      }

      if (type === "context_trim") return null;

      if (type === "system_message") {
        var div = document.createElement("div");
        div.className = "llm-event llm-system-message";
        div.innerHTML = playerSpan(event.player) + ' <span class="system-message-text">' + escapeHtml(event.message) + '</span>';
        return div;
      }

      var div = document.createElement("div");
      div.className = "llm-event llm-meta";

      if (type === "stall") {
        div.textContent = event.player + " stalled (" + (event.turnsWithoutProgress || 0) + " turns without progress)";
      } else if (type === "llm_error") {
        div.textContent = event.player + " error: " + (event.errorType || "") + " " + (event.errorMessage || "");
      } else if (type === "context_reset") {
        div.textContent = event.player + " context reset: " + (event.reason || "");
      } else if (type === "auto_pilot_mode") {
        div.textContent = event.player + " switched to auto-pilot: " + (event.reason || "");
      } else {
        div.textContent = event.player + " " + type;
      }

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
      annotations.forEach(function (ann) {
        var dot = document.createElement("div");
        dot.className = "annotation-marker severity-" + ann.severity;
        var pct = totalSnapshots > 1 ? (ann.snapshotIndex / (totalSnapshots - 1)) * 100 : 50;
        dot.style.left = pct + "%";
        dot.title = ann.player + " (" + ann.severity + "): " + ann.description.substring(0, 80);
        dot.addEventListener("click", function () { goTo(ann.snapshotIndex); });
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

      // Action log: show full accumulated log up to current snapshot
      dom.actionList.innerHTML = "";
      var prevSeq = index > 0 ? game.snapshots[index - 1].seq : 0;
      var curSeq = snap.seq;

      var SPAM_RE = / skip attack$|^Attacker: .+ unblocked$/;

      var allActions = game.actions.filter(function (a) {
        if (a.seq > curSeq) return false;
        if (!a.message && a.type !== "chat") return false;
        if (a.type !== "chat" && SPAM_RE.test(a.message)) return false;
        if (filterPlayer) {
          if (a.type === "chat") {
            if (a.from !== filterPlayer) return false;
          } else {
            if ((a.message || "").indexOf(filterPlayer) === -1) return false;
          }
        }
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
          if (filterPlayer && e.player !== filterPlayer) return;
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
      if (hasLlmEvents && showLlm) {
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
          if (filterPlayer && e.player !== filterPlayer) return false;
          return true;
        });

        systemMessages.forEach(function (sm) {
          if (useSeq) {
            if ((sm.gameSeq || 0) >= nextSeq) return;
          } else {
            if (nextTs && sm.ts >= nextTs) return;
          }
          if (filterPlayer && sm.player !== filterPlayer) return;
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
        game.annotations.forEach(function (ann) {
          if (ann.snapshotIndex <= index) {
            var annSnap = game.snapshots[ann.snapshotIndex] || {};
            var nextAnnSnap = game.snapshots[ann.snapshotIndex + 1];
            if (useSeq) {
              var annSeq = nextAnnSnap ? (nextAnnSnap.seq || 0) : (annSnap.seq || 0);
              timeline.push({ kind: "annotation", seq: annSeq, data: ann });
            } else {
              var annTs = nextAnnSnap ? (nextAnnSnap.ts || "") : (annSnap.ts || "");
              timeline.push({ kind: "annotation", ts: annTs, data: ann });
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
          el.innerHTML = colorizePlayerNames(a.message);
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
      dom.llmToggleLabel.classList.remove("hidden");
      dom.filterLlmEl.parentElement.classList.remove("hidden");
    }
    dom.filterGameEl.parentElement.classList.remove("hidden");
    if (hasChat) {
      dom.filterChatEl.parentElement.classList.remove("hidden");
    }
    if (hasAnnotations) {
      dom.filterAnnotationsEl.parentElement.classList.remove("hidden");
    }

    // Populate player filter dropdown
    if (game.players && game.players.length > 1) {
      game.players.forEach(function (p) {
        var opt = document.createElement("option");
        opt.value = p.name;
        opt.textContent = p.name;
        dom.playerFilter.appendChild(opt);
      });
      dom.playerFilter.classList.remove("hidden");
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

    // Set up transport
    dom.slider.max = String(game.snapshots.length - 1);
    dom.snapshotJump.max = String(game.snapshots.length);

    // ── Event listeners ──

    dom.llmToggle.addEventListener("change", function () {
      showLlm = dom.llmToggle.checked;
      renderSnapshot(currentIndex);
    });

    dom.playerFilter.addEventListener("change", function () {
      filterPlayer = dom.playerFilter.value;
      renderSnapshot(currentIndex);
    });

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
  };

  if (typeof root !== "undefined" && root !== null) {
    root.GameViewer = GameViewer;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = GameViewer;
  }

})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
