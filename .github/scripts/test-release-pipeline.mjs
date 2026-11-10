import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {promisify} from "node:util";

const workflow = readFileSync(new URL('../workflows/package.yml', import.meta.url), 'utf8');
const apt = workflow.split('\n  publish-apt:\n')[1].split('\n  upload-installers:\n')[0];
const dependencies = apt.split('    if:')[0];
assert.match(dependencies, /package-linux/);
assert.doesNotMatch(dependencies, /package-other/);
assert.match(apt, /needs.package-linux.result == 'success'/);
assert.doesNotMatch(apt, /require-newer:/, 'Do not reclassify approved previews at publication: build completion order may differ from tag order');
assert.match(workflow, /!cerberus\/source\/\*\*/);
assert.match(workflow, /cancel-in-progress:.*github.ref_type != 'tag'/);
assert.match(workflow, /name: Release result for CI-fix automation/);
assert.match(workflow, /if \(failed.length\) process.exit\(1\)/);
assert.equal((workflow.match(/queue: max/g) || []).length, 2, 'Preserve queued APT and R2 releases');
const wrapper = readFileSync(new URL('../workflows/package-platform.yml', import.meta.url), 'utf8');
const pin = wrapper.match(/1100soft\/CI\/\.github\/workflows\/tauri-package.yml@([0-9a-f]{40})/)[1];
for (const file of ['package.yml','publish-apt-backfill.yml','ci.yml','web.yml']) {
  const text = readFileSync(new URL('../workflows/'+file, import.meta.url),'utf8');
  for (const match of text.matchAll(/1100soft\/CI\/\.github\/workflows\/[^@\s]+@([0-9a-f]{40})/g)) assert.equal(match[1], pin);
  for (const match of text.matchAll(/shared-ci-ref: ([0-9a-f]{40})/g)) assert.equal(match[1], pin, 'Helper checkout and reusable workflow must use the same reviewed revision');
}

if (process.platform === 'linux') {
  const exec = promisify(execFile);
  const compare = async (left, op, right) => exec('dpkg', ['--compare-versions',left,op,right]);
  await compare('0.7.6~preview.470', 'gt', '0.7.6~preview.469+ge3ff340');
  await compare('0.7.6~preview.471', 'gt', '0.7.6~preview.470');
  await compare('0.7.7~preview.1', 'gt', '0.7.6~preview.471');
  await compare('0.7.7~preview.1', 'lt', '0.7.7');
  await compare('0.7.7', 'gt', '0.7.6');
  const directory = mkdtempSync(join(tmpdir(), 'mountlet-debian-'));
  try {
    const root = join(directory,'root'); mkdirSync(join(root,'DEBIAN'), {recursive:true});
    writeFileSync(join(root,'DEBIAN/control'), 'Package: mountlet\nVersion: 0.7.6\nArchitecture: amd64\nMaintainer: Fixture <fixture@example.invalid>\nDescription: Fixture\n');
    const source = join(directory,'source.deb');
    const env = {...process.env, SOURCE_DATE_EPOCH:'1700000000'};
    await exec('dpkg-deb', ['--build','--root-owner-group',root,source], {env});
    const script = resolve('.github/scripts/prepare-apt-packages.sh');
    for (const [channel, version, names] of [
      ['preview','0.7.6~preview.470',['mountlet-preview','mountlet-lean-preview']],
      ['stable','0.7.6',['mountlet','mountlet-lean']],
    ]) {
      const first = join(directory,channel+'-first'), second = join(directory,channel+'-second');
      for (const destination of [first,second]) await exec('bash',[script,source,source,destination,channel,version], {env});
      for (const file of readdirSync(first)) {
        assert.deepEqual(readFileSync(join(first,file)),readFileSync(join(second,file)), 'Identical inputs must prepare identical Debian bytes');
        const field = async key => (await exec('dpkg-deb',['--field',join(first,file),key])).stdout.trim();
        const name = await field('Package'); assert.ok(names.includes(name));
        assert.equal(await field('Version'),version);
        if (name.includes('lean')) assert.match(await field('Depends'),/rclone/);
        if (name !== 'mountlet') assert.match(await field('Conflicts'),/mountlet/);
      }
    }
  } finally { rmSync(directory,{recursive:true,force:true}); }
}
console.log('Linux-only publication gating, Debian upgrades and reproducible identity preparation passed.');
