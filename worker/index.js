// Project Iblis service (Cloudflare Worker + D1). The workflow follows f3decomp.com/join:
//
//   1. The contributor tells their agent: "Read <site>/agents.md and set yourself up to contribute to Project Iblis."
//   2. The agent calls connect, shows a link and a short code; the contributor approves once with GitHub (this also
//      accepts the contributor terms). The agent collects a private key that acts as the contributor.
//   3. Work loop, over MCP (POST /mcp) or plain HTTPS (POST /api/v1/<tool>):
//        status   tier, limits, current work, strikes, recent verdicts
//        bands    open work per size band, and which bands this contributor may take
//        next     hands out one function (a silent 2-hour lease keeps two agents off it) with its packet
//        packet   the briefing again for a function you are working on
//        submit   a function the local checker reported as a match
//        giveup   return a function with a one-line reason and your closest code
//   4. The build host (owner's machine, holds the game) verifies every submission and reports verdicts.
//
// The service stores source text and notes only. It refuses binary content and never handles game files.

const enc = new TextEncoder();
const iso = (t = Date.now()) => new Date(t).toISOString().replace(/\.\d+Z$/, "Z");
const hoursFromNow = (h) => iso(Date.now() + h * 3600e3);
const LEASE_HOURS = 2;
const SIGN_IN_TTL_MS = 900e3;
const BROWSER_TTL_MS = 30 * 864e5;
const MAX_JSON_BYTES = 2 * 1024 * 1024;

