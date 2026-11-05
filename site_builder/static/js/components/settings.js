// The Settings page: <account-settings> and <push-settings>.
import { html, nothing } from "../../vendor/lit.js";
import { LightElement } from "../base.js";
import {
	configured,
	isOwner,
	onUser,
	signInWithEmail,
	signInWithGoogle,
	signOut,
} from "../firebase.js";
import { PUSH_KEY, REPO } from "../github.js";
import {
	deviceName,
	isApple,
	pushReady,
	pushSupported,
	removeSubscription,
	saveSubscription,
	standalone,
	subscribe,
} from "../push.js";

const SETUP_URL = () => `https://github.com/${REPO}/actions/workflows/setup-database.yml`;

/** Sign in to the board's database, once per device. */
export class AccountSettings extends LightElement {
	static properties = {
		_user: { state: true },
		_owner: { state: true }, // true, false, or null while checking
		_state: { state: true },
		_busy: { state: true },
	};

	constructor() {
		super();
		this._user = undefined;
		this._owner = null;
		this._state = configured ? "Checking…" : "";
		this._busy = false;
	}

	connectedCallback() {
		super.connectedCallback();
		this._stop = onUser((user) => this._changed(user));
	}

	disconnectedCallback() {
		if (this._stop) this._stop();
		super.disconnectedCallback();
	}

	async _changed(user) {
		this._user = user;
		this._owner = null;
		if (!user) { this._state = configured ? "Not signed in on this device." : ""; return; }
		this._state = "Checking access…";
		try {
			this._owner = await isOwner();
			this._state = this._owner
				? `Signed in as ${user.email || user.uid}. The board saves to your database.`
				: `Signed in as ${user.email || user.uid}, but this account isn't allowed in yet.`;
		} catch (e) {
			this._state = "Signed in, but couldn't reach the database.";
		}
	}

	async _run(fn) {
		this._busy = true;
		try {
			await fn();
		} catch (err) {
			this._state = (err && err.message) || "Something went wrong.";
		} finally {
			this._busy = false;
		}
	}

	_google() { this._run(() => signInWithGoogle()); }

	_email(e) {
		e.preventDefault();
		const form = e.currentTarget;
		this._run(() => signInWithEmail(form.email.value.trim(), form.password.value));
	}

	_signOut() { this._run(() => signOut()); }

	_copyId() {
		if (navigator.clipboard && this._user) navigator.clipboard.writeText(this._user.uid).catch(() => { });
	}

	render() {
		if (!configured) {
			return html`
        <section class="settings">
          <h2>Account</h2>
          <p>The reading board's database isn't set up for this site yet. Add the
          <strong>FIREBASE_CONFIG</strong> repository variable (see the README), then the site
          rebuilds with sign-in here.</p>
        </section>`;
		}
		const user = this._user;
		return html`
      <section class="settings">
        <h2>Account</h2>
        <p class="meta key-state">${this._state}</p>
        ${user ? html`
          ${this._owner === false ? html`
            <p>To let this account in, copy its id and run
            <a href=${SETUP_URL()} target="_blank" rel="noopener">Set up database</a> on GitHub with it.
            You only do this once.</p>
            <p class="account-id">${user.uid}</p>
            <p><button type="button" class="button small" @click=${this._copyId}>Copy id</button></p>` : nothing}
          <p><button type="button" class="button" ?disabled=${this._busy} @click=${this._signOut}>Sign out</button></p>
        ` : user === null ? html`
          <p><button type="button" class="button" ?disabled=${this._busy} @click=${this._google}>Sign in with Google</button></p>
          <details class="sign-in-email">
            <summary>Or sign in with email and password</summary>
            <p class="meta">Use this in the Home Screen app if Google sign-in doesn't open there.
            The account is one you add under Authentication → Users in the Firebase console.</p>
            <form class="token-form" @submit=${this._email}>
              <label>Email <input type="email" name="email" autocomplete="username" required></label>
              <label>Password <input type="password" name="password" autocomplete="current-password" required></label>
              <button type="submit" class="button" ?disabled=${this._busy}>Sign in</button>
            </form>
          </details>
        ` : nothing}
      </section>`;
	}
}

