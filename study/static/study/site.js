"use strict";


function csrfToken() {
  const token = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith("csrftoken="));
  return token ? decodeURIComponent(token.split("=")[1]) : "";
}


async function jsonPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    },
    body: JSON.stringify(payload),
  });
  return {response, body: await response.json()};
}


async function synchronizeTimezone(session) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (timezone && timezone !== session.timezone) {
    await jsonPost("/api/timezone", {timezone});
  }
}


document.addEventListener("DOMContentLoaded", async () => {
  const toggle = document.querySelector("[data-mode-toggle]");
  if (toggle) {
    toggle.addEventListener("click", async () => {
      const {response} = await jsonPost("/api/mode", {mode: toggle.dataset.mode});
      if (response.ok) window.location.reload();
    });
  }

  const sessionResponse = await fetch("/api/session", {credentials: "same-origin"});
  if (sessionResponse.ok) {
    const session = await sessionResponse.json();
    if (session.authenticated) await synchronizeTimezone(session);
  }
});
