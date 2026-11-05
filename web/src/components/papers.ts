// A day's picks: <paper-card>, its links, its prerequisites and <board-button>.
import { html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { LightElement, explain, say } from "../lib/base";
import { COLUMN_NAMES, addCard, boardStore, find } from "../lib/board-store";
import type { BoardButtonData, ColumnId, Links, PaperIds, Pick, Prerequisite } from "../lib/types";

const PREREQ_LABELS: Record<Prerequisite["label"], string> = {
	read_first: "Read first",
	foundation: "Foundation",
	background: "Background",
};

/** The row of Abstract page / PDF / DOI / Code links. */
export function linkRow(links: Links | undefined, { code = true } = {}) {
	const l = links ?? {};
	const items = (
		[
			["abstract_page", "Abstract page"],
			["pdf", "PDF"],
			["doi", "DOI"],
			...(code ? [["code", "Code"]] : []),
		] as [keyof Links, string][]
	).filter(([key]) => l[key]);
	return html`<ul class="links">
		${items.map(([key, label]) => html`<li><a href=${l[key]!}>${label}</a></li>`)}
	</ul>`;
}

/**
 * <board-button .ids=${...} .payload=${...} column="to_read">: adds a paper to
 * To read, or says which column it is already in.
 */
@customElement("board-button")
export class BoardButton extends LightElement {
	@property({ type: Object }) ids: PaperIds = {};
	@property({ type: Object }) payload: BoardButtonData["payload"] | null = null;
	@property() column: ColumnId | "" = "";
	@state() private busy = false;

	private onChange = () => {
		const card = find(boardStore.board, this.ids);
		this.column = card ? card.column : "";
	};

	override connectedCallback() {
		super.connectedCallback();
		boardStore.addEventListener("change", this.onChange);
		if (boardStore.board) this.onChange();
		boardStore.refresh();
	}

	override disconnectedCallback() {
		boardStore.removeEventListener("change", this.onChange);
		super.disconnectedCallback();
	}

	private async add() {
		const payload = this.payload;
		if (!payload) return;
		this.busy = true;
		say("Saving…");
		try {
			await boardStore.change((board) => addCard(board, payload));
			say(`Added to To read: ${payload.paper.title}`);
		} catch (err) {
			explain(err);
		} finally {
			this.busy = false;
		}
	}

	override render() {
		if (this.column) {
			return html`<p class="on-board">On your reading board: <span>${COLUMN_NAMES[this.column]}</span></p>`;
		}
		return html`<button type="button" class="button" ?disabled=${this.busy} @click=${this.add}>
			Add to reading list
		</button>`;
	}
}

function boardButton(b: BoardButtonData) {
	return html`<board-button .ids=${b.ids} .payload=${b.payload} column=${b.column}></board-button>`;
}

function prereqItem(pre: Prerequisite) {
	return html`
		<li>
			<span class="badge">${PREREQ_LABELS[pre.label]}</span>
			<a href=${pre.href}>${pre.title}</a>
			<span class="meta">${[pre.year, pre.venue].filter(Boolean).join(" · ")}</span>
			${pre.reasons.length ? html`<p class="meta">${pre.reasons.join("; ")}</p>` : nothing}
			${pre.quote ? html`<blockquote>${pre.quote}</blockquote>` : nothing}
			${pre.tldr ? html`<p class="tldr">${pre.tldr}</p>` : nothing} ${boardButton(pre.board)}
		</li>
	`;
}

/** <paper-card pick='{...}'>: one of the day's picks. */
@customElement("paper-card")
export class PaperCard extends LightElement {
	@property({ type: Object }) pick: Pick | null = null;

	override render() {
		const p = this.pick;
		if (!p) return nothing;
		const status = p.status === "published" ? "Published" : p.status === "accepted" ? "Accepted" : "";
		return html`
			<article class="pick" id="pick-${p.rank}">
				<p class="badges">
					<span class="badge">${p.topic_label}</span>
					<span class="badge">${p.funnel === "waves" ? "Making waves" : "New"}</span>
					${status
						? html`<span class="badge status">${status}${p.venue ? ` · ${p.venue}` : ""}</span>`
						: html`<span class="badge">Preprint</span>`}
					${p.repick ? html`<span class="badge">Picked before</span>` : nothing}
				</p>
				<h2><a href=${p.href}>${p.title}</a></h2>
				${p.authors ? html`<p class="authors">${p.authors}</p>` : nothing}
				<p class="reason">${p.reason}</p>
				${p.tldr ? html`<p class="tldr"><strong>TL;DR</strong> ${p.tldr}</p>` : nothing}
				${p.abstract
					? html`
							<details class="abstract">
								<summary><span class="clamp">${p.abstract}</span><span class="more">Show full abstract</span></summary>
							</details>
						`
					: nothing}
				${linkRow(p.links)} ${boardButton(p.board)}
				${p.graph
					? html`<p class="graph-link"><a href=${p.graph.href}>See connected papers (${p.graph.count})</a></p>`
					: nothing}
				${p.prerequisites.length
					? html`
							<details class="prereqs">
								<summary>Read before this (${p.prerequisites.length})</summary>
								<ul>
									${p.prerequisites.map(prereqItem)}
								</ul>
							</details>
						`
					: nothing}
			</article>
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"board-button": BoardButton;
		"paper-card": PaperCard;
	}
}
