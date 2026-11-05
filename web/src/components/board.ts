// The reading board page: <reading-board>, <board-column> and <board-card>.
//
// Cards move between columns by dragging (mouse, pen or touch) or with the buttons
// on each card. On touch screens, press and hold a card for a moment to pick it up,
// so a normal swipe still scrolls the page. Every move saves through boardStore,
// the same save the buttons use.
import { html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { LightElement, explain, paperLink, say } from "../lib/base";
import {
	COLUMNS,
	COLUMN_NAMES,
	TO_READ_LIMIT,
	boardStore,
	cannotMove,
	inColumn,
	moveCard,
	removeCard,
} from "../lib/board-store";
import { currentUser } from "../lib/firebase";
import type { Board, BoardCard as Card, ColumnId, PaperIds } from "../lib/types";
import { linkRow } from "./papers";

const DONE_SHOWN = 20;
const HOLD_MS = 300; // touch: how long to hold before a card lifts
const SLOP_PX = 8; // touch: moving further than this before the hold ends is a scroll
const MOUSE_START_PX = 5; // mouse: how far to move before a press becomes a drag
const EDGE_PX = 64; // drag this close to the window edge to scroll

const MOVES: [ColumnId, string][] = [
	["reading", "Start"],
	["done", "Done"],
	["to_read", "Back to To read"],
];

interface CardEventDetail {
	ids: PaperIds;
	column?: ColumnId;
}

declare global {
	interface HTMLElementEventMap {
		"card-move": CustomEvent<CardEventDetail>;
		"card-remove": CustomEvent<CardEventDetail>;
	}
}

/** <board-card .card=${...}>: one paper on the board. */
@customElement("board-card")
export class BoardCard extends LightElement {
	@property({ type: Object }) card: Card | null = null;

	private send(name: "card-move" | "card-remove", detail: { column?: ColumnId } = {}) {
		if (!this.card) return;
		this.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: { ids: this.card.paper.ids, ...detail } }));
	}

	override render() {
		const c = this.card;
		if (!c) return nothing;
		const when = c.finished_on
			? `Finished ${c.finished_on}`
			: c.started_on
				? `Started ${c.started_on}`
				: `Added ${c.added_on}`;
		return html`
			<a href=${paperLink(c.paper.links) || "#"} draggable="false">${c.paper.title}</a>
			<span class="meta">${when}</span>
			${linkRow(c.paper.links, { code: false })}
			<p class="actions">
				${MOVES.filter(([column]) => column !== c.column).map(
					([column, label]) =>
						html`<button type="button" class="button small" @click=${() => this.send("card-move", { column })}>
							${label}
						</button>`,
				)}
				<button type="button" class="button small quiet" @click=${() => this.send("card-remove")}>Remove</button>
			</p>
		`;
	}
}

/** <board-column column="reading" .cards=${[...]}>: a column and its cards. */
@customElement("board-column")
export class BoardColumn extends LightElement {
	@property({ reflect: true }) column: ColumnId = "to_read";
	@property({ type: Array }) cards: Card[] = [];

	private list(cards: Card[]) {
		return html`<ul>
			${cards.map((card) => html`<li><board-card class="card" .card=${card}></board-card></li>`)}
		</ul>`;
	}

	override render() {
		const cards = this.cards;
		const count = this.column === "to_read" ? `${cards.length}/${TO_READ_LIMIT}` : String(cards.length);
		const shown = this.column === "done" ? cards.slice(0, DONE_SHOWN) : cards;
		const older = this.column === "done" ? cards.slice(DONE_SHOWN) : [];
		return html`
			<section aria-label=${COLUMN_NAMES[this.column]}>
				<h2>${COLUMN_NAMES[this.column]} <small>${count}</small></h2>
				${cards.length ? this.list(shown) : html`<p class="meta empty-column">Empty.</p>`}
				${older.length
					? html`<details class="older">
							<summary>${older.length} older</summary>
							${this.list(older)}
						</details>`
					: nothing}
			</section>
		`;
	}
}

interface Press {
	card: BoardCard;
	id: number;
	type: string;
	x: number;
	y: number;
	timer: number;
}

