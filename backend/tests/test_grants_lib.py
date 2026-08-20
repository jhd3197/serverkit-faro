"""Standalone unit tests for grants_lib (NO app.* imports — stdlib only).

Run from the repo root:

    python -m unittest discover -s backend/tests -v
"""
import hashlib
import json
import os
import re
import sys
import unittest
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import grants_lib  # noqa: E402

# A well-formed ed25519 line (base64-shaped blob, with comment).
ED25519 = ('ssh-ed25519 '
           'AAAAC3NzaC1lZDI1NTE5AAAAIDx2yqQJ7mH0Z8y0Qw9b2n4f6h8j0k2m4p6r8t0v2x4z '
           'faro-grant')
RSA = ('ssh-rsa ' + 'A' * 100 + ' ops@panel')
ECDSA = ('ecdsa-sha2-nistp256 ' + 'B' * 120 + ' comment')


class TokenTests(unittest.TestCase):
    def test_token_charset_and_length(self):
        token = grants_lib.new_token()
        self.assertEqual(len(token), 32)
        self.assertRegex(token, r'^[A-Za-z0-9_-]{16,128}$')

    def test_tokens_are_unique(self):
        self.assertNotEqual(grants_lib.new_token(), grants_lib.new_token())

    def test_hash_is_sha256_hex_and_stable(self):
        token = grants_lib.new_token()
        digest = grants_lib.hash_token(token)
        self.assertEqual(digest, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, grants_lib.hash_token(token))
        self.assertNotEqual(digest, grants_lib.hash_token(token + 'x'))


class ValidatePublicKeyTests(unittest.TestCase):
    def test_accepts_ed25519_rsa_ecdsa(self):
        for line in (ED25519, RSA, ECDSA):
            self.assertEqual(grants_lib.validate_public_key(line), line)

    def test_normalizes_whitespace_and_missing_comment(self):
        blob = ED25519.split()[1]
        messy = f'  ssh-ed25519   {blob}  '
        self.assertEqual(grants_lib.validate_public_key(messy),
                         f'ssh-ed25519 {blob}')

    def test_rejects_garbage(self):
        for bad in ('', '   ', 'not-a-key', 'ssh-ed25519',
                    'ssh-dss ' + 'A' * 100,
                    'ssh-ed25519 !!!notbase64!!!',
                    'ssh-ed25519 QUJD',           # blob too short
                    'ssh-ed25519 ' + 'A' * 5000,  # blob too long
                    ED25519 + '\n' + ED25519,     # multi-line
                    ED25519 + '\r\n' + ED25519,
                    None, 42):
            with self.assertRaises(ValueError, msg=repr(bad)):
                grants_lib.validate_public_key(bad)