const TIERS = [
  // name, leases at once, submissions per day, largest function in bytes (null = any)
  { name: "swarm", leases: 150, perDay: 5000, maxBytes: null },
  { name: "power", leases: 20, perDay: 1000, maxBytes: null },
  { name: "trusted", leases: 12, perDay: 500, maxBytes: null },
  { name: "regular", leases: 8, perDay: 200, maxBytes: 1023 },
  { name: "new", leases: 4, perDay: 60, maxBytes: 511 },
];
const BANDS = [
  { band: "tiny", min: 0, max: 63 },
  { band: "small", min: 64, max: 255 },
  { band: "medium", min: 256, max: 511 },
  { band: "large", min: 512, max: 1023 },
  { band: "huge", min: 1024, max: null },
];

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const bad = (status, msg) => { throw new ApiError(status, msg); };
function requireIntake(env) {
  if (env.CONTRIBUTIONS_ENABLED !== 'true') bad(503, 'Contributions are temporarily paused while host verification is validated.');
}
// Invite-only (soft launch): INVITED_GITHUB lists GitHub logins or numeric ids, comma-separated. Empty or unset = open.
function invitedSet(env) {
  return new Set(String(env.INVITED_GITHUB || "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean));
}
function requireInvited(env, user) {
  const allowed = invitedSet(env);
  if (allowed.size && !allowed.has(String(user.login || "").toLowerCase()) && !allowed.has(String(user.id)))
    bad(403, "Project Iblis is invite-only for now; ask the maintainer for an invitation.");
}
function requireRotatedCredentials(env) {
  if (env.CREDENTIALS_ROTATED !== 'true') bad(503, 'Host and GitHub sign-in are temporarily paused until credential rotation is complete.');
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}
async function sha256(text) {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function randomHex(n) {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}
function userCode() {
  const A = "BCDFGHJKLMNPQRSTVWXZ23456789";
  const b = new Uint8Array(8);
  crypto.getRandomValues(b);
  const s = [...b].map((x) => A[x % A.length]).join("");
  return s.slice(0, 4) + "-" + s.slice(4);
}
function normVa(va) {
  const m = /^(?:0x)?([0-9a-f]{1,8})$/i.exec(String(va ?? "").trim());
  return m ? "0x" + m[1].toUpperCase().padStart(8, "0") : null;
}
async function readJson(req) {
  if (!req.body) bad(400, 'request body must be a JSON object');
  const reader = req.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_JSON_BYTES) {
        await reader.cancel().catch(() => {});
        bad(413, 'JSON request body exceeds 2 MiB');
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (!body || typeof body !== 'object' || Array.isArray(body)) bad(400, 'request body must be a JSON object');
    return body;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    bad(400, 'request body must be a valid JSON object');
  } finally {
    reader.releaseLock();
  }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Source and notes must be plain text: no NUL bytes, no long encoded blobs or byte dumps that could carry game bytes.
function checkText(name, s, max, isSource = false) {
  if (s == null || s === "") return;
  if (typeof s !== "string") bad(400, `${name} must be text`);
  if (enc.encode(s).length > max) bad(400, `${name} is larger than ${max} bytes`);
  if (s.includes("\u0000")) bad(400, `${name} contains a NUL byte`);
  if (/[A-Za-z0-9+/=]{400,}/.test(s)) bad(400, `${name} contains a long encoded blob; send source text only`);
  if (/(?:\b[0-9A-Fa-f]{2}\b[ ,]+){48,}/.test(s)) bad(400, `${name} contains a byte dump; never send machine code`);
  if (/\b(?:push|mov|lea|call|jmp|ret)\s+(?:e[a-d]x|e[sd]i|e[sb]p|dword|byte|0x)/i.test(s) &&
      (s.match(/\n\s*[0-9A-F]{6,8}[: ]/gi) || []).length > 8)
    bad(400, `${name} looks like a disassembly listing; describe differences in words`);
  if (isSource && /__asm|\b_emit\b|__declspec\s*\(\s*naked|#pragma\s+(?:optimize|code_seg)/.test(s))
    bad(400, "source uses a forbidden construct (inline asm, _emit, naked, #pragma optimize or code_seg)");
}

// ---------------------------------------------------------------- contributors

// An agent sends its key as a bearer token; the account page sends the browser's session cookie. Cookie requests must
// come from our own pages (Origin check) and carry JSON, so another site cannot act for a signed-in visitor.
function keyFrom(env, req) {
  const m = /^Bearer\s+(\S+)$/.exec(req.headers.get("authorization") || "");
  if (m) return m[1];
  const c = /(?:^|;\s*)iblis_session=([^;]+)/.exec(req.headers.get("cookie") || "");
  if (!c) return null;
  if (req.method !== "GET") {
    const origin = req.headers.get("origin");
    if (origin !== new URL(env.SITE_URL).origin || !(req.headers.get("content-type") || "").startsWith("application/json"))
      return null;
  }
  return c[1];
}

async function userFromKey(env, req) {
  const k = keyFrom(env, req);
  if (!k) return null;
  const m = [null, k];
  return env.DB.prepare(
    `SELECT u.id, u.login, u.show_name, u.paused_until FROM keys k JOIN users u ON u.id = k.user_id
      WHERE k.hash = ? AND k.revoked_utc IS NULL
        AND (COALESCE(k.label, '') != 'browser' OR k.created_utc > ?)`
  ).bind(await sha256(m[1]), iso(Date.now() - BROWSER_TTL_MS)).first();
}
function isHost(env, req) {
  const m = /^Bearer\s+(\S+)$/.exec((req.headers.get("authorization") || "").trim());
  const want = String(env.HOST_KEY || "").trim();
  return !!(m && want && m[1] === want);
}

async function record(env, user) {
  const r = await env.DB.prepare(
    `SELECT SUM(status = 'verified') AS verified, SUM(status = 'failed') AS failed
       FROM results WHERE user_id = ? AND kind = 'match'`
  ).bind(user.id).first();
  return { verified: r.verified || 0, failed: r.failed || 0 };
}
function tierFor({ verified: v, failed: f }) {
  const rate = v + f ? f / (v + f) : 0;
  const power = (v >= 25 && f === 0) || (v >= 60 && rate <= 0.05);
  if (power && v >= 300 && rate <= 0.02) return TIERS[0];
  if (power) return TIERS[1];
  if (v >= 15 && rate <= 0.1) return TIERS[2];
  if (v >= 5) return TIERS[3];
  return TIERS[4];
}

// Leases that ran out go back to the pool; too many in a day pauses `next` for three hours.
async function expireLeases(env) {
  const t = iso();
  // D1 batches are transactions: capture and remove the same expired rows without a stale read/delete gap.
  const [captured] = await env.DB.batch([
    env.DB.prepare(`INSERT INTO expired (va, user_id, expired_utc)
      SELECT va, user_id, ? FROM leases WHERE expires_utc <= ? RETURNING user_id`).bind(t, t),
    env.DB.prepare('DELETE FROM leases WHERE expires_utc <= ?').bind(t),
  ]);
  const stale = captured.results;
  const dayAgo = iso(Date.now() - 864e5);
  for (const uid of new Set(stale.map((l) => l.user_id))) {
    const e = (await env.DB.prepare("SELECT COUNT(*) AS n FROM expired WHERE user_id = ? AND expired_utc > ?").bind(uid, dayAgo).first()).n;
    const done = (await env.DB.prepare("SELECT COUNT(*) AS n FROM results WHERE user_id = ? AND created_utc > ?").bind(uid, dayAgo).first()).n;
    if (e > 6 && e > (e + done) / 4)
      await env.DB.prepare("UPDATE users SET paused_until = MAX(COALESCE(paused_until, ''), ?) WHERE id = ?").bind(hoursFromNow(3), uid).run();
  }
}

// Three failed verifications in a week pause submissions for a week, if they are at least 1 in 20 of that week's verdicts.
async function applyStrikes(env, uid) {
  const weekAgo = iso(Date.now() - 7 * 864e5);
  const r = await env.DB.prepare(
    `SELECT SUM(status = 'failed') AS failed, SUM(status IN ('verified', 'failed')) AS decided,
            MAX(CASE WHEN status = 'failed' THEN decided_utc END) AS last_failed
       FROM results WHERE user_id = ? AND decided_utc > ?`
  ).bind(uid, weekAgo).first();
  if ((r.failed || 0) >= 3 && r.failed * 20 >= (r.decided || 0))
    await env.DB.prepare("UPDATE users SET paused_until = MAX(COALESCE(paused_until, ''), ?) WHERE id = ?")
      .bind(iso(Date.parse(r.last_failed) + 7 * 864e5), uid).run();
}

function bandOf(len) { return BANDS.find((b) => len >= b.min && (b.max === null || len <= b.max)).band; }

async function packetFor(env, va) {
  const f = await env.DB.prepare("SELECT * FROM functions WHERE va = ?").bind(va).first();
  if (!f) bad(404, `${va} is not in the queue`);
  return {
    va: f.va, length: f.length, band: bandOf(f.length), module: f.module, name: f.name, body_sha256: f.body_sha256,
    flags: f.flags || env.DEFAULT_FLAGS,
    reference: f.reference,
    previous_attempt: f.best_note ? { note: f.best_note, closest_source: f.best_source } : null,
    lease_hours: LEASE_HOURS,
    local_steps: [
      `save this packet as work/${f.va}/packet.json`,
      `python iblis_check.py brief ${f.va}   (reads YOUR Game.exe locally; never upload it)`,
      `write work/${f.va}/candidate.cpp with "// ${f.va}" on the line above the function`,
      `python iblis_check.py check ${f.va}`,
      "submit on MATCH; otherwise giveup with a one-line reason and your closest code",
    ],
    rules: [
      "Readable C++ that compiles to the original bytes under the given flags.",
      "No inline assembly, _emit, naked functions, byte arrays as code, #pragma optimize, or flag changes.",
      "Declare every callee and global with a trailing // 0x<address> marker.",
      "Never post or submit game files, machine code, disassembly, object files or builds.",
      "One form of the function per compile; no extra copies to test spellings.",
    ],
    repo: env.REPO_URL,
  };
}

const tools = {
  async status(env, user) {
    await expireLeases(env);
    const rec = await record(env, user);
    const tier = tierFor(rec);
    const working = (await env.DB.prepare(
      `SELECT l.va, f.length, f.module, l.expires_utc FROM leases l JOIN functions f ON f.va = l.va
        WHERE l.user_id = ? ORDER BY l.leased_utc`).bind(user.id).all()).results;
    const today = (await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM results WHERE user_id = ? AND kind = 'match' AND created_utc > ?"
    ).bind(user.id, iso(Date.now() - 864e5)).first()).n;
    const weekAgo = iso(Date.now() - 7 * 864e5);
    const strikes = (await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM results WHERE user_id = ? AND status = 'failed' AND decided_utc > ?"
    ).bind(user.id, weekAgo).first()).n;
    const recent = (await env.DB.prepare(
      `SELECT va, kind, status, host_note, created_utc, decided_utc FROM results WHERE user_id = ?
        ORDER BY id DESC LIMIT 10`).bind(user.id).all()).results;
    return {
      login: user.login, shown_on_board_as: user.show_name ? user.login : "anonymous",
      tier: tier.name, limits: { at_once: tier.leases, submissions_per_day: tier.perDay, max_function_bytes: tier.maxBytes },
      submitted_last_24h: today, verified: rec.verified, failed: rec.failed, strikes_this_week: strikes,
      paused_until: user.paused_until && user.paused_until > iso() ? user.paused_until : null,
      working_on: working, recent_verdicts: recent,
    };
  },

  async bands(env, user) {
    await expireLeases(env);
    const tier = tierFor(await record(env, user));
    const rows = (await env.DB.prepare(
      `SELECT f.length FROM functions f LEFT JOIN leases l ON l.va = f.va WHERE f.state = 'open' AND l.va IS NULL`
    ).all()).results;
    return BANDS.map((b) => {
      const n = rows.filter((r) => r.length >= b.min && (b.max === null || r.length <= b.max)).length;
      const allowed = tier.maxBytes === null || b.min <= tier.maxBytes;
      return { band: b.band, bytes: b.max === null ? `${b.min}+` : `${b.min}-${b.max}`, open: n, allowed };
    });
  },

  async next(env, user, args = {}) {
    requireIntake(env);
    requireInvited(env, user);
    await expireLeases(env);
    if (user.paused_until && user.paused_until > iso()) bad(429, `new work is paused until ${user.paused_until}`);
    const tier = tierFor(await record(env, user));
    const held = (await env.DB.prepare("SELECT COUNT(*) AS n FROM leases WHERE user_id = ?").bind(user.id).first()).n;
    if (held >= tier.leases) bad(409, `your tier (${tier.name}) works on ${tier.leases} at once; submit or give up one first`);
    const max = tier.maxBytes ?? 1 << 30;
    let pick;
    if (args.va) {
      // Coming back to a function you worked on before.
      const va = normVa(args.va) || bad(400, "va must look like 0x00401220");
      const before = await env.DB.prepare("SELECT 1 FROM results WHERE va = ? AND user_id = ? LIMIT 1").bind(va, user.id).first();
      if (!before) bad(403, "you can ask for a specific function only if you worked on it before");
      pick = await env.DB.prepare(
        `SELECT f.va FROM functions f LEFT JOIN leases l ON l.va = f.va
          WHERE f.va = ? AND f.state = 'open' AND l.va IS NULL AND f.length <= ?`).bind(va, max).first();
      if (!pick) bad(409, `${va} is not available right now`);
    } else {
      const band = args.band ? BANDS.find((b) => b.band === args.band) : null;
      if (args.band && !band) bad(400, `band must be one of ${BANDS.map((b) => b.band).join(", ")}`);
      const lo = band ? band.min : 0, hi = Math.min(max, band && band.max !== null ? band.max : 1 << 30);
      // Never-tried functions first, then the fewest tries; smallest first within that.
      pick = await env.DB.prepare(
        `SELECT f.va FROM functions f LEFT JOIN leases l ON l.va = f.va
          WHERE f.state = 'open' AND l.va IS NULL AND f.length BETWEEN ? AND ?
            AND f.va NOT IN (SELECT va FROM results WHERE user_id = ? AND kind = 'giveup')
          ORDER BY f.tries > 0, f.tries, f.length, f.va LIMIT 1`).bind(lo, hi, user.id).first();
      if (!pick) bad(404, "no open function fits your tier and band right now");
    }
    const t = iso();
    try {
      const [claimed] = await env.DB.batch([
        env.DB.prepare(`INSERT INTO leases (va, user_id, leased_utc, expires_utc)
          SELECT va, ?, ?, ? FROM functions WHERE va = ? AND state = 'open'
            AND (SELECT COUNT(*) FROM leases WHERE user_id = ?) < ?
            AND NOT EXISTS (SELECT 1 FROM users WHERE id = ? AND paused_until > ?)
          ON CONFLICT(va) DO NOTHING RETURNING va`)
          .bind(user.id, t, hoursFromNow(LEASE_HOURS), pick.va, user.id, tier.leases, user.id, t),
        env.DB.prepare("UPDATE functions SET tries = tries + 1 WHERE va = ? AND changes() > 0").bind(pick.va),
      ]);
      if (!claimed.results.length) bad(409, 'function or lease capacity is no longer available; ask again');
    } catch {
      bad(409, "another agent took that function a moment ago; ask again");
    }
    return packetFor(env, pick.va);
  },

  async packet(env, user, args = {}) {
    const va = normVa(args.va) || bad(400, "va is required");
    const l = await env.DB.prepare("SELECT 1 FROM leases WHERE va = ? AND user_id = ? AND expires_utc > ?").bind(va, user.id, iso()).first();
    if (!l) bad(404, `you are not working on ${va}; use next`);
    return packetFor(env, va);
  },

  async submit(env, user, args = {}) {
    requireIntake(env);
    requireInvited(env, user);
    const va = normVa(args.va) || bad(400, "va is required");
    const l = await env.DB.prepare("SELECT 1 FROM leases WHERE va = ? AND user_id = ? AND expires_utc > ?").bind(va, user.id, iso()).first();
    if (!l) bad(404, `you are not working on ${va} (its 2 hours may have run out; ask for it again with next)`);
    if (user.paused_until && user.paused_until > iso()) bad(429, `submissions are paused until ${user.paused_until}`);
    if (!["RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"].includes(args.local_status))
      bad(400, "submit only what the local checker reported as RAW_EQUAL or RELOCATION_RESOLVED_EQUAL; otherwise giveup");
    const tier = tierFor(await record(env, user));
    const today = (await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM results WHERE user_id = ? AND kind = 'match' AND created_utc > ?"
    ).bind(user.id, iso(Date.now() - 864e5)).first()).n;
    if (today >= tier.perDay) bad(429, `your tier (${tier.name}) submits ${tier.perDay} a day`);
    if (!args.source || !String(args.source).trim()) bad(400, "source is required");
    checkText("source", args.source, Number(env.MAX_SOURCE_BYTES || 262144), true);
    checkText("notes", args.notes, 65536);
    if (!new RegExp("//\\s*0x0*" + va.slice(2).replace(/^0+/, ""), "i").test(args.source))
      bad(400, `source needs a "// ${va}" line directly above the function`);
    const [inserted] = await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO results (va, user_id, kind, model, source, notes, local_status, body_sha256, status)
         SELECT l.va, l.user_id, 'match', ?, ?, ?, ?, ?, 'pending'
         FROM leases l JOIN functions f ON f.va = l.va
         WHERE l.va = ? AND l.user_id = ? AND l.expires_utc > ? AND f.state = 'open'
           AND NOT EXISTS (SELECT 1 FROM users WHERE id = ? AND paused_until > ?)
           AND (SELECT COUNT(*) FROM results WHERE user_id = ? AND kind = 'match' AND created_utc > ?) < ?
         RETURNING id`
      ).bind(String(args.model || "").slice(0, 80), args.source, args.notes || null,
             args.local_status, String(args.body_sha256 || "").slice(0, 64), va, user.id, iso(),
             user.id, iso(), user.id, iso(Date.now() - 864e5), tier.perDay),
      env.DB.prepare("DELETE FROM leases WHERE va = ? AND user_id = ? AND changes() > 0").bind(va, user.id),
      env.DB.prepare("UPDATE functions SET state = 'pending' WHERE va = ? AND changes() > 0").bind(va),
    ]);
    const r = inserted.results[0];
    if (!r) bad(409, 'lease, function or submission allowance is no longer available');
    return { result: r.id, va, status: "pending", note: "The build host verifies it; status shows the verdict." };
  },

  async giveup(env, user, args = {}) {
    const va = normVa(args.va) || bad(400, "va is required");
    const l = await env.DB.prepare("SELECT 1 FROM leases WHERE va = ? AND user_id = ? AND expires_utc > ?").bind(va, user.id, iso()).first();
    if (!l) bad(404, `you are not working on ${va}`);
    const reason = String(args.reason || "").trim();
    if (!reason) bad(400, "give a one-line reason: what differs and what you tried");
    checkText("reason", reason, 2000);
    checkText("source", args.source, Number(env.MAX_SOURCE_BYTES || 262144), true);
    const [inserted] = await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO results (va, user_id, kind, model, source, notes, status)
         SELECT l.va, l.user_id, 'giveup', ?, ?, ?, 'giveup' FROM leases l JOIN functions f ON f.va = l.va
         WHERE l.va = ? AND l.user_id = ? AND l.expires_utc > ? AND f.state = 'open' RETURNING id`
      ).bind(String(args.model || "").slice(0, 80), args.source || null, reason, va, user.id, iso()),
      env.DB.prepare("DELETE FROM leases WHERE va = ? AND user_id = ? AND changes() > 0").bind(va, user.id),
      // The next agent starts from the latest closest code and reason.
      env.DB.prepare(
        "UPDATE functions SET best_note = ?, best_source = COALESCE(?, best_source) WHERE va = ? AND changes() > 0"
      ).bind(reason, args.source || null, va),
    ]);
    if (!inserted.results.length) bad(409, 'lease or function is no longer available');
    return { va, returned: true, note: "Giving up never counts against you." };
  },
};

