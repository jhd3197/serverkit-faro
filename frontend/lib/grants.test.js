// Unit tests for the access-grant UI helpers. Run with: node --test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    EMPTY_GRANT_FORM, buildGrantPayload, grantKind, keyKind, summarize,
} from './grants.js';

const form = (over) => ({ ...EMPTY_GRANT_FORM, name: 'Client X', serverIds: ['a', 'b'], ...over });

test('minimal form maps to the API payload', () => {
    const { errors, payload } = buildGrantPayload(form());
    assert.deepEqual(errors, {});
    assert.deepEqual(payload, {
        name: 'Client X', server_ids: ['a', 'b'], ttl_hours: 72, ssh_username: 'root',
    });
});

test('bridge, path, user creation and source restriction are passed through', () => {
    const { payload } = buildGrantPayload(form({
        sshUsername: 'deploy', createUser: true, path: '/var/www',
        bridgeId: 'c', bridgeUsername: 'hop', restrictSource: true, ttlHours: 4,
    }));
    assert.deepEqual(payload, {
        name: 'Client X', server_ids: ['a', 'b'], ttl_hours: 4, ssh_username: 'deploy',
        path: '/var/www', create_user: true,
        jump: { server_id: 'c', username: 'hop' }, restrict_source: true,
    });
});

test('a member grant carries member_id', () => {
    const { payload } = buildGrantPayload(form({ memberId: '7' }));
    assert.equal(payload.member_id, 7);
    assert.equal(buildGrantPayload(form()).payload.member_id, undefined);
});

test('root never asks to create a user; source restriction needs a bridge', () => {
    const { payload } = buildGrantPayload(form({ createUser: true, restrictSource: true }));
    assert.equal(payload.create_user, undefined);
    assert.equal(payload.restrict_source, undefined);
});

test('validation errors', () => {
    const { errors, payload } = buildGrantPayload({
        ...EMPTY_GRANT_FORM, name: ' ', serverIds: ['a'], sshUsername: '../etc',
        path: 'var/www', bridgeId: 'a', bridgeUsername: 'Bad User',
    });
    assert.equal(payload, null);
    assert.deepEqual(errors, {
        name: 'required', sshUsername: 'invalid', path: 'absolute',
        bridgeId: 'isTarget', bridgeUsername: 'invalid',
    });
    assert.equal(buildGrantPayload(form({ serverIds: [] })).errors.serverIds, 'required');
});

test('tones: health problems override status', () => {
    assert.equal(grantKind({ status: 'redeemed', health: 'ok' }), 'green');
    assert.equal(grantKind({ status: 'redeemed', health: 'partial' }), 'amber');
    assert.equal(grantKind({ status: 'revoked', health: 'cleanup' }), 'amber');
    assert.equal(grantKind({ status: 'active' }), 'cyan');
    assert.equal(keyKind('remove_failed'), 'red');
    assert.equal(keyKind('nope'), 'gray');
});

test('summary counts', () => {
    assert.deepEqual(summarize([
        { status: 'redeemed', health: 'ok' },
        { status: 'redeemed', health: 'partial' },
        { status: 'active', health: 'ok' },
        { status: 'revoked', health: 'cleanup' },
    ]), { live: 2, waiting: 1, attention: 2 });
});
