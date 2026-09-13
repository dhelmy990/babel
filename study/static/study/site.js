function csrfToken() {
  const token = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith("csrftoken="));
  return token ? decodeURIComponent(token.split("=")[1]) : "";
}

export class RequestError extends Error {
  constructor(message, {status = 0, code = "network_error", body = null} = {}) {
    super(message);
    this.name = "RequestError";
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

/** Make same-origin JSON or multipart requests with Django CSRF protection.
 * Failures throw RequestError with status, machine-readable code, and response body.
 */
export async function requestJSON(url, options = {}) {
  const {method: requestedMethod = "GET", headers: requestedHeaders = {}, body: requestedBody, ...fetchOptions} = options;
  const method = requestedMethod.toUpperCase();
  const headers = new Headers(requestedHeaders);
  let body = requestedBody;
  if (body && !(body instanceof FormData) && typeof body !== "string" && !(body instanceof Blob)) {
    body = JSON.stringify(body);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRFToken", csrfToken());
  let response;
  try {
    response = await fetch(url, {...fetchOptions, method, credentials: "same-origin", headers, body});
  } catch (cause) {
    throw new RequestError("The network request could not be completed.", {body: cause});
  }
  const text = response.status === 204 ? "" : await response.text();
  let parsed = null;
  if (text) {
    try { parsed = JSON.parse(text); } catch { parsed = text; }
  }
  if (!response.ok) {
    const error = parsed && typeof parsed === "object" ? parsed.error : null;
    throw new RequestError(error?.message || `Request failed (${response.status}).`, {
      status: response.status, code: error?.code || "http_error", body: parsed,
    });
  }
  return parsed;
}

async function synchronizeTimezone(session) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (timezone && timezone !== session.timezone) await requestJSON("/api/timezone", {method: "POST", body: {timezone}});
}

document.addEventListener("DOMContentLoaded", async () => {
  const toggle = document.querySelector("[data-mode-toggle]");
  if (toggle) {
    toggle.addEventListener("click", async () => {
      try {
        await requestJSON("/api/mode", {method: "POST", body: {mode: toggle.dataset.mode}});
        window.location.reload();
      } catch (_) {
        // A failed UI preference update leaves the current page usable.
      }
    });
  }
  try {
    const session = await requestJSON("/api/session");
    if (session?.authenticated) await synchronizeTimezone(session);
  } catch (_) {
    // A page can still be read when the optional session update fails.
  }
});