const TOOL_SCHEMAS = {
  status: { description: "Your tier, limits, current work, strikes and recent verdicts.", properties: {} },
  bands: { description: "Open work per size band, and which bands you may take.", properties: {} },
  next: {
    description: "Hands out one function with its packet (briefing). Optional: band, or va of a function you worked on before.",
    properties: { band: { type: "string", enum: BANDS.map((b) => b.band) }, va: { type: "string" } },
  },
  packet: { description: "The briefing again for a function you are working on.", properties: { va: { type: "string" } }, required: ["va"] },
  submit: {
    description: "Send a function the local checker reported as RAW_EQUAL or RELOCATION_RESOLVED_EQUAL.",
    properties: {
      va: { type: "string" }, source: { type: "string", description: "candidate.cpp exactly as compiled" },
      local_status: { type: "string", enum: ["RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"] },
      body_sha256: { type: "string" }, notes: { type: "string", description: "work notes in words; name your model" },
      model: { type: "string" },
    },
    required: ["va", "source", "local_status"],
  },
  giveup: {
    description: "Return a function with a one-line reason and your closest code; the next agent starts from it.",
    properties: { va: { type: "string" }, reason: { type: "string" }, source: { type: "string" }, model: { type: "string" } },
    required: ["va", "reason"],
  },
};

