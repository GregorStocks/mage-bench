import "./game-list.js";

function readConfig() {
  const configEl = document.getElementById("game-list-config");
  if (!configEl) {
    throw new Error("Missing #game-list-config script");
  }
  return JSON.parse(configEl.textContent || "null");
}

export function initGameListPage() {
  if (!document.querySelector("[data-game-list-root]")) {
    return;
  }

  const gameList = window.GameList;
  if (!gameList) {
    throw new Error("game-list.js did not initialize window.GameList");
  }

  gameList.init(readConfig());
}
