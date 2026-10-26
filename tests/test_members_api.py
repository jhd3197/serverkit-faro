"""Members, devices and invites against the real ServerKit app (fake agents).

Same harness as test_grants_api: skips cleanly without a sibling ServerKit
checkout. Run with: python -m unittest discover -s tests
"""
import unittest
from datetime import datetime, timedelta

from test_grants_api import KEY, KEY2, GrantHarness, db

KEY3 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHk3bR5uT8wQ1zXc4Vn6Mp9Ls2Kd7Fg0Hj3Yt6Ue8Wq4 phone"


class MemberApiTests(GrantHarness):

    # ---- helpers ----
    def post(self, path, body=None):
        return self.client.post(f"/api/v1/faro-grants{path}", json=body or {}, headers=self.auth())

    def get(self, path):
        return self.client.get(f"/api/v1/faro-grants{path}", headers=self.auth())

    def member(self, name="Ana"):
        return self.post("/members", {"name": name, "email": "ana@example.com"}).get_json()["member"]["id"]

    def member_grant(self, member_id, **body):
        body.setdefault("name", "Client X")
        body.setdefault("server_ids", [self.web1.id, self.web2.id])
        resp = self.post("/grants", {"member_id": member_id, **body})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.assertIsNone(resp.get_json()["link"])
        return resp.get_json()["grant"]["id"]

    def invite(self, member_id, **body):
        resp = self.post(f"/members/{member_id}/invites", body)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return self.token_of(resp.get_json()["link"])

    def last_add(self, server_id):
        return [c[2] for c in self.fleet.calls if c[0] == server_id and c[1] == "ssh:keys:add"][-1]

    # ---- tests ----
    def test_invite_needs_access_first(self):
        member_id = self.member()
        self.assertEqual(self.post(f"/members/{member_id}/invites").status_code, 409)

    def test_device_approval_flow(self):
        member_id = self.member()
        self.member_grant(member_id, jump={"server_id": self.bastion.id, "username": "hop"})
        token = self.invite(member_id)

        manifest = self.client.get(f"/api/v1/faro-grants/.well-known/faro-grant/{token}").get_json()
        self.assertEqual(manifest["name"], "Ana")
        self.assertEqual([c["name"] for c in manifest["connections"]], ["web-1", "web-2"])
        self.assertEqual(manifest["connections"][0]["jump"]["username"], "hop")

        resp = self.redeem(token)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["pending_approval"])
        self.assertEqual(resp.get_json()["installed"], ["web-1", "web-2"])
        self.assertFalse(self.fleet.has_key(self.web1.id))  # nothing before approval
        self.assertEqual(self.redeem(token, KEY2).status_code, 404)  # single use

        detail = self.get(f"/members/{member_id}").get_json()
        device = detail["devices"][0]
        self.assertEqual(device["status"], "pending")
        resp = self.post(f"/devices/{device['id']}/approve")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(self.fleet.has_key(self.web1.id))
        self.assertTrue(self.fleet.has_key(self.web2.id))
        self.assertTrue(self.fleet.has_key(self.bastion.id, user="hop"))
        self.assertTrue(self.last_add(self.web1.id)["key"].endswith(f"faro-device:{device['id']}"))
        self.assertEqual(sorted(o for o in self.last_add(self.bastion.id)["options"] if o.startswith("permitopen")),
                         ['permitopen="10.0.0.5:22"', 'permitopen="10.0.0.6:22"'])

        # second device, no approval needed -> live right away
        token2 = self.invite(member_id, require_approval=False)
        resp = self.redeem(token2, KEY2)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("pending_approval", resp.get_json())
        self.assertTrue(self.fleet.has_key(self.web1.id, key=KEY2))

        # revoking device 1 leaves device 2 alone
        self.post(f"/devices/{device['id']}/revoke")
        self.assertFalse(self.fleet.has_key(self.web1.id))
        self.assertFalse(self.fleet.has_key(self.bastion.id, user="hop"))
        self.assertTrue(self.fleet.has_key(self.web1.id, key=KEY2))

    def test_duplicate_key_and_reject(self):
        member_id = self.member()
        self.member_grant(member_id)
        self.redeem(self.invite(member_id, require_approval=False))
        self.assertEqual(self.redeem(self.invite(member_id), KEY).status_code, 409)

        token = self.invite(member_id)
        self.redeem(token, KEY3)
        pending = [d for d in self.get(f"/members/{member_id}").get_json()["devices"]
                   if d["status"] == "pending"][0]
        self.post(f"/devices/{pending['id']}/reject")
        self.assertFalse(self.fleet.has_key(self.web1.id, key=KEY3))

    def test_overlapping_grants_keep_shared_lines(self):
        member_id = self.member()
        grant_a = self.member_grant(member_id, name="A", server_ids=[self.web1.id],
                                    jump={"server_id": self.bastion.id})
        self.member_grant(member_id, name="B", server_ids=[self.web1.id, self.web2.id],
                          jump={"server_id": self.bastion.id})
        self.redeem(self.invite(member_id, require_approval=False))
        self.assertTrue(self.fleet.has_key(self.web1.id))

        self.post(f"/grants/{grant_a}/revoke")
        # B still wants web-1 as root and the shared bridge line
        self.assertTrue(self.fleet.has_key(self.web1.id))
        self.assertTrue(self.fleet.has_key(self.bastion.id))
        detail = self.grant(grant_a)
        self.assertEqual((detail["status"], detail["health"]), ("revoked", "ok"))

    def test_bridge_permitopen_shrinks_when_a_grant_ends(self):
        member_id = self.member()
        self.member_grant(member_id, name="A", server_ids=[self.web1.id],
                          jump={"server_id": self.bastion.id})
        grant_b = self.member_grant(member_id, name="B", server_ids=[self.web2.id],
                                    jump={"server_id": self.bastion.id})
        self.redeem(self.invite(member_id, require_approval=False))
        self.assertEqual(len([o for o in self.last_add(self.bastion.id)["options"]
                              if o.startswith("permitopen")]), 2)
        self.post(f"/grants/{grant_b}/revoke")
        self.assertFalse(self.fleet.has_key(self.web2.id))
        self.assertEqual([o for o in self.last_add(self.bastion.id)["options"] if o.startswith("permitopen")],
                         ['permitopen="10.0.0.5:22"'])

    def test_install_failure_rolls_back_device(self):
        member_id = self.member()
        self.member_grant(member_id, server_ids=[self.web1.id])
        token = self.invite(member_id, require_approval=False)
        self.fleet.fail[self.web1.id] = "AGENT_OFFLINE"
        self.assertEqual(self.redeem(token).status_code, 502)
        self.assertEqual(self.get(f"/members/{member_id}").get_json()["devices"], [])
        del self.fleet.fail[self.web1.id]
        self.assertEqual(self.redeem(token).status_code, 200)  # invite still usable

    def test_partial_failure_is_retried_by_sweeper(self):
        member_id = self.member()
        grant_id = self.member_grant(member_id)
        self.fleet.fail[self.web2.id] = "TIMEOUT"
        resp = self.redeem(self.invite(member_id, require_approval=False))
        self.assertEqual([f["name"] for f in resp.get_json()["failed"]], ["web-2"])
        self.assertEqual(self.grant(grant_id)["health"], "partial")
        del self.fleet.fail[self.web2.id]
        self.plugin.jobs.expire_grants(None)
        self.assertTrue(self.fleet.has_key(self.web2.id))
        self.assertEqual(self.grant(grant_id)["health"], "ok")

    def test_expiry_removes_member_keys(self):
        member_id = self.member()
        grant_id = self.member_grant(member_id)
        self.redeem(self.invite(member_id, require_approval=False))
        grant = db.session.get(self.plugin.models.FaroGrant, grant_id)
        grant.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        self.plugin.jobs.expire_grants(None)
        self.assertFalse(self.fleet.has_key(self.web1.id))

    def test_revoke_all_then_delete(self):
        member_id = self.member()
        self.member_grant(member_id)
        self.redeem(self.invite(member_id, require_approval=False))
        self.assertEqual(self.client.delete(f"/api/v1/faro-grants/members/{member_id}",
                                            headers=self.auth()).status_code, 409)
        resp = self.post(f"/members/{member_id}/revoke-all")
        self.assertEqual(resp.get_json()["failed"], [])
        self.assertFalse(self.fleet.has_key(self.web1.id))
        self.assertEqual(self.client.delete(f"/api/v1/faro-grants/members/{member_id}",
                                            headers=self.auth()).status_code, 200)

    def test_list_shows_member_grants(self):
        member_id = self.member()
        self.member_grant(member_id)
        grants = self.get("/grants").get_json()["grants"]
        self.assertEqual(grants[0]["member_name"], "Ana")
        members = self.get("/members").get_json()["members"]
        self.assertEqual((members[0]["grants_live"], members[0]["devices_active"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