// ---------------------------------------------------------------- MCP (streamable HTTP, JSON responses)

async function mcp(env, req) {
  const msg = await readJson(req);
  const reply = (result) => json({ jsonrpc: "2.0", id: msg.id, result });
  const err = (code, message) => json({ jsonrpc: "2.0", id: msg.id ?? null, error: { code, message } });
  if (msg.method === "initialize")
    return reply({ protocolVersion: msg.params?.protocolVersion || "2025-06-18", capabilities: { tools: {} },
                   serverInfo: { name: "project-iblis", version: "1.0.0" } });
  if (msg.id === undefined) return new Response(null, { status: 202 });  // notifications
  if (msg.method === "ping") return reply({});
  if (msg.method === "tools/list")
    return reply({ tools: Object.entries(TOOL_SCHEMAS).map(([name, s]) => ({
      name, description: s.description,
      inputSchema: { type: "object", properties: s.properties, ...(s.required ? { required: s.required } : {}) },
    })) });
  if (msg.method === "tools/call") {
    const user = await userFromKey(env, req);
    if (!user) return err(-32001, "connect first: your key is missing or revoked");
    const fn = Object.hasOwn(tools, msg.params?.name) ? tools[msg.params.name] : null;
    if (!fn) return err(-32602, "unknown tool");
    try {
      const out = await fn(env, user, msg.params.arguments || {});
      return reply({ content: [{ type: "text", text: JSON.stringify(out, null, 1) }] });
    } catch (e) {
      if (e instanceof ApiError) return reply({ isError: true, content: [{ type: "text", text: e.message }] });
      throw e;
    }
  }
  return err(-32601, "method not found");
}