interface Drag {
	card: BoardCard;
	ghost: HTMLElement;
	ids: PaperIds;
	from: ColumnId;
	dx: number;
	dy: number;
	x: number;
	y: number;
	over: BoardColumn | null;
}

/** <reading-board board='{...}'>: the three columns, with drag and drop. */
@customElement("reading-board")
export class ReadingBoard extends LightElement {
	/** The board baked into the page at build time. */
	@property({ type: Object }) board: Board | null = null;
	@state() private live: Board | null = null;

	private press: Press | null = null; // a pointer that is down on a card
	private drag: Drag | null = null; // the card being dragged
	private quietClicksUntil = 0;
	private raf = 0;

	private onStore = () => {
		this.live = boardStore.board;
	};

	override connectedCallback() {
		super.connectedCallback();
		boardStore.addEventListener("change", this.onStore);
		boardStore.seed(this.board);
		this.live = boardStore.board;
		boardStore.refresh();
		currentUser().then((user) => {
			if (!user) say("Your board is private. To see and change it, sign in on the ", { settingsLink: true });
		});

		this.addEventListener("card-move", (e) => this.move(e.detail.ids, e.detail.column!));
		this.addEventListener("card-remove", (e) => this.removeFromBoard(e.detail.ids));
		this.addEventListener("pointerdown", this.onDown);
		// Stop the page scrolling under a lifted card. This has to be a non-passive
		// touchmove listener for iOS Safari to honour preventDefault.
		this.addEventListener(
			"touchmove",
			(e) => {
				if (this.drag) e.preventDefault();
			},
			{ passive: false },
		);
		// A long press would otherwise open the link menu or select text.
		this.addEventListener("contextmenu", (e) => {
			if (this.press || this.drag) e.preventDefault();
		});
		this.addEventListener("dragstart", (e) => e.preventDefault());
		// The click that ends a drag shouldn't also open the card's link.
		this.addEventListener(
			"click",
			(e) => {
				if (Date.now() < this.quietClicksUntil) {
					e.preventDefault();
					e.stopPropagation();
				}
			},
			true,
		);
	}

	override disconnectedCallback() {
		boardStore.removeEventListener("change", this.onStore);
		this.finish();
		super.disconnectedCallback();
	}

	// --- saving --------------------------------------------------------------------

	private async move(ids: PaperIds, column: ColumnId) {
		const reason = cannotMove(this.live, ids, column);
		if (reason) {
			say(reason, { error: true });
			return;
		}
		say("Saving…");
		try {
			await boardStore.change((b) => moveCard(b, ids, column), { optimistic: true });
			say(`Moved to ${COLUMN_NAMES[column]}.`);
		} catch (err) {
			explain(err);
		}
	}

	private async removeFromBoard(ids: PaperIds) {
		say("Saving…");
		try {
			await boardStore.change((b) => removeCard(b, ids), { optimistic: true });
			say("Removed from the board.");
		} catch (err) {
			explain(err);
		}
	}

	// --- dragging --------------------------------------------------------------------

	private onDown = (e: PointerEvent) => {
		if (this.press || this.drag || !e.isPrimary) return;
		if (e.pointerType === "mouse" && e.button !== 0) return;
		const target = e.target as Element;
		const card = target.closest("board-card");
		if (!card || target.closest("button, summary, input, select, textarea")) return;
		this.press = { card, id: e.pointerId, type: e.pointerType, x: e.clientX, y: e.clientY, timer: 0 };
		if (e.pointerType !== "mouse") {
			card.classList.add("pressing");
			this.press.timer = window.setTimeout(() => this.lift(), HOLD_MS);
		}
		window.addEventListener("pointermove", this.onMove, { passive: false });
		window.addEventListener("pointerup", this.onUp);
		window.addEventListener("pointercancel", this.onCancel);
		window.addEventListener("keydown", this.onKey);
	};

	private onMove = (e: PointerEvent) => {
		const press = this.press;
		if (!press || e.pointerId !== press.id) return;
		if (!this.drag) {
			const dist = Math.hypot(e.clientX - press.x, e.clientY - press.y);
			if (press.type === "mouse") {
				if (dist < MOUSE_START_PX) return;
				this.lift();
			} else {
				if (dist > SLOP_PX) this.finish(); // a swipe: let the page scroll
				return;
			}
		}
		if (!this.drag) return;
		e.preventDefault();
		this.drag.x = e.clientX;
		this.drag.y = e.clientY;
		this.place();
	};

