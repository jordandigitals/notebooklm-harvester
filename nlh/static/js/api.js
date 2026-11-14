// Calls to the local server. The page key travels in an HttpOnly cookie (never in the URL
// after the first open); writes also send X-NLH-Req, which a cross-site form cannot.

/** Show or clear a banner when the laptop cannot be reached (laptop off, app closed, other Wi-Fi). */
function unreachable(on) {
  let n = document.getElementById("unreachable");
  if (!on) { if (n) n.remove(); return; }
  if (n) return;
  n = document.createElement("div");
  n.id = "unreachable";
  n.className = "notice notice-bad unreachable";
  n.setAttribute("role", "alert");
  n.textContent = "Cannot reach the laptop. Check that it is on, the app is running and this phone is on the same Wi-Fi. This page does not play offline.";
  (document.getElementById("banners") || document.body).prepend(n);
}

/** Fetch JSON from the app; throws an Error with the server's plain message. */
export async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-NLH-Req": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    unreachable(true);
    throw new Error("Cannot reach the laptop. Check that it is on, the app is running and you are on the same Wi-Fi.");
  }
  unreachable(false);
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
