// The reading board page: <reading-board>, <board-column> and <board-card>.
//
// Cards move between columns by dragging (mouse, pen or touch) or with the buttons
// on each card. On touch screens, press and hold a card for a moment to pick it up,
// so a normal swipe still scrolls the page. Every move saves through boardStore,
// the same save the buttons use.
import { html, nothing } from "../../vendor/lit.js";
import { LightElement, explain, paperLink, say } from "../base.js";
import {
  COLUMNS,
  COLUMN_NAMES,
  TO_READ_LIMIT,
  boardStore,
  cannotMove,
  inColumn,
  moveCard,
  removeCard,
} from "../board-store.js";
import { currentUser } from "../firebase.js";
import { linkRow } from "./papers.js";

const DONE_SHOWN = 20;
const HOLD_MS = 300; // touch: how long to hold before a card lifts
const SLOP_PX = 8; // touch: moving further than this before the hold ends is a scroll
const MOUSE_START_PX = 5; // mouse: how far to move before a press becomes a drag
const EDGE_PX = 64; // drag this close to the window edge to scroll

const MOVES = [
  ["reading", "Start"],
  ["done", "Done"],
  ["to_read", "Back to To read"],
];

/** <board-card .card=${...}>: one paper on the board. */
export class BoardCard extends LightElement {
  static properties = { card: { type: Object } };

  _send(name, detail) {
    this.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: { ids: this.card.paper.ids, ...detail } }));
  }

  render() {
    const c = this.card;
    if (!c) return nothing;
    const when = c.finished_on ? `Finished ${c.finished_on}`
      : c.started_on ? `Started ${c.started_on}` : `Added ${c.added_on}`;
    return html`
      <a href=${paperLink(c.paper.links) || "#"} draggable="false">${c.paper.title}</a>
      <span class="meta">${when}</span>
      ${linkRow(c.paper.links, { code: false })}
      <p class="actions">
        ${MOVES.filter(([column]) => column !== c.column).map(([column, label]) => html`
          <button type="button" class="button small" @click=${() => this._send("card-move", { column })}>${label}</button>`)}
        <button type="button" class="button small quiet" @click=${() => this._send("card-remove")}>Remove</button>
      </p>`;
  }
}

/** <board-column column="reading" .cards=${[...]}>: a column and its cards. */
export class BoardColumn extends LightElement {
  static properties = { column: { reflect: true }, cards: { type: Array } };

  constructor() {
    super();
    this.cards = [];
  }

  _list(cards) {
    return html`<ul>${cards.map((card) => html`<li><board-card class="card" .card=${card}></board-card></li>`)}</ul>`;
  }

  render() {
    const cards = this.cards;
    const count = this.column === "to_read" ? `${cards.length}/${TO_READ_LIMIT}` : String(cards.length);
    const shown = this.column === "done" ? cards.slice(0, DONE_SHOWN) : cards;
    const older = this.column === "done" ? cards.slice(DONE_SHOWN) : [];
    return html`
      <section aria-label=${COLUMN_NAMES[this.column]}>
        <h2>${COLUMN_NAMES[this.column]} <small>${count}</small></h2>
        ${cards.length ? this._list(shown) : html`<p class="meta empty-column">Empty.</p>`}
        ${older.length ? html`
          <details class="older"><summary>${older.length} older</summary>${this._list(older)}</details>` : nothing}
      </section>`;
  }
}

/** <reading-board board='{...}'>: the three columns, with drag and drop. */
export class ReadingBoard extends LightElement {
  static properties = { board: { type: Object }, _board: { state: true } };

  constructor() {
    super();
    this._board = null;
    this._press = null; // a pointer that is down on a card
    this._drag = null; // the card being dragged
    this._quietClicksUntil = 0;
    this._onStore = () => { this._board = boardStore.board; };
    this._onDown = this._onDown.bind(this);
    this._onMove = this._onMove.bind(this);
    this._onUp = this._onUp.bind(this);
    this._onCancel = () => this._finish();
    this._onKey = (e) => { if (e.key === "Escape" && this._drag) this._finish(); };
    this._tick = this._tick.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    boardStore.addEventListener("change", this._onStore);
    boardStore.seed(this.board);
    this._board = boardStore.board;
    boardStore.refresh();
    currentUser().then((user) => {
      if (!user) say("Your board is private. To see and change it, sign in on the ", { settingsLink: true });
    });

    this.addEventListener("card-move", (e) => this._move(e.detail.ids, e.detail.column));
    this.addEventListener("card-remove", (e) => this._remove(e.detail.ids));
    this.addEventListener("pointerdown", this._onDown);
    // Stop the page scrolling under a lifted card. This has to be a non-passive
    // touchmove listener for iOS Safari to honour preventDefault.
    this.addEventListener("touchmove", (e) => { if (this._drag) e.preventDefault(); }, { passive: false });
    // A long press would otherwise open the link menu or select text.
    this.addEventListener("contextmenu", (e) => { if (this._press || this._drag) e.preventDefault(); });
    this.addEventListener("dragstart", (e) => e.preventDefault());
    // The click that ends a drag shouldn't also open the card's link.
    this.addEventListener("click", (e) => {
      if (Date.now() < this._quietClicksUntil) { e.preventDefault(); e.stopPropagation(); }
    }, true);
  }

  disconnectedCallback() {
    boardStore.removeEventListener("change", this._onStore);
    this._finish();
    super.disconnectedCallback();
  }

  // --- saving --------------------------------------------------------------------

