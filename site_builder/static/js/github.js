// Where the site lives: values the site builder bakes into each page's <html>.
// (The board and notification devices are saved in Firestore; see firebase.js.)

const html = document.documentElement;
export const REPO = html.dataset.repo || "";
export const ROOT = html.dataset.root || "";
export const PUSH_KEY = html.dataset.pushKey || "";
