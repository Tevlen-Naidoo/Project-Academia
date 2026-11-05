// The Settings page: <account-settings> and <push-settings>.
import { html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import type { User } from "firebase/auth";
import { LightElement, errorMessage } from "../lib/base";
import { configured, isOwner, onUser, signInWithEmail, signInWithGoogle, signOut } from "../lib/firebase";
import { PUSH_KEY, REPO } from "../lib/page";
import {
	deviceName,
	isApple,
	pushReady,
	pushSupported,
	removeSubscription,
	saveSubscription,
	standalone,
	subscribe,
} from "../lib/push";

const SETUP_URL = () => `https://github.com/${REPO}/actions/workflows/setup-database.yml`;

interface SignInForm extends HTMLFormElement {
	email: HTMLInputElement;
	password: HTMLInputElement;
}

/** Sign in to the board's database, once per device. */
@customElement("account-settings")
export class AccountSettings extends LightElement {
	/** undefined while Firebase is still restoring a saved sign-in. */
	@state() private user: User | null | undefined = undefined;
	/** true, false, or null while checking. */
	@state() private owner: boolean | null = null;
	@state() private status = configured ? "Checking…" : "";
	@state() private busy = false;
	private stop: (() => void) | null = null;

	override connectedCallback() {
		super.connectedCallback();
		this.stop = onUser((user) => this.changed(user));
	}

	override disconnectedCallback() {
		this.stop?.();
		super.disconnectedCallback();
	}

	private async changed(user: User | null) {
		this.user = user;
		this.owner = null;
		if (!user) {
			this.status = configured ? "Not signed in on this device." : "";
			return;
		}
		this.status = "Checking access…";
		try {
			this.owner = await isOwner();
			this.status = this.owner
				? `Signed in as ${user.email || user.uid}. The board saves to your database.`
				: `Signed in as ${user.email || user.uid}, but this account isn't allowed in yet.`;
		} catch {
			this.status = "Signed in, but couldn't reach the database.";
		}
	}

	private async run(fn: () => Promise<void>) {
		this.busy = true;
		try {
			await fn();
		} catch (err) {
			this.status = errorMessage(err) || "Something went wrong.";
		} finally {
			this.busy = false;
		}
	}

	private google() {
		this.run(() => signInWithGoogle());
	}

	private email(e: SubmitEvent) {
		e.preventDefault();
		const form = e.currentTarget as SignInForm;
		this.run(() => signInWithEmail(form.email.value.trim(), form.password.value));
	}

	private signOut() {
		this.run(() => signOut());
	}

	private copyId() {
		if (navigator.clipboard && this.user) navigator.clipboard.writeText(this.user.uid).catch(() => {});
	}

	override render() {
		if (!configured) {
			return html`
				<section class="settings">
					<h2>Account</h2>
					<p>
						The reading board's database isn't set up for this site yet. Add the
						<strong>FIREBASE_CONFIG</strong> repository variable (see the README), then the site rebuilds with
						sign-in here.
					</p>
				</section>
			`;
		}
		const user = this.user;
		return html`
			<section class="settings">
				<h2>Account</h2>
				<p class="meta key-state">${this.status}</p>
				${user
					? html`
							${this.owner === false
								? html`
										<p>
											To let this account in, copy its id and run
											<a href=${SETUP_URL()} target="_blank" rel="noopener">Set up database</a> on GitHub with it. You
											only do this once.
										</p>
										<p class="account-id">${user.uid}</p>
										<p><button type="button" class="button small" @click=${this.copyId}>Copy id</button></p>
									`
								: nothing}
							<p>
								<button type="button" class="button" ?disabled=${this.busy} @click=${this.signOut}>Sign out</button>
							</p>
						`
					: user === null
						? html`
								<p>
									<button type="button" class="button" ?disabled=${this.busy} @click=${this.google}>
										Sign in with Google
									</button>
								</p>
								<details class="sign-in-email">
									<summary>Or sign in with email and password</summary>
									<p class="meta">
										Use this in the Home Screen app if Google sign-in doesn't open there. The account is one you add
										under Authentication → Users in the Firebase console.
									</p>
									<form class="token-form" @submit=${this.email}>
										<label>Email <input type="email" name="email" autocomplete="username" required /></label>
										<label
											>Password <input type="password" name="password" autocomplete="current-password" required
										/></label>
										<button type="submit" class="button" ?disabled=${this.busy}>Sign in</button>
									</form>
								</details>
							`
						: nothing}
			</section>
		`;
	}
}

/** Turn this device's daily notification on or off. */
@customElement("push-settings")
export class PushSettings extends LightElement {
	@state() private status = "Checking…";
	/** true, false, or null when neither button applies. */
	@state() private on: boolean | null = null;
	@state() private busy = false;
	@state() private ios = false;

	override connectedCallback() {
		super.connectedCallback();
		if (!PUSH_KEY) {
			this.status = "Notifications aren't set up for this site yet (the VAPID key is missing).";
			return;
		}
		if (!pushSupported) {
			if (isApple && !standalone) {
				this.ios = true;
				this.status = "On iPhone and iPad, notifications only work from the Home Screen app.";
			} else {
				this.status = "This browser can't receive notifications.";
			}
			return;
		}
		this.refresh();
	}

	private show(text: string, on: boolean | null = null) {
		this.status = text;
		this.on = on;
		this.busy = false;
	}

	private async refresh() {
		if (Notification.permission === "denied") {
			this.show("Notifications are blocked for this site. Allow them in your device's settings, then come back.");
			return;
		}
		try {
			const reg = await pushReady();
			const sub = await reg.pushManager.getSubscription();
			if (sub && Notification.permission === "granted") {
				this.show(`Notifications are on for this ${deviceName()}.`, true);
			} else {
				this.show("Notifications are off for this device.", false);
			}
		} catch {
			this.show("Couldn't start notifications in this browser.");
		}
	}

	private failed(err: unknown, fallback: string) {
		this.busy = false;
		const code = errorMessage(err);
		if (code === "NO_SIGNIN") this.status = "Sign in above first, so this device can be saved.";
		else if (code === "NOT_ALLOWED") this.status = "This account isn't allowed in yet (see Account above).";
		else if (code === "NOT_CONFIGURED") this.status = "The database isn't set up for this site yet.";
		else this.status = code || fallback;
	}

	private async turnOn() {
		this.busy = true;
		this.status = "Asking for permission…";
		try {
			// Ask straight from the tap: iOS only shows the prompt for a tap.
			const permission = await Notification.requestPermission();
			if (permission !== "granted") {
				this.refresh();
				return;
			}
			const sub = await subscribe();
			this.status = "Saving this device…";
			await saveSubscription(sub);
			this.show(`Notifications are on for this ${deviceName()}. The next one comes with tomorrow's papers.`, true);
		} catch (err) {
			this.failed(err, "Couldn't turn on notifications.");
		}
	}

	private async turnOff() {
		this.busy = true;
		this.status = "Turning off…";
		try {
			const reg = await pushReady();
			const sub = await reg.pushManager.getSubscription();
			if (sub) {
				const endpoint = sub.endpoint;
				await sub.unsubscribe();
				// If this fails (signed out), the next send finds the device gone and drops it.
				await removeSubscription(endpoint).catch(() => {});
			}
			this.refresh();
		} catch (err) {
			this.failed(err, "Couldn't turn off notifications.");
		}
	}

	override render() {
		return html`
			<section class="settings" id="push">
				<h2>Notifications</h2>
				<p>
					Get a notification on this device when the day's papers are up, usually before 7am SAST. Tapping it opens
					Today. Sign in above, then turn it on once on each device.
				</p>
				${this.ios
					? html`
							<div class="push-ios">
								<p>On iPhone and iPad (iOS 16.4 or later):</p>
								<ol>
									<li>In Safari, tap the <strong>Share</strong> button, then <strong>Add to Home Screen</strong>.</li>
									<li>Open <strong>Academia</strong> from your Home Screen and go to Settings.</li>
									<li>Sign in, then tap <strong>Turn on notifications</strong> and allow them.</li>
								</ol>
							</div>
						`
					: nothing}
				<p class="meta push-state">${this.status}</p>
				<p>
					${this.on === false
						? html`<button type="button" class="button" ?disabled=${this.busy} @click=${this.turnOn}>
								Turn on notifications
							</button>`
						: nothing}
					${this.on === true
						? html`<button type="button" class="button" ?disabled=${this.busy} @click=${this.turnOff}>
								Turn off notifications
							</button>`
						: nothing}
				</p>
			</section>
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"account-settings": AccountSettings;
		"push-settings": PushSettings;
	}
}
