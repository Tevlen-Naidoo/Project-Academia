// Shared bits for the site's web components.
import { LitElement } from "lit";
import type { Links } from "./types";
import { ROOT } from "./page";

/**
 * A Lit element that renders into its own light DOM rather than a shadow root,
 * so the site's stylesheets (web/src/styles/) style every component.
 */
export class LightElement extends LitElement {
	protected override createRenderRoot(): HTMLElement {
		return this;
	}
}

/** A link inside the site, from the site root. */
export function href(path: string): string {
	return ROOT + path;
}

/** The best page to open a paper on. */
export function paperLink(links: Links | undefined): string {
	const l = links ?? {};
	return l.abstract_page || l.doi || l.pdf || "";
}

export interface Message {
	text: string;
	error: boolean;
	settingsLink: boolean;
}

declare global {
	interface WindowEventMap {
		"academia-message": CustomEvent<Message>;
	}
}

/** Show a line in the page's <site-message>. */
export function say(text: string, { error = false, settingsLink = false } = {}): void {
	window.dispatchEvent(new CustomEvent("academia-message", { detail: { text, error, settingsLink } }));
}

/** The error codes firebase.ts throws, so the page can say what to do next. */
export type ErrorCode = "NO_SIGNIN" | "NOT_ALLOWED" | "NOT_CONFIGURED";

export function errorMessage(err: unknown): string {
	return err instanceof Error ? err.message : "";
}

/** Show what went wrong with a save. */
export function explain(err: unknown): void {
	const code = errorMessage(err);
	if (code === "NO_SIGNIN") {
		say("To save to the board, sign in on the ", { error: true, settingsLink: true });
	} else if (code === "NOT_ALLOWED") {
		say("This account isn't allowed to use the board yet. See the ", { error: true, settingsLink: true });
	} else if (code === "NOT_CONFIGURED") {
		say("The board's database isn't set up for this site yet. See the ", { error: true, settingsLink: true });
	} else {
		say(code || "Something went wrong.", { error: true });
	}
}

/** Today's date as YYYY-MM-DD, in local time. */
export function today(): string {
	const d = new Date();
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
