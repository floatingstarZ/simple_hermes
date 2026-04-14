const fs = require('fs');
const path = require('path');
const vm = require('vm');

const projectRoot = __dirname ? path.resolve(__dirname, '..') : process.cwd();
const gamePath = path.join(projectRoot, 'src', 'game.js');
const source = fs.readFileSync(gamePath, 'utf8');

const elements = new Map();

function makeElement(id) {
  return {
    id,
    textContent: '',
    listeners: {},
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
  };
}

['score', 'high-score', 'status', 'coin', 'pause-toggle'].forEach((id) => {
  elements.set(id, makeElement(id));
});

const localStorageStore = new Map([['coinCatcherHighScore', '20']]);
const localStorage = {
  getItem(key) {
    return localStorageStore.has(key) ? localStorageStore.get(key) : null;
  },
  setItem(key, value) {
    localStorageStore.set(key, String(value));
  },
};

const document = {
  getElementById(id) {
    return elements.get(id) || null;
  },
};

const windowObject = { localStorage };
const context = {
  window: windowObject,
  document,
  console,
  setTimeout,
  clearTimeout,
};
windowObject.document = document;
windowObject.window = windowObject;

vm.createContext(context);
vm.runInContext(source, context, { filename: 'src/game.js' });

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(elements.get('high-score').textContent === 'High Score: 20', 'should render saved high score on init');
assert(elements.get('status').textContent === 'Status: Running', 'should render running status on init');
assert(elements.get('pause-toggle').textContent === 'Pause', 'should label pause button on init');

const api = windowObject.coinCatcher;
assert(api, 'window.coinCatcher should be exposed');

api.togglePause();
assert(elements.get('status').textContent === 'Status: Paused', 'should render paused status after toggle');
assert(elements.get('pause-toggle').textContent === 'Resume', 'should relabel button to Resume when paused');

const pausedScore = api.collectCoin();
assert(pausedScore === 0, 'collecting while paused should not increase score');
assert(elements.get('score').textContent === 'Score: 0', 'score display should remain unchanged while paused');

api.togglePause();
assert(elements.get('status').textContent === 'Status: Running', 'should resume running status after second toggle');
assert(elements.get('pause-toggle').textContent === 'Pause', 'should relabel button back to Pause when resumed');

const runningScore = api.collectCoin();
assert(runningScore === 10, 'collecting while running should add ten points');
assert(elements.get('score').textContent === 'Score: 10', 'score display should update after collecting');
assert(elements.get('high-score').textContent === 'High Score: 20', 'high score should remain saved value when score is lower');

api.collectCoin();
api.collectCoin();
assert(elements.get('high-score').textContent === 'High Score: 30', 'high score should update when surpassed');
assert(localStorageStore.get('coinCatcherHighScore') === '30', 'high score should persist to localStorage');

console.log('verify_game_features.js: ok');