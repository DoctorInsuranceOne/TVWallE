"use strict";
// TVWallE controller page. Talks to controller.py on the kiosk.

const $ = (id) => document.getElementById(id);
const buttons = () => document.querySelectorAll("button");
let busy = false;
let updating = false;

async function api(path, body) {
  const options = { credentials: "same-origin", cache: "no-store" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  let data = {};
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error(data.error || `The kiosk answered with error ${res.status}.`);
  return data;
}

function say(text, kind) {
  const m = $("message");
  m.textContent = text || "";
  m.className = kind || "";
}

async function loadStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch (e) {
    if (updating) {
      // The update restarts this page's server; it answers again shortly.
      say("Updating: the kiosk is restarting its services…");
      return;
    }
    $("host").textContent = "Not connected";
    say("Can't reach the kiosk. " + e.message, "error");
    return;
  }
  $("host").textContent = s.hostname;
  $("login").hidden = s.authenticated;
  $("control").hidden = !s.authenticated;
  $("login-form").hidden = !s.password_set;
  $("no-password").hidden = s.password_set;
  if (!s.authenticated) return;

  // On the kiosk itself (SSH tunnel) nobody signs in, so there is nothing to sign out of.
  $("logout").hidden = s.local;

  const b = s.browser;
  if (b.answering) {
    $("now-title").textContent = b.title || "(no title)";
    $("now-url").textContent = b.url;
  } else {
    $("now-title").textContent = "The browser isn't answering";
    $("now-url").textContent = "Try Restart browser.";
  }
  $("kept").textContent = s.saved_url
    ? "Opened from here, kept after a restart: " + s.saved_url
    : "Default page: " + s.default_url;

  const field = $("url");
  if (!field.value && document.activeElement !== field) {
    field.value = s.saved_url || s.default_url;
  }

  showSoftware(s.software);
}

// "Nov 20, 4:02 AM" in this browser's time zone.
function when(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const TRIGGERS = { nightly: "nightly", controller: "from this page", "first-boot": "at the first start", manual: "by hand" };

function showSoftware(sw) {
  if (!sw) return;
  updating = sw.updating;
  // The VERSION file: "1.2" (+ a line on how it came) for a release, one
  // free-form line for a copy deployed or built from a Mac.
  const lines = (sw.version || "").split("\n");
  const release = /^\d+(\.\d+)*$/.test(lines[0] || "");
  $("sw-version").textContent = release
    ? "TVWallE " + lines[0]
    : "TVWallE development copy" + (lines[0] ? " (" + lines[0] + ")" : "");

  $("sw-by-hand").hidden = !sw.deployed_by_hand;
  $("sw-by-hand").textContent = sw.deployed_by_hand
    ? "Deployed by hand from a Mac: the nightly update leaves this PC alone. Update now puts the latest release on it."
    : "";

  const u = sw.last_update;
  let last = "";
  if (sw.updating) {
    last = "Updating now" + (u && u.state === "running" && u.started_at ? " (started " + when(u.started_at) + ")" : "") +
      ". The screen and this page come back by themselves.";
  } else if (u) {
    const at = when(u.finished_at || u.started_at);
    const how = TRIGGERS[u.trigger] || u.trigger || "";
    last = "Last update " + [at, how].filter(Boolean).join(", ") + ": " + (u.message || u.state);
  } else {
    last = "No update has run on this PC yet.";
  }
  $("sw-last").textContent = last;

  const c = sw.last_check;
  $("sw-check").hidden = !c;
  if (c) {
    $("sw-check").textContent = c.ok
      ? "On GitHub (" + when(c.checked_at) + "): TVWallE " + c.latest + (c.newer ? ", newer than this one." : ", the one installed.")
      : "Check at " + when(c.checked_at) + " didn't work: " + c.error;
  }
  if (!busy) {
    $("update-start").disabled = sw.updating;
    $("update-check").disabled = sw.updating;
  }
}

async function act(path, body, confirmText) {
  if (busy) return;
  if (confirmText && !window.confirm(confirmText)) return;
  busy = true;
  buttons().forEach((b) => { b.disabled = true; });
  say("Working…");
  try {
    const r = await api(path, body || {});
    say(r.message, "ok");
  } catch (e) {
    say(e.message, "error");
  } finally {
    busy = false;
    buttons().forEach((b) => { b.disabled = false; });
    setTimeout(loadStatus, 1500);
  }
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/login", { password: $("password").value });
    $("password").value = "";
    say("");
    loadStatus();
  } catch (e) {
    say(e.message, "error");
  }
});

$("refresh").addEventListener("click", () => act("/api/refresh"));
$("restart").addEventListener("click", () =>
  act("/api/restart", {}, "Restart the browser? The screen goes dark for a few seconds."));
$("open-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  act("/api/open", { url: $("url").value.trim() });
});
$("reset").addEventListener("click", () => {
  $("url").value = "";
  act("/api/reset");
});
$("update-check").addEventListener("click", () => act("/api/update/check"));
$("update-start").addEventListener("click", () =>
  act("/api/update/start", {},
    "Install the latest TVWallE release now? The screen restarts once it's installed (a few minutes)."));
$("logout").addEventListener("click", async () => {
  try { await api("/api/logout", {}); } catch { /* signed out anyway */ }
  say("");
  loadStatus();
});

loadStatus();
// Every 10 seconds; every 3 while an update runs (the page reconnects after it).
setInterval(() => { if (!document.hidden && !busy) loadStatus(); }, 10000);
setInterval(() => { if (updating && !document.hidden && !busy) loadStatus(); }, 3000);
