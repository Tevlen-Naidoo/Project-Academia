// The reading board, saved straight to data/board.json in the GitHub repository.
//
// Reading works for anyone (the repository is public). Saving needs a fine-grained
// GitHub key with Contents: read and write on this one repository, pasted once on
// the Settings page and kept in this browser's localStorage. Cards follow the
// rules of reader/board.py: To read holds at most 10 papers, a paper is on the
// board once, and the start and finish dates match the column.
(function () {
  "use strict";

  var html = document.documentElement;
  var REPO = html.dataset.repo;
  var ROOT = html.dataset.root || "";
  var PATH = "data/board.json";
  var BRANCH = "main";
  var TOKEN_KEY = "academia.githubToken";
  var TO_READ_LIMIT = 10;
  var COLUMN_NAMES = { to_read: "To read", reading: "Reading", done: "Done" };
  var API = "https://api.github.com";

  // --- storage ---------------------------------------------------------------

  function getToken() {
    try { return window.localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
  }
  function setToken(value) {
    try {
      if (value) window.localStorage.setItem(TOKEN_KEY, value);
      else window.localStorage.removeItem(TOKEN_KEY);
      return true;
    } catch (e) { return false; }
  }

  // --- GitHub ----------------------------------------------------------------

  function request(method, url, body, token) {
    var headers = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    if (token) headers.Authorization = "Bearer " + token;
    if (body) headers["Content-Type"] = "application/json";
    return fetch(API + url, {
      method: method,
      headers: headers,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store"
    });
  }

  function decode(b64) {
    var bin = atob(b64.replace(/\s/g, ""));
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }
  function encode(text) {
    var bytes = new TextEncoder().encode(text);
    var bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  function emptyBoard() { return { schema_version: 1, cards: [] }; }

  // Read a JSON file from the repository. A missing file reads as `empty`.
  function loadFile(path, empty) {
    var url = "/repos/" + REPO + "/contents/" + path + "?ref=" + BRANCH;
    return request("GET", url, null, getToken()).then(function (res) {
      if (res.status === 404) return { data: empty, sha: null };
      if (!res.ok) throw new Error("GitHub said " + res.status + " when reading " + path + ".");
      return res.json().then(function (file) {
        return { data: JSON.parse(decode(file.content)), sha: file.sha };
      });
    });
  }

  function saveFile(path, data, sha, message) {
    data.updated_at = new Date().toISOString();
    var body = {
      message: message,
      content: encode(JSON.stringify(data, null, 2) + "\n"),
      branch: BRANCH
    };
    if (sha) body.sha = sha;
    return request("PUT", "/repos/" + REPO + "/contents/" + path, body, getToken());
  }

  // Load, change, save. If someone else saved in between, start again once.
  function changeFile(path, empty, apply, message, attempt) {
    attempt = attempt || 1;
    if (!getToken()) {
      return Promise.reject(new Error("NO_TOKEN"));
    }
    return loadFile(path, empty()).then(function (current) {
      var result = apply(current.data);
      return saveFile(path, current.data, current.sha, message).then(function (res) {
        if ((res.status === 409 || res.status === 422) && attempt < 2) {
          return changeFile(path, empty, apply, message, attempt + 1);
        }
        if (res.status === 401 || res.status === 403) {
          throw new Error("GitHub refused the key. Check it on the Settings page.");
        }
        if (!res.ok) throw new Error("GitHub said " + res.status + " when saving " + path + ".");
        return { data: current.data, result: result };
      });
    });
  }

  function load() {
    return loadFile(PATH, emptyBoard()).then(function (c) { return { board: c.data, sha: c.sha }; });
  }

  function change(apply, message) {
    return changeFile(PATH, emptyBoard, apply, message).then(function (done) {
      return { board: done.data, result: done.result };
    });
  }

  // --- board rules (mirror reader/board.py) ------------------------------------

  var ID_KEYS = ["arxiv", "doi", "s2", "openalex"];

  function sameIds(a, b) {
    return ID_KEYS.some(function (k) {
      return a[k] && b[k] && String(a[k]).toLowerCase() === String(b[k]).toLowerCase();
    });
  }
  function find(board, ids) {
    for (var i = 0; i < board.cards.length; i++) {
      if (sameIds(board.cards[i].paper.ids, ids)) return board.cards[i];
    }
    return null;
  }
  function inColumn(board, column) {
    return board.cards.filter(function (c) { return c.column === column; });
  }
  function today() {
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }

  function addCard(board, payload) {
    var existing = find(board, payload.paper.ids);
    if (existing) {
      throw new Error("Already on the board, in " + COLUMN_NAMES[existing.column] + ".");
    }
    if (inColumn(board, "to_read").length >= TO_READ_LIMIT) {
      throw new Error("To read already holds " + TO_READ_LIMIT +
        " papers. Start, finish or remove one first.");
    }
    var card = { paper: payload.paper, column: "to_read", source: payload.source, added_on: today() };
    board.cards.push(card);
    return card;
  }

  function moveCard(board, ids, column) {
    var card = find(board, ids);
    if (!card) throw new Error("That paper is no longer on the board.");
    if (card.column === column) return card;
    if (column === "to_read" && inColumn(board, "to_read").length >= TO_READ_LIMIT) {
      throw new Error("To read already holds " + TO_READ_LIMIT + " papers.");
    }
    var moved = Object.assign({}, card, { column: column });
    if (column === "to_read") {
      delete moved.started_on;
      delete moved.finished_on;
    } else if (column === "reading") {
      moved.started_on = card.started_on || today();
      delete moved.finished_on;
    } else {
      moved.started_on = card.started_on || today();
      moved.finished_on = today();
    }
    board.cards = board.cards.filter(function (c) { return c !== card; });
    board.cards.push(moved);
    return moved;
  }

  function removeCard(board, ids) {
    var card = find(board, ids);
    if (!card) throw new Error("That paper is no longer on the board.");
    board.cards = board.cards.filter(function (c) { return c !== card; });
    return card;
  }

  // --- page ------------------------------------------------------------------

  var messageEl = document.getElementById("board-message");
  function say(text, isError) {
    if (!messageEl) return;
    messageEl.hidden = !text;
    messageEl.textContent = text || "";
    messageEl.classList.toggle("error", !!isError);
  }

  function explain(err) {
    if (err && err.message === "NO_TOKEN") {
      say("", false);
      messageEl.hidden = false;
      messageEl.classList.add("error");
      messageEl.textContent = "To save to the board, add your GitHub key on the ";
      var a = document.createElement("a");
      a.href = ROOT + "settings/index.html";
      a.textContent = "Settings page";
      messageEl.appendChild(a);
      messageEl.appendChild(document.createTextNode("."));
      return;
    }
    say(err && err.message ? err.message : "Something went wrong.", true);
  }

  function updateControls(board) {
    document.querySelectorAll(".board-control").forEach(function (el) {
      var ids = JSON.parse(el.dataset.ids);
      var card = find(board, ids);
      var note = el.querySelector(".on-board");
      var button = el.querySelector("[data-board-add]");
      note.hidden = !card;
      note.querySelector("span").textContent = card ? COLUMN_NAMES[card.column] : "";
      button.hidden = !!card;
      button.disabled = false;
    });
  }

  function onAdd(event) {
    var button = event.target.closest("[data-board-add]");
    if (!button) return;
    var control = button.closest(".board-control");
    var payload = JSON.parse(control.dataset.card);
    button.disabled = true;
    say("Saving…");
    change(function (board) { return addCard(board, payload); },
      "Reading board: add " + payload.paper.id)
      .then(function (done) {
        updateControls(done.board);
        renderBoard(done.board);
        say("Added to To read: " + payload.paper.title);
      })
      .catch(function (err) { button.disabled = false; explain(err); });
  }

  // --- board page --------------------------------------------------------------

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function cardLink(paper) {
    var links = paper.links || {};
    return links.abstract_page || links.doi || links.pdf || "#";
  }

  function cardItem(card) {
    var li = el("li", { class: "card" });
    li.appendChild(el("a", { href: cardLink(card.paper) }, card.paper.title));
    var when = card.finished_on ? "Finished " + card.finished_on
      : card.started_on ? "Started " + card.started_on : "Added " + card.added_on;
    li.appendChild(el("span", { class: "meta" }, " " + when));
    var links = el("ul", { class: "links" });
    [["abstract_page", "Abstract page"], ["pdf", "PDF"], ["doi", "DOI"]].forEach(function (pair) {
      var url = card.paper.links && card.paper.links[pair[0]];
      if (!url) return;
      var item = el("li");
      item.appendChild(el("a", { href: url }, pair[1]));
      links.appendChild(item);
    });
    li.appendChild(links);
    var actions = el("p", { class: "actions" });
    var moves = [["reading", "Start"], ["done", "Done"], ["to_read", "Back to To read"]];
    moves.forEach(function (m) {
      if (m[0] === card.column) return;
      var b = el("button", { type: "button", class: "button small", "data-move": m[0] }, m[1]);
      actions.appendChild(b);
    });
    actions.appendChild(el("button", { type: "button", class: "button small quiet", "data-remove": "1" }, "Remove"));
    li.appendChild(actions);
    li.dataset.ids = JSON.stringify(card.paper.ids);
    return li;
  }

  function renderBoard(board) {
    var root = document.getElementById("board");
    if (!root) return;
    root.textContent = "";
    [["to_read", "To read"], ["reading", "Reading"], ["done", "Done"]].forEach(function (col) {
      var cards = inColumn(board, col[0]);
      if (col[0] === "done") {
        cards.sort(function (a, b) { return (b.finished_on || "").localeCompare(a.finished_on || ""); });
      }
      var section = el("section");
      var h2 = el("h2", {}, col[1] + " ");
      h2.appendChild(el("small", {}, col[0] === "to_read" ? cards.length + "/" + TO_READ_LIMIT : String(cards.length)));
      section.appendChild(h2);
      if (!cards.length) {
        section.appendChild(el("p", { class: "meta" }, "Empty."));
      } else {
        var ul = el("ul");
        cards.forEach(function (c) { ul.appendChild(cardItem(c)); });
        section.appendChild(ul);
      }
      root.appendChild(section);
    });
  }

  function onBoardClick(event) {
    var button = event.target.closest("button[data-move], button[data-remove]");
    if (!button) return;
    var ids = JSON.parse(button.closest("li.card").dataset.ids);
    var column = button.dataset.move;
    button.disabled = true;
    say("Saving…");
    var apply = column
      ? function (board) { return moveCard(board, ids, column); }
      : function (board) { return removeCard(board, ids); };
    var verb = column ? "move to " + column : "remove";
    change(apply, "Reading board: " + verb)
      .then(function (done) {
        renderBoard(done.board);
        say(column ? "Moved to " + COLUMN_NAMES[column] + "." : "Removed from the board.");
      })
      .catch(function (err) { button.disabled = false; explain(err); });
  }

  // --- settings page -----------------------------------------------------------

  function checkToken(stateEl) {
    var token = getToken();
    if (!token) { stateEl.textContent = "No key saved in this browser yet."; return; }
    stateEl.textContent = "Checking the saved key…";
    request("GET", "/repos/" + REPO, null, token).then(function (res) {
      if (!res.ok) { stateEl.textContent = "GitHub refused the saved key (" + res.status + ")."; return null; }
      return res.json();
    }).then(function (repo) {
      if (!repo) return;
      stateEl.textContent = repo.permissions && repo.permissions.push
        ? "A key is saved, and it can save the board."
        : "A key is saved, but it can't write to " + REPO + ". Give it Contents: read and write.";
    }).catch(function () { stateEl.textContent = "Couldn't reach GitHub to check the key."; });
  }

  function setupSettings() {
    var form = document.getElementById("token-form");
    if (!form) return;
    var stateEl = document.getElementById("token-state");
    checkToken(stateEl);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var value = form.token.value.trim();
      if (!setToken(value)) { stateEl.textContent = "This browser won't store the key (private mode?)."; return; }
      form.token.value = "";
      checkToken(stateEl);
    });
    document.getElementById("token-forget").addEventListener("click", function () {
      setToken("");
      checkToken(stateEl);
    });
  }

  // --- start -------------------------------------------------------------------

  document.addEventListener("click", onAdd);
  var boardRoot = document.getElementById("board");
  if (boardRoot) boardRoot.addEventListener("click", onBoardClick);
  setupSettings();

  if (document.querySelector(".board-control") || boardRoot) {
    load().then(function (current) {
      updateControls(current.board);
      renderBoard(current.board);
    }).catch(function () {
      // Keep the board as it was when the site was built.
    });
  }

  window.AcademiaBoard = { addCard: addCard, moveCard: moveCard, removeCard: removeCard, find: find };
  window.AcademiaGitHub = { getToken: getToken, changeFile: changeFile };
})();