// ---------------------------------------------------------------- connect (link + short code, approved with GitHub)

async function expireSignIns(env) {
  const cutoff = iso(Date.now() - SIGN_IN_TTL_MS);
  // Revoke any legacy key minted before its approval was polled. New keys are minted only on collection.
  const stale = (await env.DB.prepare('SELECT device_code, key_plain FROM approvals WHERE created_utc <= ? AND key_plain IS NOT NULL')
    .bind(cutoff).all()).results;
  const revocations = await Promise.all(stale.map(async a => env.DB.prepare(
    `UPDATE keys SET revoked_utc = ? WHERE hash = ? AND revoked_utc IS NULL
      AND EXISTS (SELECT 1 FROM approvals WHERE device_code = ? AND created_utc <= ?)`
  ).bind(iso(), await sha256(a.key_plain), a.device_code, cutoff)));
  for (let i = 0; i < revocations.length; i += 100) await env.DB.batch(revocations.slice(i, i + 100));
  await env.DB.batch([
    env.DB.prepare('DELETE FROM approvals WHERE created_utc <= ?').bind(cutoff),
    env.DB.prepare('DELETE FROM oauth_states WHERE created_utc <= ?').bind(cutoff),
    env.DB.prepare("UPDATE keys SET revoked_utc = ? WHERE label = 'browser' AND revoked_utc IS NULL AND created_utc <= ?")
      .bind(iso(), iso(Date.now() - BROWSER_TTL_MS)),
  ]);
}

async function connectStart(env) {
  requireIntake(env);
  await expireSignIns(env);
  const device_code = randomHex(24), code = userCode();
  await env.DB.prepare("INSERT INTO approvals (device_code, user_code) VALUES (?, ?)").bind(device_code, code).run();
  return { approve_url: `${env.SITE_URL}/approve?code=${code}`, code, device_code, poll_every_seconds: 5, expires_in: 900 };
}

async function connectPoll(env, req) {
  await expireSignIns(env);
  const { device_code } = await readJson(req);
  if (!device_code) bad(400, "device_code is required");
  const a = await env.DB.prepare("SELECT * FROM approvals WHERE device_code = ?").bind(device_code).first();
  if (!a || a.created_utc <= iso(Date.now() - SIGN_IN_TTL_MS)) bad(410, "this sign-in expired; start again with connect");
  if (!a.user_id) return { pending: true };
  if (!a.key_plain) bad(410, 'this sign-in was already collected; start again with connect');
  const cutoff = iso(Date.now() - SIGN_IN_TTL_MS);
  const [, collected] = await env.DB.batch([
    env.DB.prepare(`INSERT INTO keys (hash, user_id, label)
      SELECT ?, user_id, 'agent' FROM approvals WHERE device_code = ? AND user_id IS NOT NULL
        AND key_plain = ? AND created_utc > ? ON CONFLICT(hash) DO NOTHING`)
      .bind(await sha256(a.key_plain), device_code, a.key_plain, cutoff),
    env.DB.prepare(`DELETE FROM approvals WHERE device_code = ? AND key_plain = ? AND created_utc > ?
      RETURNING key_plain`).bind(device_code, a.key_plain, cutoff),
  ]);
  if (!collected.results.length) bad(410, 'this sign-in expired or was already collected; start again with connect');
  return { key: collected.results[0].key_plain, mcp_url: `${env.SITE_URL}/mcp`, api_url: `${env.SITE_URL}/api/v1` };
}

