// The reading board, saved to data/board.json in the repository, shared by every
// component on the page. Cards follow the rules of reader/board.py: To read holds
// at most 10 papers, a paper is on the board once, and the start and finish dates
// match the column.
import { changeFile, getToken, loadFile } from "./github.js";

const PATH = "data/board.json";
export const TO_READ_LIMIT = 10;
export const COLUMNS = [
  ["to_read", "To read"],
  ["reading", "Reading"],
  ["done", "Done"],
];
export const COLUMN_NAMES = Object.fromEntries(COLUMNS);
const ID_KEYS = ["arxiv", "doi", "s2", "openalex"];

function emptyBoard() { return { schema_version: 1, cards: [] }; }

// --- rules (mirror reader/board.py) ------------------------------------------

function sameIds(a, b) {
  return ID_KEYS.some((k) => a[k] && b[k] && String(a[k]).toLowerCase() === String(b[k]).toLowerCase());
}

export function find(board, ids) {
  return board.cards.find((c) => sameIds(c.paper.ids, ids)) || null;
}

export function inColumn(board, column) {
  return board.cards.filter((c) => c.column === column);
}

export function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Whether `ids` may move to `column`; returns the reason it can't, or "". */
export function cannotMove(board, ids, column) {
  const card = find(board, ids);
  if (!card) return "That paper is no longer on the board.";
  if (card.column !== column && column === "to_read" && inColumn(board, "to_read").length >= TO_READ_LIMIT) {
    return `To read already holds ${TO_READ_LIMIT} papers.`;
  }
  return "";
}

export function addCard(board, payload) {
  const existing = find(board, payload.paper.ids);
  if (existing) throw new Error(`Already on the board, in ${COLUMN_NAMES[existing.column]}.`);
  if (inColumn(board, "to_read").length >= TO_READ_LIMIT) {
    throw new Error(`To read already holds ${TO_READ_LIMIT} papers. Start, finish or remove one first.`);
  }
  const card = { paper: payload.paper, column: "to_read", source: payload.source, added_on: today() };
  board.cards.push(card);
  return card;
}

export function moveCard(board, ids, column) {
  const reason = cannotMove(board, ids, column);
  if (reason) throw new Error(reason);
  const card = find(board, ids);
  if (card.column === column) return card;
  const moved = { ...card, column };
  if (column === "to_read") {
    delete moved.started_on;
    delete moved.finished_on;
  } else if (column === "reading") {
    moved.started_on = card.started_on || today();
    delete moved.finished_on;
  } else {
    moved.started_on = card.started_on || today();
    moved.finished_on = today();
  }
  board.cards = board.cards.filter((c) => c !== card);
  board.cards.push(moved);
  return moved;
}

export function removeCard(board, ids) {
  const card = find(board, ids);
  if (!card) throw new Error("That paper is no longer on the board.");
  board.cards = board.cards.filter((c) => c !== card);
  return card;
}

// --- store ---------------------------------------------------------------------

/**
 * Holds the board the page shows. It starts from the copy baked into the page at
 * build time (if any), then reads the live file from GitHub once. Components
 * listen for "change".
 */
class BoardStore extends EventTarget {
  constructor() {
    super();
    this.board = null;
    this._loading = null;
    this._queue = Promise.resolve();
    this._pending = 0;
    this._confirmed = null;
  }

  _set(board, confirmed = true) {
    this.board = board;
    if (confirmed) this._confirmed = board;
    this.dispatchEvent(new Event("change"));
  }

  /** Use the build-time board until the live one arrives. */
  seed(board) {
    if (!this.board && board) this._set(board);
  }

  /** Read the live board once per page. Failing keeps the build-time board. */
  refresh() {
    if (!this._loading) {
      this._loading = loadFile(PATH, emptyBoard())
        .then((file) => {
          if (this._pending) this._confirmed = file.data;
          else this._set(file.data);
        })
        .catch(() => {});
    }
    return this._loading;
  }

  /**
   * Change the board and save it. With `optimistic`, the page shows the change
   * straight away; if the save fails, the page reloads the board from GitHub.
   * Saves run one at a time, so quick moves in a row don't race each other.
   */
  change(apply, message, { optimistic = false } = {}) {
    if (!getToken()) return Promise.reject(new Error("NO_TOKEN"));
    if (optimistic && this.board) {
      const draft = JSON.parse(JSON.stringify(this.board));
      try {
        apply(draft); // the rules say no before anything is shown
      } catch (err) {
        return Promise.reject(err);
      }
      this._set(draft, false);
    }
    this._pending += 1;
    const run = this._queue.then(() => changeFile(PATH, emptyBoard, apply, message));
    this._queue = run.catch(() => {});
    return run.then(
      (done) => {
        this._pending -= 1;
        // A later move is still saving: keep showing it rather than this older copy.
        if (this._pending) this._confirmed = done.data;
        else this._set(done.data);
        return done.result;
      },
      (err) => {
        this._pending -= 1;
        if (!this._pending) {
          // Go back to the last saved board, then check GitHub for the truth.
          if (this._confirmed) this._set(this._confirmed);
          this._loading = null;
          this.refresh();
        }
        throw err;
      },
    );
  }
}

export const boardStore = new BoardStore();
