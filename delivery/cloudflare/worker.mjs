const PLATFORMS = Object.freeze({windows: 'Windows', 'mac-arm': 'Mac — Apple Silicon', 'mac-intel': 'Mac — Intel', linux: 'Linux'});
const encode = value => new TextEncoder().encode(value);
const hex = bytes => [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
const now = () => Math.floor(Date.now() / 1000);
const privacy = {
  'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex, nofollow',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
};
class Failure extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const fail = (status, message) => { throw new Failure(status, message); };
const json = (value, status = 200) => Response.json(value, {status});
const sha256 = async text => hex(await crypto.subtle.digest('SHA-256', encode(text)));
async function hmacKey(secret) {
  return crypto.subtle.importKey('raw', encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
}
async function sign(secret, text) {
  return hex(await crypto.subtle.sign('HMAC', await hmacKey(secret), encode(text)));
}
async function authentic(secret, text, signature) {
  if (!/^[a-f0-9]{64}$/.test(signature || '')) return false;
  const bytes = Uint8Array.from(signature.match(/../g), pair => parseInt(pair, 16));
  return crypto.subtle.verify('HMAC', await hmacKey(secret), bytes, encode(text));
}
function catalog(env) {
  const result = JSON.parse(env.CATALOG || '{}');
  if (!result.release || !result.artifacts || Object.keys(result.artifacts).sort().join() !== Object.keys(PLATFORMS).sort().join()) {
    fail(503, 'Downloads are being prepared. Please try again later.');
  }
  return result;
}
function configured(env) {
  return env.DELIVERY_OPEN === 'true' && ['test', 'live'].includes(env.PAYMENT_MODE)
    && !!env.STRIPE_PAYMENT_LINK_ID && !!env.STRIPE_WEBHOOK_SECRET
    && env.DOWNLOAD_SIGNING_KEY?.length >= 32 && !!env.FILES && !!env.PURCHASES;
}
async function limitedBody(request, max = 262144) {
  if (Number(request.headers.get('Content-Length')) > max) fail(413, 'Request is too large.');
  if (!request.body) fail(400, 'Missing request.');
  const reader = request.body.getReader();
  const chunks = []; let total = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) { await reader.cancel(); fail(413, 'Request is too large.'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(bytes);
}
function parseJson(raw) {
  try { return JSON.parse(raw); } catch { fail(400, 'Invalid request.'); }
}
async function webhook(request, env, release) {
  if (!env.STRIPE_WEBHOOK_SECRET) fail(503, 'Payment connection is being configured.');
  const raw = await limitedBody(request);
  const fields = (request.headers.get('Stripe-Signature') || '').split(',').map(field => field.trim());
  const timestamps = fields.filter(field => /^t=\d+$/.test(field));
  const timestamp = timestamps.length === 1 ? Number(timestamps[0].slice(2)) : 0;
  const signatures = fields.filter(field => /^v1=[a-f0-9]{64}$/.test(field)).map(field => field.slice(3));
  if (Math.abs(now() - timestamp) > 300 || signatures.length > 8) fail(400, 'Invalid Stripe signature.');
  let valid = false;
  for (const signature of signatures) valid ||= await authentic(env.STRIPE_WEBHOOK_SECRET, `${timestamp}.${raw}`, signature);
  if (!valid) fail(400, 'Invalid Stripe signature.');
  const event = parseJson(raw);
  if (event?.livemode !== (env.PAYMENT_MODE === 'live')) fail(400, 'Payment mode does not match.');
  const obj = event.data?.object;
  if (!obj || typeof obj !== 'object') fail(400, 'Invalid payment event.');
  if (['checkout.session.completed', 'checkout.session.async_payment_succeeded'].includes(event.type)) {
    // The configured Payment Link has one fixed $1 price; Stripe's signed event
    // identifies that link without putting a Stripe API key on this server.
    if (obj.payment_link !== env.STRIPE_PAYMENT_LINK_ID) return json({received: true});
    if (obj.mode !== 'payment' || obj.payment_status !== 'paid' || obj.status !== 'complete') return json({received: true});
    if (obj.currency !== 'usd' || obj.amount_subtotal !== 100 || !Number.isInteger(obj.amount_total)
        || obj.amount_total < 100 || (obj.total_details?.amount_discount || 0) !== 0
        || !new RegExp(`^cs_${env.PAYMENT_MODE}_[A-Za-z0-9]{8,200}$`).test(obj.id)
        || !/^pi_[A-Za-z0-9]+$/.test(obj.payment_intent)) fail(400, 'Payment does not match the download offer.');
    const reference = typeof obj.client_reference_id === 'string' ? obj.client_reference_id : '';
    const selected = reference.startsWith('as_v1_') ? reference.slice(6) : null;
    const platform = Object.hasOwn(PLATFORMS, selected) ? selected : null;
    const hash = await sha256(obj.id);
    await env.PURCHASES.prepare('INSERT OR IGNORE INTO purchases (session_hash, payment_intent, release, platform, created_at) VALUES (?, ?, ?, ?, ?)')
      .bind(hash, obj.payment_intent, release, platform, now()).run();
  } else if (['charge.refunded', 'charge.dispute.created', 'checkout.session.async_payment_failed'].includes(event.type)) {
    if (typeof obj.payment_intent === 'string' && /^pi_[A-Za-z0-9]+$/.test(obj.payment_intent)) {
      // Keep revocations even if they arrive before the successful-payment event.
      await env.PURCHASES.prepare('INSERT OR IGNORE INTO revoked_payments (payment_intent, created_at) VALUES (?, ?)')
        .bind(obj.payment_intent, now()).run();
    }
  }
  return json({received: true});
}
async function purchase(env, hash, release) {
  const row = await env.PURCHASES.prepare('SELECT p.*, r.payment_intent AS revoked FROM purchases p LEFT JOIN revoked_payments r ON r.payment_intent = p.payment_intent WHERE p.session_hash = ?')
    .bind(hash).first();
  if (!row) fail(409, 'Stripe has not confirmed this payment yet. Try again shortly; you do not need to pay again.');
  if (row.revoked) fail(403, 'Download access is unavailable for this payment. Contact support.');
  if (row.release.split('.')[0] !== release.split('.')[0]) fail(403, 'This purchase covers an earlier major version. Contact support for its download.');
  return row;
}
async function claim(request, env, files) {
  if (!request.headers.get('Content-Type')?.startsWith('application/json')) fail(400, 'Expected a purchase reference.');
  const sid = parseJson(await limitedBody(request, 4096))?.session_id;
  if (typeof sid !== 'string' || !new RegExp(`^cs_${env.PAYMENT_MODE}_[A-Za-z0-9]{8,200}$`).test(sid)) fail(400, 'Open the download link from your Stripe payment again.');
  const hash = await sha256(sid);
  const row = await purchase(env, hash, files.release);
  const payload = btoa(JSON.stringify({hash, release: files.release, expires: now() + 900})).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
  const ticket = `${payload}.${await sign(env.DOWNLOAD_SIGNING_KEY, payload)}`;
  return json({mode: env.PAYMENT_MODE, release: files.release, platform: row.platform,
    downloads: Object.entries(files.artifacts).map(([platform, item]) => ({platform, label: PLATFORMS[platform],
      filename: item.filename, sha256: item.sha256, url: `/files/${ticket}/${platform}`,
      materials_url: item.materials_url}))});
}
function byteRange(header, size) {
  if (!header) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header);
  if (!match || (!match[1] && !match[2])) fail(416, 'This download range is not available.');
  const start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
  const end = match[1] && match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size) fail(416, 'This download range is not available.');
  return {offset: start, length: end - start + 1};
}
async function download(request, env, files, ticket, platform, part) {
  if (!Object.hasOwn(files.artifacts, platform) || part !== 'installer') fail(404, 'Download not found.');
  if (ticket.length > 2048) fail(403, 'Invalid download link.');
  const pieces = ticket.split('.');
  if (pieces.length !== 2 || !await authentic(env.DOWNLOAD_SIGNING_KEY, pieces[0], pieces[1])) fail(403, 'Invalid download link.');
  let data;
  try { data = JSON.parse(atob(pieces[0].replaceAll('-', '+').replaceAll('_', '/'))); }
  catch { fail(403, 'Invalid download link.'); }
  if (!data || !/^[a-f0-9]{64}$/.test(data.hash) || data.release !== files.release
      || !Number.isInteger(data.expires) || data.expires <= now() || data.expires > now() + 900) {
    fail(403, 'This download link expired. Return to the download page and press Try again.');
  }
  await purchase(env, data.hash, files.release);
  const item = files.artifacts[platform];
  const range = byteRange(request.headers.get('Range'), item.size);
  const object = request.method === 'HEAD' ? await env.FILES.head(item.key)
    : await env.FILES.get(item.key, range ? {range} : undefined);
  if (!object || object.size !== item.size) fail(503, 'This installer is temporarily unavailable. Please try again later.');
  const headers = {'Content-Type': 'application/octet-stream', 'Content-Disposition': `attachment; filename="${item.filename}"`,
    'Content-Length': String(range?.length ?? item.size), 'Accept-Ranges': 'bytes', 'ETag': object.httpEtag};
  if (range) headers['Content-Range'] = `bytes ${range.offset}-${range.offset + range.length - 1}/${item.size}`;
  return new Response(request.method === 'HEAD' ? null : object.body, {status: range ? 206 : 200, headers});
}
async function route(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (path === '/health' && request.method === 'GET') return json({ready: !!configured(env), mode: env.PAYMENT_MODE});
  if (['/download-success', '/assets/success.js', '/assets/success.css'].includes(path) && ['GET', 'HEAD'].includes(request.method)) {
    if (path === '/download-success') url.pathname = '/success.html';
    url.search = '';
    return env.ASSETS.fetch(new Request(url, {method: request.method}));
  }
  if (path === '/webhook' && request.method === 'POST') return webhook(request, env, catalog(env).release);
  const match = /^\/files\/([^/]+)\/([^/]+)$/.exec(path);
  if ((path === '/api/claim' && request.method === 'POST') || (match && ['GET', 'HEAD'].includes(request.method))) {
    if (!configured(env)) fail(503, 'Installer delivery is being connected. Please try again later.');
    const origin = request.headers.get('Origin');
    if (origin && origin !== url.origin) fail(403, 'Open the download page directly.');
    if (env.LIMITER && !(await env.LIMITER.limit({key: request.headers.get('CF-Connecting-IP') || 'unknown'})).success) fail(429, 'Too many requests. Please wait a minute and try again.');
    const files = catalog(env);
    if (match) return download(request, env, files, match[1], match[2], url.searchParams.get('part') || 'installer');
    return claim(request, env, files);
  }
  fail(404, 'Page not found.');
}
export default {
  async fetch(request, env) {
    let response;
    try { response = await route(request, env); }
    catch (error) { response = json({error: error instanceof Failure ? error.message : 'The download service is temporarily unavailable. Please try again.'}, error instanceof Failure ? error.status : 503); }
    const result = new Response(response.body, response);
    for (const [key, value] of Object.entries(privacy)) result.headers.set(key, value);
    return result;
  }
};
