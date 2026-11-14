// Calls to the local server. The page key travels in an HttpOnly cookie (never in the URL
// after the first open); writes also send X-NLH-Req, which a cross-site form cannot.

/** Fetch JSON from the app; throws an Error with the server's plain message. */
export async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-NLH-Req": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) {
    const err = new Error(data.error || "The app did not answer as expected. Try again.");
    err.data = data; // extra fields, such as needs_ack
    err.status = res.status;
    throw err;
  }
  return data;
}
