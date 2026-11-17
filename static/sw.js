// Service worker: lets browsers install the site as an app.
// Nothing is cached on purpose: client records are confidential and must
// always come fresh from the server, never from a copy stored on the device.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => event.respondWith(fetch(event.request)));
