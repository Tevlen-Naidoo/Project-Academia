// Web Push: the "Notifications" part of the Settings page.
//
// Turning notifications on asks the browser for a push subscription and saves it
// in data/push/subscriptions.json through the GitHub API, with the same GitHub key
// the reading board uses. After each daily run, `python -m notifier send` pushes a
// message to every saved device. On iPhone and iPad this only works from the
// Home Screen app (iOS 16.4 or later), which keeps its own copy of the GitHub key.
(function () {
  "use strict";

  var html = document.documentElement;
  var ROOT = html.dataset.root || "";
  var KEY = html.dataset.pushKey || "";
  var PATH = "data/push/subscriptions.json";
  var SAVED_KEY = "academia.pushEndpoint";
  var gh = window.AcademiaGitHub;

  var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  var isApple = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = window.navigator.standalone === true ||
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

  function deviceName() {
    var ua = navigator.userAgent;
    if (/iPad/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)) return "iPad";
    if (/iPhone|iPod/.test(ua)) return "iPhone";
    if (/Android/.test(ua)) return "Android";
    if (/Macintosh/.test(ua)) return "Mac";
    if (/Windows/.test(ua)) return "Windows";
    return "Other";
  }
  function today() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0");
  }
  function keyBytes(b64) {
    var s = (b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    var bin = atob(s);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function emptyFile() { return { schema_version: 1, subscriptions: [] }; }

  function saveSubscription(sub) {
    var json = sub.toJSON();
    var entry = { endpoint: json.endpoint, keys: json.keys, device: deviceName(), added_on: today() };
    return gh.changeFile(PATH, emptyFile, function (file) {
      file.subscriptions = (file.subscriptions || []).filter(function (s) {
        return s.endpoint !== entry.endpoint;
      });
      file.subscriptions.push(entry);
    }, "Notifications: add " + entry.device).then(function () { remember(entry.endpoint); });
  }

  function removeSubscription(endpoint) {
    return gh.changeFile(PATH, emptyFile, function (file) {
      file.subscriptions = (file.subscriptions || []).filter(function (s) {
        return s.endpoint !== endpoint;
      });
    }, "Notifications: remove " + deviceName()).then(function () { remember(""); });
  }

  function registration() {
    return navigator.serviceWorker.register(ROOT + "sw.js").then(function () {
      return navigator.serviceWorker.ready;
    });
  }

  // On any page: if this device's subscription changed since it was saved, save it again.
  function resync() {
    if (!supported || !KEY || !gh || !gh.getToken()) return;
    if (Notification.permission !== "granted") return;
    navigator.serviceWorker.getRegistration(ROOT).then(function (reg) {
      return reg && reg.pushManager.getSubscription();
    }).then(function (sub) {
      if (sub && sub.endpoint !== remembered()) return saveSubscription(sub);
    }).catch(function () { /* try again on the next page */ });
  }

  // --- settings page -----------------------------------------------------------

  function setup(section) {
    var state = document.getElementById("push-state");
    var ios = document.getElementById("push-ios");
    var on = document.getElementById("push-on");
    var off = document.getElementById("push-off");

    function show(text, subscribed) {
      state.textContent = text;
      on.hidden = subscribed !== false;
      off.hidden = subscribed !== true;
      on.disabled = false;
      off.disabled = false;
    }

    if (!KEY) { show("Notifications aren't set up for this site yet (the VAPID key is missing)."); return; }
    if (!supported) {
      if (isApple && !standalone) {
        ios.hidden = false;
        show("On iPhone and iPad, notifications only work from the Home Screen app.");
      } else {
        show("This browser can't receive notifications.");
      }
      return;
    }

    var ready = registration();

    function refresh() {
      if (Notification.permission === "denied") {
        show("Notifications are blocked for this site. Allow them in your device's settings, then come back.");
        return;
      }
      ready.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
        if (sub && Notification.permission === "granted") {
          show("Notifications are on for this " + deviceName() + ".", true);
        } else {
          show("Notifications are off for this device.", false);
        }
      }).catch(function () { show("Couldn't start notifications in this browser."); });
    }

    on.addEventListener("click", function () {
      if (!gh.getToken()) {
        state.textContent = "Save your GitHub key above first. It's needed to save this device.";
        return;
      }
      on.disabled = true;
      state.textContent = "Asking for permission…";
      // Ask straight from the tap: iOS only shows the prompt for a tap.
      Notification.requestPermission().then(function (permission) {
        if (permission !== "granted") throw new Error("PERMISSION");
        return ready;
      }).then(function (reg) {
        var options = { userVisibleOnly: true, applicationServerKey: keyBytes(KEY) };
        return reg.pushManager.subscribe(options).catch(function () {
          // An old subscription made with another key blocks a new one.
          return reg.pushManager.getSubscription().then(function (old) {
            return (old ? old.unsubscribe() : Promise.resolve()).then(function () {
              return reg.pushManager.subscribe(options);
            });
          });
        });
      }).then(function (sub) {
        state.textContent = "Saving this device…";
        return saveSubscription(sub);
      }).then(function () {
        show("Notifications are on for this " + deviceName() + ". The next one comes with tomorrow's papers.", true);
      }).catch(function (err) {
        on.disabled = false;
        if (err && err.message === "PERMISSION") { refresh(); return; }
        if (err && err.message === "NO_TOKEN") {
          state.textContent = "Save your GitHub key above first. It's needed to save this device.";
          return;
        }
        state.textContent = (err && err.message) || "Couldn't turn on notifications.";
      });
    });

    off.addEventListener("click", function () {
      off.disabled = true;
      state.textContent = "Turning off…";
      ready.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
        if (!sub) return null;
        var endpoint = sub.endpoint;
        return sub.unsubscribe().then(function () {
          return gh.getToken() ? removeSubscription(endpoint) : null;
        });
      }).then(function () { refresh(); }).catch(function (err) {
        off.disabled = false;
        state.textContent = (err && err.message) || "Couldn't turn off notifications.";
      });
    });

    refresh();
  }

  var section = document.getElementById("push");
  if (section) setup(section);
  resync();
})();
