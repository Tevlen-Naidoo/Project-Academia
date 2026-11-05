// Shared bits for the site's web components.
import { LitElement } from "../vendor/lit.js";
import { ROOT } from "./github.js";

/**
 * A Lit element that renders into its own light DOM, so the site's one plain
 * stylesheet styles every component.
 */
export class LightElement extends LitElement {
	createRenderRoot() { return this; }
}

/** A link inside the site, from the site root. */
export function href(path) { return ROOT + path; }

/** The best page to open a paper on. */
export function paperLink(links) {
	links = links || {};
	return links.abstract_page || links.doi || links.pdf || "";
}

/** Show a line in the page's <site-message>. */
export function say(text, { error = false, settingsLink = false } = {}) {
	window.dispatchEvent(new CustomEvent("academia-message", { detail: { text, error, settingsLink } }));
}

/** Show what went wrong with a save. */
export function explain(err) {
	if (err && err.message === "NO_SIGNIN") {
		say("To save to the board, sign in on the ", { error: true, settingsLink: true });
		return;
	}
	if (err && err.message === "NOT_ALLOWED") {
		say("This account isn't allowed to use the board yet. See the ", { error: true, settingsLink: true });
		return;
	}
	if (err && err.message === "NOT_CONFIGURED") {
		say("The board's database isn't set up for this site yet. See the ", { error: true, settingsLink: true });
		return;
	}
	say(err && err.message ? err.message : "Something went wrong.", { error: true });
}
