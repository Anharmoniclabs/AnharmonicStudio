import {after, before, test} from 'node:test';
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {Miniflare, convertV4MiniflareOptions} from 'miniflare';

const secret = 'whsec_local_test_fixture';
const signing = 'test-only-download-signing-key-longer-than-32-characters';
const files = {release: '0.1.0-rc.1', artifacts: {}};
for (const [platform, extension] of [['windows', '.exe'], ['mac-arm', '.dmg'], ['mac-intel', '.dmg'], ['linux', '.tar.gz']]) {
  const filename = platform + extension;
  files.artifacts[platform] = {filename, key: filename, size: 16, sha256: '0'.repeat(64), materials_url: `https://github.com/Anharmoniclabs/AnharmonicStudio/releases/download/v0.1.0-rc.1-source/${platform}-source-and-notices.zip`};
}
let mf, db, counter = 0;
before(async () => {
  mf = new Miniflare(convertV4MiniflareOptions({workers: [{name: 'delivery', modules: true, scriptPath: new URL('./worker.mjs', import.meta.url).pathname,
    compatibilityDate: '2026-09-09', bindings: {DELIVERY_OPEN: 'true', PAYMENT_MODE: 'test',
      STRIPE_WEBHOOK_SECRET: secret, STRIPE_PAYMENT_LINK_ID: 'plink_fixture',
      DOWNLOAD_SIGNING_KEY: signing, CATALOG: JSON.stringify(files)},
    d1Databases: ['PURCHASES'], r2Buckets: ['FILES'], serviceBindings: {ASSETS: () => new Response('public asset')}
  }]}));
  db = await mf.getD1Database('PURCHASES');
  const schema = await readFile(new URL('./schema.sql', import.meta.url), 'utf8');
  for (const sql of schema.split(';').filter(sql => sql.trim())) await db.prepare(sql).run();
  const bucket = await mf.getR2Bucket('FILES');
  for (const artifact of Object.values(files.artifacts)) {
    await bucket.put(artifact.key, 'installer-binary');
  }
});
after(async () => { if (mf) await mf.dispose(); });
function session(platform = 'windows') {
  const suffix = String(++counter).padStart(12, '0');
  return {id: `cs_test_${suffix}`, payment_intent: `pi_${suffix}`, mode: 'payment', payment_status: 'paid',
    status: 'complete', payment_link: 'plink_fixture', currency: 'usd', amount_subtotal: 100, amount_total: 109,
    client_reference_id: platform ? `as_v1_${platform}` : null};
}
const request = (path, init) => mf.dispatchFetch('https://downloads.example' + path, init);
async function event(object, type = 'checkout.session.completed', options = {}) {
  const timestamp = options.timestamp ?? Math.floor(Date.now() / 1000);
  const raw = JSON.stringify({id: 'evt_fixture', livemode: options.livemode ?? false, type, data: {object}});
  const sig = createHmac('sha256', options.secret || secret).update(`${timestamp}.${raw}`).digest('hex');
  return request('/webhook', {method: 'POST', headers: {'Stripe-Signature': `t=${timestamp},v1=${sig}`, 'Content-Type': 'application/json'}, body: raw + (options.tamper || '')});
}
const claim = s => request('/api/claim', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: s.id})});

