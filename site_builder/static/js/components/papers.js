// A day's picks: <paper-card>, its links, its prerequisites and <board-button>.
import { html, nothing } from "../../vendor/lit.js";
import { LightElement, explain, say } from "../base.js";
import { COLUMN_NAMES, addCard, boardStore, find } from "../board-store.js";

const PREREQ_LABELS = { read_first: "Read first", foundation: "Foundation", background: "Background" };

/** The row of Abstract page / PDF / DOI / Code links. */
export function linkRow(links, { code = true } = {}) {
  links = links || {};
  const items = [
    ["abstract_page", "Abstract page"],
    ["pdf", "PDF"],
    ["doi", "DOI"],
    ...(code ? [["code", "Code"]] : []),
  ].filter(([key]) => links[key]);
  return html`<ul class="links">${items.map(([key, label]) => html`<li><a href=${links[key]}>${label}</a></li>`)}</ul>`;
}

/**
 * <board-button .ids=${...} .payload=${...} column="to_read">: adds a paper to
 * To read, or says which column it is already in.
 */
export class BoardButton extends LightElement {
  static properties = {
    ids: { type: Object },
    payload: { type: Object },
    column: {},
    _busy: { state: true },
  };

  constructor() {
    super();
    this.column = "";
    this._busy = false;
    this._onChange = () => {
      const card = find(boardStore.board, this.ids);
      this.column = card ? card.column : "";
    };
  }

  connectedCallback() {
    super.connectedCallback();
    boardStore.addEventListener("change", this._onChange);
    if (boardStore.board) this._onChange();
    boardStore.refresh();
  }

  disconnectedCallback() {
    boardStore.removeEventListener("change", this._onChange);
    super.disconnectedCallback();
  }

  async _add() {
    this._busy = true;
    say("Saving…");
    try {
      await boardStore.change((board) => addCard(board, this.payload), `Reading board: add ${this.payload.paper.id}`);
      say(`Added to To read: ${this.payload.paper.title}`);
    } catch (err) {
      explain(err);
    } finally {
      this._busy = false;
    }
  }

  render() {
    if (this.column) {
      return html`<p class="on-board">On your reading board: <span>${COLUMN_NAMES[this.column]}</span></p>`;
    }
    return html`<button type="button" class="button" ?disabled=${this._busy} @click=${this._add}>Add to reading list</button>`;
  }
}

function prereqItem(pre) {
  return html`
    <li>
      <span class="badge">${PREREQ_LABELS[pre.label]}</span>
      <a href=${pre.href}>${pre.title}</a>
      <span class="meta">${[pre.year, pre.venue].filter(Boolean).join(" · ")}</span>
      ${pre.reasons.length ? html`<p class="meta">${pre.reasons.join("; ")}</p>` : nothing}
      ${pre.quote ? html`<blockquote>${pre.quote}</blockquote>` : nothing}
      ${pre.tldr ? html`<p class="tldr">${pre.tldr}</p>` : nothing}
      <board-button .ids=${pre.board.ids} .payload=${pre.board.payload} column=${pre.board.column}></board-button>
    </li>`;
}

/** <paper-card pick='{...}'>: one of the day's picks. */
export class PaperCard extends LightElement {
  static properties = { pick: { type: Object } };

  render() {
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
        ${p.abstract ? html`
          <details class="abstract">
            <summary><span class="clamp">${p.abstract}</span><span class="more">Show full abstract</span></summary>
          </details>` : nothing}
        ${linkRow(p.links)}
        <board-button .ids=${p.board.ids} .payload=${p.board.payload} column=${p.board.column}></board-button>
        ${p.graph ? html`<p class="graph-link"><a href=${p.graph.href}>See connected papers (${p.graph.count})</a></p>` : nothing}
        ${p.prerequisites.length ? html`
          <details class="prereqs">
            <summary>Read before this (${p.prerequisites.length})</summary>
            <ul>${p.prerequisites.map(prereqItem)}</ul>
          </details>` : nothing}
      </article>`;
  }
}

customElements.define("board-button", BoardButton);
customElements.define("paper-card", PaperCard);

