// Unit tests for the faro:// link builder. Run with: node --test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildFaroConnect } from './faroLink.js';

test('builds a basic sftp connect link', () => {
    const url = buildFaroConnect({ host: 'wp.example.com' });
    assert.equal(url, 'faro://connect?protocol=sftp&host=wp.example.com');
});

test('URL-encodes path and name', () => {
    const url = buildFaroConnect({
        host: 'wp-prod.example.com',
        port: 22,
        username: 'wp_deploy',
        path: '/home/wp/public_html',
        name: 'My WordPress (prod)',
    });
    const q = new URLSearchParams(url.slice('faro://connect?'.length));
    assert.equal(q.get('host'), 'wp-prod.example.com');
    assert.equal(q.get('port'), '22');
    assert.equal(q.get('username'), 'wp_deploy');
    assert.equal(q.get('path'), '/home/wp/public_html');
    assert.equal(q.get('name'), 'My WordPress (prod)');
    // the raw string must be percent-encoded (no literal spaces or slashes-in-value issues)
    assert.ok(!url.includes(' '));
    assert.ok(url.includes('%2Fhome%2Fwp%2Fpublic_html'));
});

test('drops empty and null params', () => {
    const url = buildFaroConnect({ host: 'h', port: '', username: null, path: undefined, name: '  ' });
    assert.equal(url, 'faro://connect?protocol=sftp&host=h');
});

test('never emits a password or other secret params', () => {
    const url = buildFaroConnect({ host: 'h', password: 'hunter2', secret: 'x', passphrase: 'y', token: 'z' });
    assert.ok(!/password|hunter2|secret|passphrase|token/i.test(url));
});

test('returns null when host is missing or blank', () => {
    assert.equal(buildFaroConnect({ host: '' }), null);
    assert.equal(buildFaroConnect({ host: '   ' }), null);
    assert.equal(buildFaroConnect({}), null);
});

test('honors a non-default protocol', () => {
    const url = buildFaroConnect({ host: 'h', protocol: 'ftps' });
    assert.ok(url.startsWith('faro://connect?protocol=ftps&host=h'));
});
