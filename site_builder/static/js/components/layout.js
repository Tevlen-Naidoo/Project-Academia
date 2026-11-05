// The parts every page shares: header, message line and footer.
import { html, nothing } from "../../vendor/lit.js";
import { LightElement, href } from "../base.js";
import { REPO } from "../github.js";

const NAV = [
	["today", "index.html", "Today"],
	["archive", "archive/index.html", "Archive"],
	["board", "board/index.html", "Reading board"],
	["settings", "settings/index.html", "Settings"],
];

/** <site-header current="board"> */
export class SiteHeader extends LightElement {
	static properties = { current: {} };

	render() {
		return html`
      <header class="top">
        <a class="brand" href=${href("index.html")}>Project Academia</a>
        <nav>
          ${NAV.map(([key, path, label]) => html`
            <a href=${href(path)} aria-current=${this.current === key ? "page" : nothing}>${label}</a>`)}
        </nav>
      </header>`;
	}
}

/** <site-message>: the status line that saves and errors write to. */
export class SiteMessage extends LightElement {
	static properties = { _msg: { state: true } };

	constructor() {
		super();
		this._msg = null;
		this._onMessage = (e) => { this._msg = e.detail.text ? e.detail : null; };
	}

	connectedCallback() {
		super.connectedCallback();
		window.addEventListener("academia-message", this._onMessage);
	}

	disconnectedCallback() {
		window.removeEventListener("academia-message", this._onMessage);
		super.disconnectedCallback();
	}

	render() {
		const m = this._msg;
		return html`
      <p class="message ${m && m.error ? "error" : ""}" role="status" aria-live="polite" ?hidden=${!m}>
        ${m ? m.text : ""}${m && m.settingsLink
				? html`<a href=${href("settings/index.html")}>Settings page</a>.` : nothing}
      </p>`;
	}
}

/** <site-footer built="2026-11-05 06:00"> */
export class SiteFooter extends LightElement {
	static properties = { built: {} };

	render() {
		return html`
      <footer>
        Built ${this.built} UTC from free metadata: arXiv, Semantic Scholar, OpenAlex,
        Hugging Face and Hacker News.
        <a href="https://github.com/${REPO}">Source</a>
      </footer>`;
	}
}

/** <day-pager older-href older-label newer-href newer-label> */
export class DayPager extends LightElement {
	static properties = {
		olderHref: { attribute: "older-href" },
		olderLabel: { attribute: "older-label" },
		newerHref: { attribute: "newer-href" },
		newerLabel: { attribute: "newer-label" },
	};

	render() {
		return html`
      <nav class="pager">
        ${this.olderHref ? html`<a href=${this.olderHref}>← ${this.olderLabel}</a>` : html`<span></span>`}
        ${this.newerHref ? html`<a href=${this.newerHref}>${this.newerLabel} →</a>` : nothing}
      </nav>`;
	}
}

customElements.define("site-header", SiteHeader);
customElements.define("site-message", SiteMessage);
customElements.define("site-footer", SiteFooter);
customElements.define("day-pager", DayPager);
