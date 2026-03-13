import "./game-list.js";

export function initGameListPage() {
  const root = document.querySelector("[data-game-list-root]");
  if (!root) {
    return;
  }

  const gameList = window.GameList;
  if (!gameList) {
    throw new Error("game-list.js did not initialize window.GameList");
  }

  gameList.init({
    showSeasonFilter: root.dataset.showSeasonFilter === "1",
  });
}
