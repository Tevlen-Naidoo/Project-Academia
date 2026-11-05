// Sign-in and the database (Firebase Auth and Firestore) for the reading board and
// the notification devices. The site stays static: the browser talks to Firestore
// directly, and Firestore's security rules (firestore.rules) let only the accounts
// listed under owners/ read or write anything.
//
// The web config comes from the FIREBASE_CONFIG repository variable, baked into
// each page as data-firebase. It is public by design; the rules protect the data.
const SDK = "https://www.gstatic.com/firebasejs/10.14.1/";
const BOARD_DOC = ["boards", "main"];
const DEVICES = "devices";
const OWNERS = "owners";

let config = null;
try {
  config = JSON.parse(document.documentElement.dataset.firebase || "null");
} catch (e) {
  config = null;
}
export const configured = !!(config && config.apiKey && config.projectId);

let loading = null;
/** The Firebase modules and app, loaded the first time anything needs them. */
function sdk() {
  if (!configured) return Promise.reject(new Error("NOT_CONFIGURED"));
  if (!loading) {
    loading = Promise.all([
      import(SDK + "firebase-app.js"),
      import(SDK + "firebase-auth.js"),
      import(SDK + "firebase-firestore.js"),
    ]).then(([app, auth, fs]) => {
      const instance = app.initializeApp(config);
      const a = auth.getAuth(instance);
      const db = fs.getFirestore(instance);
      // After a redirect sign-in (used when a popup is blocked), finish it quietly.
      auth.getRedirectResult(a).catch(() => {});
      return { auth, fs, a, db };
    });
  }
  return loading;
}

// --- who is signed in ------------------------------------------------------------

const users = new EventTarget();
let current = undefined; // undefined until Firebase says; then a user or null
let firstState = null;

function watch() {
  if (!firstState) {
    firstState = sdk()
      .then(({ auth, a }) => new Promise((resolve) => {
        auth.onAuthStateChanged(a, (user) => {
          current = user || null;
          users.dispatchEvent(new Event("change"));
          resolve(current);
        });
      }))
      .catch(() => { current = null; return null; });
  }
  return firstState;
}

/** The signed-in user, or null. Waits for Firebase to restore a saved sign-in. */
export async function currentUser() {
  if (!configured) return null;
  await watch();
  return current;
}

/** Call `fn(user)` now and whenever the user signs in or out. */
export function onUser(fn) {
  const handler = () => fn(current || null);
  users.addEventListener("change", handler);
  if (configured) watch(); else fn(null);
  if (current !== undefined) handler();
  return () => users.removeEventListener("change", handler);
}

export async function signInWithGoogle() {
  const { auth, a } = await sdk();
  const provider = new auth.GoogleAuthProvider();
  try {
    await auth.signInWithPopup(a, provider);
  } catch (err) {
    const code = err && err.code;
    if (code === "auth/popup-blocked" || code === "auth/operation-not-supported-in-this-environment") {
      await auth.signInWithRedirect(a, provider); // the page reloads signed in
      return;
    }
    if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") return;
    throw friendly(err);
  }
}

export async function signInWithEmail(email, password) {
  const { auth, a } = await sdk();
  try {
    await auth.signInWithEmailAndPassword(a, email, password);
  } catch (err) {
    throw friendly(err);
  }
}

export async function signOut() {
  const { auth, a } = await sdk();
  await auth.signOut(a);
}

function friendly(err) {
  const code = (err && err.code) || "";
  if (code === "auth/unauthorized-domain") {
    return new Error("Firebase doesn't allow sign-in from this address yet. Add it under Authentication → Settings → Authorised domains.");
  }
  if (code === "auth/invalid-credential" || code === "auth/wrong-password" || code === "auth/user-not-found") {
    return new Error("That email and password don't match an account.");
  }
  if (code === "auth/operation-not-allowed") {
    return new Error("That sign-in method is turned off in the Firebase console.");
  }
  return new Error((err && err.message) || "Couldn't sign in.");
}

function denied(err) {
  return err && (err.code === "permission-denied" || err.code === "unauthenticated");
}

/** Whether the signed-in account is allowed in by the security rules. */
export async function isOwner() {
  const user = await currentUser();
  if (!user) return false;
  const { fs, db } = await sdk();
  try {
    return (await fs.getDoc(fs.doc(db, OWNERS, user.uid))).exists();
  } catch (err) {
    if (denied(err)) return false;
    throw err;
  }
}

async function signedIn() {
  const user = await currentUser();
  if (!user) throw new Error("NO_SIGNIN");
  return sdk();
}

function storeError(err) {
  if (denied(err)) return new Error("NOT_ALLOWED");
  return err;
}

// Firestore rejects `undefined`; JSON round-trips drop it, as the Python side expects.
function plain(data) { return JSON.parse(JSON.stringify(data)); }

// --- the board -------------------------------------------------------------------

/** The board, or `empty` if it hasn't been saved yet. */
export async function readBoard(empty) {
  const { fs, db } = await signedIn();
  try {
    const snap = await fs.getDoc(fs.doc(db, ...BOARD_DOC));
    return snap.exists() ? snap.data() : empty;
  } catch (err) {
    throw storeError(err);
  }
}

/**
 * Read, change and save the board in one transaction, so two devices can't
 * overwrite each other. `apply` changes the board in place and may throw to stop.
 */
export async function changeBoard(empty, apply) {
  const { fs, db } = await signedIn();
  const ref = fs.doc(db, ...BOARD_DOC);
  try {
    return await fs.runTransaction(db, async (tx) => {
      const snap = await tx.get(ref);
      const board = snap.exists() ? snap.data() : empty();
      const result = apply(board);
      board.updated_at = new Date().toISOString();
      tx.set(ref, plain(board));
      return { data: board, result };
    });
  } catch (err) {
    throw storeError(err);
  }
}

// --- notification devices --------------------------------------------------------

/** The device document id: SHA-256 of the push endpoint, as hex (store/data.py too). */
export async function deviceId(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function saveDevice(entry) {
  const { fs, db } = await signedIn();
  try {
    await fs.setDoc(fs.doc(db, DEVICES, await deviceId(entry.endpoint)), plain(entry));
  } catch (err) {
    throw storeError(err);
  }
}

export async function removeDevice(endpoint) {
  const { fs, db } = await signedIn();
  try {
    await fs.deleteDoc(fs.doc(db, DEVICES, await deviceId(endpoint)));
  } catch (err) {
    throw storeError(err);
  }
}
