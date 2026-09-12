"""Shared, timestamped per-tool admission for the existing EuCAP owner.

This module has no process-launch or scientific code. Counts are permitted
wrapper slots, never evidence of actual native solver concurrency.
"""
from datetime import datetime
import math
import threading

TOOLS = ('cadence', 'calibre', 'emx')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def stamp(value):
    at = datetime.fromisoformat(value)
    require(at.utcoffset() is not None, 'AWARE_RESOURCE_TIME_REQUIRED')
    return at.timestamp()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ResourceHistory:
    """Count only consecutive, nonoverlapping actual measurement intervals."""

    def __init__(self, binding, *, required_checks=5, maximum_age=90):
        require(required_checks == 5 and maximum_age == 90, 'UNCHANGED_HEALTH_HISTORY_REQUIRED')
        self.binding = binding
        self.required_checks = required_checks
        self.maximum_age = maximum_age
        self.last = None
        self.streak = 0
        self.seen = set()

    def observe(self, sample, identity):
        require(sample['binding'] == self.binding, 'RESOURCE_OWNER_RELEASE_BINDING_CHANGED')
        require(set(identity) == {'path', 'sha256', 'bytes'} and len(identity['sha256']) == 64,
                'PERSISTED_RESOURCE_IDENTITY_REQUIRED')
        require(identity['sha256'] not in self.seen, 'REPEATED_RESOURCE_SNAPSHOT')
        start, end = stamp(sample['started_utc']), stamp(sample['utc'])
        require(end - start >= 60, 'INDEPENDENT_60_SECOND_SAMPLE_REQUIRED')
        if self.last:
            require(start >= stamp(self.last['utc']), 'OVERLAPPING_OR_BACKWARDS_RESOURCE_SAMPLE')
        checks = sample['checks']
        require(set(checks) == {'cpu', 'memory', 'swap', 'oom', 'iowait', 'sample', 'storage', 'isolation'},
                'COMPLETE_HARD_RESOURCE_CHECKS_REQUIRED')
        require(all(type(v) is bool for v in checks.values()), 'BOOLEAN_RESOURCE_CHECKS_REQUIRED')
        for tool in TOOLS:
            count = sample['free_license_slots'][tool]
            require(type(count) is int and count >= 0, 'ACTUAL_LICENSE_CAPACITY_REQUIRED')
        self.streak = self.streak + 1 if all(checks.values()) else 0
        self.last = sample
        self.seen.add(identity['sha256'])

    def limits(self, at, running, pending, policy):
        require(set(running) == set(pending) == set(TOOLS), 'ALL_TOOL_RESERVATIONS_REQUIRED')
        require(all(type(x) is int and x >= 0 for x in [*running.values(), *pending.values()]),
                'INVALID_INFLIGHT_ACCOUNTING')
        require(policy['requested_emx'] == 48 and policy['cpu_per_solver'] == 2,
                'EXACT_FIXED48_NATIVE2_POLICY_REQUIRED')
        require(policy['normalized_load1_max'] == policy['normalized_load5_max'] == 1.10 and
                policy['minimum_available_memory_fraction'] == 0.20,
                'APPROVED_RESOURCE_THRESHOLDS_CHANGED')
        zero = {tool: 0 for tool in TOOLS}
        if self.last is None:
            return dict(additional=zero, reason='NO_RESOURCE_SAMPLE', healthy_check_streak=0)
        age = stamp(at) - stamp(self.last['utc'])
        require(age >= 0, 'FUTURE_RESOURCE_SAMPLE')
        if age > self.maximum_age or self.streak < self.required_checks:
            return dict(additional=zero, reason='STALE_OR_INSUFFICIENT_INDEPENDENT_CHECKS',
                        healthy_check_streak=self.streak)
        sample = self.last
        if not all(sample['checks'].values()):
            return dict(additional=zero, reason='HARD_RESOURCE_WAIT', healthy_check_streak=self.streak)
        for key in ('idle_cpu_equivalents', 'available_memory_bytes', 'total_memory_bytes', 'free_disk_bytes'):
            require(finite(sample[key]) and sample[key] >= 0, 'INVALID_MEASURED_CAPACITY: ' + key)
        require(sample['normalized_load1'] <= 1.1 and sample['normalized_load5'] <= 1.1 and
                sample['available_memory_bytes'] >= .2 * sample['total_memory_bytes'],
                'RESOURCE_CHECK_BOOLEAN_DISAGREES_WITH_MEASUREMENT')
        for tool in TOOLS:
            require(type(policy['memory_reservation_bytes'][tool]) is int and policy['memory_reservation_bytes'][tool] > 0,
                    'POSITIVE_EXPLICIT_MEMORY_RESERVATION_REQUIRED')
            require(type(policy['cpu_reservation'][tool]) is int and policy['cpu_reservation'][tool] > 0,
                    'POSITIVE_EXPLICIT_CPU_RESERVATION_REQUIRED')
        # Snapshot availability already reflects running jobs. Only reservations
        # not yet represented by live tools are subtracted from that observation.
        cpu = max(0, sample['idle_cpu_equivalents'] - policy['system_cpu_reserve'] -
                  sum(pending[t] * policy['cpu_reservation'][t] for t in TOOLS))
        memory = max(0, sample['available_memory_bytes'] - .2 * sample['total_memory_bytes'] -
                     sum(pending[t] * policy['memory_reservation_bytes'][t] for t in TOOLS))
        limits = {}
        for tool in TOOLS:
            limits[tool] = max(0, min(
                policy['tool_executor_capacity'][tool] - running[tool] - pending[tool],
                sample['free_license_slots'][tool] - pending[tool],
                math.floor(cpu / policy['cpu_reservation'][tool]),
                math.floor(memory / policy['memory_reservation_bytes'][tool])))
        return dict(additional=limits, reason='PARTIAL_OR_FULL_CAPACITY_AVAILABLE',
                    healthy_check_streak=self.streak, observed_utc=sample['utc'])


class InflightReservations:
    """Shared projected disk headroom, with one claim per original candidate."""

    def __init__(self, ceiling_bytes):
        require(type(ceiling_bytes) is int and ceiling_bytes > 0, 'POSITIVE_ORIGINAL_DISK_BUDGET_REQUIRED')
        self.ceiling = ceiling_bytes
        self.claims = {}
        self.lock = threading.RLock()

    def claim(self, candidate_id, candidate_allocated, total_allocated, projected_peak):
        require(all(type(x) is int and x >= 0 for x in (candidate_allocated, total_allocated, projected_peak)),
                'ACTUAL_ALLOCATION_AND_EXPLICIT_ESTIMATE_REQUIRED')
        with self.lock:
            require(candidate_id not in self.claims, 'DUPLICATE_INFLIGHT_CLAIM')
            remaining = max(0, projected_peak - candidate_allocated)
            other = sum(v['remaining'] for v in self.claims.values())
            if total_allocated + other + remaining > self.ceiling:
                return False
            self.claims[candidate_id] = dict(peak=projected_peak, remaining=remaining)
            return True

    def refresh(self, allocations):
        with self.lock:
            require(set(allocations) == set(self.claims), 'COMPLETE_INFLIGHT_ALLOCATION_SNAPSHOT_REQUIRED')
            for key, value in allocations.items():
                require(type(value) is int and value >= 0, 'INVALID_ALLOCATED_BYTES')
                self.claims[key]['remaining'] = max(0, self.claims[key]['peak'] - value)

    def release(self, candidate_id):
        with self.lock:
            require(candidate_id in self.claims, 'UNKNOWN_INFLIGHT_CLAIM')
            del self.claims[candidate_id]
