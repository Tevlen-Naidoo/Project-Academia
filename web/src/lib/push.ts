// Web Push for the daily picks.
//
// Turning notifications on asks the browser for a push subscription and saves it in
// Firestore (devices/<hash of the endpoint>), which needs you signed in. After each
// daily run, `python -m notifier send` pushes a message to every saved device. On
// iPhone and iPad this only works from the Home Screen app (iOS 16.4 or later).
import { today } from "./base";
import { currentUser, removeDevice, saveDevice } from "./firebase";
import { PUSH_KEY, ROOT } from "./page";

const SAVED_KEY = "academia.pushEndpoint";

export const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const ipadOS = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
export const isApple = /iPad|iPhone|iPod/.test(navigator.userAgent) || ipadOS;
export const standalone =
	(navigator as Navigator & { standalone?: boolean }).standalone === true ||
	(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);

function remembered(): string {
	try {
		return window.localStorage.getItem(SAVED_KEY) || "";
	} catch {
		return "";
	}
}

function remember(endpoint: string): void {
	try {
		if (endpoint) window.localStorage.setItem(SAVED_KEY, endpoint);
		else window.localStorage.removeItem(SAVED_KEY);
	} catch {
		/* only means we save again next time */
	}
}

export function deviceName(): string {
	const ua = navigator.userAgent;
	if (/iPad/.test(ua) || ipadOS) return "iPad";
	if (/iPhone|iPod/.test(ua)) return "iPhone";
	if (/Android/.test(ua)) return "Android";
	if (/Macintosh/.test(ua)) return "Mac";
	if (/Windows/.test(ua)) return "Windows";
	return "Other";
}

function keyBytes(b64: string): Uint8Array<ArrayBuffer> {
	const s = (b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
	const bin = atob(s);
	const out = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
	return out;
}

export async function saveSubscription(sub: PushSubscription): Promise<void> {
	const json = sub.toJSON();
	const entry = { endpoint: json.endpoint ?? sub.endpoint, keys: json.keys, device: deviceName(), added_on: today() };
	await saveDevice(entry);
	remember(entry.endpoint);
}

export async function removeSubscription(endpoint: string): Promise<void> {
	await removeDevice(endpoint);
	remember("");
}

let ready: Promise<ServiceWorkerRegistration> | null = null;
export function pushReady(): Promise<ServiceWorkerRegistration> {
	if (!ready) ready = navigator.serviceWorker.register(ROOT + "sw.js").then(() => navigator.serviceWorker.ready);
	return ready;
}

export async function subscribe(): Promise<PushSubscription> {
	const reg = await pushReady();
	const options = { userVisibleOnly: true, applicationServerKey: keyBytes(PUSH_KEY) };
	try {
		return await reg.pushManager.subscribe(options);
	} catch {
		// An old subscription made with another key blocks a new one.
		const old = await reg.pushManager.getSubscription();
		if (old) await old.unsubscribe();
		return reg.pushManager.subscribe(options);
	}
}

/** On any page: if this device's subscription changed since it was saved, save it again. */
export async function resync(): Promise<void> {
	if (!pushSupported || !PUSH_KEY) return;
	if (Notification.permission !== "granted") return;
	try {
		if (!(await currentUser())) return;
		const reg = await navigator.serviceWorker.getRegistration(ROOT);
		const sub = reg && (await reg.pushManager.getSubscription());
		if (sub && sub.endpoint !== remembered()) await saveSubscription(sub);
	} catch {
		/* try again on the next page */
	}
}
