// Web Push for the daily picks.
//
// Turning notifications on asks the browser for a push subscription and saves it in
// Firestore (devices/<hash of the endpoint>), which needs you signed in. After each
// daily run, `python -m notifier send` pushes a message to every saved device. On
// iPhone and iPad this only works from the Home Screen app (iOS 16.4 or later).
import { currentUser, removeDevice, saveDevice } from "./firebase.js";
import { PUSH_KEY, ROOT } from "./github.js";

const SAVED_KEY = "academia.pushEndpoint";

export const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const ipadOS = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
export const isApple = /iPad|iPhone|iPod/.test(navigator.userAgent) || ipadOS;
export const standalone = window.navigator.standalone === true ||
  (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);

function remembered() {
  try { return window.localStorage.getItem(SAVED_KEY) || ""; } catch (e) { return ""; }
}

function remember(endpoint) {
  try {
    if (endpoint) window.localStorage.setItem(SAVED_KEY, endpoint);
    else window.localStorage.removeItem(SAVED_KEY);
  } catch (e) { /* only means we save again next time */ }
}

export function deviceName() {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua) || ipadOS) return "iPad";
  if (/iPhone|iPod/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows";
  return "Other";
}

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function keyBytes(b64) {
  const s = (b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function saveSubscription(sub) {
  const json = sub.toJSON();
  const entry = { endpoint: json.endpoint, keys: json.keys, device: deviceName(), added_on: today() };
  await saveDevice(entry);
  remember(entry.endpoint);
}

export async function removeSubscription(endpoint) {
  await removeDevice(endpoint);
  remember("");
}

let ready = null;
export function pushReady() {
  if (!ready) ready = navigator.serviceWorker.register(ROOT + "sw.js").then(() => navigator.serviceWorker.ready);
  return ready;
}

export async function subscribe() {
  const reg = await pushReady();
  const options = { userVisibleOnly: true, applicationServerKey: keyBytes(PUSH_KEY) };
  try {
    return await reg.pushManager.subscribe(options);
  } catch (e) {
    // An old subscription made with another key blocks a new one.
    const old = await reg.pushManager.getSubscription();
    if (old) await old.unsubscribe();
    return reg.pushManager.subscribe(options);
  }
}

/** On any page: if this device's subscription changed since it was saved, save it again. */
export async function resync() {
  if (!pushSupported || !PUSH_KEY) return;
  if (Notification.permission !== "granted") return;
  try {
    if (!(await currentUser())) return;
    const reg = await navigator.serviceWorker.getRegistration(ROOT);
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub && sub.endpoint !== remembered()) await saveSubscription(sub);
  } catch (e) { /* try again on the next page */ }
}
