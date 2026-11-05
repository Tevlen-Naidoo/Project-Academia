// Sign-in and the database (Firebase Auth and Firestore) for the reading board and
// the notification devices. The site stays static: the browser talks to Firestore
// directly, and Firestore's security rules (firestore.rules) let only the accounts
// listed under owners/ read or write anything.
//
// The web config comes from the FIREBASE_CONFIG repository variable, baked into
// each page as data-firebase. It is public by design; the rules protect the data.
//
// The Firebase SDK is an npm dependency. It is imported lazily, so Vite splits it
// into its own chunks and pages only download it once something needs it.
import type { FirebaseOptions } from "firebase/app";
import type { Auth, User } from "firebase/auth";
import type { Firestore } from "firebase/firestore";
import type { Board } from "./types";
import { FIREBASE_CONFIG } from "./page";

const BOARD_DOC = ["boards", "main"] as const;
const DEVICES = "devices";
const OWNERS = "owners";

function readConfig(): FirebaseOptions | null {
	try {
		return JSON.parse(FIREBASE_CONFIG || "null") as FirebaseOptions | null;
	} catch {
		return null;
	}
}

const config = readConfig();
export const configured = !!(config && config.apiKey && config.projectId);

type AuthModule = typeof import("firebase/auth");
type FirestoreModule = typeof import("firebase/firestore");

interface Sdk {
	auth: AuthModule;
	fs: FirestoreModule;
	a: Auth;
	db: Firestore;
}

let loading: Promise<Sdk> | null = null;

/** The Firebase modules and app, loaded the first time anything needs them. */
function sdk(): Promise<Sdk> {
	if (!configured || !config) return Promise.reject(new Error("NOT_CONFIGURED"));
	if (!loading) {
		loading = Promise.all([import("firebase/app"), import("firebase/auth"), import("firebase/firestore")]).then(
			([app, auth, fs]) => {
				const instance = app.initializeApp(config);
				const a = auth.getAuth(instance);
				const db = fs.getFirestore(instance);
				// After a redirect sign-in (used when a popup is blocked), finish it quietly.
				auth.getRedirectResult(a).catch(() => {});
				return { auth, fs, a, db };
			},
		);
	}
	return loading;
}

// --- who is signed in ------------------------------------------------------------

const users = new EventTarget();
let current: User | null | undefined = undefined; // undefined until Firebase says
let firstState: Promise<User | null> | null = null;

function watch(): Promise<User | null> {
	if (!firstState) {
		firstState = sdk()
			.then(
				({ auth, a }) =>
					new Promise<User | null>((resolve) => {
						auth.onAuthStateChanged(a, (user) => {
							current = user ?? null;
							users.dispatchEvent(new Event("change"));
							resolve(current);
						});
					}),
			)
			.catch(() => {
				current = null;
				return null;
			});
	}
	return firstState;
}

/** The signed-in user, or null. Waits for Firebase to restore a saved sign-in. */
export async function currentUser(): Promise<User | null> {
	if (!configured) return null;
	await watch();
	return current ?? null;
}

/** Call `fn(user)` now and whenever the user signs in or out. Returns a stop function. */
export function onUser(fn: (user: User | null) => void): () => void {
	const handler = () => fn(current ?? null);
	users.addEventListener("change", handler);
	if (configured) watch();
	else fn(null);
	if (current !== undefined) handler();
	return () => users.removeEventListener("change", handler);
}

function code(err: unknown): string {
	return (err as { code?: string } | null)?.code ?? "";
}

export async function signInWithGoogle(): Promise<void> {
	const { auth, a } = await sdk();
	const provider = new auth.GoogleAuthProvider();
	try {
		await auth.signInWithPopup(a, provider);
	} catch (err) {
		const c = code(err);
		if (c === "auth/popup-blocked" || c === "auth/operation-not-supported-in-this-environment") {
			await auth.signInWithRedirect(a, provider); // the page reloads signed in
			return;
		}
		if (c === "auth/popup-closed-by-user" || c === "auth/cancelled-popup-request") return;
		throw friendly(err);
	}
}

export async function signInWithEmail(email: string, password: string): Promise<void> {
	const { auth, a } = await sdk();
	try {
		await auth.signInWithEmailAndPassword(a, email, password);
	} catch (err) {
		throw friendly(err);
	}
}

export async function signOut(): Promise<void> {
	const { auth, a } = await sdk();
	await auth.signOut(a);
}

function friendly(err: unknown): Error {
	const c = code(err);
	if (c === "auth/unauthorized-domain") {
		return new Error(
			"Firebase doesn't allow sign-in from this address yet. Add it under Authentication → Settings → Authorised domains.",
		);
	}
	if (c === "auth/invalid-credential" || c === "auth/wrong-password" || c === "auth/user-not-found") {
		return new Error("That email and password don't match an account.");
	}
	if (c === "auth/operation-not-allowed") {
		return new Error("That sign-in method is turned off in the Firebase console.");
	}
	return new Error((err instanceof Error && err.message) || "Couldn't sign in.");
}

function denied(err: unknown): boolean {
	const c = code(err);
	return c === "permission-denied" || c === "unauthenticated";
}

/** Whether the signed-in account is allowed in by the security rules. */
export async function isOwner(): Promise<boolean> {
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

async function signedIn(): Promise<Sdk> {
	const user = await currentUser();
	if (!user) throw new Error("NO_SIGNIN");
	return sdk();
}

function storeError(err: unknown): unknown {
	return denied(err) ? new Error("NOT_ALLOWED") : err;
}

// Firestore rejects `undefined`; JSON round-trips drop it, as the Python side expects.
function plain<T>(data: T): T {
	return JSON.parse(JSON.stringify(data)) as T;
}

// --- the board -------------------------------------------------------------------

/** The board, or `empty` if it hasn't been saved yet. */
export async function readBoard(empty: Board): Promise<Board> {
	const { fs, db } = await signedIn();
	try {
		const snap = await fs.getDoc(fs.doc(db, ...BOARD_DOC));
		return snap.exists() ? (snap.data() as Board) : empty;
	} catch (err) {
		throw storeError(err);
	}
}

/**
 * Read, change and save the board in one transaction, so two devices can't
 * overwrite each other. `apply` changes the board in place and may throw to stop.
 */
export async function changeBoard<R>(empty: () => Board, apply: (board: Board) => R): Promise<{ data: Board; result: R }> {
	const { fs, db } = await signedIn();
	const ref = fs.doc(db, ...BOARD_DOC);
	try {
		return await fs.runTransaction(db, async (tx) => {
			const snap = await tx.get(ref);
			const board = snap.exists() ? (snap.data() as Board) : empty();
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

export interface DeviceEntry {
	endpoint: string;
	keys: Record<string, string> | undefined;
	device: string;
	added_on: string;
}

/** The device document id: SHA-256 of the push endpoint, as hex (store/data.py too). */
export async function deviceId(endpoint: string): Promise<string> {
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
	return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function saveDevice(entry: DeviceEntry): Promise<void> {
	const { fs, db } = await signedIn();
	try {
		await fs.setDoc(fs.doc(db, DEVICES, await deviceId(entry.endpoint)), plain(entry));
	} catch (err) {
		throw storeError(err);
	}
}

export async function removeDevice(endpoint: string): Promise<void> {
	const { fs, db } = await signedIn();
	try {
		await fs.deleteDoc(fs.doc(db, DEVICES, await deviceId(endpoint)));
	} catch (err) {
		throw storeError(err);
	}
}
