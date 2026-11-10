import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';
import {readReleaseConfig, releaseObjectKey} from './release-layout.mjs';

const mode = process.argv[2];
if (!mode) {
  const exec = promisify(execFile);
  for (const kind of ['new','same','conflict','race-same','race-conflict']) {
    let output = '', failed = false;
    try { output = (await exec(process.execPath, [fileURLToPath(import.meta.url),kind])).stdout; }
    catch (error) { failed = true; output = error.stdout; }
    assert.equal(failed, ['conflict','race-conflict'].includes(kind), kind);
    const receipt = JSON.parse(output.trim().split('\n').at(-1));
    if (failed) assert.equal(receipt.indexWrites,0,'A conflicting artifact must never update the release index');
    if (kind === 'conflict') assert.equal(receipt.objectWrites,0,'Preflight all files before writing any');
  }
  console.log('R2 immutable preflight, idempotence and atomic conditional-write race checks passed.');
} else {
  const directory = mkdtempSync(join(tmpdir(),'mountlet-r2-test-'));
  let indexWrites = 0, objectWrites = 0;
  process.on('exit', () => { rmSync(directory,{recursive:true,force:true}); console.log(JSON.stringify({indexWrites,objectWrites})); });
  const config = readReleaseConfig(), version = '0.7.7-preview.1';
  const objects = new Map(), files = {};
  for (const [name, artifact] of Object.entries(config.artifacts)) {
    const body = Buffer.from('fixture-installer-'+name);
    writeFileSync(join(directory,artifact.source),body);
    const key = releaseObjectKey(config.objectPrefix,version,artifact);
    files[name] = {objectKey:key,sha256:createHash('sha256').update(body).digest('hex')};
    if (mode === 'same' || mode === 'conflict') objects.set(key,body);
  }
  if (mode === 'conflict') objects.set(Object.values(files)[0].objectKey, Buffer.from('different orphaned bytes'));
  if (mode === 'same') objects.set('releases/index.json',Buffer.from(JSON.stringify({releases:[{version,files}]})));
  globalThis.fetch = async (url,options) => {
    const key = decodeURIComponent(new URL(url).pathname.split('/').slice(2).join('/'));
    if (options.method === 'GET') return objects.has(key) ? new Response(objects.get(key)) : new Response('',{status:404});
    if (options.method === 'DELETE') { objects.delete(key); return new Response(''); }
    if (options.method === 'PUT') {
      if (key === 'releases/index.json') indexWrites++;
      else {
        assert.equal(options.headers['if-none-match'],'*','Installer writes must be atomic create-only operations');
        if (mode.startsWith('race')) objects.set(key,mode === 'race-same' ? options.body : Buffer.from('racing replacement'));
        if (objects.has(key)) return new Response('',{status:412});
        objectWrites++;
      }
      objects.set(key, options.body); return new Response('');
    }
    throw Error('Unexpected request: '+options.method);
  };
  Object.assign(process.env,{CLOUDFLARE_ACCOUNT_ID:'fixture',CLOUDFLARE_R2_ACCESS_KEY_ID:'fixture',CLOUDFLARE_R2_SECRET_ACCESS_KEY:'fixture',MOUNTLET_RELEASE_VERSION:version,MOUNTLET_RELEASE_QUALIFIER:'',GITHUB_REF_TYPE:'tag',GITHUB_REF_NAME:'v'+version});
  process.argv=[process.execPath,'upload-release-r2.mjs','fixture',directory,'--remote'];
  await import('./upload-release-r2.mjs');
}
