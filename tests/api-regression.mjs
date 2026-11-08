import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';

const workerSource = readFileSync(new URL('../worker/index.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(workerSource).toString('base64'));
const stamp = (delta = 0) => new Date(Date.now() + delta).toISOString().replace(/\.\d+Z$/, 'Z');
const hash = async value => Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))).toString('hex');
const va = '0x00401220';
const submission = {va, source: '// 0x00401220\nint foo(){return 0;}', local_status: 'RAW_EQUAL'};

async function fixture(t) {
  const sql = new DatabaseSync(':memory:');
  t.after(() => sql.close());
  sql.exec(readFileSync(new URL('../worker/schema.sql', import.meta.url), 'utf8'));
  // Real SQLite transactions; yield between requests to exercise read/write races.
  const delay = () => new Promise(resolve => setImmediate(resolve));
  const execute = s => {
    const statement = sql.prepare(s.sql);
    const results = statement.columns().length ? statement.all(...s.args) : (statement.run(...s.args), []);
    return {results, meta: {changes: sql.prepare('SELECT changes() AS n').get().n}};
  };
  const DB = {
    prepare(query) { return {sql: query, args: [], bind(...args) { this.args = args; return this; },
      async first() { await delay(); return execute(this).results[0] || null; },
      async all() { await delay(); return execute(this); }, async run() { await delay(); return execute(this); }}; },
    async batch(statements) { await delay(); sql.exec('BEGIN'); try {
      const result = statements.map(execute); sql.exec('COMMIT'); return result;
    } catch (e) { sql.exec('ROLLBACK'); throw e; } },
  };
  sql.prepare('INSERT INTO users(id,login) VALUES(1,?)').run('tester');
  sql.prepare("INSERT INTO keys(hash,user_id,label) VALUES(?,1,'agent')").run(await hash('testkey'));
  sql.prepare("INSERT INTO functions(va,length,module,state) VALUES(?,16,'fixture','open')").run(va);
  const env = {DB, CONTRIBUTIONS_ENABLED: 'true', CREDENTIALS_ROTATED: 'true', SITE_URL: 'https://test.invalid', HOST_KEY: 'test-host-secret', GITHUB_CLIENT_SECRET: 'test-oauth-secret', ASSETS: {fetch: () => new Response('asset')}};
  const request = async (path, body = {}, options = {}) => {
    const method = options.method || 'POST';
    const headers = {'Content-Type': 'application/json', Authorization: `Bearer ${options.host ? env.HOST_KEY : 'testkey'}`, ...options.headers};
    const response = await worker.fetch(new Request(env.SITE_URL + path, {method, headers, ...(method === 'GET' ? {} : {body: JSON.stringify(body)})}), env);
    const text = await response.text();
    return {status: response.status, body: response.headers.get('content-type')?.includes('json') ? JSON.parse(text) : text, headers: response.headers};
  };
  const lease = (expiry = stamp(3600e3)) => sql.prepare('INSERT INTO leases VALUES(?,1,?,?)').run(va, stamp(), expiry);
  const result = (status = 'pending') => Number(sql.prepare("INSERT INTO results(va,user_id,kind,source,status) VALUES(?,1,'match',?,?)").run(va, submission.source, status).lastInsertRowid);
  return {sql, DB, env, request, lease, result};
}

test('HTTP and MCP reject inherited tool names without disclosing bindings', async t => {
  const f = await fixture(t);
  for (const name of ['constructor', '__proto__', 'toString']) {
    const http = await f.request('/api/v1/' + name);
    assert.equal(http.status, 404);
    const mcp = await f.request('/mcp', {jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:{}}});
    assert.equal(mcp.body.error?.code, -32602);
    assert.ok(!JSON.stringify([http,mcp]).includes('test-oauth-secret'));
  }
  assert.equal((await f.request('/api/v1/status')).status, 200);
});

test('concurrent submits consume exactly one lease', async t => {
  const f = await fixture(t); f.lease();
  const replies = await Promise.all([f.request('/api/v1/submit', submission),f.request('/api/v1/submit', submission)]);
  assert.equal(replies.filter(r => r.status === 200).length, 1);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM results').get().n, 1);
  assert.equal(f.sql.prepare('SELECT state FROM functions').get().state, 'pending');
});

