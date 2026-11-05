// The reading board, saved in Firestore (boards/main) and shared by every
// component on the page. Only a signed-in owner can read or change it. Cards follow
// the rules of reader/board.py: To read holds at most 10 papers, a paper is on the
// board once, and the start and finish dates match the column.
import type { Board, BoardButtonData, BoardCard, ColumnId, PaperIds } from "./types";
import { today } from "./base";
import { changeBoard, currentUser, onUser, readBoard } from "./firebase";

export const TO_READ_LIMIT = 10;
export const COLUMNS: [ColumnId, string][] = [
	["to_read", "To read"],
	["reading", "Reading"],
	["done", "Done"],
];
export const COLUMN_NAMES = Object.fromEntries(COLUMNS) as Record<ColumnId, string>;
const ID_KEYS = ["arxiv", "doi", "s2", "openalex"] as const;

function emptyBoard(): Board {
	return { schema_version: 1, cards: [] };
}

// --- rules (mirror reader/board.py) ------------------------------------------

function sameIds(a: PaperIds, b: PaperIds): boolean {
	return ID_KEYS.some((k) => a[k] && b[k] && String(a[k]).toLowerCase() === String(b[k]).toLowerCase());
}

export function find(board: Board | null, ids: PaperIds): BoardCard | null {
	return board?.cards.find((c) => sameIds(c.paper.ids, ids)) ?? null;
}

export function inColumn(board: Board, column: ColumnId): BoardCard[] {
	return board.cards.filter((c) => c.column === column);
}

/** Whether `ids` may move to `column`; returns the reason it can't, or "". */
export function cannotMove(board: Board | null, ids: PaperIds, column: ColumnId): string {
	const card = board && find(board, ids);
	if (!board || !card) return "That paper is no longer on the board.";
	if (card.column !== column && column === "to_read" && inColumn(board, "to_read").length >= TO_READ_LIMIT) {
		return `To read already holds ${TO_READ_LIMIT} papers.`;
	}
	return "";
}

export function addCard(board: Board, payload: BoardButtonData["payload"]): BoardCard {
	const existing = find(board, payload.paper.ids);
	if (existing) throw new Error(`Already on the board, in ${COLUMN_NAMES[existing.column]}.`);
	if (inColumn(board, "to_read").length >= TO_READ_LIMIT) {
		throw new Error(`To read already holds ${TO_READ_LIMIT} papers. Start, finish or remove one first.`);
	}
	const card: BoardCard = { paper: payload.paper, column: "to_read", source: payload.source, added_on: today() };
	board.cards.push(card);
	return card;
}

export function moveCard(board: Board, ids: PaperIds, column: ColumnId): BoardCard {
	const reason = cannotMove(board, ids, column);
	if (reason) throw new Error(reason);
	const card = find(board, ids)!;
	if (card.column === column) return card;
	const moved: BoardCard = { ...card, column };
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

export function removeCard(board: Board, ids: PaperIds): BoardCard {
	const card = find(board, ids);
	if (!card) throw new Error("That paper is no longer on the board.");
	board.cards = board.cards.filter((c) => c !== card);
	return card;
}

// --- store ---------------------------------------------------------------------

/**
 * Holds the board the page shows. It starts from the copy baked into the page at
 * build time (empty once the board lives in Firestore), then reads the live board
 * once you're signed in. Components listen for "change".
 */
class BoardStore extends EventTarget {
	board: Board | null = null;
	signedIn = false;
	private loading: Promise<void> | null = null;
	private queue: Promise<unknown> = Promise.resolve();
	private pending = 0;
	private confirmed: Board | null = null;
	private watching = false;

	private watch(): void {
		if (this.watching) return;
		this.watching = true;
		onUser((user) => {
			const signedIn = !!user;
			if (signedIn === this.signedIn) return;
			this.signedIn = signedIn;
			this.loading = null;
			if (!signedIn) this.set(emptyBoard());
			else this.refresh();
		});
	}

	private set(board: Board, confirmed = true): void {
		this.board = board;
		if (confirmed) this.confirmed = board;
		this.dispatchEvent(new Event("change"));
	}

	/** Use the build-time board until the live one arrives. */
	seed(board: Board | null | undefined): void {
		if (!this.board && board) this.set(board);
	}

	/** Read the live board once per page (and again after signing in). */
	refresh(): Promise<void> {
		this.watch();
		if (!this.loading) {
			this.loading = currentUser()
				.then((user) => (user ? readBoard(emptyBoard()) : null))
				.then((data) => {
					if (!data) return;
					if (this.pending) this.confirmed = data;
					else this.set(data);
				})
				.catch(() => {});
		}
		return this.loading;
	}

	/**
	 * Change the board and save it. With `optimistic`, the page shows the change
	 * straight away; if the save fails, the page reloads the board from Firestore.
	 * Saves run one at a time, so quick moves in a row don't race each other.
	 */
	async change<R>(apply: (board: Board) => R, { optimistic = false } = {}): Promise<R> {
		if (!this.signedIn) {
			// A saved sign-in may still be loading: wait for it before saying no.
			const user = await currentUser();
			if (!user) throw new Error("NO_SIGNIN");
			this.signedIn = true;
		}
		if (optimistic && this.board) {
			const draft = JSON.parse(JSON.stringify(this.board)) as Board;
			apply(draft); // the rules say no before anything is shown
			this.set(draft, false);
		}
		this.pending += 1;
		const run = this.queue.then(() => changeBoard(emptyBoard, apply));
		this.queue = run.catch(() => {});
		try {
			const done = await run;
			this.pending -= 1;
			// A later move is still saving: keep showing it rather than this older copy.
			if (this.pending) this.confirmed = done.data;
			else this.set(done.data);
			return done.result;
		} catch (err) {
			this.pending -= 1;
			if (!this.pending) {
				// Go back to the last saved board, then check Firestore for the truth.
				if (this.confirmed) this.set(this.confirmed);
				this.loading = null;
				this.refresh();
			}
			throw err;
		}
	}
}

export const boardStore = new BoardStore();