/** Turn this device's daily notification on or off. */
export class PushSettings extends LightElement {
	static properties = {
		_state: { state: true },
		_on: { state: true }, // true, false, or null when neither button applies
		_busy: { state: true },
		_ios: { state: true },
	};

	constructor() {
		super();
		this._state = "Checking…";
		this._on = null;
		this._busy = false;
		this._ios = false;
	}

	connectedCallback() {
		super.connectedCallback();
		if (!PUSH_KEY) { this._state = "Notifications aren't set up for this site yet (the VAPID key is missing)."; return; }
		if (!pushSupported) {
			if (isApple && !standalone) {
				this._ios = true;
				this._state = "On iPhone and iPad, notifications only work from the Home Screen app.";
			} else {
				this._state = "This browser can't receive notifications.";
			}
			return;
		}
		this._refresh();
	}

	_show(text, on = null) {
		this._state = text;
		this._on = on;
		this._busy = false;
	}

	async _refresh() {
		if (Notification.permission === "denied") {
			this._show("Notifications are blocked for this site. Allow them in your device's settings, then come back.");
			return;
		}
		try {
			const reg = await pushReady();
			const sub = await reg.pushManager.getSubscription();
			if (sub && Notification.permission === "granted") {
				this._show(`Notifications are on for this ${deviceName()}.`, true);
			} else {
				this._show("Notifications are off for this device.", false);
			}
		} catch (e) {
			this._show("Couldn't start notifications in this browser.");
		}
	}

	_failed(err, fallback) {
		this._busy = false;
		const code = err && err.message;
		if (code === "NO_SIGNIN") this._state = "Sign in above first, so this device can be saved.";
		else if (code === "NOT_ALLOWED") this._state = "This account isn't allowed in yet (see Account above).";
		else if (code === "NOT_CONFIGURED") this._state = "The database isn't set up for this site yet.";
		else this._state = code || fallback;
	}

	async _turnOn() {
		this._busy = true;
		this._state = "Asking for permission…";
		try {
			// Ask straight from the tap: iOS only shows the prompt for a tap.
			const permission = await Notification.requestPermission();
			if (permission !== "granted") { this._refresh(); return; }
			const sub = await subscribe();
			this._state = "Saving this device…";
			await saveSubscription(sub);
			this._show(`Notifications are on for this ${deviceName()}. The next one comes with tomorrow's papers.`, true);
		} catch (err) {
			this._failed(err, "Couldn't turn on notifications.");
		}
	}

	async _turnOff() {
		this._busy = true;
		this._state = "Turning off…";
		try {
			const reg = await pushReady();
			const sub = await reg.pushManager.getSubscription();
			if (sub) {
				const endpoint = sub.endpoint;
				await sub.unsubscribe();
				// If this fails (signed out), the next send finds the device gone and drops it.
				await removeSubscription(endpoint).catch(() => { });
			}
			this._refresh();
		} catch (err) {
			this._failed(err, "Couldn't turn off notifications.");
		}
	}

	render() {
		return html`
      <section class="settings" id="push">
        <h2>Notifications</h2>
        <p>Get a notification on this device when the day's papers are up, usually before 7am
        SAST. Tapping it opens Today. Sign in above, then turn it on once on each device.</p>
        ${this._ios ? html`
          <div class="push-ios">
            <p>On iPhone and iPad (iOS 16.4 or later):</p>
            <ol>
              <li>In Safari, tap the <strong>Share</strong> button, then <strong>Add to Home Screen</strong>.</li>
              <li>Open <strong>Academia</strong> from your Home Screen and go to Settings.</li>
              <li>Sign in, then tap <strong>Turn on notifications</strong> and allow them.</li>
            </ol>
          </div>` : nothing}
        <p class="meta push-state">${this._state}</p>
        <p>
          ${this._on === false ? html`<button type="button" class="button" ?disabled=${this._busy} @click=${this._turnOn}>Turn on notifications</button>` : nothing}
          ${this._on === true ? html`<button type="button" class="button" ?disabled=${this._busy} @click=${this._turnOff}>Turn off notifications</button>` : nothing}
        </p>
      </section>`;
	}
}

customElements.define("account-settings", AccountSettings);
customElements.define("push-settings", PushSettings);
