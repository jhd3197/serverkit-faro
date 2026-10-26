"""Grant lifecycle against the real ServerKit app, with a fake agent fleet.

Covers the issuer end to end: issue -> manifest -> key upload (claim, install
on bridge + targets, partial success, rollback) -> revoke/extend/retry/delete
and the sweeper. The fleet is an in-memory stand-in for the agents' ssh:keys
actions, so it checks exactly what the agents would be asked to do.

These tests run against the real ServerKit app from the sibling checkout
(``SERVERKIT_BACKEND`` env overrides the default ``../ServerKit/backend``) and
skip cleanly when it isn't available — the same pattern serverkit-vela uses.

Run with: python -m unittest discover -s tests
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = Path(os.environ.get("SERVERKIT_BACKEND")
               or (ROOT.parent / "ServerKit" / "backend"))

if not (BACKEND / "app" / "__init__.py").is_file():
    raise unittest.SkipTest(
        f"ServerKit backend not found at {BACKEND} (set SERVERKIT_BACKEND)")

os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SERVERKIT_REGISTRY_URL", "")
_DB_FILE = os.path.join(
    tempfile.gettempdir(), f"serverkit_faro_test_{os.getpid()}.db").replace("\\", "/")
os.environ["TEST_DATABASE_URL"] = "sqlite:///" + _DB_FILE

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app import create_app, db  # noqa: E402
from app.models import User  # noqa: E402
from app.models.plugin import InstalledPlugin  # noqa: E402
from app.models.server import Server  # noqa: E402
from app.plugins_sdk.agents_sdk import AgentCommandError  # noqa: E402
from flask_jwt_extended import create_access_token  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PLUGIN_PKG = "app.plugins.serverkit-faro"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDx2yqQJ7mH0Z8y0Qw9b2n4f6h8j0k2m4p6r8t0v2x4z laptop"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK9yZ0x8Yv2Qm1Wn3Rt4Uo5Ip6Lk7Jh8Gf9Ds0Aq1Zc2 other"


def load_plugin(app):
    if PLUGIN_PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PLUGIN_PKG, ROOT / "backend" / "__init__.py",
            submodule_search_locations=[str(ROOT / "backend")])
        module = importlib.util.module_from_spec(spec)
        sys.modules[PLUGIN_PKG] = module
        spec.loader.exec_module(module)
        importlib.import_module(PLUGIN_PKG + ".jobs")
    bp = sys.modules[PLUGIN_PKG].bp
    if bp.name not in app.blueprints:
        app.register_blueprint(bp, url_prefix="/api/v1/faro-grants")
    return sys.modules[PLUGIN_PKG]


class FakeFleet:
    """In-memory agents: keys[(server_id, user)] = {material: (options, comment)}."""

    def __init__(self):
        self.online = set()
        self.capable = set()
        self.fail = {}       # server_id -> error code to raise on run()
        self.keys = {}
        self.calls = []

    def is_online(self, server_id):
        return server_id in self.online

    def has_capability(self, server_id, cap):
        return cap == "ssh.keys" and server_id in self.capable

    def run(self, server_id, action, params=None, timeout=None, user_id=None, requires=None):
        self.calls.append((server_id, action, dict(params or {})))
        if server_id in self.fail:
            raise AgentCommandError("boom", code=self.fail[server_id])
        material = " ".join(params["key"].split()[:2])
        bucket = self.keys.setdefault((server_id, params["user"]), {})
        if action == "ssh:keys:add":
            bucket[material] = (params.get("options"), params["key"].split()[2:])
            return {"success": True, "changed": True, "expiry_applied": True}
        if action == "ssh:keys:remove":
            bucket.pop(material, None)
            return {"success": True}
        raise AssertionError(action)

    def has_key(self, server_id, user="root", key=KEY):
        return " ".join(key.split()[:2]) in self.keys.get((server_id, user), {})


class GrantApiTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app("testing")
        cls.plugin = load_plugin(cls.app)
        with cls.app.app_context():
            cls.plugin.models.register(db)
            db.create_all()
        cls.client = cls.app.test_client()
        cls.manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        db.drop_all()
        db.create_all()
        db.session.add(InstalledPlugin(
            name="serverkit-faro", display_name="Faro", slug="serverkit-faro",
            version=self.manifest["version"], status=InstalledPlugin.STATUS_ACTIVE,
            has_backend=True, url_prefix="/api/v1/faro-grants", manifest=self.manifest))
        self.admin = User(email="a@t.local", username="admin",
                          password_hash=generate_password_hash("x"),
                          role="admin", is_active=True)
        db.session.add(self.admin)
        self.web1 = Server(name="web-1", ip_address="10.0.0.5")
        self.web2 = Server(name="web-2", ip_address="10.0.0.6")
        self.bastion = Server(name="bastion", ip_address="203.0.113.9")
        db.session.add_all([self.web1, self.web2, self.bastion])
        db.session.commit()

        self.fleet = FakeFleet()
        ids = {self.web1.id, self.web2.id, self.bastion.id}
        self.fleet.online |= ids
        self.fleet.capable |= ids
        original = self.plugin.keys.fleet
        self.plugin.keys.fleet = lambda: self.fleet
        self.addCleanup(setattr, self.plugin.keys, "fleet", original)
        self.plugin.grants.reset_rate_limits()

    # ---- helpers ----
    def auth(self):
        return {"Authorization": f"Bearer {create_access_token(identity=self.admin.id)}"}

    def issue(self, **body):
        body.setdefault("name", "Client X")
        body.setdefault("server_ids", [self.web1.id, self.web2.id])
        resp = self.client.post("/api/v1/faro-grants/grants", json=body, headers=self.auth())
        return resp

    def token_of(self, link):
        return parse_qs(urlparse(link).query)["token"][0]

    def redeem(self, token, key=KEY):
        return self.client.post(f"/api/v1/faro-grants/.well-known/faro-grant/{token}/key",
                                json={"public_key": key})

    def grant(self, grant_id):
        return self.client.get(f"/api/v1/faro-grants/grants/{grant_id}",
                               headers=self.auth()).get_json()["grant"]

    # ---- issue ----
    def test_issue_validates_input(self):
        self.assertEqual(self.issue(ssh_username="../etc").status_code, 400)
        self.assertEqual(self.issue(ttl_hours="soon").status_code, 400)
        self.assertEqual(self.issue(jump={"host": "x.example.com"}).status_code, 400)
        self.assertEqual(self.issue(jump={"server_id": self.web1.id}).status_code, 400)
        self.assertEqual(self.issue(server_ids=["nope"]).status_code, 400)

    def test_issue_refuses_agents_without_ssh_keys(self):
        self.fleet.capable.discard(self.web2.id)
        resp = self.issue()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["unsupported"], ["web-2"])

    def test_servers_route_reports_capability(self):
        self.fleet.capable.discard(self.web2.id)
        servers = self.client.get("/api/v1/faro-grants/servers",
                                  headers=self.auth()).get_json()["servers"]
        by_name = {s["name"]: s for s in servers}
        self.assertTrue(by_name["web-1"]["ssh_keys"])
        self.assertFalse(by_name["web-2"]["ssh_keys"])

    # ---- redeem ----
    def test_full_redeem_installs_on_bridge_and_targets_with_options(self):
        resp = self.issue(jump={"server_id": self.bastion.id, "username": "hop"},
                          restrict_source=True)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        token = self.token_of(resp.get_json()["link"])
        grant_id = resp.get_json()["grant"]["id"]

        manifest = self.client.get(f"/api/v1/faro-grants/.well-known/faro-grant/{token}").get_json()
        self.assertEqual(manifest["connections"][0]["jump"],
                         {"host": "203.0.113.9", "port": 22, "username": "hop"})

        resp = self.redeem(token)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(self.fleet.has_key(self.web1.id))
        self.assertTrue(self.fleet.has_key(self.bastion.id, user="hop"))

        adds = {c[0]: c[2] for c in self.fleet.calls if c[1] == "ssh:keys:add"}
        self.assertIn('permitopen="10.0.0.5:22"', adds[self.bastion.id]["options"])
        self.assertIn("no-pty", adds[self.bastion.id]["options"])
        self.assertIn('from="203.0.113.9"', adds[self.web1.id]["options"])
        self.assertTrue(adds[self.web1.id]["key"].endswith(f"faro-grant:{grant_id}"))
        self.assertGreater(adds[self.web1.id]["expires_at"], 0)

        data = self.grant(grant_id)
        self.assertEqual(data["status"], "redeemed")
        self.assertEqual(data["health"], "ok")
        self.assertTrue(data["key_fingerprint"].startswith("SHA256:"))
        # token is single-use
        self.assertEqual(self.redeem(token, KEY2).status_code, 404)

    def test_partial_install_is_redeemed_and_retryable(self):
        resp = self.issue()
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.fleet.fail[self.web2.id] = "TIMEOUT"
        resp = self.redeem(token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([f["name"] for f in resp.get_json()["failed"]], ["web-2"])
        self.assertEqual(self.grant(grant_id)["health"], "partial")

        del self.fleet.fail[self.web2.id]
        resp = self.client.post(f"/api/v1/faro-grants/grants/{grant_id}/retry", headers=self.auth())
        self.assertEqual(resp.get_json()["failed"], [])
        self.assertTrue(self.fleet.has_key(self.web2.id))
        self.assertEqual(self.grant(grant_id)["health"], "ok")

    def test_bridge_failure_rolls_back_and_keeps_token(self):
        resp = self.issue(jump={"server_id": self.bastion.id})
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.fleet.fail[self.bastion.id] = "AGENT_OFFLINE"
        self.assertEqual(self.redeem(token).status_code, 502)
        self.assertFalse(self.fleet.has_key(self.web1.id))  # rolled back
        self.assertEqual(self.grant(grant_id)["status"], "active")

        del self.fleet.fail[self.bastion.id]
        self.assertEqual(self.redeem(token).status_code, 200)

    def test_permission_denied_reads_as_actionable(self):
        resp = self.issue(server_ids=[self.web1.id])
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.fleet.fail[self.web1.id] = "PERMISSION_DENIED"
        resp = self.redeem(token)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("Full Access", resp.get_json()["failed"][0]["error"])

    # ---- revoke / cleanup ----
    def test_revoke_failure_stays_visible_and_sweeper_retries(self):
        resp = self.issue()
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.redeem(token)

        self.fleet.fail[self.web1.id] = "AGENT_OFFLINE"
        resp = self.client.post(f"/api/v1/faro-grants/grants/{grant_id}/revoke", headers=self.auth())
        self.assertEqual(len(resp.get_json()["failed"]), 1)
        data = self.grant(grant_id)
        self.assertEqual((data["status"], data["health"]), ("revoked", "cleanup"))
        self.assertFalse(self.fleet.has_key(self.web2.id))
        self.assertTrue(self.fleet.has_key(self.web1.id))

        # can't delete while a key is still out there
        resp = self.client.delete(f"/api/v1/faro-grants/grants/{grant_id}", headers=self.auth())
        self.assertEqual(resp.status_code, 409)

        del self.fleet.fail[self.web1.id]
        result = self.plugin.jobs.expire_grants(None)
        self.assertEqual(result["retried"], 1)
        self.assertFalse(self.fleet.has_key(self.web1.id))
        self.assertEqual(self.grant(grant_id)["health"], "ok")

        resp = self.client.delete(f"/api/v1/faro-grants/grants/{grant_id}", headers=self.auth())
        self.assertEqual(resp.status_code, 200)

    def test_revoked_token_is_410(self):
        resp = self.issue()
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.client.post(f"/api/v1/faro-grants/grants/{grant_id}/revoke", headers=self.auth())
        self.assertEqual(self.redeem(token).status_code, 410)

    def test_sweeper_expires_and_removes_keys(self):
        resp = self.issue()
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.redeem(token)
        grant = db.session.get(self.plugin.models.FaroGrant, grant_id)
        grant.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        result = self.plugin.jobs.expire_grants(None)
        self.assertEqual(result["expired"], 1)
        self.assertFalse(self.fleet.has_key(self.web1.id))
        self.assertEqual(self.grant(grant_id)["status"], "expired")

    def test_extend_reinstalls_with_new_expiry(self):
        resp = self.issue(ttl_hours=4)
        token, grant_id = self.token_of(resp.get_json()["link"]), resp.get_json()["grant"]["id"]
        self.redeem(token)
        before = [c[2]["expires_at"] for c in self.fleet.calls if c[1] == "ssh:keys:add"][0]
        resp = self.client.post(f"/api/v1/faro-grants/grants/{grant_id}/extend",
                                json={"ttl_hours": 48}, headers=self.auth())
        self.assertEqual(resp.status_code, 200, resp.get_json())
        after = [c[2]["expires_at"] for c in self.fleet.calls if c[1] == "ssh:keys:add"][-1]
        self.assertGreater(after - before, 40 * 3600)

    def test_admin_routes_need_auth(self):
        self.assertEqual(self.client.get("/api/v1/faro-grants/grants").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/faro-grants/servers").status_code, 401)


if __name__ == "__main__":
    unittest.main()