  async _move(ids, column) {
    const reason = cannotMove(this._board, ids, column);
    if (reason) { say(reason, { error: true }); return; }
    say("Saving…");
    try {
      await boardStore.change((b) => moveCard(b, ids, column), `Reading board: move to ${column}`, { optimistic: true });
      say(`Moved to ${COLUMN_NAMES[column]}.`);
    } catch (err) {
      explain(err);
    }
  }

  async _remove(ids) {
    say("Saving…");
    try {
      await boardStore.change((b) => removeCard(b, ids), "Reading board: remove", { optimistic: true });
      say("Removed from the board.");
    } catch (err) {
      explain(err);
    }
  }

  // --- dragging --------------------------------------------------------------------

  _onDown(e) {
    if (this._press || this._drag || !e.isPrimary) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const card = e.target.closest("board-card");
    if (!card || e.target.closest("button, summary, input, select, textarea")) return;
    this._press = { card, id: e.pointerId, type: e.pointerType, x: e.clientX, y: e.clientY, timer: 0 };
    if (e.pointerType !== "mouse") {
      card.classList.add("pressing");
      this._press.timer = setTimeout(() => this._lift(), HOLD_MS);
    }
    window.addEventListener("pointermove", this._onMove, { passive: false });
    window.addEventListener("pointerup", this._onUp);
    window.addEventListener("pointercancel", this._onCancel);
    window.addEventListener("keydown", this._onKey);
  }

  _onMove(e) {
    const press = this._press;
    if (!press || e.pointerId !== press.id) return;
    if (!this._drag) {
      const dist = Math.hypot(e.clientX - press.x, e.clientY - press.y);
      if (press.type === "mouse") {
        if (dist < MOUSE_START_PX) return;
        this._lift();
      } else {
        if (dist > SLOP_PX) this._finish(); // a swipe: let the page scroll
        return;
      }
    }
    e.preventDefault();
    this._drag.x = e.clientX;
    this._drag.y = e.clientY;
    this._place();
  }

  _onUp(e) {
    if (!this._press || e.pointerId !== this._press.id) return;
    const drag = this._drag;
    const target = drag && drag.over;
    this._finish();
    if (!drag) return;
    this._quietClicksUntil = Date.now() + 400;
    if (target && target.column !== drag.from) this._move(drag.ids, target.column);
  }

  _lift() {
    const press = this._press;
    if (!press || this._drag) return;
    const card = press.card;
    card.classList.remove("pressing");
    const rect = card.getBoundingClientRect();
    const ghost = document.createElement("div");
    ghost.className = "card drag-ghost";
    ghost.setAttribute("aria-hidden", "true");
    ghost.innerHTML = card.innerHTML;
    ghost.style.width = `${rect.width}px`;
    document.body.appendChild(ghost);
    this._drag = {
      card,
      ghost,
      ids: card.card.paper.ids,
      from: card.card.column,
      dx: press.x - rect.left,
      dy: press.y - rect.top,
      x: press.x,
      y: press.y,
      over: null,
    };
    card.classList.add("dragging");
    this.classList.add("is-dragging");
    if (press.type !== "mouse" && navigator.vibrate) navigator.vibrate(10);
    this._place();
    this._raf = requestAnimationFrame(this._tick);
  }

  /** Move the lifted card under the pointer and light up the column below it. */
  _place() {
    const d = this._drag;
    d.ghost.style.transform = `translate(${d.x - d.dx}px, ${d.y - d.dy}px)`;
    const under = document.elementFromPoint(d.x, d.y);
    const column = under && under.closest("board-column");
    const over = column && this.contains(column) ? column : null;
    if (over === d.over) return;
    if (d.over) d.over.classList.remove("drop-ok", "drop-blocked");
    d.over = over;
    if (over && over.column !== d.from) {
      over.classList.add(cannotMove(this._board, d.ids, over.column) ? "drop-blocked" : "drop-ok");
    }
  }

  /** Scroll the window while the card is held near its top or bottom edge. */
  _tick() {
    const d = this._drag;
    if (!d) return;
    let step = 0;
    if (d.y < EDGE_PX) step = -Math.ceil((EDGE_PX - d.y) / 4);
    else if (d.y > window.innerHeight - EDGE_PX) step = Math.ceil((d.y - (window.innerHeight - EDGE_PX)) / 4);
    if (step) {
      window.scrollBy(0, step);
      this._place();
    }
    this._raf = requestAnimationFrame(this._tick);
  }

  _finish() {
    const press = this._press;
    if (press) {
      clearTimeout(press.timer);
      press.card.classList.remove("pressing");
    }
    const d = this._drag;
    if (d) {
      cancelAnimationFrame(this._raf);
      d.ghost.remove();
      d.card.classList.remove("dragging");
      if (d.over) d.over.classList.remove("drop-ok", "drop-blocked");
      this.classList.remove("is-dragging");
    }
    this._press = null;
    this._drag = null;
    window.removeEventListener("pointermove", this._onMove);
    window.removeEventListener("pointerup", this._onUp);
    window.removeEventListener("pointercancel", this._onCancel);
    window.removeEventListener("keydown", this._onKey);
  }

  // --- drawing --------------------------------------------------------------------

  render() {
    const board = this._board || { cards: [] };
    return html`${COLUMNS.map(([column]) => {
      let cards = inColumn(board, column);
      if (column === "done") {
        cards = [...cards].sort((a, b) => (b.finished_on || "").localeCompare(a.finished_on || ""));
      }
      return html`<board-column column=${column} .cards=${cards}></board-column>`;
    })}`;
  }
}

customElements.define("board-card", BoardCard);
customElements.define("board-column", BoardColumn);
customElements.define("reading-board", ReadingBoard);
