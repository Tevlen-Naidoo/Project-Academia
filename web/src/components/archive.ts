// <archive-list days='[...]' topics='[...]'>: every past day, filtered by topic,
// kind and title words.
import { html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { LightElement } from "../lib/base";
import type { ArchiveDay, ArchivePick, Topic } from "../lib/types";

interface FilterForm extends HTMLFormElement {
	topic: HTMLSelectElement;
	funnel: HTMLSelectElement;
	q: HTMLInputElement;
}

@customElement("archive-list")
export class ArchiveList extends LightElement {
	@property({ type: Array }) days: ArchiveDay[] = [];
	@property({ type: Array }) topics: Topic[] = [];
	@state() private topic = "";
	@state() private funnel = "";
	@state() private words: string[] = [];

	private onInput(e: Event) {
		const form = e.currentTarget as FilterForm;
		this.topic = form.topic.value;
		this.funnel = form.funnel.value;
		this.words = form.q.value.toLowerCase().split(/\s+/).filter(Boolean);
	}

	private keep(pick: ArchivePick): boolean {
		const title = pick.title.toLowerCase();
		return (
			(!this.topic || pick.topic === this.topic) &&
			(!this.funnel || pick.funnel === this.funnel) &&
			this.words.every((w) => title.includes(w))
		);
	}

	override render() {
		if (!this.days.length) return html`<p>No past days yet.</p>`;
		const days = this.days
			.map((day) => ({ ...day, picks: day.picks.filter((p) => this.keep(p)) }))
			.filter((day) => day.picks.length);
		return html`
			<form class="filters" @input=${this.onInput} @submit=${(e: Event) => e.preventDefault()}>
				<label
					>Topic
					<select name="topic">
						<option value="">All topics</option>
						${this.topics.map(([tag, label]) => html`<option value=${tag}>${label}</option>`)}
					</select>
				</label>
				<label
					>Kind
					<select name="funnel">
						<option value="">New and making waves</option>
						<option value="fresh">New</option>
						<option value="waves">Making waves</option>
					</select>
				</label>
				<label>Search <input type="search" name="q" placeholder="Words in a title" /></label>
			</form>
			<ol class="days">
				${days.map(
					(day) => html`
						<li class="day">
							<h2><a href=${day.href}>${day.label}</a></h2>
							<ul>
								${day.picks.map(
									(p) => html`<li><a href=${p.href}>${p.title}</a> <span class="meta">${p.topic_label}</span></li>`,
								)}
							</ul>
						</li>
					`,
				)}
			</ol>
			${days.length ? nothing : html`<p class="empty">No papers match.</p>`}
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"archive-list": ArchiveList;
	}
}
