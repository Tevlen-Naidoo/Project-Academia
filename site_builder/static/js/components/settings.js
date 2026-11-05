// The Settings page: <github-key-settings> and <push-settings>.
import { html, nothing } from "../../vendor/lit.js";
import { LightElement } from "../base.js";
import { PUSH_KEY, REPO, getToken, request, setToken } from "../github.js";
import {
  deviceName,
  isApple,
  pushReady,
  pushSupported,
  removeSubscription,
  saveSubscription,
  signupCode,
  standalone,
  subscribe,
} from "../push.js";

const NEW_KEY_URL = "https://github.com/settings/personal-access-tokens/new?name=Project+Academia+board" +
  "&description=Lets+the+Project+Academia+site+save+the+reading+board&expires_in=366&contents=write";

/** Paste, check and forget the GitHub key that saves the board. */
export class GithubKeySettings extends LightElement {
  static properties = { _state: { state: true } };

  constructor() {
    super();
    this._state = "Checking…";
  }

  connectedCallback() {
    super.connectedCallback();
    this._check();
  }

  async _check() {
    const token = getToken();
    if (!token) { this._state = "No key saved in this browser yet."; return; }
    this._state = "Checking the saved key…";
    try {
      const res = await request("GET", `/repos/${REPO}`, null, token);
      if (!res.ok) { this._state = `GitHub refused the saved key (${res.status}).`; return; }
      const repo = await res.json();
      this._state = repo.permissions && repo.permissions.push
        ? "A key is saved, and it can save the board."
        : `A key is saved, but it can't write to ${REPO}. Give it Contents: read and write.`;
    } catch (e) {
      this._state = "Couldn't reach GitHub to check the key.";
    }
  }

  _save(e) {
    e.preventDefault();
    const form = e.currentTarget;
    if (!setToken(form.token.value.trim())) {
      this._state = "This browser won't store the key (private mode?).";
      return;
    }
    form.token.value = "";
    this._check();
  }

  _forget() {
    setToken("");
    this._check();
  }

  render() {
    return html`
      <section class="settings">
        <h2>GitHub key for the reading board</h2>
        <p>The board is saved in the project's GitHub repository, so the daily picks can skip papers
        you've saved. To let this site save it, give it a GitHub key once on each device. The key
        stays in this browser and is sent only to GitHub.</p>
        <p class="meta key-state">${this._state}</p>
        <ol>
          <li><a href=${NEW_KEY_URL} target="_blank" rel="noopener">Create a fine-grained key on GitHub</a>.</li>
          <li>Under <strong>Repository access</strong>, choose <strong>Only select repositories</strong> and pick <strong>${REPO.split("/")[1]}</strong>.</li>
          <li>Under <strong>Permissions → Repository permissions</strong>, set <strong>Contents</strong> to <strong>Read and write</strong>. Nothing else is needed.</li>
          <li>Press <strong>Generate token</strong>, copy it, and paste it here.</li>
        </ol>
        <form class="token-form" @submit=${this._save}>
          <label>GitHub key <input type="password" name="token" autocomplete="off" spellcheck="false" placeholder="github_pat_…" required></label>
          <button type="submit" class="button">Save key</button>
          <button type="button" class="button" @click=${this._forget}>Forget key</button>
        </form>
      </section>`;
  }
}

/** Turn this device's daily notification on or off. */
export class PushSettings extends LightElement {
  static properties = {
    _state: { state: true },
    _on: { state: true }, // true, false, or null when neither button applies
    _busy: { state: true },
    _code: { state: true },
    _openCode: { state: true },
    _ios: { state: true },
    _copied: { state: true },
  };

  constructor() {
    super();
    this._state = "Checking…";
    this._on = null;
    this._busy = false;
    this._code = "";
    this._openCode = false;
    this._ios = false;
    this._copied = false;
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

  _show(text, on = null, sub = null, openCode = false) {
    this._state = text;
    this._on = on;
    this._code = sub ? signupCode(sub) : "";
    this._openCode = openCode;
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
        this._show(`Notifications are on for this ${deviceName()}.`, true, sub);
      } else {
        this._show("Notifications are off for this device.", false);
      }
    } catch (e) {
      this._show("Couldn't start notifications in this browser.");
    }
  }

  async _turnOn() {
    this._busy = true;
    this._state = "Asking for permission…";
    try {
      // Ask straight from the tap: iOS only shows the prompt for a tap.
      const permission = await Notification.requestPermission();
      if (permission !== "granted") { this._refresh(); return; }
      const sub = await subscribe();
      if (!getToken()) {
        // No GitHub key here: the workflow saves the device from its code.
        this._show("Almost done: copy the code below into GitHub to finish.", true, sub, true);
        return;
      }
      this._state = "Saving this device…";
      await saveSubscription(sub);
      this._show(`Notifications are on for this ${deviceName()}. The next one comes with tomorrow's papers.`, true, sub);
    } catch (err) {
      this._busy = false;
      this._state = (err && err.message) || "Couldn't turn on notifications.";
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
        // Without a key the next send finds it gone and drops it.
        if (getToken()) await removeSubscription(endpoint);
      }
      this._refresh();
    } catch (err) {
      this._busy = false;
      this._state = (err && err.message) || "Couldn't turn off notifications.";
    }
  }

  async _copy() {
    const box = this.querySelector("textarea");
    box.select();
    try {
      await navigator.clipboard.writeText(this._code);
    } catch (e) {
      document.execCommand("copy");
    }
    this._copied = true;
  }

  render() {
    return html`
      <section class="settings" id="push">
        <h2>Notifications</h2>
        <p>Get a notification on this device when the day's papers are up, usually before 7am
        SAST. Tapping it opens Today. Turn it on once on each device.</p>
        ${this._ios ? html`
          <div class="push-ios">
            <p>On iPhone and iPad (iOS 16.4 or later):</p>
            <ol>
              <li>In Safari, tap the <strong>Share</strong> button, then <strong>Add to Home Screen</strong>.</li>
              <li>Open <strong>Academia</strong> from your Home Screen and go to Settings.</li>
              <li>Tap <strong>Turn on notifications</strong> and allow them.</li>
              <li>Copy the sign-up code it shows into the <strong>Add notification device</strong> workflow on GitHub.</li>
            </ol>
          </div>` : nothing}
        <p class="meta push-state">${this._state}</p>
        ${this._code ? html`
          <details class="push-code" ?open=${this._openCode}>
            <summary>Sign-up code for this device</summary>
            <p>To finish, copy this code, open
            <a href="https://github.com/${REPO}/actions/workflows/push-device.yml" target="_blank" rel="noopener">Add notification device</a>
            on GitHub (the GitHub app works), tap <strong>Run workflow</strong>, paste the code and run it.
            A test notification arrives within a minute or two. You only do this once per device.</p>
            <textarea rows="4" readonly spellcheck="false" .value=${this._code}></textarea>
            <p><button type="button" class="button small" @click=${this._copy}>${this._copied ? "Copied" : "Copy code"}</button></p>
          </details>` : nothing}
        <p>
          ${this._on === false ? html`<button type="button" class="button" ?disabled=${this._busy} @click=${this._turnOn}>Turn on notifications</button>` : nothing}
          ${this._on === true ? html`<button type="button" class="button" ?disabled=${this._busy} @click=${this._turnOff}>Turn off notifications</button>` : nothing}
        </p>
      </section>`;
  }
}

customElements.define("github-key-settings", GithubKeySettings);
customElements.define("push-settings", PushSettings);
