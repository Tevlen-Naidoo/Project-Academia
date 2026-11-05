// The parts every page shares: header, message line, footer and day pager.
import { html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { LightElement, href, type Message } from "../lib/base";
import { REPO } from "../lib/page";

const NAV: [string, string, string][] = [
	["today", "index.html", "Today"],
	["archive", "archive/index.html", "Archive"],
	["board", "board/index.html", "Reading board"],
	["settings", "settings/index.html", "Settings"],
];

/** <site-header current="board"> */
@customElement("site-header")
export class SiteHeader extends LightElement {
	@property() current = "";

	override render() {
		return html`
			<header class="top">
				<a class="brand" href=${href("index.html")}>Project Academia</a>
				<nav>
					${NAV.map(
						([key, path, label]) =>
							html`<a href=${href(path)} aria-current=${this.current === key ? "page" : nothing}>${label}</a>`,
					)}
				</nav>
			</header>
		`;
	}
}

/** <site-message>: the status line that saves and errors write to. */
@customElement("site-message")
export class SiteMessage extends LightElement {
	@state() private msg: Message | null = null;

	private onMessage = (e: CustomEvent<Message>) => {
		this.msg = e.detail.text ? e.detail : null;
	};

	override connectedCallback() {
		super.connectedCallback();
		window.addEventListener("academia-message", this.onMessage);
	}

	override disconnectedCallback() {
		window.removeEventListener("academia-message", this.onMessage);
		super.disconnectedCallback();
	}

	override render() {
		const m = this.msg;
		return html`
			<p class="message ${m?.error ? "error" : ""}" role="status" aria-live="polite" ?hidden=${!m}>
				${m ? m.text : ""}${m?.settingsLink ? html`<a href=${href("settings/index.html")}>Settings page</a>.` : nothing}
			</p>
		`;
	}
}

/** <site-footer built="2026-11-05 06:00"> */
@customElement("site-footer")
export class SiteFooter extends LightElement {
	@property() built = "";

	override render() {
		return html`
			<footer>
				Built ${this.built} UTC from free metadata: arXiv, Semantic Scholar, OpenAlex, Hugging Face and Hacker News.
				<a href="https://github.com/${REPO}">Source</a>
			</footer>
		`;
	}
}

/** <day-pager older-href older-label newer-href newer-label> */
@customElement("day-pager")
export class DayPager extends LightElement {
	@property({ attribute: "older-href" }) olderHref = "";
	@property({ attribute: "older-label" }) olderLabel = "";
	@property({ attribute: "newer-href" }) newerHref = "";
	@property({ attribute: "newer-label" }) newerLabel = "";

	override render() {
		return html`
			<nav class="pager">
				${this.olderHref ? html`<a href=${this.olderHref}>← ${this.olderLabel}</a>` : html`<span></span>`}
				${this.newerHref ? html`<a href=${this.newerHref}>${this.newerLabel} →</a>` : nothing}
			</nav>
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"site-header": SiteHeader;
		"site-message": SiteMessage;
		"site-footer": SiteFooter;
		"day-pager": DayPager;
	}
}