test('submit racing giveup produces one result and cannot overwrite accepted work', async t => {
  const f = await fixture(t); f.lease();
  const replies = await Promise.all([f.request('/api/v1/submit', submission), f.request('/api/v1/giveup', {va,reason:'stuck',source:submission.source})]);
  assert.equal(replies.filter(r => r.status === 200).length, 1);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM results').get().n, 1);
});

test('expired leases cannot submit, giveup or fetch packets', async t => {
  for (const tool of ['submit','giveup','packet']) {
    const f = await fixture(t); f.lease(stamp(-1000));
    assert.notEqual((await f.request('/api/v1/' + tool, {...submission,reason:'stuck'})).status, 200);
    assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM results').get().n, 0);
  }
});

test('simultaneous expiry sweeps record an expiration only once', async t => {
  const f = await fixture(t); f.lease(stamp(-1000));
  await Promise.all([f.request('/api/v1/status'),f.request('/api/v1/status')]);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM expired').get().n, 1);
});

test('next respects tier capacity across concurrent requests', async t => {
  const f = await fixture(t);
  for(let i=0;i<3;i++) {
    const address = '0x0050000'+i;
    f.sql.prepare("INSERT INTO functions(va,length,module,state) VALUES(?,16,'fixture','open')").run(address);
    f.sql.prepare('INSERT INTO leases VALUES(?,1,?,?)').run(address,stamp(),stamp(3600e3));
  }
  f.sql.prepare("INSERT INTO functions(va,length,module,state) VALUES('0x00401230',128,'fixture','open')").run();
  await Promise.all([f.request('/api/v1/next',{band:'tiny'}),f.request('/api/v1/next',{band:'small'})]);
  assert.ok(f.sql.prepare('SELECT COUNT(*) n FROM leases').get().n <= 4);
});

test('verdict retries are idempotent and conflicting terminal decisions fail', async t => {
  const f = await fixture(t); const id = f.result();
  const send = verdict => f.request('/api/v1/host/verdict',{id,verdict},{host:true});
  assert.equal((await send('verified')).status,200);
  assert.equal((await send('verified')).status,200);
  assert.equal((await send('failed')).status,409);
  assert.equal(f.sql.prepare('SELECT state FROM functions').get().state,'matched');
});

test('concurrent terminal verdicts cannot overwrite one another', async t => {
  const f = await fixture(t); const id = f.result();
  const responses = await Promise.all(['verified','failed'].map(verdict => f.request('/api/v1/host/verdict',{id,verdict},{host:true})));
  assert.deepEqual(responses.map(r=>r.status).sort(),[200,409]);
});

test('legacy duplicate failure and stale sync never reopen verified work', async t => {
  const f = await fixture(t); const a=f.result(), b=f.result();
  await f.request('/api/v1/host/verdict',{id:a,verdict:'verified'},{host:true});
  await f.request('/api/v1/host/verdict',{id:b,verdict:'failed'},{host:true});
  assert.equal(f.sql.prepare('SELECT state FROM functions').get().state,'matched');
  await f.request('/api/v1/host/sync',{functions:[{va,length:16,module:'fixture',state:'open'}]},{host:true});
  assert.equal(f.sql.prepare('SELECT state FROM functions').get().state,'matched');
});

test('owner can retrieve review submissions and decide them', async t => {
  const f=await fixture(t); const id=f.result('review');
  assert.equal((await f.request('/api/v1/host/review',{}, {method:'GET'})).status,401);
  const response=await f.request('/api/v1/host/review',{}, {method:'GET',host:true});
  assert.equal(response.status,200); assert.equal(response.body.review[0].id,id);
  assert.equal((await f.request('/api/v1/host/verdict',{id,verdict:'verified'},{host:true})).status,200);
});

test('expired approvals cannot begin OAuth and polling clears retained plaintext', async t => {
  const f=await fixture(t);
  f.sql.prepare('INSERT INTO approvals(device_code,user_code,key_plain,created_utc) VALUES(?,?,?,?)').run('old-device','BBBB-CCCC','old-plaintext',stamp(-901e3));
  assert.equal((await f.request('/auth/github?code=BBBB-CCCC',{}, {method:'GET'})).status,410);
  assert.equal((await f.request('/api/v1/connect/poll',{device_code:'old-device'})).status,410);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM approvals').get().n,0);
});

