import asyncio
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

# Safe local-only settings must be set before importing application modules.
_TEST_ROOT = tempfile.mkdtemp(prefix="bahaa-orders-tests-")
os.environ.setdefault("DEMO_MODE", "1")
os.environ.setdefault("RUN_BOT", "0")
os.environ.setdefault("BOT_TOKEN", "")
os.environ.setdefault("SUPPLIER_API_TOKEN", "")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("DB_PATH", os.path.join(_TEST_ROOT, "store.db"))

import httpx
from app import bot, config, db, orders
from app.supplier import MaxStoreClient, SupplierError


class OrderReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if db._db is not None:
            await db.close()
        db._db = None
        db._open_lock = None
        self.temp = tempfile.TemporaryDirectory(prefix="bahaa-test-case-")
        config.DB_PATH = os.path.join(self.temp.name, "store.db")
        await db.connect()
        self.uid = 987654321
        await db.upsert_user(self.uid, "customer", "Customer")
        self.oid = str(uuid.uuid4())
        self.balance_before = await db.adjust_balance(self.uid, 10, "test-funding", "fixture")
        self.balance_before = await db.adjust_balance(self.uid, -2, "order", self.oid)
        await db.create_order({
            "id": self.oid,
            "user_id": self.uid,
            "product_id": 12,
            "product_name": "UC 60",
            "qty": 1,
            "params": json.dumps({"player_id": "1234"}),
            "unit_price": 2,
            "price": 2,
            "cost": 1,
            "status": "wait",
            "balance_after": self.balance_before,
        })
        self.notify_user = patch.object(orders.notify, "user", new_callable=AsyncMock)
        self.notify_admins = patch.object(orders.notify, "admins", new_callable=AsyncMock)
        self.notify_user_mock = self.notify_user.start()
        self.notify_admins_mock = self.notify_admins.start()

    async def asyncTearDown(self):
        self.notify_user.stop()
        self.notify_admins.stop()
        await db.close()
        db._db = None
        self.temp.cleanup()

    async def test_accept_updates_stats_and_notifies_once(self):
        pending = await db.pending_orders()
        result = {"order_id": self.oid, "status": "accept", "delivery": ["REDACTED-CODE"]}
        await orders.reconcile_check_results(pending, [result])
        # A repeated poll/result must not repeat state changes or final notifications.
        await orders.reconcile_check_results(pending, [result])

        stored = await db.get_order(self.oid)
        user = await db.get_user(self.uid)
        stats = await db.stats(0)
        self.assertEqual(stored["status"], "accept")
        self.assertEqual(user["spent"], 2)
        self.assertEqual(stats["orders"], 1)
        self.assertEqual(stats["sales"], 2)
        self.assertEqual(stats["profit"], 1)
        self.notify_user_mock.assert_awaited_once()
        self.notify_admins_mock.assert_awaited_once()

    async def test_reject_refunds_once_and_notifies_once(self):
        pending = await db.pending_orders()
        result = {"order_id": self.oid, "status": "reject", "reason": "not available"}
        await orders.reconcile_check_results(pending, [result])
        await orders.reconcile_check_results(pending, [result])

        stored = await db.get_order(self.oid)
        user = await db.get_user(self.uid)
        d = await db.connect()
        refunds = await d.all("SELECT * FROM ledger WHERE ref=? AND reason='refund'", (self.oid,))
        stats = await db.stats(0)
        self.assertEqual(stored["status"], "reject")
        self.assertEqual(user["balance"], 10)
        self.assertEqual(len(refunds), 1)
        self.assertEqual(stats["orders"], 0)
        self.assertEqual(stats["sales"], 0)
        self.assertEqual(stats["profit"], 0)
        self.notify_user_mock.assert_awaited_once()
        self.notify_admins_mock.assert_awaited_once()

    async def test_concurrent_reject_results_refund_and_notify_once(self):
        order = await db.get_order(self.oid)
        await asyncio.gather(
            orders.finish(order, "reject", note="rejected"),
            orders.finish(order, "reject", note="rejected"),
        )
        user = await db.get_user(self.uid)
        d = await db.connect()
        refunds = await d.all("SELECT * FROM ledger WHERE ref=? AND reason='refund'", (self.oid,))
        self.assertEqual(user["balance"], 10)
        self.assertEqual(len(refunds), 1)
        self.notify_user_mock.assert_awaited_once()
        self.notify_admins_mock.assert_awaited_once()

    async def test_wait_unknown_status_and_unmatched_id_stay_pending(self):
        pending = await db.pending_orders()
        await orders.reconcile_check_results(pending, [
            {"order_id": self.oid, "status": "wait"},
            {"order_id": self.oid, "status": "completed"},  # undocumented/ambiguous
            {"order_id": str(uuid.uuid4()), "status": "accept"},
        ])
        stored = await db.get_order(self.oid)
        user = await db.get_user(self.uid)
        self.assertEqual(stored["status"], "wait")
        self.assertEqual(user["balance"], 8)
        self.assertEqual((await db.stats(0))["orders"], 0)
        self.notify_user_mock.assert_not_awaited()
        self.notify_admins_mock.assert_not_awaited()

    async def test_uuid_format_is_canonicalized_but_unrelated_ids_are_ignored(self):
        pending = await db.pending_orders()
        await orders.reconcile_check_results(pending, [
            {"order_id": self.oid.upper(), "status": "accept"},
        ])
        self.assertEqual((await db.get_order(self.oid))["status"], "accept")

    async def test_admin_orders_command_includes_pending_order_details(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123456789),
            answer=AsyncMock(),
        )
        await bot.admin_orders(message)
        message.answer.assert_awaited_once()
        text = message.answer.await_args.args[0]
        self.assertIn("#1001", text)
        self.assertIn("UC 60", text)
        self.assertIn("2.00$", text)
        self.assertIn(str(self.uid), text)
        self.assertIn("قيد التنفيذ", text)
        self.assertIn("آخر تحديث", text)

    async def test_invalid_admin_username_is_ignored_instead_of_crashing_config(self):
        previous = os.environ.get("ADMIN_IDS")
        try:
            os.environ["ADMIN_IDS"] = "@not-a-numeric-id"
            self.assertEqual(config._admin_ids(), set())
        finally:
            if previous is None:
                os.environ.pop("ADMIN_IDS", None)
            else:
                os.environ["ADMIN_IDS"] = previous

    async def test_check_uses_documented_envelope_and_logs_no_secret_or_delivery(self):
        secret = "test-api-token-must-not-be-logged"
        body = {"status": "OK", "data": [{
            "order_id": self.oid,
            "status": "accept",
            "delivery": ["SENSITIVE-DELIVERY-CODE"],
            "data": {"player_id": "PRIVATE-PLAYER-ID"},
        }]}
        requested = []

        async def handler(request):
            requested.append(request)
            return httpx.Response(200, json=body)

        client = MaxStoreClient("https://maxstore.example", secret)
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            base_url="https://maxstore.example",
            headers={"api-token": secret, "Accept": "application/json"},
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertLogs("supplier", level="INFO") as captured:
                rows = await client.check([self.oid])
            self.assertEqual(rows[0]["order_id"], self.oid)
            self.assertEqual(rows[0]["status"], "accept")
            self.assertEqual(requested[0].url.path, "/api/v2/check")
            self.assertEqual(requested[0].url.params["orders"], self.oid)
            logged = "\n".join(captured.output)
            self.assertIn(self.oid, logged)
            self.assertIn("accept", logged)
            self.assertNotIn(secret, logged)
            self.assertNotIn("SENSITIVE-DELIVERY-CODE", logged)
            self.assertNotIn("PRIVATE-PLAYER-ID", logged)
        finally:
            await client.http.aclose()

    async def test_malformed_check_payload_is_retryable_not_terminal(self):
        async def handler(request):
            return httpx.Response(200, json={"status": "OK", "data": "unexpected"})

        client = MaxStoreClient("https://maxstore.example", "test-only")
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            base_url="https://maxstore.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(SupplierError) as caught:
                await client.check([self.oid])
            self.assertTrue(caught.exception.retryable)
        finally:
            await client.http.aclose()


if __name__ == "__main__":
    unittest.main()
