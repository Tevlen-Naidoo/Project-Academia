// Service worker for Project Academia: shows the daily notification and opens
// the main page when it is tapped. It caches nothing.
"use strict";

self.addEventListener("install", function() { self.skipWaiting(); });
self.addEventListener("activate", function(event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("push", function(event) {
	var data = {};
	if (event.data) {
		try { data = event.data.json(); } catch (e) { data = { body: event.data.text() }; }
	}
	// iOS turns notifications off for a site whose push shows nothing, so always show one.
	event.waitUntil(self.registration.showNotification(data.title || "Project Academia", {
		body: data.body || "Today's papers are ready.",
		icon: "static/icon-192.png",
		badge: "static/icon-192.png",
		tag: data.tag || "daily",
		data: { date: data.date || "" }
	}));
});

self.addEventListener("notificationclick", function(event) {
	event.notification.close();
	var date = (event.notification.data && event.notification.data.date) || "";
	// The date skips any copy of yesterday's page the browser still holds.
	var url = self.registration.scope + "index.html" + (date ? "?d=" + date : "");
	event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
		.then(function(windows) {
			for (var i = 0; i < windows.length; i++) {
				var w = windows[i];
				if (w.url.indexOf(self.registration.scope) === 0 && "navigate" in w) {
					return w.navigate(url).then(function(c) { return (c || w).focus(); })
						.catch(function() { return self.clients.openWindow(url); });
				}
			}
			return self.clients.openWindow(url);
		}));
});
