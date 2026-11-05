// Values the site builder bakes into each page's <html> element.
// (The board and notification devices are saved in Firestore; see firebase.ts.)

const html = document.documentElement;

/** owner/name of the GitHub repository the site is built from. */
export const REPO = html.dataset.repo ?? "";
/** The way back to the site root from this page, such as "../../". */
export const ROOT = html.dataset.root ?? "";
/** The VAPID public key for Web Push; empty leaves notifications off. */
export const PUSH_KEY = html.dataset.pushKey ?? "";
/** The Firebase web config as JSON; empty leaves sign-in and the live board off. */
export const FIREBASE_CONFIG = html.dataset.firebase ?? "";