for (const platform of Object.keys(files.artifacts)) {
  test(`verified ${platform} purchase returns correct private installer and source`, async () => {
    const s = session(platform);
    assert.equal((await event(s)).status, 200);
    const response = await claim(s);
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.platform, platform);
    const selected = body.downloads.find(file => file.platform === platform);
    const download = await request(selected.url);
    assert.equal(download.status, 200);
    assert.match(download.headers.get('Content-Disposition'), new RegExp(platform));
    assert.match(download.headers.get('Cache-Control'), /no-store/);
    assert.equal(await download.text(), 'installer-binary');
    assert.equal(selected.materials_url, files.artifacts[platform].materials_url);
    assert.equal((await request(selected.url + '?part=materials')).status, 404);
    const resumed = await request(selected.url, {headers: {Range: 'bytes=2-5'}});
    assert.equal(resumed.status, 206);
    assert.equal(resumed.headers.get('Content-Range'), 'bytes 2-5/16');
    assert.equal(await resumed.text(), 'stal');
    assert.equal((await request(selected.url, {headers: {Range: 'bytes=500-'}})).status, 416);
    assert.equal((await request(selected.url, {method: 'HEAD'})).headers.get('Content-Length'), '16');
  });
}
test('an unknown or unpaid session never receives downloads', async () => {
  const s = session();
  assert.equal((await claim(s)).status, 409);
  await event({...s, payment_status: 'unpaid'});
  assert.equal((await claim(s)).status, 409);
  await event(s, 'checkout.session.async_payment_succeeded');
  assert.equal((await claim(s)).status, 200);
});
for (const [field, value] of [['currency', 'eur'], ['amount_subtotal', 99], ['amount_total', 50], ['total_details', {amount_discount: 50}], ['mode', 'subscription'], ['payment_link', 'plink_donation']]) {
  test(`rejects a payment with wrong ${field}`, async () => {
    const s = session();
    await event({...s, [field]: value});
    assert.equal((await claim(s)).status, 409);
  });
}
test('unsigned, altered, expired, wrong-secret and wrong-mode webhooks are rejected', async () => {
  const s = session();
  assert.equal((await request('/webhook', {method: 'POST', body: '{}'})).status, 400);
  for (const options of [{tamper: ' '}, {secret: 'wrong'}, {livemode: true}, {timestamp: Math.floor(Date.now() / 1000) - 301}]) {
    assert.equal((await event(s, undefined, options)).status, 400);
  }
  assert.equal((await claim(s)).status, 409);
});
test('duplicate webhooks are idempotent', async () => {
  const s = session();
  await event(s); await event(s);
  const result = await db.prepare('SELECT count(*) AS total FROM purchases WHERE payment_intent = ?').bind(s.payment_intent).first();
  assert.equal(result.total, 1);
});
for (const type of ['charge.refunded', 'charge.dispute.created', 'checkout.session.async_payment_failed']) {
  test(`${type} revokes existing download tickets`, async () => {
    const s = session(); await event(s);
    const link = (await (await claim(s)).json()).downloads[0].url;
    await event({payment_intent: s.payment_intent}, type);
    assert.equal((await request(link)).status, 403);
    await event(s); // replay must not restore access
    assert.equal((await claim(s)).status, 403);
  });
}
test('refund arriving before purchase remains revoked', async () => {
  const s = session();
  await event({payment_intent: s.payment_intent}, 'charge.refunded'); await event(s);
  assert.equal((await claim(s)).status, 403);
});
test('legacy purchases get a platform chooser', async () => {
  const s = session(null); await event(s);
  assert.equal((await (await claim(s)).json()).platform, null);
});
test('forged and expired tickets cannot access installers', async () => {
  const s = session(); await event(s);
  const link = (await (await claim(s)).json()).downloads[0].url;
  const parts = link.split('/');
  const [payload, signature] = parts[2].split('.');
  assert.equal((await request(`/files/${payload}.${'0'.repeat(64)}/windows`)).status, 403);
  const data = JSON.parse(Buffer.from(payload, 'base64url').toString());
  data.expires = Math.floor(Date.now() / 1000) - 1;
  const expired = Buffer.from(JSON.stringify(data)).toString('base64url');
  const sig = createHmac('sha256', signing).update(expired).digest('hex');
  assert.equal((await request(`/files/${expired}.${sig}/windows`)).status, 403);
  assert.equal((await request(`/files/${payload}.${signature}/../../secrets`)).status, 404);
  assert.equal((await request(link + '?part=secrets')).status, 404);
});
test('missing files return a retryable error instead of an empty download', async () => {
  const s = session(); await event(s);
  const selected = (await (await claim(s)).json()).downloads.find(file => file.platform === 'linux');
  const bucket = await mf.getR2Bucket('FILES');
  await bucket.delete(files.artifacts.linux.key);
  assert.equal((await request(selected.url)).status, 503);
  await bucket.put(files.artifacts.linux.key, 'installer-binary');
});
test('invalid input, cross-origin requests and oversized bodies are rejected', async () => {
  assert.equal((await claim({id: '../../secrets'})).status, 400);
  assert.equal((await claim({id: 'cs_live_1234567890'})).status, 400);
  assert.equal((await request('/api/claim', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{'})).status, 400);
  assert.equal((await request('/api/claim', {method: 'POST', headers: {Origin: 'https://unrelated.example'}, body: '{}'})).status, 403);
  assert.equal((await request('/webhook', {method: 'POST', body: 'x'.repeat(262145)})).status, 413);
  assert.equal((await request('/success.html')).status, 404);
  assert.equal((await request('/download-success')).status, 200);
});