test('concurrent polls deliver an approved key only once', async t => {
  const f=await fixture(t);
  f.sql.prepare('INSERT INTO approvals(device_code,user_code,user_id,key_plain) VALUES(?,?,1,?)').run('device','BBBB-CCCC','new-key');
  const replies=await Promise.all([f.request('/api/v1/connect/poll',{device_code:'device'}),f.request('/api/v1/connect/poll',{device_code:'device'})]);
  assert.equal(replies.filter(r=>r.body.key === 'new-key').length,1);
});

test('expired OAuth states never reach GitHub', async t => {
  const f=await fixture(t);
  f.sql.prepare('INSERT INTO oauth_states(state,created_utc) VALUES(?,?)').run('expired-state',stamp(-901e3));
  const original=globalThis.fetch; let calls=0;
  globalThis.fetch=async()=> {calls++;return Response.json({});}; t.after(()=> {globalThis.fetch=original;});
  const response=await f.request('/auth/callback?state=expired-state&code=test',{}, {method:'GET'});
  assert.equal(response.status,400); assert.equal(calls,0);
});

test('approval expiring during OAuth cannot create an agent credential', async t => {
  const f=await fixture(t);
  f.sql.prepare('INSERT INTO approvals(device_code,user_code) VALUES(?,?)').run('device','BBBB-CCCC');
  f.sql.prepare('INSERT INTO oauth_states(state,user_code) VALUES(?,?)').run('state','BBBB-CCCC');
  const original=globalThis.fetch;
  globalThis.fetch=async url=> {
    if(String(url).includes('access_token')) return Response.json({access_token:'fixture-token'});
    f.sql.prepare('UPDATE approvals SET created_utc=?').run(stamp(-901e3));
    return Response.json({id:1,login:'tester'});
  }; t.after(()=> {globalThis.fetch=original;});
  assert.equal((await f.request('/auth/callback?state=state&code=test',{}, {method:'GET'})).status,410);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM keys').get().n,1);
});

test('browser credentials expire on the server even when sent as bearer tokens', async t => {
  const f=await fixture(t);
  f.sql.prepare("INSERT INTO keys(hash,user_id,label,created_utc) VALUES(?,1,'browser',?)").run(await hash('old-browser'),stamp(-31*864e5));
  const response=await f.request('/api/v1/status',{}, {headers:{Authorization:'Bearer old-browser'}});
  assert.equal(response.status,401);
});

test('contributor intake is closed by default while status and returning work remain available', async t => {
  const f=await fixture(t); delete f.env.CONTRIBUTIONS_ENABLED; f.lease();
  for(const path of ['/api/v1/connect','/api/v1/next','/api/v1/submit'])
    assert.equal((await f.request(path,submission)).status,503);
  assert.equal((await f.request('/api/v1/status')).status,200);
  assert.equal((await f.request('/api/v1/giveup',{va,reason:'maintenance'})).status,200);
  f.env.CONTRIBUTIONS_ENABLED='true';
  assert.equal((await f.request('/api/v1/connect')).status,200);
});

test('an already expired device approval is rejected before exchanging OAuth credentials', async t => {
  const f=await fixture(t);
  f.sql.prepare('INSERT INTO approvals(device_code,user_code,created_utc) VALUES(?,?,?)').run('device','BBBB-CCCC',stamp(-901e3));
  f.sql.prepare('INSERT INTO oauth_states(state,user_code) VALUES(?,?)').run('state','BBBB-CCCC');
  const original=globalThis.fetch; let calls=0;
  globalThis.fetch=async()=> {calls++;return Response.json({});}; t.after(()=> {globalThis.fetch=original;});
  assert.equal((await f.request('/auth/callback?state=state&code=test',{}, {method:'GET'})).status,410);
  assert.equal(calls,0);
});