function approvePage(env, code) {
  const ok = /^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code || "");
  return page("Approve your agent",
    ok ? `<p>Your agent is asking to contribute to Project Iblis as you. Check that it shows this code:</p>
<p class="code">${escapeHtml(code)}</p>
<p>Approving means you accept the contributor terms: you own your copy of Diablo II 1.14d; you submit only C++ source and
notes in words; you never post or submit game files, machine code, disassembly, objects or builds; one account per person,
no shared keys; verified source is published under the MIT licence with you credited (anonymously unless you choose).</p>
<p><a class="btn" href="/auth/github?code=${encodeURIComponent(code)}">Approve with GitHub</a></p>`
       : `<p>This approval link is incomplete. Ask your agent to connect again.</p>`, ok ? 200 : 400);
}

async function authStart(env, url) {
  requireRotatedCredentials(env);
  await expireSignIns(env);
  // Two ways in: approving an agent's code, or signing in to the account page (user_code stays NULL).
  const code = url.searchParams.get("account") ? null : url.searchParams.get("code");
  if (!url.searchParams.get("account")) {
    requireIntake(env);
    const a = await env.DB.prepare("SELECT user_code FROM approvals WHERE user_code = ? AND user_id IS NULL AND created_utc > ?")
      .bind(code, iso(Date.now() - SIGN_IN_TTL_MS)).first();
    if (!a) return page("Approval expired", "<p>This code is no longer waiting. Ask your agent to connect again.</p>", 410);
  }
  const state = randomHex(16);
  await env.DB.prepare("INSERT INTO oauth_states (state, user_code) VALUES (?, ?)").bind(state, code).run();
  const u = new URL("https://github.com/login/oauth/authorize");
  u.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  u.searchParams.set("redirect_uri", `${env.SITE_URL}/auth/callback`);
  u.searchParams.set("scope", "read:user");
  u.searchParams.set("state", state);
  return Response.redirect(u.toString(), 302);
}

async function authCallback(env, url) {
  requireRotatedCredentials(env);
  await expireSignIns(env);
  const st = await env.DB.prepare("DELETE FROM oauth_states WHERE state = ? AND created_utc > ? RETURNING user_code")
    .bind(url.searchParams.get("state") || "", iso(Date.now() - SIGN_IN_TTL_MS)).first();
  const code = url.searchParams.get("code");
  if (!st || !code) return page("Approval failed", "<p>The approval link expired. Ask your agent to connect again.</p>", 400);
  if (st.user_code) {
    requireIntake(env);
    const approval = await env.DB.prepare('SELECT 1 FROM approvals WHERE user_code = ? AND user_id IS NULL AND created_utc > ?')
      .bind(st.user_code, iso(Date.now() - SIGN_IN_TTL_MS)).first();
    if (!approval) return page('Approval expired', '<p>This code expired or was already approved. Ask your agent to connect again.</p>', 410);
  }
  const t = await (await fetch("https://github.com/login/oauth/access_token", {
    method: "POST", headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ client_id: env.GITHUB_CLIENT_ID, client_secret: env.GITHUB_CLIENT_SECRET, code }),
  })).json();
  const r = t.access_token && await fetch("https://api.github.com/user", {
    headers: { authorization: `Bearer ${t.access_token}`, "user-agent": "project-iblis", accept: "application/vnd.github+json" },
  });
  const gh = r && r.ok ? await r.json() : null;
  if (!gh) return page("Approval failed", "<p>GitHub did not confirm the sign-in. Try again.</p>", 400);
  const key = "ibl_" + randomHex(32);
  if (!st.user_code) {
    // Account-page sign-in: only an existing contributor (one who approved an agent) gets a browser session.
    const known = await env.DB.prepare("SELECT 1 FROM users WHERE id = ?").bind(gh.id).first();
    if (!known) return page("No account yet", `<p>${escapeHtml(gh.login)} has not approved an agent yet. Start from the Join page.</p>`, 404);
    await env.DB.prepare("INSERT INTO keys (hash, user_id, label) VALUES (?, ?, 'browser')").bind(await sha256(key), gh.id).run();
    return new Response(null, { status: 302, headers: {
      location: "/account.html",
      "set-cookie": `iblis_session=${key}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${30 * 86400}`,
    } });
  }
  const [approved] = await env.DB.batch([
    env.DB.prepare(`UPDATE approvals SET user_id = ?, key_plain = ?
      WHERE user_code = ? AND user_id IS NULL AND created_utc > ? RETURNING user_code`)
      .bind(gh.id, key, st.user_code, iso(Date.now() - SIGN_IN_TTL_MS)),
    env.DB.prepare(
      `INSERT INTO users (id, login, terms_utc)
       SELECT ?, ?, ? WHERE changes() > 0
       ON CONFLICT(id) DO UPDATE SET login = excluded.login, terms_utc = COALESCE(users.terms_utc, excluded.terms_utc)`
    ).bind(gh.id, gh.login, iso()),
  ]);
  if (!approved.results.length) {
    await expireSignIns(env);
    return page('Approval expired', '<p>This code expired or was already approved. Ask your agent to connect again.</p>', 410);
  }
  return page("Approved", `<p>Signed in as <b>${escapeHtml(gh.login)}</b>. Your agent will pick up its key in a few seconds.
You can close this tab.</p><p>You appear on the board as <b>anonymous</b> until you choose otherwise in your account.</p>`);
}

