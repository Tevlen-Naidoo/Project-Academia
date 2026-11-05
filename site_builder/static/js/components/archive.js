// <archive-list days='[...]' topics='[...]'>: every past day, filtered by topic,
// kind and title words.
import { html, nothing } from "../../vendor/lit.js";
import { LightElement } from "../base.js";

export class ArchiveList extends LightElement {
  static properties = {
    days: { type: Array },
    topics: { type: Array },
    _topic: { state: true },
    _funnel: { state: true },
    _words: { state: true },
  };

  constructor() {
    super();
    this.days = [];
    this.topics = [];
    this._topic = "";
    this._funnel = "";
    this._words = [];
  }

  _onInput(e) {
    const form = e.currentTarget;
    this._topic = form.topic.value;
    this._funnel = form.funnel.value;
    this._words = form.q.value.toLowerCase().split(/\s+/).filter(Boolean);
  }

  _keep(pick) {
    const title = pick.title.toLowerCase();
    return (!this._topic || pick.topic === this._topic) &&
      (!this._funnel || pick.funnel === this._funnel) &&
      this._words.every((w) => title.includes(w));
  }

  render() {
    if (!this.days.length) return html`<p>No past days yet.</p>`;
    const days = this.days
      .map((day) => ({ ...day, picks: day.picks.filter((p) => this._keep(p)) }))
      .filter((day) => day.picks.length);
    return html`
      <form class="filters" @input=${this._onInput} @submit=${(e) => e.preventDefault()}>
        <label>Topic
          <select name="topic">
            <option value="">All topics</option>
            ${this.topics.map(([tag, label]) => html`<option value=${tag}>${label}</option>`)}
          </select>
        </label>
        <label>Kind
          <select name="funnel">
            <option value="">New and making waves</option>
            <option value="fresh">New</option>
            <option value="waves">Making waves</option>
          </select>
        </label>
        <label>Search <input type="search" name="q" placeholder="Words in a title"></label>
      </form>
      <ol class="days">
        ${days.map((day) => html`
          <li class="day">
            <h2><a href=${day.href}>${day.label}</a></h2>
            <ul>
              ${day.picks.map((p) => html`
                <li><a href=${p.href}>${p.title}</a> <span class="meta">${p.topic_label}</span></li>`)}
            </ul>
          </li>`)}
      </ol>
      ${days.length ? nothing : html`<p class="empty">No papers match.</p>`}`;
  }
}

customElements.define("archive-list", ArchiveList);