test('fresh approval creates one usable credential only when collected and OAuth state cannot replay', async t => {
  const f=await fixture(t);
  const connected=await f.request('/api/v1/connect');
  const start=await f.request('/auth/github?code='+connected.body.code,{}, {method:'GET'});
  const state=new URL(start.headers.get('location')).searchParams.get('state');
  const original=globalThis.fetch;
  globalThis.fetch=async url => Response.json(String(url).includes('access_token') ? {access_token:'fixture-token'} : {id:2,login:'new-user'});
  t.after(()=> {globalThis.fetch=original;});
  assert.equal((await f.request('/auth/callback?state='+state+'&code=test',{}, {method:'GET'})).status,200);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM keys WHERE user_id=2').get().n,0);
  assert.equal((await f.request('/auth/callback?state='+state+'&code=test',{}, {method:'GET'})).status,400);
  const collected=await f.request('/api/v1/connect/poll',{device_code:connected.body.device_code});
  assert.equal(collected.status,200); assert.match(collected.body.key,/^ibl_/);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM approvals').get().n,0);
  assert.equal((await f.request('/api/v1/status',{}, {headers:{Authorization:'Bearer '+collected.body.key}})).body.login,'new-user');
});

test('scheduled cleanup erases expired plaintext and revokes legacy uncollected keys', async t => {
  const f=await fixture(t);
  f.sql.prepare("INSERT INTO keys(hash,user_id,label) VALUES(?,1,'agent')").run(await hash('uncollected'));
  f.sql.prepare('INSERT INTO approvals(device_code,user_code,user_id,key_plain,created_utc) VALUES(?,?,1,?,?)').run('old','BBBB-CCCC','uncollected',stamp(-901e3));
  await worker.scheduled({},f.env);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM approvals').get().n,0);
  assert.equal((await f.request('/api/v1/status',{}, {headers:{Authorization:'Bearer uncollected'}})).status,401);
});

test('a function made matched after selection cannot be leased', async t => {
  const f=await fixture(t); const batch=f.DB.batch.bind(f.DB);
  f.DB.batch=async statements => {
    if(statements.some(s=>s.sql.includes('INSERT INTO leases'))) f.sql.prepare("UPDATE functions SET state='matched'").run();
    return batch(statements);
  };
  assert.equal((await f.request('/api/v1/next')).status,409);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM leases').get().n,0);
  assert.equal(f.sql.prepare('SELECT tries FROM functions').get().tries,0);
});

test('expiry cleanup never deletes or penalizes a replacement live lease', async t => {
  const f=await fixture(t); f.lease(stamp(-1000));
  const batch=f.DB.batch.bind(f.DB); let replaced=false;
  f.DB.batch=async statements => {
    if(!replaced && statements.some(s=>s.sql.includes('DELETE FROM leases'))) {
      replaced=true;
      f.sql.prepare('UPDATE leases SET leased_utc=?, expires_utc=?').run(stamp(),stamp(3600e3));
    }
    return batch(statements);
  };
  assert.equal((await f.request('/api/v1/status')).status,200);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM leases').get().n,1);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM expired').get().n,0);
});

test('simultaneous approvals cannot replace the account or create an orphan credential', async t => {
  const f=await fixture(t);
  f.sql.prepare('INSERT INTO approvals(device_code,user_code) VALUES(?,?)').run('device','BBBB-CCCC');
  f.sql.prepare('INSERT INTO oauth_states(state,user_code) VALUES(?,?)').run('state-a','BBBB-CCCC');
  f.sql.prepare('INSERT INTO oauth_states(state,user_code) VALUES(?,?)').run('state-b','BBBB-CCCC');
  const original=globalThis.fetch;
  globalThis.fetch=async url => Response.json(String(url).includes('access_token') ? {access_token:'fixture-token'} : {id:2,login:'new-user'});
  t.after(()=> {globalThis.fetch=original;});
  const responses=await Promise.all(['state-a','state-b'].map(state=>f.request('/auth/callback?state='+state+'&code=test',{}, {method:'GET'})));
  assert.deepEqual(responses.map(r=>r.status).sort(),[200,410]);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM keys WHERE user_id=2').get().n,0);
  const polled=await f.request('/api/v1/connect/poll',{device_code:'device'});
  assert.equal(polled.status,200);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM keys WHERE user_id=2').get().n,1);
});