	private onUp = (e: PointerEvent) => {
		if (!this.press || e.pointerId !== this.press.id) return;
		const drag = this.drag;
		const target = drag?.over;
		this.finish();
		if (!drag) return;
		this.quietClicksUntil = Date.now() + 400;
		if (target && target.column !== drag.from) this.move(drag.ids, target.column);
	};

	private onCancel = () => this.finish();

	private onKey = (e: KeyboardEvent) => {
		if (e.key === "Escape" && this.drag) this.finish();
	};

	private lift() {
		const press = this.press;
		if (!press || this.drag || !press.card.card) return;
		const card = press.card;
		card.classList.remove("pressing");
		const rect = card.getBoundingClientRect();
		const ghost = document.createElement("div");
		ghost.className = "card drag-ghost";
		ghost.setAttribute("aria-hidden", "true");
		ghost.innerHTML = card.innerHTML;
		ghost.style.width = `${rect.width}px`;
		document.body.appendChild(ghost);
		this.drag = {
			card,
			ghost,
			ids: press.card.card.paper.ids,
			from: press.card.card.column,
			dx: press.x - rect.left,
			dy: press.y - rect.top,
			x: press.x,
			y: press.y,
			over: null,
		};
		card.classList.add("dragging");
		this.classList.add("is-dragging");
		if (press.type !== "mouse" && navigator.vibrate) navigator.vibrate(10);
		this.place();
		this.raf = requestAnimationFrame(this.tick);
	}

	/** Move the lifted card under the pointer and light up the column below it. */
	private place() {
		const d = this.drag;
		if (!d) return;
		d.ghost.style.transform = `translate(${d.x - d.dx}px, ${d.y - d.dy}px)`;
		const under = document.elementFromPoint(d.x, d.y);
		const column = under?.closest("board-column") ?? null;
		const over = column && this.contains(column) ? column : null;
		if (over === d.over) return;
		d.over?.classList.remove("drop-ok", "drop-blocked");
		d.over = over;
		if (over && over.column !== d.from) {
			over.classList.add(cannotMove(this.live, d.ids, over.column) ? "drop-blocked" : "drop-ok");
		}
	}

	/** Scroll the window while the card is held near its top or bottom edge. */
	private tick = () => {
		const d = this.drag;
		if (!d) return;
		let step = 0;
		if (d.y < EDGE_PX) step = -Math.ceil((EDGE_PX - d.y) / 4);
		else if (d.y > window.innerHeight - EDGE_PX) step = Math.ceil((d.y - (window.innerHeight - EDGE_PX)) / 4);
		if (step) {
			window.scrollBy(0, step);
			this.place();
		}
		this.raf = requestAnimationFrame(this.tick);
	};

	private finish() {
		const press = this.press;
		if (press) {
			clearTimeout(press.timer);
			press.card.classList.remove("pressing");
		}
		const d = this.drag;
		if (d) {
			cancelAnimationFrame(this.raf);
			d.ghost.remove();
			d.card.classList.remove("dragging");
			d.over?.classList.remove("drop-ok", "drop-blocked");
			this.classList.remove("is-dragging");
		}
		this.press = null;
		this.drag = null;
		window.removeEventListener("pointermove", this.onMove);
		window.removeEventListener("pointerup", this.onUp);
		window.removeEventListener("pointercancel", this.onCancel);
		window.removeEventListener("keydown", this.onKey);
	}

	// --- drawing --------------------------------------------------------------------

	override render() {
		const board = this.live ?? { schema_version: 1, cards: [] };
		return html`${COLUMNS.map(([column]) => {
			let cards = inColumn(board, column);
			if (column === "done") {
				cards = [...cards].sort((a, b) => (b.finished_on || "").localeCompare(a.finished_on || ""));
			}
			return html`<board-column column=${column} .cards=${cards}></board-column>`;
		})}`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"board-card": BoardCard;
		"board-column": BoardColumn;
		"reading-board": ReadingBoard;
	}
}
