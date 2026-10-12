# SPDX-License-Identifier: Apache-2.0
"""Unwired, bounded MTP2 admission state machine; no native wire API is assumed.

Integration supplies prepare/retire callbacks using the EXISTING physical
ownership barrier and its MTP0 FIFO. They run outside our condition lock.
prepare must return only after exact ownership/readiness proof; retire must
confirm exact owned exit before releasing that same physical barrier. Failed
callbacks pause admission rather than inventing empty GPU state.

NativeEvent origin is an in-process capability from a trusted event adapter,
not a boolean or a request header. No stderr trace is treated as that adapter.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import math
import time

from common import DistributionError
from mtp2_shared_config import PUBLIC_ALIASES
from mtp2_shared_ingress import VerifiedIngress


@dataclass(frozen=True)
class RuntimeBinding:
    epoch: str
    event_origin: object
    physical_owner: object


@dataclass(frozen=True)
class NativeEvent:
    epoch: str
    request_id: str
    phase: str


@dataclass
class _Request:
    ingress: VerifiedIngress
    deadline: float
    granted: asyncio.Future
    stage: str = "pending"
    epoch: str | None = None
    acquired: bool = False
    cleanup_deadline: float | None = None


class Mtp2SharedCoordinator:
    ACTIVE_LIMIT = 2

    def __init__(self, *, prepare_runtime, retire_runtime, pending_limit=8,
                 replay_limit=4096, cleanup_seconds=60, ttl_seconds=1800,
                 clock=time.monotonic, wall_clock=time.time):
        if (type(pending_limit) is not int or not 1 <= pending_limit <= 64
                or type(replay_limit) is not int or not self.ACTIVE_LIMIT + pending_limit <= replay_limit <= 65536
                or type(cleanup_seconds) is not int or not 1 <= cleanup_seconds <= 60
                or type(ttl_seconds) is not int or ttl_seconds != 1800):
            raise DistributionError("MTP2 coordinator bounds differ")
        self._prepare, self._retire = prepare_runtime, retire_runtime
        self._pending_limit, self._replay_limit = pending_limit, replay_limit
        self._cleanup_seconds, self._ttl = cleanup_seconds, ttl_seconds
        self._clock, self._wall = clock, wall_clock
        self._condition = asyncio.Condition()
        self._pending = deque()
        self._active = {}
        self._seen = {}
        self._binding = None
        self._ready_task = None
        self._paused = None
        self._mutating = False
        self._idle_since = None

    def _busy_locked(self):
        return bool(self._active or self._pending or self._ready_task and not self._ready_task.done())

    def _pump_locked(self):
        if self._paused or self._mutating:
            return
        while self._pending and len(self._active) < self.ACTIVE_LIMIT:
            row = self._pending.popleft()
            if row.deadline <= self._clock():
                row.granted.set_exception(DistributionError("MTP2 pending request deadline elapsed"))
                continue
            row.stage = "reserved"
            self._active[row.ingress.request_id] = row
            row.granted.set_result(row.ingress.request_id)
        if self._busy_locked():
            self._idle_since = None
        elif self._binding is not None and self._idle_since is None:
            self._idle_since = self._clock()

    def _row_locked(self, request_id):
        row = self._active.get(request_id)
        if row is None:
            raise DistributionError("MTP2 request is not an accepted active request")
        return row

    async def reserve(self, ingress, *, deadline):
        """Reserve a slot or bounded FIFO position; a queued cancellation never loads."""
        if (not isinstance(ingress, VerifiedIngress) or ingress.alias not in PUBLIC_ALIASES
                or not isinstance(deadline, (int, float)) or isinstance(deadline, bool)
                or not math.isfinite(deadline) or deadline <= self._clock()):
            raise DistributionError("MTP2 admission identity or deadline differs")
        async with self._condition:
            if self._paused or self._mutating:
                raise DistributionError("MTP2 admission is paused")
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > self._wall()}
            if ingress.expires_at <= self._wall() or ingress.request_id in self._seen or ingress.request_id in self._active:
                raise DistributionError("MTP2 admission capability is expired or replayed")
            if len(self._seen) >= self._replay_limit:
                raise DistributionError("MTP2 admission replay table is full")
            if len(self._active) >= self.ACTIVE_LIMIT and len(self._pending) >= self._pending_limit:
                raise DistributionError("MTP2 pending FIFO is full")
            row = _Request(ingress, deadline, asyncio.get_running_loop().create_future())
            self._seen[ingress.request_id] = ingress.expires_at
            self._pending.append(row)
            self._pump_locked()
        try:
            return await asyncio.wait_for(asyncio.shield(row.granted), max(0, deadline - self._clock()))
        except BaseException:
            await self.cancel(ingress.request_id)
            if row.granted.done() and not row.granted.cancelled():
                row.granted.exception()
            raise

    async def _start(self):
        try:
            binding = await self._prepare()
            if (not isinstance(binding, RuntimeBinding) or not isinstance(binding.epoch, str)
                    or not 1 <= len(binding.epoch) <= 128 or binding.event_origin is None
                    or binding.physical_owner is None):
                raise DistributionError("MTP2 ready proof lacks a trusted runtime binding")
            async with self._condition:
                self._binding = binding
                if not self._active and not self._pending:
                    self._idle_since = self._clock()
                self._condition.notify_all()
            return binding
        except BaseException:
            async with self._condition:
                self._paused = "runtime_prepare_failed"
                self._condition.notify_all()
            raise

    async def ensure_ready(self, request_id):
        async with self._condition:
            row = self._row_locked(request_id)
            if self._paused or self._mutating or row.stage != "reserved":
                raise DistributionError("MTP2 request cannot load or enter this runtime")
            if self._binding is None and self._ready_task is None:
                self._ready_task = asyncio.create_task(self._start())
                # A disconnected last waiter must not create an unobserved error.
                self._ready_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            ready_task, binding, deadline = self._ready_task, self._binding, row.deadline
        try:
            if binding is None:
                binding = await asyncio.wait_for(asyncio.shield(ready_task), max(0, deadline - self._clock()))
        except BaseException:
            await self.cancel(request_id)
            raise
        async with self._condition:
            row = self._row_locked(request_id)
            if self._paused or row.stage != "reserved" or row.deadline <= self._clock():
                raise DistributionError("MTP2 request expired or was cancelled before forwarding")
            row.epoch = binding.epoch
            return binding

    async def mark_forwarded(self, request_id):
        """Call immediately before native I/O; ambiguity then retains busy ownership."""
        async with self._condition:
            row = self._row_locked(request_id)
            if self._paused or self._mutating or row.stage != "reserved" or row.epoch is None or row.deadline <= self._clock():
                raise DistributionError("MTP2 request cannot be forwarded")
            row.stage = "forwarded"
            return (row.epoch, request_id)

    async def cancel(self, request_id):
        """Return True when caller must send an ID-specific native cancel.

        Reader close, deadline, schema rejection and transport failure all use
        this path. A forwarded request occupies its slot until native release.
        """
        async with self._condition:
            pending = next((row for row in self._pending if row.ingress.request_id == request_id), None)
            if pending is not None:
                self._pending.remove(pending)
                pending.granted.cancel()
                self._pump_locked()
                return False
            row = self._active.get(request_id)
            if row is None:
                return False
            if row.stage == "reserved":
                del self._active[request_id]
                self._pump_locked()
                self._condition.notify_all()
                return False
            if row.stage != "cleanup":
                row.stage = "cleanup"
                row.cleanup_deadline = self._clock() + self._cleanup_seconds
            return True

    async def native_event(self, origin, event):
        """Accept only authenticated adapter events correlated to epoch AND ID."""
        async with self._condition:
            binding = self._binding
            if (not isinstance(event, NativeEvent) or binding is None or origin is not binding.event_origin
                    or event.epoch != binding.epoch or event.phase not in {"acquired", "progress", "released"}):
                self._paused = "native_event_identity_failed"
                raise DistributionError("MTP2 native event origin, epoch or phase differs")
            row = self._active.get(event.request_id)
            if row is None or row.epoch != event.epoch or row.stage == "reserved":
                self._paused = "native_event_request_failed"
                raise DistributionError("MTP2 native event has no matching forwarded request")
            if event.phase == "acquired":
                if row.acquired:
                    self._paused = "native_event_duplicate_acquire"
                    raise DistributionError("MTP2 duplicate native acquisition")
                row.acquired = True
                if row.stage != "cleanup":
                    row.stage = "active"
            elif not row.acquired:
                self._paused = "native_event_missing_acquire"
                raise DistributionError("MTP2 native progress or release lacks acquisition")
            elif event.phase == "released":
                del self._active[event.request_id]
                self._pump_locked()
                self._condition.notify_all()

    async def tick(self):
        """Expire queue/requests, returning only request IDs requiring cancel I/O."""
        cancel_ids = []
        async with self._condition:
            now = self._clock()
            for row in list(self._pending):
                if row.deadline <= now:
                    self._pending.remove(row)
                    row.granted.set_exception(DistributionError("MTP2 pending request deadline elapsed"))
            for request_id, row in list(self._active.items()):
                if row.stage == "reserved" and row.deadline <= now:
                    del self._active[request_id]
                elif row.stage == "cleanup" and row.cleanup_deadline <= now:
                    self._paused = "native_cleanup_deadline"
                elif row.stage != "cleanup" and row.deadline <= now:
                    row.stage = "cleanup"
                    row.cleanup_deadline = now + self._cleanup_seconds
                    cancel_ids.append(request_id)
            self._pump_locked()
        return tuple(cancel_ids)

    async def event_channel_lost(self):
        async with self._condition:
            self._paused = "native_event_channel_lost"

    async def apply_verified_record(self, actor, handle, record, *, owned_runtime):
        """Apply an authenticated actor-issued row after exact owned rechecks.

        Integration must recheck its immutable owned runtime before and after
        the private exchange. The same in-process physical-owner object bound
        at readiness is required here. No dict/HTTP status/EOF is a receipt.
        """
        from mtp2_shared_transport import NativeTransport
        if type(actor) is not NativeTransport:
            raise DistributionError("MTP2 record lacks its authenticated transport actor")
        record = actor.require_verified_record(handle, record)
        async with self._condition:
            binding = self._binding
            if (binding is None or binding.event_origin is not actor
                    or binding.physical_owner is not owned_runtime
                    or binding.epoch != record.epoch or handle.epoch != record.epoch):
                self._paused = "native_record_ownership_failed"
                raise DistributionError("MTP2 authenticated row has a different runtime owner or epoch")
            row = self._active.get(record.nonce)
            if (row is None or row.epoch != record.epoch or row.stage == "reserved"
                    or row.ingress.alias != record.alias or row.ingress.profile_id != record.profile
                    or row.ingress.tenant != handle.tenant):
                self._paused = "native_record_request_failed"
                raise DistributionError("MTP2 authenticated row has no matching forwarded request")
            if record.acquired:
                row.acquired = True
                if row.stage != "cleanup":
                    row.stage = "active"
            if record.no_state_proven:
                if row.acquired or record.acquired != 0 or record.released != 0:
                    self._paused = "native_record_zero_state_failed"
                    raise DistributionError("MTP2 zero-state receipt contradicts acquisition")
            elif record.release_proven:
                if not row.acquired or record.acquired != 1 or record.released != 1:
                    self._paused = "native_record_release_failed"
                    raise DistributionError("MTP2 release receipt contradicts acquisition")
            else:
                return False
            del self._active[record.nonce]
            self._pump_locked()
            self._condition.notify_all()
            return True

    async def unload(self, *, idle_ttl=False):
        """Atomic whole-runtime busy/TTL barrier, with owned stop outside the lock."""
        async with self._condition:
            if self._paused or self._mutating or self._busy_locked():
                raise DistributionError("MTP2 shared runtime is busy or requires recovery")
            if self._binding is None:
                return False
            if idle_ttl and (self._idle_since is None or self._clock() - self._idle_since < self._ttl):
                return False
            self._mutating = True
            binding = self._binding
        try:
            # Contract: callback proves owned exit AND releases the original
            # physical lease. It raises if either operation is unconfirmed.
            await self._retire(binding)
        except BaseException:
            async with self._condition:
                self._paused = "runtime_retire_failed"
                self._mutating = False
            raise
        async with self._condition:
            self._binding, self._ready_task, self._idle_since = None, None, None
            self._mutating = False
            self._condition.notify_all()
        return True

    async def status(self):
        """Bounded operational metadata; never prompts, keys, tenant or cache keys."""
        async with self._condition:
            return {"active": len(self._active), "pending": len(self._pending),
                    "busy": self._busy_locked(), "paused": self._paused,
                    "mutating": self._mutating, "epoch": self._binding.epoch if self._binding else None,
                    "requests": tuple((row.epoch, key, row.ingress.alias, row.stage)
                                      for key, row in self._active.items())}