test('daily submission quota is enforced inside the lease consumption transaction', async t => {
  const f=await fixture(t); f.lease();
  f.sql.prepare("INSERT INTO functions(va,length,module,state) VALUES('0x00401230',16,'fixture','open')").run();
  f.sql.prepare("INSERT INTO leases VALUES('0x00401230',1,?,?)").run(stamp(),stamp(3600e3));
  for(let i=0;i<59;i++) f.result();
  const replies=await Promise.all([f.request('/api/v1/submit',submission),f.request('/api/v1/submit',{...submission,va:'0x00401230',source:'// 0x00401230\nint f(){return 0;}'})]);
  assert.equal(replies.filter(r=>r.status===200).length,1);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM results').get().n,60);
  assert.equal(f.sql.prepare('SELECT COUNT(*) n FROM leases').get().n,1);
});

test('owner review pagination and exact id lookup reach submissions beyond the first page', async t => {
  const f=await fixture(t);
  for(let i=0;i<23;i++) f.result('review');
  const first=await f.request('/api/v1/host/review',{}, {method:'GET',host:true});
  assert.equal(first.body.review.length,20); assert.equal(first.body.next_cursor,20);
  const second=await f.request('/api/v1/host/review?after=20',{}, {method:'GET',host:true});
  assert.deepEqual(second.body.review.map(r=>r.id),[21,22,23]); assert.equal(second.body.next_cursor,null);
  const exact=await f.request('/api/v1/host/review?id=23',{}, {method:'GET',host:true});
  assert.deepEqual(exact.body.review.map(r=>r.id),[23]);
});

test('failed verdict retry repairs a transient strike update failure without extending the pause', async t => {
  const f=await fixture(t);
  for(let i=0;i<2;i++) f.result('failed');
  f.sql.prepare('UPDATE results SET decided_utc=?').run(stamp());
  const id=f.result(); const prepare=f.DB.prepare.bind(f.DB); let fail=true;
  f.DB.prepare=query=> {
    const statement=prepare(query); const run=statement.run;
    statement.run=async function() {
      if(fail && query.includes('UPDATE users SET paused_until')) {fail=false;throw new Error('injected transient database failure');}
      return run.call(this);
    }; return statement;
  };
  const send=()=>f.request('/api/v1/host/verdict',{id,verdict:'failed'}, {host:true});
  assert.equal((await send()).status,500);
  assert.equal(f.sql.prepare('SELECT status FROM results WHERE id=?').get(id).status,'failed');
  assert.equal((await send()).status,200);
  const pause=f.sql.prepare('SELECT paused_until FROM users').get().paused_until;
  assert.ok(pause > stamp(6*864e5));
  // Replay derives the same end time from the original decision, not from retry time.
  f.sql.prepare('UPDATE results SET decided_utc=?').run(stamp(-3600e3));
  await send();
  assert.equal(f.sql.prepare('SELECT paused_until FROM users').get().paused_until,pause);
});

test('lease expiration never shortens an existing longer strike pause', async t => {
  const f=await fixture(t); const pause=stamp(7*864e5); f.lease(stamp(-1000));
  f.sql.prepare('UPDATE users SET paused_until=?').run(pause);
  for(let i=0;i<6;i++) f.sql.prepare('INSERT INTO expired VALUES(?,1,?)').run(va,stamp());
  await f.request('/api/v1/status');
  assert.equal(f.sql.prepare('SELECT paused_until FROM users').get().paused_until,pause);
});

test('kit downloads allow only the current manifest archive and checksum', async t => {
  const f=await fixture(t);
  f.env.ASSETS.fetch=async request => new URL(request.url).pathname==='/kit/latest.json'
    ? Response.json({file:'iblis-current.zip'}) : new Response('archive fixture');
  for(const path of ['/kit/iblis-retired.zip','/kit/iblis-retired.zip.sha256','/kit/iblis-retired%2Ezip'])
    assert.equal((await f.request(path,{}, {method:'GET'})).status,410);
  for(const path of ['/kit/iblis-current.zip','/kit/iblis-current.zip.sha256'])
    assert.equal((await f.request(path,{}, {method:'GET'})).body,'archive fixture');
  f.env.ASSETS.fetch=async()=>new Response('missing', {status:404});
  assert.equal((await f.request('/kit/iblis-current.zip',{}, {method:'GET'})).status,503);
});