class BuildManifestTests(unittest.TestCase):
    def setUp(self):
        self.grant = {
            'name': 'Client X — 2 servers',
            'expires_at': datetime(2026, 8, 7, 0, 0, 0),
            'ssh_username': 'deploy',
            'path': '/var/www',
            'jump': {'host': 'bastion.agency.com', 'port': 22,
                     'username': 'faro-grant'},
        }
        self.servers = [
            {'server_id': 1, 'name': 'web-1', 'host': '10.0.0.11'},
            {'server_id': 2, 'name': 'web-2', 'host': '10.0.0.12'},
        ]

    def test_manifest_shape_matches_spec(self):
        m = grants_lib.build_manifest(issuer_name='ServerKit',
                                      grant=self.grant,
                                      servers=self.servers,
                                      panel_url='https://panel.agency.com')
        # snake_case top-level keys, exactly per spec.
        self.assertEqual(
            list(m.keys()),
            ['version', 'issuer', 'name', 'group', 'expires_at', 'auth',
             'connections'])
        self.assertEqual(m['version'], 1)
        self.assertEqual(m['issuer'], 'ServerKit · panel.agency.com')
        self.assertEqual(m['name'], 'Client X — 2 servers')
        self.assertEqual(m['group'], 'Client X — 2 servers')
        self.assertEqual(m['expires_at'], '2026-08-07T00:00:00Z')
        self.assertEqual(m['auth'], {'type': 'key-install'})
        self.assertEqual(len(m['connections']), 2)

        conn = m['connections'][0]
        self.assertEqual(conn['name'], 'web-1')
        self.assertEqual(conn['protocol'], 'sftp')
        self.assertEqual(conn['host'], '10.0.0.11')
        self.assertEqual(conn['port'], 22)
        self.assertEqual(conn['username'], 'deploy')
        self.assertEqual(conn['path'], '/var/www')
        self.assertEqual(conn['jump'], {'host': 'bastion.agency.com',
                                        'port': 22, 'username': 'faro-grant'})

    def test_manifest_round_trips_json_snake_case(self):
        m = grants_lib.build_manifest(issuer_name='ServerKit',
                                      grant=self.grant,
                                      servers=self.servers,
                                      panel_url='panel.agency.com')
        text = json.dumps(m)
        self.assertIn('"expires_at"', text)
        self.assertNotIn('expiresAt', text)
        self.assertEqual(json.loads(text), m)

    def test_minimal_grant_omits_optional_fields(self):
        grant = {'name': 'g', 'expires_at': None, 'ssh_username': 'root',
                 'path': None, 'jump': None}
        m = grants_lib.build_manifest(issuer_name='ServerKit', grant=grant,
                                      servers=self.servers[:1],
                                      panel_url='https://panel.agency.com')
        self.assertNotIn('expires_at', m)
        conn = m['connections'][0]
        self.assertNotIn('path', conn)
        self.assertNotIn('jump', conn)
        self.assertEqual(conn['username'], 'root')

    def test_aware_datetime_is_normalized_to_z(self):
        grant = dict(self.grant)
        grant['expires_at'] = datetime(
            2026, 8, 7, 2, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        m = grants_lib.build_manifest(issuer_name='ServerKit', grant=grant,
                                      servers=self.servers[:1],
                                      panel_url='panel.agency.com')
        self.assertEqual(m['expires_at'], '2026-08-07T00:00:00Z')


class BuildFaroLinkTests(unittest.TestCase):
    def test_link_shape_and_encoding(self):
        link = grants_lib.build_faro_link('https://panel.agency.com/',
                                          'gr_9f2kQ7-x', 'Client X servers')
        self.assertTrue(link.startswith('faro://grant?'))
        qs = parse_qs(urlparse(link).query)
        self.assertEqual(qs['issuer'],
                         ['https://panel.agency.com/api/v1/faro-grants'])
        self.assertEqual(qs['token'], ['gr_9f2kQ7-x'])
        self.assertEqual(qs['name'], ['Client X servers'])
        # Spaces encode as %20 (matching the spec example), not '+'.
        self.assertIn('Client%20X%20servers', link)
        self.assertIn(
            'issuer=https%3A%2F%2Fpanel.agency.com%2Fapi%2Fv1%2Ffaro-grants',
            link)


class AuthorizedKeysTests(unittest.TestCase):
    OTHER = 'ssh-ed25519 ' + 'C' * 68 + ' someone-else'

    def test_add_to_empty(self):
        out = grants_lib.authorized_keys_add('', ED25519)
        self.assertEqual(out, ED25519 + '\n')

    def test_add_appends_and_fixes_missing_newline(self):
        out = grants_lib.authorized_keys_add(self.OTHER, ED25519)
        self.assertEqual(out, self.OTHER + '\n' + ED25519 + '\n')

    def test_add_is_idempotent(self):
        once = grants_lib.authorized_keys_add(self.OTHER + '\n', ED25519)
        twice = grants_lib.authorized_keys_add(once, ED25519)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(ED25519.split()[1]), 1)

    def test_add_matches_on_key_material_not_comment(self):
        same_key_other_comment = ('ssh-ed25519 ' + ED25519.split()[1]
                                  + ' different-comment')
        content = ED25519 + '\n'
        self.assertEqual(
            grants_lib.authorized_keys_add(content, same_key_other_comment),
            content)

    def test_remove(self):
        content = self.OTHER + '\n' + ED25519 + '\n'
        out = grants_lib.authorized_keys_remove(content, ED25519)
        self.assertEqual(out, self.OTHER + '\n')

    def test_remove_matches_on_key_material(self):
        same_key_other_comment = ('ssh-ed25519 ' + ED25519.split()[1]
                                  + ' edited-by-admin')
        out = grants_lib.authorized_keys_remove(ED25519 + '\n',
                                                same_key_other_comment)
        self.assertEqual(out, '')

    def test_remove_absent_is_noop(self):
        content = self.OTHER + '\n'
        self.assertEqual(grants_lib.authorized_keys_remove(content, ED25519),
                         content)

    def test_remove_from_empty(self):
        self.assertEqual(grants_lib.authorized_keys_remove('', ED25519), '')

    def test_add_then_remove_round_trip(self):
        content = self.OTHER + '\n'
        added = grants_lib.authorized_keys_add(content, ED25519)
        removed = grants_lib.authorized_keys_remove(added, ED25519)
        self.assertEqual(removed, content)


if __name__ == '__main__':
    unittest.main()
