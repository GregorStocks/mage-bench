import {
  buildPlayerColorMap,
  colorizePlayerNames,
  escapeHtml,
  getGameRenderer,
  getPreviewElements,
  getRequiredElement,
  parseJsonAttribute,
  setupPreviewLifecycle,
} from "./spectator-runtime.js";
import {
  extractSystemMessages,
  formatToolArgs,
  mergeLlmEvents,
  tryFormatJson,
} from "./game-log-helpers.ts";

export function initGoldenViewerPage(options) {
  var visualizer = options && options.root ? options.root : document.getElementById("visualizer");
  if (!visualizer) {
    return;
  }

  var renderer = getGameRenderer();
  var game = parseJsonAttribute(visualizer, "data-game", "Missing data-game");
  var fixture = parseJsonAttribute(visualizer, "data-fixture", "Missing data-fixture");

  if (!game.snapshots || game.snapshots.length === 0) {
    visualizer.innerHTML = '<div style="text-align:center;padding:4rem;color:#e94560">No snapshots in export.</div>';
    return;
  }

  var btnStart = getRequiredElement(visualizer, "#btn-start");
  var btnPrev = getRequiredElement(visualizer, "#btn-prev");
  var btnNext = getRequiredElement(visualizer, "#btn-next");
  var btnEnd = getRequiredElement(visualizer, "#btn-end");
  var btnAuto = getRequiredElement(visualizer, "#btn-auto");
  var slider = getRequiredElement(visualizer, "#slider");
  var counterEl = getRequiredElement(visualizer, "#snapshot-counter");
  var snapshotJump = getRequiredElement(visualizer, "#snapshot-jump");
  var playersGrid = getRequiredElement(visualizer, "#players-grid");
  var stackSection = getRequiredElement(visualizer, "#stack-section");
  var stackCards = getRequiredElement(visualizer, "#stack-cards");
  var actionList = getRequiredElement(visualizer, "#action-list");
  var turnSelect = getRequiredElement(visualizer, "#turn-select");
  var btnPrevTurn = getRequiredElement(visualizer, "#btn-prev-turn");
  var btnNextTurn = getRequiredElement(visualizer, "#btn-next-turn");
  var previewEls = getPreviewElements(visualizer);
  var mcpJsonBlock = getRequiredElement(visualizer, "#mcp-json-block");

  if (fixture.mcp_game_state) {
    mcpJsonBlock.textContent = JSON.stringify(fixture.mcp_game_state, null, 2);
  } else {
    mcpJsonBlock.textContent = "(no prompt fixture found)";
  }

  var currentIndex = 0;
  var autoPlayInterval = null;
  var playerColorMap = buildPlayerColorMap(game.players);
  var playerMeta = {};
  var turnStartIndices = [];
  var phaseTransitions = [];
  var playerTurnNumbers = [];
  var showLlm = true;
  var isCommander = false;
  var filterPlayer = "";
  var filterEventType = "";
  var useSeq = game.version != null;

  if (useSeq && game.llmEvents) {
    var lastSeq = 0;
    game.llmEvents.forEach(function (event) {
      if (event.gameSeq != null) {
        lastSeq = event.gameSeq;
      } else {
        event.gameSeq = lastSeq;
      }
    });
  }

  (game.players || []).forEach(function (player) {
    if (player.model || player.totalCostUsd != null) {
      playerMeta[player.name] = { model: player.model, totalCostUsd: 0 };
    }
  });

  isCommander = (game.players || []).some(function (player) {
    return !!player.commander;
  });

  var hasLlm = game.llmEvents && game.llmEvents.length > 0;
  var hasChat = game.actions && game.actions.some(function (action) {
    return action.type === "chat";
  });

  var llmToggleLabel = getRequiredElement(visualizer, "#llm-toggle-label");
  var llmToggle = getRequiredElement(visualizer, "#llm-toggle");
  if (hasLlm) {
    llmToggleLabel.classList.remove("hidden");
  }

  var eventTypeFilter = getRequiredElement(visualizer, "#event-type-filter");
  if (hasLlm || hasChat) {
    eventTypeFilter.classList.remove("hidden");
  }

  llmToggle.addEventListener("change", function () {
    showLlm = llmToggle.checked;
    renderSnapshot(currentIndex);
  });

  var playerFilter = getRequiredElement(visualizer, "#player-filter");
  playerFilter.addEventListener("change", function () {
    filterPlayer = playerFilter.value;
    renderSnapshot(currentIndex);
  });

  eventTypeFilter.addEventListener("change", function () {
    filterEventType = eventTypeFilter.value;
    renderSnapshot(currentIndex);
  });

  if (game.players && game.players.length > 1) {
    game.players.forEach(function (player) {
      var option = document.createElement("option");
      option.value = player.name;
      option.textContent = player.name;
      playerFilter.appendChild(option);
    });
    playerFilter.classList.remove("hidden");
  }

  playerTurnNumbers = renderer.computePlayerTurnNumbers(game.snapshots);

  var lastTurn = -1;
  game.snapshots.forEach(function (snapshot, index) {
    if (snapshot.turn !== lastTurn) {
      turnStartIndices.push({ turn: snapshot.turn, index: index });
      lastTurn = snapshot.turn;
    }
  });

  game.snapshots.forEach(function (snapshot, index) {
    if (index === 0) {
      return;
    }
    var previous = game.snapshots[index - 1];
    var turnChanged = snapshot.turn !== previous.turn;
    var phaseChanged = snapshot.phase !== previous.phase || snapshot.step !== previous.step;
    if (turnChanged || phaseChanged) {
      phaseTransitions.push({
        index: index,
        ts: snapshot.ts || "",
        seq: snapshot.seq || 0,
        turn: snapshot.turn,
        phase: snapshot.phase,
        step: snapshot.step,
        turnChanged: turnChanged,
      });
    }
  });

  turnStartIndices.forEach(function (turnStart) {
    var option = document.createElement("option");
    option.value = String(turnStart.index);
    option.textContent = "Turn " + turnStart.turn;
    turnSelect.appendChild(option);
  });

  function playerColorClass(playerName) {
    var idx = playerColorMap[playerName];
    return idx != null ? "llm-player-" + idx : "";
  }

  function playerSpan(playerName) {
    var idx = playerColorMap[playerName];
    var cls = idx != null ? "action-" + renderer.PLAYER_COLORS[idx] : "";
    return '<span class="llm-player ' + cls + '">' + escapeHtml(playerName) + "</span>";
  }

  function formatTimestamp(ts) {
    if (!ts) {
      return null;
    }
    var match = ts.match(/T(\d{2}:\d{2}:\d{2})(\.\d+)?/);
    if (!match) {
      return null;
    }
    return { display: match[1], full: match[1] + (match[2] || "") };
  }

  function makeTimestampEl(ts) {
    var parsed = formatTimestamp(ts);
    if (!parsed) {
      return null;
    }
    var span = document.createElement("span");
    span.className = "entry-ts";
    span.textContent = parsed.display;
    span.title = parsed.full;
    return span;
  }

  function renderToolResult(toolCall) {
    var details = document.createElement("details");
    details.className = "llm-tool-detail";
    var summary = document.createElement("summary");
    var argsSummary = formatToolArgs(toolCall.args);
    var latency = toolCall.latencyMs != null ? " " + (toolCall.latencyMs / 1000).toFixed(1) + "s" : "";
    summary.innerHTML =
      escapeHtml(toolCall.tool) +
      "(" +
      escapeHtml(argsSummary) +
      ")" +
      (latency ? '<span class="llm-cost">' + latency + "</span>" : "");
    details.appendChild(summary);
    if (toolCall.result) {
      var pre = document.createElement("pre");
      pre.textContent = tryFormatJson(toolCall.result);
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
        var thoughtDiv = document.createElement("div");
        thoughtDiv.className = "llm-event llm-thought " + playerColorClass(event.player);

        var headerHtml = '<span class="thinking-badge">thinking</span>' + playerSpan(event.player);
        if (event.costUsd != null) {
          headerHtml += '<span class="llm-cost">$' + event.costUsd.toFixed(4) + "</span>";
        }
        var headerEl = document.createElement("div");
        headerEl.innerHTML = headerHtml;
        thoughtDiv.appendChild(headerEl);

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
          thoughtDiv.appendChild(thinkDetails);
        }

        if (hasReasoning) {
          var reasoningEl = document.createElement("div");
          reasoningEl.className = "llm-reasoning";
          reasoningEl.textContent = event.reasoning;
          thoughtDiv.appendChild(reasoningEl);
        }

        if (hasToolResults) {
          event.toolResults.forEach(function (toolCall) {
            thoughtDiv.appendChild(renderToolResult(toolCall));
          });
        }

        return thoughtDiv;
      }

      if (hasToolResults) {
        var compactDiv = document.createElement("div");
        compactDiv.className = "llm-event llm-compact";
        var compactHeader = playerSpan(event.player);
        if (event.costUsd != null) {
          compactHeader += '<span class="llm-cost">$' + event.costUsd.toFixed(4) + "</span>";
        }
        var compactHeaderEl = document.createElement("span");
        compactHeaderEl.innerHTML = compactHeader;
        compactDiv.appendChild(compactHeaderEl);
        event.toolResults.forEach(function (toolCall) {
          compactDiv.appendChild(renderToolResult(toolCall));
        });
        return compactDiv;
      }
      return null;
    }

    if (type === "context_trim") {
      return null;
    }

    if (type === "system_message") {
      var systemDiv = document.createElement("div");
      systemDiv.className = "llm-event llm-system-message";
      systemDiv.innerHTML =
        playerSpan(event.player) +
        ' <span class="system-message-text">' +
        escapeHtml(event.message) +
        "</span>";
      return systemDiv;
    }

    var metaDiv = document.createElement("div");
    metaDiv.className = "llm-event llm-meta";

    if (type === "stall") {
      metaDiv.textContent = event.player + " stalled (" + (event.turnsWithoutProgress || 0) + " turns without progress)";
    } else if (type === "llm_error") {
      metaDiv.textContent = event.player + " error: " + (event.errorType || "") + " " + (event.errorMessage || "");
    } else if (type === "context_reset") {
      metaDiv.textContent = event.player + " context reset: " + (event.reason || "");
    } else if (type === "auto_pilot_mode") {
      metaDiv.textContent = event.player + " switched to auto-pilot: " + (event.reason || "");
    } else {
      metaDiv.textContent = event.player + " " + type;
    }

    return metaDiv;
  }

  function renderSnapshot(index) {
    var snapshot = game.snapshots[index];
    if (!snapshot) {
      return;
    }

    var previousSnapshot = index > 0 ? game.snapshots[index - 1] : null;
    var diffs = renderer.computeDiff(previousSnapshot, snapshot);

    renderer.renderPlayers(playersGrid, snapshot.players, {
      cardImages: game.cardImages,
      playerColorMap: playerColorMap,
      playerMeta: playerMeta,
      diffs: diffs,
      previewEls: previewEls,
      priorityPlayerName: snapshot.priority_player,
      isCommander: isCommander,
    });

    if (playersGrid.children.length >= 2) {
      var insertIdx = playersGrid.children.length <= 2 ? 1 : 2;
      var phaseBar = document.createElement("div");
      phaseBar.id = "phase-bar";
      var phase = snapshot.phase || "";
      var step = snapshot.step || "";
      var phaseDisplay = step && step !== phase ? phase + " / " + step : phase;
      phaseBar.textContent =
        renderer.formatTurnLabel(playerTurnNumbers[index], snapshot.active_player) +
        " \u2014 " +
        phaseDisplay;
      playersGrid.insertBefore(phaseBar, playersGrid.children[insertIdx]);
    }

    renderer.renderStack(stackSection, stackCards, snapshot.stack, game.cardImages, previewEls);

    actionList.innerHTML = "";
    var prevSeq = index > 0 ? game.snapshots[index - 1].seq : 0;
    var curSeq = snapshot.seq;
    var SPAM_RE = / skip attack$|^Attacker: .+ unblocked$/;

    var allActions = game.actions.filter(function (action) {
      if (action.seq > curSeq) {
        return false;
      }
      if (!action.message && action.type !== "chat") {
        return false;
      }
      if (action.type !== "chat" && SPAM_RE.test(action.message)) {
        return false;
      }
      if (filterPlayer) {
        if (action.type === "chat") {
          if (action.from !== filterPlayer) {
            return false;
          }
        } else if ((action.message || "").indexOf(filterPlayer) === -1) {
          return false;
        }
      }
      return true;
    });

    var hasLlmEvents = game.llmEvents && game.llmEvents.length > 0;
    var relevantLlm = [];
    if (hasLlmEvents && showLlm) {
      var nextSnapshot = index < game.snapshots.length - 1 ? game.snapshots[index + 1] : null;
      var nextTs = nextSnapshot ? (nextSnapshot.ts || "") : "";
      var nextSeq = nextSnapshot ? (nextSnapshot.seq || Infinity) : Infinity;
      var systemMessages = [];
      var lastPlayerTs = {};
      var lastPlayerSeq = {};
      game.llmEvents.forEach(function (event) {
        if (event.type === "tool_call") {
          var systemEvents = extractSystemMessages(event);
          if (systemEvents.length > 0) {
            var backdatedTs = lastPlayerTs[event.player] || event.ts;
            var backdatedSeq = lastPlayerSeq[event.player] || event.gameSeq || 0;
            systemEvents.forEach(function (message) {
              systemMessages.push({
                type: "system_message",
                ts: backdatedTs,
                gameSeq: backdatedSeq,
                player: event.player,
                message: message,
              });
            });
          }
          lastPlayerTs[event.player] = event.ts;
          lastPlayerSeq[event.player] = event.gameSeq || 0;
        }
      });

      relevantLlm = game.llmEvents.filter(function (event) {
        if (useSeq) {
          if ((event.gameSeq || 0) >= nextSeq) {
            return false;
          }
        } else if (nextTs && event.ts >= nextTs) {
          return false;
        }
        if (filterPlayer && event.player !== filterPlayer) {
          return false;
        }
        return true;
      });

      systemMessages.forEach(function (event) {
        if (useSeq) {
          if ((event.gameSeq || 0) >= nextSeq) {
            return;
          }
        } else if (nextTs && event.ts >= nextTs) {
          return;
        }
        if (filterPlayer && event.player !== filterPlayer) {
          return;
        }
        relevantLlm.push(event);
      });
    }

    var mergedLlm = mergeLlmEvents(relevantLlm);
    var timeline = [];
    var showGameActions = filterEventType === "" || filterEventType === "game";
    var showChatMessages = filterEventType === "" || filterEventType === "chat";
    var showLlmEventsFilter = filterEventType === "" || filterEventType === "llm";

    function itemSeq(item) {
      if (item.kind === "action" || item.kind === "chat") {
        return item.data.seq || 0;
      }
      if (item.kind === "llm") {
        return item.data.gameSeq || item.data.seq || 0;
      }
      if (item.kind === "turn-sep" || item.kind === "phase-sep") {
        return item.data.seq || 0;
      }
      return 0;
    }

    allActions.forEach(function (action) {
      var isChat = action.type === "chat";
      if (isChat && showChatMessages) {
        timeline.push({ kind: "chat", ts: action.ts || "", data: action });
      } else if (!isChat && showGameActions) {
        timeline.push({ kind: "action", ts: action.ts || "", data: action });
      }
    });
    if (showLlmEventsFilter) {
      mergedLlm.forEach(function (event) {
        timeline.push({ kind: "llm", ts: event.ts || "", data: event });
      });
    }

    function timelineSort(left, right) {
      if (useSeq) {
        var leftSeq = itemSeq(left);
        var rightSeq = itemSeq(right);
        if (leftSeq !== rightSeq) {
          return leftSeq - rightSeq;
        }
      } else {
        if (left.ts < right.ts) {
          return -1;
        }
        if (left.ts > right.ts) {
          return 1;
        }
      }
      var order = { "turn-sep": -2, "phase-sep": -1 };
      var leftOrder = order[left.kind] || 0;
      var rightOrder = order[right.kind] || 0;
      return leftOrder - rightOrder;
    }

    timeline.sort(timelineSort);

    phaseTransitions.forEach(function (transition) {
      if (transition.index <= index) {
        timeline.push({
          kind: transition.turnChanged ? "turn-sep" : "phase-sep",
          ts: transition.ts,
          data: transition,
        });
      }
    });
    timeline.sort(timelineSort);

    timeline.forEach(function (item) {
      var element = null;
      if (item.kind === "chat") {
        var chat = item.data;
        element = document.createElement("div");
        element.className = "chat-line";
        var fromIdx = playerColorMap[chat.from];
        var fromCls = fromIdx != null ? "action-" + renderer.PLAYER_COLORS[fromIdx] : "";
        element.innerHTML =
          '<span class="chat-badge">chat</span><span class="chat-from ' +
          fromCls +
          '">' +
          escapeHtml(chat.from || "") +
          ":</span> " +
          escapeHtml(chat.message || "");
      } else if (item.kind === "action") {
        var action = item.data;
        element = document.createElement("div");
        element.className = "action-line";
        if (action.seq > prevSeq) {
          element.style.color = "#e0e0f0";
        }
        element.innerHTML = colorizePlayerNames(action.message, playerColorMap, renderer);
      } else if (item.kind === "llm") {
        element = renderLlmEvent(item.data);
      } else if (item.kind === "turn-sep") {
        element = document.createElement("div");
        element.className = "turn-separator";
        element.textContent = "\u2014 Turn " + item.data.turn + " \u2014";
      } else if (item.kind === "phase-sep") {
        element = document.createElement("div");
        element.className = "phase-separator";
        element.textContent = "\u2014 " + renderer.formatPhaseStep(item.data.phase, item.data.step) + " \u2014";
      }
      if (element) {
        var tsEl = makeTimestampEl(item.ts);
        if (tsEl) {
          element.insertBefore(tsEl, element.firstChild);
        }
        actionList.appendChild(element);
      }
    });

    if (game.winner && index === game.snapshots.length - 1) {
      var winLine = document.createElement("div");
      winLine.className = "game-result-line";
      var winIdx = playerColorMap[game.winner];
      var winCls = winIdx != null ? "action-" + renderer.PLAYER_COLORS[winIdx] : "";
      winLine.innerHTML = '<span class="' + winCls + '">' + escapeHtml(game.winner) + "</span> wins the game!";
      actionList.appendChild(winLine);
    }

    setTimeout(function () {
      var leftHeight = getRequiredElement(visualizer, "#game-left").offsetHeight;
      var headerHeight = actionList.parentElement.offsetHeight - actionList.offsetHeight;
      var targetHeight = Math.max(400, leftHeight - headerHeight);
      actionList.style.maxHeight = targetHeight + "px";
      actionList.style.minHeight = targetHeight + "px";
      actionList.scrollTop = actionList.scrollHeight;
    }, 50);

    slider.value = String(index);
    snapshotJump.value = String(index + 1);
    counterEl.textContent = index + 1 + " / " + game.snapshots.length;

    if (turnStartIndices.length > 0) {
      var currentTurn = snapshot.turn;
      for (var turnIndex = turnStartIndices.length - 1; turnIndex >= 0; turnIndex--) {
        if (turnStartIndices[turnIndex].turn <= currentTurn) {
          turnSelect.selectedIndex = turnIndex;
          break;
        }
      }
    }
  }

  function goTo(index) {
    currentIndex = Math.max(0, Math.min(index, game.snapshots.length - 1));
    renderSnapshot(currentIndex);
    var url = new URL(window.location.href);
    if (currentIndex === game.snapshots.length - 1) {
      url.searchParams.delete("s");
    } else {
      url.searchParams.set("s", String(currentIndex));
    }
    history.replaceState(null, "", url.toString());
  }

  function toggleAutoPlay() {
    if (autoPlayInterval) {
      clearInterval(autoPlayInterval);
      autoPlayInterval = null;
      btnAuto.textContent = "Play";
      btnAuto.classList.remove("active");
      return;
    }

    btnAuto.textContent = "Pause";
    btnAuto.classList.add("active");
    autoPlayInterval = setInterval(function () {
      if (currentIndex >= game.snapshots.length - 1) {
        toggleAutoPlay();
        return;
      }
      goTo(currentIndex + 1);
    }, 500);
  }

  slider.max = String(game.snapshots.length - 1);
  snapshotJump.max = String(game.snapshots.length);

  snapshotJump.addEventListener("change", function () {
    var value = Number(snapshotJump.value);
    if (value >= 1 && value <= game.snapshots.length) {
      goTo(value - 1);
    }
  });
  snapshotJump.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      snapshotJump.blur();
      var value = Number(snapshotJump.value);
      if (value >= 1 && value <= game.snapshots.length) {
        goTo(value - 1);
      }
    }
  });

  btnStart.addEventListener("click", function () {
    goTo(0);
  });
  btnPrev.addEventListener("click", function () {
    goTo(currentIndex - 1);
  });
  btnNext.addEventListener("click", function () {
    goTo(currentIndex + 1);
  });
  btnEnd.addEventListener("click", function () {
    goTo(game.snapshots.length - 1);
  });
  btnAuto.addEventListener("click", toggleAutoPlay);
  slider.addEventListener("input", function () {
    goTo(Number(slider.value));
  });

  turnSelect.addEventListener("change", function () {
    goTo(Number(turnSelect.value));
  });
  btnPrevTurn.addEventListener("click", function () {
    var index = Math.max(0, turnSelect.selectedIndex - 1);
    turnSelect.selectedIndex = index;
    goTo(Number(turnSelect.value));
  });
  btnNextTurn.addEventListener("click", function () {
    var index = Math.min(turnStartIndices.length - 1, turnSelect.selectedIndex + 1);
    turnSelect.selectedIndex = index;
    goTo(Number(turnSelect.value));
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      goTo(currentIndex + 1);
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      goTo(currentIndex - 1);
    }
    if (event.key === "Home") {
      event.preventDefault();
      goTo(0);
    }
    if (event.key === "End") {
      event.preventDefault();
      goTo(game.snapshots.length - 1);
    }
    if (event.key === " ") {
      event.preventDefault();
      toggleAutoPlay();
    }
    if (event.key === "Escape") {
      renderer.hidePreview(previewEls);
    }
    if (event.key === "[") {
      event.preventDefault();
      btnPrevTurn.click();
    }
    if (event.key === "]") {
      event.preventDefault();
      btnNextTurn.click();
    }
  });

  setupPreviewLifecycle(renderer, previewEls, { hideOnEscape: false });

  var params = new URLSearchParams(window.location.search);
  var startSnap = params.get("s");
  if (startSnap !== null) {
    var snapNum = parseInt(startSnap, 10);
    if (!isNaN(snapNum) && snapNum >= 0 && snapNum < game.snapshots.length) {
      goTo(snapNum);
    } else {
      goTo(game.snapshots.length - 1);
    }
  } else {
    goTo(game.snapshots.length - 1);
  }
}