test('JSON endpoints reject malformed and non-object bodies with 400', async t => {
  const f=await fixture(t);
  for(const path of ['/api/v1/status','/mcp']) {
    for(const body of ['null','[]','"text"','42','true','{broken','']) {
      const response=await worker.fetch(new Request(f.env.SITE_URL+path,{method:'POST',headers:{Authorization:'Bearer testkey','Content-Type':'application/json'},body}),f.env);
      assert.equal(response.status,400, path+' '+body);
      assert.ok(!(await response.text()).includes('test-oauth-secret'));
    }
  }
});

test('JSON body byte limit rejects oversized MCP and API streams before buffering them fully', async t => {
  const f=await fixture(t);
  for(const path of ['/api/v1/status','/mcp']) {
    let pulls=0, cancelled=false;
    const stream=new ReadableStream({
      pull(controller) { pulls++; controller.enqueue(new Uint8Array(512*1024).fill(32)); if(pulls===8)controller.close(); },
      cancel() {cancelled=true;},
    },{highWaterMark:0});
    const response=await worker.fetch(new Request(f.env.SITE_URL+path,{method:'POST',headers:{Authorization:'Bearer testkey','Content-Type':'application/json'},body:stream,duplex:'half'}),f.env);
    assert.equal(response.status,413); assert.ok(pulls<=5); assert.equal(cancelled,true);
  }
});

test('credential rotation gate defaults closed for host and OAuth while account revocation remains usable', async t => {
  const f=await fixture(t); delete f.env.CREDENTIALS_ROTATED;
  for(const path of ['/api/v1/host/pending','/api/v1/host/review','/auth/github?account=1','/auth/callback?state=old&code=old'])
    assert.equal((await f.request(path,{}, {method:'GET',host:true})).status,503);
  assert.equal((await f.request('/api/v1/host/verdict',{id:1,verdict:'verified'}, {host:true})).status,503);
  assert.equal((await f.request('/api/v1/status')).status,200);
  assert.equal((await f.request('/api/v1/account',{revoke_agent_keys:true})).status,200);
  f.env.CREDENTIALS_ROTATED='true';
  assert.equal((await f.request('/api/v1/host/pending',{}, {method:'GET',host:true})).status,200);
  assert.equal((await f.request('/auth/github?account=1',{}, {method:'GET'})).status,302);
});

test('host sync carries the closest parked source and examples into packets; a give-up source is kept', async t => {
  const f=await fixture(t); f.lease();
  const example={va:'0x00402000',length:12,why:'same module, 12 B',source:'// 0x00402000\nint g(){return 1;}'};
  const synced=await f.request('/api/v1/host/sync',{functions:[{va,length:16,module:'fixture',state:'open',note:'parked: 2 bytes off',
    best_source:'// 0x00401220\nint foo(){return 1;}',context:{examples:[example,{va:'bad',source:'x'}]}}]},{host:true});
  assert.equal(synced.status,200);
  let pk=(await f.request('/api/v1/packet',{va})).body;
  assert.equal(pk.previous_attempt.note,'parked: 2 bytes off');
  assert.equal(pk.previous_attempt.closest_source,'// 0x00401220\nint foo(){return 1;}');
  assert.deepEqual(pk.context.examples,[example]);
  assert.equal((await f.request('/api/v1/giveup',{va,reason:'stuck at 1 byte',source:'// 0x00401220\nint foo(){return 2;}'})).status,200);
  await f.request('/api/v1/host/sync',{functions:[{va,length:16,module:'fixture',state:'open',best_source:'// 0x00401220\nint foo(){return 1;}'}]},{host:true});
  const row=f.sql.prepare('SELECT best_note,best_source,context FROM functions WHERE va=?').get(va);
  assert.equal(row.best_source,'// 0x00401220\nint foo(){return 2;}');
  assert.equal(row.context,null);
});

test('host sync drops oversized sources and contexts instead of storing them', async t => {
  const f=await fixture(t);
  const big='x'.repeat(70000);
  await f.request('/api/v1/host/sync',{functions:[{va,length:16,module:'fixture',state:'open',best_source:big,
    context:{examples:[{va:'0x00402000',source:'y'.repeat(60000)}]}}]},{host:true});
  const row=f.sql.prepare('SELECT best_source,context FROM functions WHERE va=?').get(va);
  assert.equal(row.best_source,null); assert.equal(row.context,null);
});
