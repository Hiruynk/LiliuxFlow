# SPDX-License-Identifier: Apache-2.0
"""CPU state-machine tests with synthetic trusted events, never a native API."""
import asyncio
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
from mtp2_shared_config import PUBLIC_ALIASES, PROFILE_IDS
from mtp2_shared_coordinator import Mtp2SharedCoordinator, NativeEvent, RuntimeBinding
from mtp2_shared_ingress import VerifiedIngress


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now, self.wall, self.ids = 100.0, 1000.0, 0
        self.loads, self.unloads = 0, 0
        self.origin, self.physical_owner = object(), object()
        self.binding = RuntimeBinding("synthetic-epoch-1", self.origin, self.physical_owner)
        self.gate = asyncio.Event()
        self.gate.set()
        async def prepare():
            self.assertFalse(self.coordinator._condition.locked())
            self.loads += 1
            await self.gate.wait()
            return self.binding
        async def retire(binding):
            self.assertFalse(self.coordinator._condition.locked())
            self.assertIs(binding.physical_owner, self.physical_owner)
            self.unloads += 1
        self.coordinator = Mtp2SharedCoordinator(prepare_runtime=prepare, retire_runtime=retire,
                                               pending_limit=2, cleanup_seconds=5,
                                               clock=lambda: self.now, wall_clock=lambda: self.wall)

    def ingress(self, index=0, *, request_id=None, expiry=1060):
        self.ids += 1
        return VerifiedIngress(request_id or f"{self.ids:032x}", "0" * 64,
                               PUBLIC_ALIASES[index], PROFILE_IDS[index], expiry)

    async def reserved(self, index=0, *, deadline=None):
        row = self.ingress(index)
        return await self.coordinator.reserve(row, deadline=self.now + 100 if deadline is None else deadline)

    async def forwarded(self, index=0, *, deadline=None):
        request_id = await self.reserved(index, deadline=deadline)
        await self.coordinator.ensure_ready(request_id)
        await self.coordinator.mark_forwarded(request_id)
        return request_id

    async def event(self, request_id, phase, *, epoch=None, origin=None):
        await self.coordinator.native_event(self.origin if origin is None else origin,
                                           NativeEvent(epoch or self.binding.epoch, request_id, phase))

    async def acquired(self, index=0, *, deadline=None):
        request_id = await self.forwarded(index, deadline=deadline)
        await self.event(request_id, "acquired")
        return request_id

    async def test_two_same_alias_requests_have_independent_ids_and_release_order(self):
        first, second = await self.acquired(), await self.acquired()
        self.assertNotEqual(first, second)
        self.assertEqual((await self.coordinator.status())["active"], 2)
        await self.event(second, "released")
        status = await self.coordinator.status()
        self.assertEqual(status["active"], 1)
        self.assertEqual(status["requests"][0][1], first)
        await self.event(first, "released")
        self.assertFalse((await self.coordinator.status())["busy"])

    async def test_different_aliases_share_one_cold_start_future(self):
        self.gate.clear()
        first, second = await self.reserved(0), await self.reserved(2)
        tasks = [asyncio.create_task(self.coordinator.ensure_ready(key)) for key in (first, second)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.loads, 1)
        # This read completes while prepare is waiting, proving no lock over I/O.
        self.assertEqual((await asyncio.wait_for(self.coordinator.status(), 0.5))["active"], 2)
        self.gate.set()
        results = await asyncio.gather(*tasks)
        self.assertIs(results[0], results[1])

    async def test_pending_fifo_has_active_limit_two_and_one_slot_per_release(self):
        first, second = await self.acquired(), await self.acquired()
        rows = [self.ingress(1), self.ingress(2)]
        queued = [asyncio.create_task(self.coordinator.reserve(row, deadline=self.now + 100)) for row in rows]
        await asyncio.sleep(0)
        self.assertEqual((await self.coordinator.status())["pending"], 2)
        await self.event(second, "released")
        self.assertEqual(await queued[0], rows[0].request_id)
        self.assertFalse(queued[1].done())
        await self.event(first, "released")
        self.assertEqual(await queued[1], rows[1].request_id)

    async def test_queue_full_rejects_without_loading(self):
        await self.reserved()
        await self.reserved()
        queued = [asyncio.create_task(self.coordinator.reserve(self.ingress(), deadline=self.now + 100)) for _ in range(2)]
        await asyncio.sleep(0)
        with self.assertRaises(DistributionError):
            await self.reserved()
        for task in queued:
            task.cancel()
        await asyncio.gather(*queued, return_exceptions=True)
        self.assertEqual(self.loads, 0)

    async def test_cancel_waiting_removes_only_waiter_and_never_loads(self):
        await self.reserved()
        await self.reserved()
        queued = asyncio.create_task(self.coordinator.reserve(self.ingress(), deadline=self.now + 100))
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertEqual((await self.coordinator.status())["pending"], 0)
        self.assertEqual(self.loads, 0)

    async def test_cancel_reserved_does_not_load(self):
        request_id = await self.reserved()
        self.assertFalse(await self.coordinator.cancel(request_id))
        self.assertEqual(self.loads, 0)
        with self.assertRaises(DistributionError):
            await self.coordinator.ensure_ready(request_id)

    async def test_peer_cancel_keeps_peer_and_shared_runtime_busy_until_native_release(self):
        first, second = await self.acquired(), await self.acquired(2)
        self.assertTrue(await self.coordinator.cancel(first))
        self.assertEqual((await self.coordinator.status())["active"], 2)
        with self.assertRaises(DistributionError):
            await self.coordinator.unload()
        await self.event(second, "progress")
        await self.event(first, "released")
        self.assertEqual((await self.coordinator.status())["requests"][0][1], second)
        self.assertEqual(self.unloads, 0)

    async def test_cancel_before_native_acquire_race_still_needs_release(self):
        request_id = await self.forwarded()
        self.assertTrue(await self.coordinator.cancel(request_id))
        await self.event(request_id, "acquired")
        self.assertEqual((await self.coordinator.status())["requests"][0][-1], "cleanup")
        await self.event(request_id, "released")
        self.assertFalse((await self.coordinator.status())["busy"])

    async def test_reader_close_is_cancel_not_release(self):
        request_id = await self.acquired()
        await self.coordinator.cancel(request_id)
        self.assertTrue((await self.coordinator.status())["busy"])
        self.now += 6
        await self.coordinator.tick()
        status = await self.coordinator.status()
        self.assertEqual(status["active"], 1)
        self.assertEqual(status["paused"], "native_cleanup_deadline")

    async def test_request_deadline_cancels_only_its_id_and_retains_cleanup(self):
        first = await self.acquired(deadline=self.now + 1)
        second = await self.acquired(1)
        self.now += 2
        self.assertEqual(await self.coordinator.tick(), (first,))
        self.assertEqual((await self.coordinator.status())["active"], 2)
        await self.event(second, "progress")
        await self.event(first, "released")

    async def test_queued_deadline_expires_without_native_acquisition(self):
        await self.reserved()
        await self.reserved()
        queued = asyncio.create_task(self.coordinator.reserve(self.ingress(), deadline=self.now + 1))
        await asyncio.sleep(0)
        self.now += 2
        self.assertEqual(await self.coordinator.tick(), ())
        with self.assertRaises(DistributionError):
            await queued
        self.assertEqual(self.loads, 0)

    async def test_untrusted_origin_or_stale_epoch_pauses_without_clearing_active(self):
        request_id = await self.acquired()
        with self.assertRaises(DistributionError):
            await self.event(request_id, "released", origin=object())
        self.assertEqual((await self.coordinator.status())["active"], 1)
        with self.assertRaises(DistributionError):
            await self.event(request_id, "released", epoch="old-epoch")
        with self.assertRaises(DistributionError):
            await self.reserved()

    async def test_missing_acquisition_and_wrong_request_cannot_clear_active(self):
        request_id = await self.forwarded()
        for key in (request_id, "unknown"):
            with self.assertRaises(DistributionError):
                await self.event(key, "released")
        self.assertEqual((await self.coordinator.status())["active"], 1)

    async def test_duplicate_native_acquire_is_not_counted_twice(self):
        request_id = await self.acquired()
        with self.assertRaises(DistributionError):
            await self.event(request_id, "acquired")
        self.assertEqual((await self.coordinator.status())["active"], 1)

    async def test_channel_loss_retains_busy_and_refuses_admission_and_unload(self):
        request_id = await self.acquired()
        await self.coordinator.event_channel_lost()
        status = await self.coordinator.status()
        self.assertEqual((status["active"], status["paused"]), (1, "native_event_channel_lost"))
        with self.assertRaises(DistributionError):
            await self.reserved()
        with self.assertRaises(DistributionError):
            await self.coordinator.unload()
        await self.event(request_id, "released")
        self.assertEqual((await self.coordinator.status())["paused"], "native_event_channel_lost")

    async def test_capability_replay_rejected_after_finish(self):
        ingress = self.ingress()
        request_id = await self.coordinator.reserve(ingress, deadline=self.now + 100)
        await self.coordinator.cancel(request_id)
        with self.assertRaises(DistributionError):
            await self.coordinator.reserve(ingress, deadline=self.now + 100)

    async def test_expired_capability_and_invalid_deadline_rejected(self):
        with self.assertRaises(DistributionError):
            await self.coordinator.reserve(self.ingress(expiry=1000), deadline=self.now + 1)
        for deadline in (float("nan"), float("inf"), True, self.now):
            with self.assertRaises(DistributionError):
                await self.coordinator.reserve(self.ingress(), deadline=deadline)

    async def test_TTL_measures_whole_group_idle_and_cleanup_blocks_unload(self):
        first, second = await self.acquired(), await self.acquired(2)
        await self.event(first, "released")
        self.now += 1801
        with self.assertRaises(DistributionError):
            await self.coordinator.unload(idle_ttl=True)
        await self.event(second, "released")
        self.assertFalse(await self.coordinator.unload(idle_ttl=True))
        self.now += 1800
        self.assertTrue(await self.coordinator.unload(idle_ttl=True))
        self.assertEqual(self.unloads, 1)

    async def test_unload_is_atomic_against_new_admission_with_no_lock_over_IO(self):
        request_id = await self.acquired()
        await self.event(request_id, "released")
        entered, finish = asyncio.Event(), asyncio.Event()
        async def retire(binding):
            self.assertFalse(self.coordinator._condition.locked())
            entered.set()
            await finish.wait()
        self.coordinator._retire = retire
        task = asyncio.create_task(self.coordinator.unload())
        await entered.wait()
        self.assertTrue((await self.coordinator.status())["mutating"])
        with self.assertRaises(DistributionError):
            await self.reserved()
        finish.set()
        self.assertTrue(await task)

    async def test_failed_retire_keeps_binding_and_pauses(self):
        request_id = await self.acquired()
        await self.event(request_id, "released")
        async def retire(binding):
            raise DistributionError("synthetic owned exit not proven")
        self.coordinator._retire = retire
        with self.assertRaises(DistributionError):
            await self.coordinator.unload()
        status = await self.coordinator.status()
        self.assertEqual(status["epoch"], self.binding.epoch)
        self.assertEqual(status["paused"], "runtime_retire_failed")

    async def test_failed_start_pauses_instead_of_faking_readiness(self):
        async def prepare():
            raise DistributionError("synthetic readiness failure")
        self.coordinator._prepare = prepare
        request_id = await self.reserved()
        with self.assertRaises(DistributionError):
            await self.coordinator.ensure_ready(request_id)
        status = await self.coordinator.status()
        self.assertIsNone(status["epoch"])
        self.assertEqual(status["paused"], "runtime_prepare_failed")

    async def test_cancel_one_cold_start_waiter_preserves_peer_start(self):
        self.gate.clear()
        first, second = await self.reserved(), await self.reserved(2)
        tasks = [asyncio.create_task(self.coordinator.ensure_ready(key)) for key in (first, second)]
        await asyncio.sleep(0)
        tasks[0].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tasks[0]
        self.gate.set()
        self.assertIs(await tasks[1], self.binding)
        self.assertEqual(self.loads, 1)
        self.assertEqual((await self.coordinator.status())["active"], 1)

    async def test_last_waiter_cancel_during_started_load_stays_busy_until_ready_settles(self):
        self.gate.clear()
        request_id = await self.reserved()
        task = asyncio.create_task(self.coordinator.ensure_ready(request_id))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.loads, 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        status = await self.coordinator.status()
        self.assertEqual(status["active"], 0)
        self.assertTrue(status["busy"])
        with self.assertRaises(DistributionError):
            await self.coordinator.unload()
        self.gate.set()
        await self.coordinator._ready_task
        self.assertFalse((await self.coordinator.status())["busy"])
        self.assertTrue(await self.coordinator.unload())

    async def test_readiness_deadline_does_not_cancel_shared_atomic_load(self):
        self.gate.clear()
        request_id = await self.reserved(deadline=self.now + 0.01)
        with self.assertRaises(asyncio.TimeoutError):
            await self.coordinator.ensure_ready(request_id)
        self.assertTrue((await self.coordinator.status())["busy"])
        self.gate.set()
        await self.coordinator._ready_task
        self.assertTrue(await self.coordinator.unload())

    async def test_existing_physical_barrier_preserves_MTP0_FIFO_and_single_resident(self):
        # Synthetic existing FIFO. The coordinator gets no second lease/lock.
        physical = asyncio.Lock()
        await physical.acquire()  # MTP0 currently owns the sole resident.
        order = []
        async def mtp0_waiter():
            async with physical:
                order.append("MTP0-next")
        mtp0 = asyncio.create_task(mtp0_waiter())
        await asyncio.sleep(0)
        async def prepare():
            await physical.acquire()
            order.append("MTP2")
            return self.binding
        async def retire(binding):
            self.assertTrue(physical.locked())
            physical.release()
        self.coordinator._prepare, self.coordinator._retire = prepare, retire
        request_id = await self.reserved()
        mtp2 = asyncio.create_task(self.coordinator.ensure_ready(request_id))
        await asyncio.sleep(0)
        self.assertEqual(order, [])
        physical.release()  # exact owned MTP0 exit is represented by the fixture.
        await mtp0
        await mtp2
        self.assertEqual(order, ["MTP0-next", "MTP2"])
        self.assertTrue(physical.locked())
        await self.coordinator.cancel(request_id)
        self.assertTrue(await self.coordinator.unload())
        self.assertFalse(physical.locked())


if __name__ == "__main__":
    unittest.main()