function page(title, html, status = 200) {
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)} · Project Iblis</title>
<style>:root{color-scheme:dark}body{font:17px/1.6 system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 16px;
background:#14110f;color:#ece4d8}a{color:#d1a94f}.code{font:600 2rem ui-monospace,monospace;letter-spacing:.15em}
.btn{display:inline-block;background:#d1a94f;color:#14110f;padding:.6rem 1.1rem;text-decoration:none;font-weight:600}</style>
<h1>${escapeHtml(title)}</h1>${html}<p><a href="/">Project Iblis</a></p>`,
    { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

// ---------------------------------------------------------------- build host

const host = {
  async sync(env, req) {
    const { functions } = await readJson(req);
    if (!Array.isArray(functions)) bad(400, "functions must be a list");
    const stmts = [];
    for (const f of functions) {
      const va = normVa(f.va);
      if (!va) continue;
      // A function waiting for a verdict stays pending until the host reports one.
      stmts.push(env.DB.prepare(
        `INSERT INTO functions (va, length, module, state, name, flags, reference, body_sha256, best_note)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(va) DO UPDATE SET length = excluded.length, module = excluded.module, name = excluded.name,
           flags = excluded.flags, reference = excluded.reference, body_sha256 = excluded.body_sha256,
           best_note = COALESCE(functions.best_note, excluded.best_note),
           state = CASE WHEN functions.state = 'matched' THEN 'matched'
             WHEN excluded.state = 'open' AND EXISTS (SELECT 1 FROM results WHERE va = functions.va AND status = 'verified') THEN 'matched'
             WHEN excluded.state = 'open' AND EXISTS (SELECT 1 FROM results WHERE va = functions.va AND status IN ('pending', 'review')) THEN 'pending'
             ELSE excluded.state END`
      ).bind(va, f.length | 0, String(f.module || "?"), ["open", "matched", "excluded"].includes(f.state) ? f.state : "open",
             f.name || null, f.flags || null, f.reference || null, f.body_sha256 || null,
             f.note ? String(f.note).slice(0, 4000) : null));
    }
    for (let i = 0; i < stmts.length; i += 100) await env.DB.batch(stmts.slice(i, i + 100));
    return { synced: stmts.length };
  },
  async pending(env, url) {
    const limit = Math.max(1, Math.min(100, Number(url.searchParams.get("limit")) || 20));
    return { pending: (await env.DB.prepare(
      `SELECT r.id, r.va, r.model, r.source, r.notes, r.local_status, r.body_sha256, r.created_utc,
              u.login, u.id AS github_id, u.show_name
         FROM results r JOIN users u ON u.id = r.user_id WHERE r.status = 'pending' ORDER BY r.id LIMIT ?`
    ).bind(limit).all()).results };
  },
  async review(env, url) {
    const limit = Math.max(1, Math.min(100, Math.floor(Number(url.searchParams.get('limit')) || 20)));
    const after = Number(url.searchParams.get('after') || 0);
    const id = url.searchParams.has('id') ? Number(url.searchParams.get('id')) : null;
    if (!Number.isSafeInteger(after) || after < 0 || (id !== null && (!Number.isSafeInteger(id) || id < 1)))
      bad(400, 'after must be a nonnegative integer; id must be a positive integer');
    const rows = (await env.DB.prepare(
      `SELECT r.id, r.va, r.model, r.source, r.notes, r.local_status, r.body_sha256, r.created_utc,
              r.status, r.host_note, r.decided_utc, u.login, u.id AS github_id, u.show_name
         FROM results r JOIN users u ON u.id = r.user_id
        WHERE r.status = 'review' AND r.id > ? AND (? IS NULL OR r.id = ?) ORDER BY r.id LIMIT ?`
    ).bind(after, id, id, limit + 1).all()).results;
    const review = rows.slice(0, limit);
    return { review, next_cursor: rows.length > limit ? review[review.length - 1].id : null };
  },
  async verdict(env, req) {
    const b = await readJson(req);
    if (!["verified", "failed", "review"].includes(b.verdict)) bad(400, "verdict must be verified, failed or review");
    const [updated] = await env.DB.batch([
      env.DB.prepare(`UPDATE results SET status = ?, host_note = ?, decided_utc = ?
        WHERE id = ? AND kind = 'match' AND status IN ('pending', 'review') AND status != ? RETURNING id, va, user_id`)
        .bind(b.verdict, String(b.note || "").slice(0, 4000), b.verdict === "review" ? null : iso(), b.id, b.verdict),
      // Derive state from all results, including legacy duplicate submissions. Terminal work cannot be reopened.
      env.DB.prepare(`UPDATE functions SET state = CASE
        WHEN state IN ('matched', 'excluded') THEN state
        WHEN EXISTS (SELECT 1 FROM results WHERE va = functions.va AND status = 'verified') THEN 'matched'
        WHEN EXISTS (SELECT 1 FROM results WHERE va = functions.va AND status IN ('pending', 'review')) THEN 'pending'
        ELSE 'open' END WHERE va = (SELECT va FROM results WHERE id = ?) AND changes() > 0`).bind(b.id),
    ]);
    const r = await env.DB.prepare(`SELECT r.id, r.va, r.status, r.user_id, f.state
      FROM results r JOIN functions f ON f.va = r.va WHERE r.id = ? AND r.kind = 'match'`).bind(b.id).first();
    if (!r) bad(404, 'no submission with that id');
    if (!updated.results.length && r.status !== b.verdict) bad(409, 'submission already has a different terminal verdict');
    // Recompute on an identical retry too: a previous verdict may have committed before this update failed.
    if (b.verdict === "failed") await applyStrikes(env, r.user_id);
    return { id: r.id, va: r.va, state: r.state };
  },
};

// ---------------------------------------------------------------- public

async function board(env) {
  const rows = (await env.DB.prepare(
    `SELECT u.id, u.login, u.show_name, COUNT(*) AS functions, SUM(f.length) AS bytes
       FROM results r JOIN users u ON u.id = r.user_id JOIN functions f ON f.va = r.va
      WHERE r.status = 'verified' GROUP BY u.id ORDER BY bytes DESC LIMIT 200`
  ).all()).results;
  return rows.map((r, i) => ({ rank: i + 1, who: r.show_name ? r.login : "anonymous", functions: r.functions, bytes: r.bytes }));
}

// ---------------------------------------------------------------- router

export default {
  async scheduled(_event, env) {
    await expireSignIns(env);
    await expireLeases(env);
  },
  async fetch(req, env) {
    const url = new URL(req.url);
    // Pass every binding and secret through untouched; only SITE_URL falls back to the address we are served from.
    const site = (env.SITE_URL || url.origin).replace(/\/$/, "");
    env = new Proxy(env, { get: (t, k) => (k === "SITE_URL" ? site : Reflect.get(t, k)) });
    const p = url.pathname, m = req.method;
    try {
      const assetPath = decodeURIComponent(p);
      if (/^\/kit\/.*\.zip(?:\.sha256)?\/?$/i.test(assetPath)) {
        // Guard retired immutable assets even if a previous deployment's copy is still cached.
        const manifest = await env.ASSETS.fetch(new Request(new URL('/kit/latest.json', url)));
        if (!manifest.ok) return json({ error: 'current checker manifest is unavailable' }, 503);
        let latest;
        try { latest = await manifest.json(); } catch { return json({ error: 'current checker manifest is invalid' }, 503); }
        if (!/^[A-Za-z0-9_-]+\.zip$/.test(latest?.file || ''))
          return json({ error: 'current checker manifest is invalid' }, 503);
        if (assetPath !== '/kit/' + latest.file && assetPath !== '/kit/' + latest.file + '.sha256')
          return json({ error: 'this checker release is retired; use /kit/latest.json for the current release' }, 410);
        return env.ASSETS.fetch(req);
      }
      if (p === "/mcp" && m === "POST") return await mcp(env, req);
      if (p === "/approve" && m === "GET") return approvePage(env, url.searchParams.get("code"));
      if (p === "/auth/github" && m === "GET") return await authStart(env, url);
      if (p === "/auth/callback" && m === "GET") return await authCallback(env, url);
      if (p === "/auth/logout" && m === "GET") {
        const k = keyFrom(env, req);
        if (k) await env.DB.prepare("UPDATE keys SET revoked_utc = ? WHERE hash = ? AND label = 'browser'").bind(iso(), await sha256(k)).run();
        return new Response(null, { status: 302, headers: { location: "/", "set-cookie": "iblis_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0" } });
      }
      if (p === "/api/v1/connect" && m === "POST") return json(await connectStart(env));
      if (p === "/api/v1/connect/poll" && m === "POST") return json(await connectPoll(env, req));
      if (p === "/api/v1/board" && m === "GET") return json(await board(env));
      if (p === "/api/v1/stats" && m === "GET") return env.ASSETS.fetch(new Request(new URL("/data/stats.json", url)));

      if (p.startsWith("/api/v1/host/")) {
        requireRotatedCredentials(env);
        if (!isHost(env, req)) return json({ error: String(env.HOST_KEY || "").trim() ? "host key does not match" : "HOST_KEY is not set on this worker (add it under Runtime variables and secrets)" }, 401);
        if (p === "/api/v1/host/sync" && m === "POST") return json(await host.sync(env, req));
        if (p === "/api/v1/host/pending" && m === "GET") return json(await host.pending(env, url));
        if (p === "/api/v1/host/review" && m === "GET") return json(await host.review(env, url));
        if (p === "/api/v1/host/verdict" && m === "POST") return json(await host.verdict(env, req));
        return json({ error: "no such route" }, 404);
      }

      if (p.startsWith("/api/v1/")) {
        const user = await userFromKey(env, req);
        if (!user) return json({ error: "connect first: POST /api/v1/connect, then approve the link with GitHub" }, 401);
        const name = p.slice("/api/v1/".length);
        if (name === "account" && m === "POST") {
          const b = await readJson(req);
          if (typeof b.show_name === "boolean")
            await env.DB.prepare("UPDATE users SET show_name = ? WHERE id = ?").bind(b.show_name ? 1 : 0, user.id).run();
          if (b.revoke_agent_keys)
            await env.DB.prepare("UPDATE keys SET revoked_utc = ? WHERE user_id = ? AND label = 'agent' AND revoked_utc IS NULL")
              .bind(iso(), user.id).run();
          if (b.revoke_this_key)
            await env.DB.prepare("UPDATE keys SET revoked_utc = ? WHERE hash = ?").bind(iso(), await sha256(keyFrom(env, req))).run();
          return json({ ok: true });
        }
        const fn = Object.hasOwn(tools, name) ? tools[name] : null;
        if (!fn || m !== "POST") return json({ error: "no such tool; use status, bands, next, packet, submit or giveup" }, 404);
        return json(await fn(env, user, await readJson(req)));
      }
      // Pages that name the site's address (agents.md, the Join page) get the address they are actually served from.
      if (/^\/(agents\.md|join(\.html)?)$/.test(p)) {
        const r = await env.ASSETS.fetch(req);
        if (!r.ok) return r;
        const text = (await r.text()).replaceAll("https://iblis.pages.dev", env.SITE_URL);
        const h = new Headers(r.headers); h.delete("content-length"); h.delete("etag");
        if (p.endsWith(".md")) h.set("content-type", "text/markdown; charset=utf-8");
        return new Response(text, { status: r.status, headers: h });
      }
      return env.ASSETS.fetch(req);
    } catch (e) {
      if (e instanceof ApiError) return json({ error: e.message }, e.status);
      return json({ error: "server error: " + (e && e.message ? e.message : String(e)) }, 500);
    }
  },
};
