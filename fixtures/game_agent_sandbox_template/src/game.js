let score = 0;

function renderScore() {
  const scoreEl = document.getElementById("score");
  if (scoreEl) {
    scoreEl.textContent = `Score: ${score}`;
  }
}

function collectCoin() {
  score += 1;
  renderScore();
  return score;
}

const coinButton = document.getElementById("coin");
if (coinButton) {
  coinButton.addEventListener("click", collectCoin);
}

window.coinCatcher = { collectCoin, renderScore };
