// Project Iblis is closed: every address serves the closing notice; the service routes answer 410 Gone.
const GONE = ["/mcp", "/api/", "/auth/", "/approve", "/join", "/kit/", "/agents.md"];
export default {
  async fetch(req, env) {
    const p = new URL(req.url).pathname;
    if (GONE.some((g) => p === g || p.startsWith(g))) {
      return new Response(JSON.stringify({ error: "Project Iblis is complete; this service is closed." }),
        { status: 410, headers: { "content-type": "application/json" } });
    }
    const page = await env.ASSETS.fetch(new Request(new URL("/index.html", req.url)));
    return new Response(page.body, { status: p === "/" || p === "/index.html" ? 200 : 410,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
  },
};
