// Reading and saving JSON files in the project's GitHub repository.
//
// Reading works for anyone (the repository is public). Saving needs a fine-grained
// GitHub key with Contents: read and write on this one repository, pasted once on
// the Settings page and kept in this browser's localStorage.

const html = document.documentElement;
export const REPO = html.dataset.repo || "";
export const ROOT = html.dataset.root || "";
export const PUSH_KEY = html.dataset.pushKey || "";

const BRANCH = "main";
const TOKEN_KEY = "academia.githubToken";
const API = "https://api.github.com";

export function getToken() {
  try { return window.localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
}

export function setToken(value) {
  try {
    if (value) window.localStorage.setItem(TOKEN_KEY, value);
    else window.localStorage.removeItem(TOKEN_KEY);
    return true;
  } catch (e) { return false; }
}

export function request(method, url, body, token) {
  const headers = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
  if (token) headers.Authorization = "Bearer " + token;
  if (body) headers["Content-Type"] = "application/json";
  return fetch(API + url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
}

function decode(b64) {
  const bin = atob(b64.replace(/\s/g, ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function encode(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

/** Read a JSON file from the repository. A missing file reads as `empty`. */
export async function loadFile(path, empty) {
  const res = await request("GET", `/repos/${REPO}/contents/${path}?ref=${BRANCH}`, null, getToken());
  if (res.status === 404) return { data: empty, sha: null };
  if (!res.ok) throw new Error(`GitHub said ${res.status} when reading ${path}.`);
  const file = await res.json();
  return { data: JSON.parse(decode(file.content)), sha: file.sha };
}

function saveFile(path, data, sha, message) {
  data.updated_at = new Date().toISOString();
  const body = { message, content: encode(JSON.stringify(data, null, 2) + "\n"), branch: BRANCH };
  if (sha) body.sha = sha;
  return request("PUT", `/repos/${REPO}/contents/${path}`, body, getToken());
}

/**
 * Load, change, save. If someone else saved in between, start again once.
 * `apply` changes the loaded data in place and may throw to stop the save.
 */
export async function changeFile(path, empty, apply, message, attempt = 1) {
  if (!getToken()) throw new Error("NO_TOKEN");
  const current = await loadFile(path, empty());
  const result = apply(current.data);
  const res = await saveFile(path, current.data, current.sha, message);
  if ((res.status === 409 || res.status === 422) && attempt < 2) {
    return changeFile(path, empty, apply, message, attempt + 1);
  }
  if (res.status === 401 || res.status === 403) {
    throw new Error("GitHub refused the key. Check it on the Settings page.");
  }
  if (!res.ok) throw new Error(`GitHub said ${res.status} when saving ${path}.`);
  return { data: current.data, result };
}
