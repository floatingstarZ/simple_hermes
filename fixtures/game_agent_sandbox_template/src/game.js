let score = 0;
let highScore = 0;
let isPaused = false;

const HIGH_SCORE_KEY = "coinCatcherHighScore";

function getScoreElement() {
  return document.getElementById("score");
}

function getHighScoreElement() {
  return document.getElementById("high-score");
}

function getStatusElement() {
  return document.getElementById("status");
}

function getPauseButton() {
  return document.getElementById("pause-toggle") || document.getElementById("pause");
}

function loadHighScore() {
  if (typeof window === "undefined" || !window.localStorage) {
    return 0;
  }

  const saved = window.localStorage.getItem(HIGH_SCORE_KEY);
  const parsed = Number.parseInt(saved ?? "0", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function saveHighScore() {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }

  window.localStorage.setItem(HIGH_SCORE_KEY, String(highScore));
}

function renderScore() {
  const scoreEl = getScoreElement();
  if (scoreEl) {
    scoreEl.textContent = `Score: ${score}`;
  }
}

function renderHighScore() {
  const highScoreEl = getHighScoreElement();
  if (highScoreEl) {
    highScoreEl.textContent = `High Score: ${highScore}`;
  }
}

function renderStatus() {
  const statusEl = getStatusElement();
  if (statusEl) {
    statusEl.textContent = isPaused ? "Status: Paused" : "Status: Running";
  }

  const pauseButton = getPauseButton();
  if (pauseButton) {
    pauseButton.textContent = isPaused ? "Resume" : "Pause";
  }
}

function syncHighScore() {
  if (score > highScore) {
    highScore = score;
    saveHighScore();
    renderHighScore();
  }
}

function collectCoin() {
  if (isPaused) {
    return score;
  }

  score += 10;
  syncHighScore();
  renderScore();
  return score;
}

function togglePause() {
  isPaused = !isPaused;
  renderStatus();
  return isPaused;
}

function initGame() {
  highScore = loadHighScore();
  renderScore();
  renderHighScore();
  renderStatus();

  const coinButton = document.getElementById("coin");
  if (coinButton) {
    coinButton.addEventListener("click", collectCoin);
  }

  const pauseButton = getPauseButton();
  if (pauseButton) {
    pauseButton.addEventListener("click", togglePause);
  }
}

initGame();

window.coinCatcher = {
  collectCoin,
  renderScore,
  renderHighScore,
  togglePause,
  loadHighScore,
};