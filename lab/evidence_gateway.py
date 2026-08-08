#!/usr/bin/env python3
"""Controlled localhost TCP evidence transport and fail-closed gateway.

This module intentionally models two different fault layers:

* transport failure: a TCP delivery attempt fails and the unchanged record may
  be retried by the sender/relay; and
* application-record loss: the relay deliberately discards a complete record,
  which TCP retransmission cannot recover.

It is a deterministic laboratory component, not a production transport,
authentication system, or reliability claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


STREAM_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "configuration": ("configuration",),
    "policy": ("policy", "basis"),
    "authorization": ("authorization", "lineage", "basis"),
    "intent": ("intent", "authorization", "basis"),
    "environment": ("inventory", "basis"),
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def make_envelope(
    *,
    stream: str,
    subject: str,
    sequence: int,
    captured_at: float,
    delivered_at: float | None = None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create the canonical envelope used on both TCP hops."""
    return {
        "stream": stream,
        "subject": subject,
        "sequence": int(sequence),
        "captured_at": float(captured_at),
        "delivered_at": None if delivered_at is None else float(delivered_at),
        "payload": payload,
        "payload_hash": payload_hash(payload),
    }


class ControlledReceiverClock:
    """Thread-safe logical receiver clock used only by the localhost lab."""

    def __init__(self, initial: float = 0.0) -> None:
        self._value = float(initial)
        self._lock = threading.RLock()

    def set(self, value: float) -> None:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("receiver time must be finite")
        with self._lock:
            self._value = numeric

    def __call__(self) -> float:
        with self._lock:
            return self._value


@dataclass
class StreamState:
    sequence: int = -1
    captured_at: float = float("-inf")
    delivered_at: float = float("-inf")
    payload_hash: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    invalid: bool = False
    reason: str = "missing evidence"


class EvidenceGateway:
    """Validate envelopes and expose component-local three-valued verdicts."""

    def __init__(
        self,
        *,
        expected_subject: str,
        max_age_seconds: float = 2.0,
        max_transport_delay_seconds: float = 2.0,
        dependencies: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        if not isinstance(expected_subject, str) or not expected_subject:
            raise ValueError("expected_subject must be a non-empty string")
        if not math.isfinite(float(max_age_seconds)) or float(max_age_seconds) <= 0.0:
            raise ValueError("max_age_seconds must be finite and positive")
        if (
            not math.isfinite(float(max_transport_delay_seconds))
            or float(max_transport_delay_seconds) < 0.0
        ):
            raise ValueError("max_transport_delay_seconds must be finite and non-negative")
        self.expected_subject = expected_subject
        self.max_age_seconds = float(max_age_seconds)
        self.max_transport_delay_seconds = float(max_transport_delay_seconds)
        self.dependencies = dict(dependencies or STREAM_DEPENDENCIES)
        streams = {stream for values in self.dependencies.values() for stream in values}
        self._states = {stream: StreamState() for stream in streams}
        self._lock = threading.RLock()

    def _reject(
        self,
        *,
        stream: str | None,
        sequence: int | None,
        reason: str,
        poison: bool,
    ) -> dict[str, Any]:
        if poison and stream in self._states and sequence is not None:
            state = self._states[stream]
            if sequence > state.sequence:
                state.invalid = True
                state.reason = reason
        return {
            "status": "invalid_rejected",
            "stream": stream,
            "sequence": sequence,
            "reason": reason,
        }

    def ingest(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Validate fully, then atomically ingest without regressing state."""
        with self._lock:
            if not isinstance(envelope, dict):
                return self._reject(
                    stream=None,
                    sequence=None,
                    reason="envelope must be a JSON object",
                    poison=False,
                )

            required = {
                "stream", "subject", "sequence", "captured_at", "delivered_at",
                "payload", "payload_hash",
            }
            missing = sorted(required.difference(envelope))
            if missing:
                return self._reject(
                    stream=None,
                    sequence=None,
                    reason=f"missing envelope fields: {','.join(missing)}",
                    poison=False,
                )

            stream_value = envelope["stream"]
            subject_value = envelope["subject"]
            sequence_value = envelope["sequence"]
            stream = stream_value if isinstance(stream_value, str) else None
            sequence = (
                sequence_value
                if isinstance(sequence_value, int) and not isinstance(sequence_value, bool)
                else None
            )
            if stream is None or not stream:
                return self._reject(
                    stream=None,
                    sequence=sequence,
                    reason="stream must be a non-empty string",
                    poison=False,
                )
            if sequence is None or sequence < 0:
                return self._reject(
                    stream=stream,
                    sequence=None,
                    reason="sequence must be a non-negative integer",
                    poison=False,
                )

            if stream not in self._states:
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="unknown evidence stream",
                    poison=False,
                )
            state = self._states[stream]
            if not isinstance(subject_value, str) or subject_value != self.expected_subject:
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="subject mismatch",
                    poison=True,
                )

            captured_value = envelope["captured_at"]
            delivered_value = envelope["delivered_at"]
            numeric_types = (int, float)
            if (
                isinstance(captured_value, bool)
                or not isinstance(captured_value, numeric_types)
                or isinstance(delivered_value, bool)
                or not isinstance(delivered_value, numeric_types)
            ):
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="timestamps must be numeric",
                    poison=True,
                )
            captured_at = float(captured_value)
            delivered_at = float(delivered_value)
            if not math.isfinite(captured_at) or not math.isfinite(delivered_at):
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="timestamps must be finite",
                    poison=True,
                )
            if captured_at > delivered_at:
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="timestamps inverted",
                    poison=True,
                )
            if delivered_at - captured_at > self.max_transport_delay_seconds:
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="transport delay exceeded",
                    poison=True,
                )

            payload = envelope["payload"]
            supplied_hash = envelope["payload_hash"]
            if not isinstance(payload, dict):
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="payload must be a JSON object",
                    poison=True,
                )
            if type(payload.get("consistent")) is not bool:
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="payload.consistent must be boolean",
                    poison=True,
                )
            if not isinstance(supplied_hash, str):
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="payload_hash must be a string",
                    poison=True,
                )
            try:
                canonical_payload = canonical_json(payload)
                computed_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
                payload_copy = json.loads(canonical_payload)
            except (TypeError, ValueError, OverflowError):
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="payload must contain finite JSON values",
                    poison=True,
                )
            if supplied_hash != computed_hash:
                return self._reject(
                    stream=stream,
                    sequence=sequence,
                    reason="payload hash mismatch",
                    poison=True,
                )

            if sequence < state.sequence:
                return {
                    "status": "reordered_ignored",
                    "stream": stream,
                    "sequence": sequence,
                    "reason": "sequence older than accepted state",
                }
            if sequence == state.sequence:
                if supplied_hash == state.payload_hash:
                    return {
                        "status": "duplicate_ignored",
                        "stream": stream,
                        "sequence": sequence,
                        "reason": "idempotent duplicate",
                    }
                state.invalid = True
                state.reason = "conflicting duplicate sequence"
                return {
                    "status": "invalid_rejected",
                    "stream": stream,
                    "sequence": sequence,
                    "reason": state.reason,
                }

            state.sequence = sequence
            state.captured_at = captured_at
            state.delivered_at = delivered_at
            state.payload_hash = supplied_hash
            state.payload = payload_copy
            state.invalid = False
            state.reason = "accepted"
            return {
                "status": "accepted",
                "stream": stream,
                "sequence": sequence,
                "reason": "fresh monotone evidence",
            }

    def evaluate(self, *, now: float) -> dict[str, Any]:
        """Compute component verdicts and a component-local observability mask."""
        with self._lock:
            try:
                observation_now = float(now)
            except (TypeError, ValueError, OverflowError):
                observation_now = float("nan")
            invalid_observation_time = not math.isfinite(observation_now)
            stream_status: dict[str, dict[str, Any]] = {}
            for stream, state in self._states.items():
                if invalid_observation_time:
                    valid, reason = False, "observation time must be finite"
                elif state.sequence < 0:
                    valid, reason = False, "missing evidence"
                elif state.invalid:
                    valid, reason = False, state.reason
                elif observation_now < state.delivered_at:
                    valid, reason = False, "evidence not yet delivered"
                elif observation_now - state.captured_at > self.max_age_seconds:
                    valid, reason = False, "stale evidence"
                else:
                    valid, reason = True, "valid"
                stream_status[stream] = {
                    "valid": valid,
                    "reason": reason,
                    "sequence": state.sequence,
                    "captured_at": state.captured_at,
                    "delivered_at": state.delivered_at,
                    "payload_hash": state.payload_hash,
                }

            components: dict[str, str] = {}
            mask: dict[str, bool] = {}
            reasons: dict[str, list[str]] = {}
            for component, required in self.dependencies.items():
                invalid = [
                    f"{stream}: {stream_status[stream]['reason']}"
                    for stream in required
                    if not stream_status[stream]["valid"]
                ]
                if invalid:
                    components[component] = "undecidable"
                    mask[component] = False
                    reasons[component] = invalid
                    continue
                inconsistent = [
                    stream
                    for stream in required
                    if self._states[stream].payload.get("consistent") is False
                ]
                components[component] = "inconsistent" if inconsistent else "consistent"
                mask[component] = True
                reasons[component] = [f"{stream}: inconsistent" for stream in inconsistent]

            total = (
                "undecidable"
                if any(value == "undecidable" for value in components.values())
                else "inconsistent"
                if any(value == "inconsistent" for value in components.values())
                else "consistent"
            )
            return {
                "subject": self.expected_subject,
                "now": observation_now,
                "verdict": total,
                "components": components,
                "mask": mask,
                "reasons": reasons,
                "streams": stream_status,
            }


class AuditLog:
    """Thread-safe ordinal event log for the controlled transport."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._condition = threading.Condition()
        self._started = time.perf_counter()

    def add(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._condition:
            item = {
                "ordinal": len(self._events) + 1,
                "event_type": event_type,
                "wall_elapsed_ms": round((time.perf_counter() - self._started) * 1000.0, 3),
                **fields,
            }
            self._events.append(item)
            self._condition.notify_all()
            return item

    def snapshot(self) -> list[dict[str, Any]]:
        with self._condition:
            return [dict(item) for item in self._events]

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for item in self._events:
                    if predicate(item):
                        return dict(item)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("transport event did not arrive")
                self._condition.wait(remaining)


class _ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class GatewayTCPServer:
    """TCP JSON-lines endpoint backed by :class:`EvidenceGateway`."""

    def __init__(
        self,
        gateway: EvidenceGateway,
        audit: AuditLog,
        delivery_clock: Callable[[], float] | None = None,
    ) -> None:
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                raw = self.rfile.readline()
                envelope: dict[str, Any] = {}
                wire_delivered_at: Any = None
                try:
                    decoded = json.loads(raw)
                    if not isinstance(decoded, dict):
                        raise TypeError("wire envelope must be a JSON object")
                    envelope = dict(decoded)
                    wire_delivered_at = envelope.get("delivered_at")
                    envelope["delivered_at"] = float(owner.delivery_clock())
                    result = owner.gateway.ingest(envelope)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    result = {"status": "invalid_rejected", "reason": f"wire JSON: {exc}"}
                except Exception as exc:  # fail closed at the transport boundary
                    result = {
                        "status": "invalid_rejected",
                        "reason": f"gateway validation boundary: {type(exc).__name__}",
                    }
                owner.audit.add(
                    "gateway_ingest",
                    stream=envelope.get("stream"),
                    subject=envelope.get("subject"),
                    sequence=envelope.get("sequence"),
                    captured_at=envelope.get("captured_at"),
                    delivered_at=envelope.get("delivered_at"),
                    wire_delivered_at=wire_delivered_at,
                    delivery_source="gateway_receiver_clock",
                    payload_hash=envelope.get("payload_hash"),
                    status=result.get("status"),
                    reason=result.get("reason"),
                    fault_layer="gateway_validation",
                )
                self.wfile.write((canonical_json(result) + "\n").encode("utf-8"))

        self.gateway = gateway
        self.audit = audit
        self.delivery_clock = delivery_clock or time.monotonic
        self._server = _ReusableThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.host, self.port = self._server.server_address
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def tcp_request(host: str, port: int, document: dict[str, Any], *, timeout: float = 2.0) -> dict[str, Any]:
    wire = (canonical_json(document) + "\n").encode("utf-8")
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.sendall(wire)
        reader = connection.makefile("rb")
        raw = reader.readline()
    if not raw:
        raise ConnectionError("TCP peer closed without an acknowledgement")
    return json.loads(raw)


@dataclass
class RelayProfile:
    """Deterministic application-record actions keyed by ``(stream, sequence)``."""

    name: str
    delay_ms: dict[tuple[str, int], int] = field(default_factory=dict)
    drop: set[tuple[str, int]] = field(default_factory=set)
    duplicate: set[tuple[str, int]] = field(default_factory=set)
    hold_for_reorder: set[tuple[str, int]] = field(default_factory=set)
    outage_then_retry: set[tuple[str, int]] = field(default_factory=set)
    stale_replay: set[tuple[str, int]] = field(default_factory=set)
    retry_delay_ms: int = 40


class FaultRelayTCPServer:
    """Real localhost TCP relay with deterministic record-level faults."""

    def __init__(
        self,
        *,
        downstream_host: str,
        downstream_port: int,
        profile: RelayProfile,
        audit: AuditLog,
    ) -> None:
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                raw = self.rfile.readline()
                try:
                    envelope = json.loads(raw)
                    if not isinstance(envelope, dict):
                        raise TypeError("wire envelope must be a JSON object")
                    result = owner._process(envelope)
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, ValueError) as exc:
                    owner.audit.add(
                        "relay_rejected",
                        reason=f"relay boundary: {type(exc).__name__}",
                        fault_layer="tcp_transport",
                    )
                    result = {
                        "status": "invalid_rejected",
                        "reason": f"relay boundary: {type(exc).__name__}",
                    }
                self.wfile.write((canonical_json(result) + "\n").encode("utf-8"))

        self.downstream_host = downstream_host
        self.downstream_port = int(downstream_port)
        self.profile = profile
        self.audit = audit
        self._held: dict[str, list[dict[str, Any]]] = {}
        self._receive_counts: dict[tuple[str, int], int] = {}
        self._lock = threading.RLock()
        self._server = _ReusableThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.host, self.port = self._server.server_address
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    @staticmethod
    def _key(envelope: dict[str, Any]) -> tuple[str, int]:
        return str(envelope["stream"]), int(envelope["sequence"])

    def _deliver(self, envelope: dict[str, Any]) -> dict[str, Any]:
        key = self._key(envelope)
        self.audit.add(
            "transport_attempt",
            stream=key[0],
            sequence=key[1],
            fault_layer="tcp_transport",
        )
        result = tcp_request(self.downstream_host, self.downstream_port, envelope)
        self.audit.add(
            "transport_delivered",
            stream=key[0],
            sequence=key[1],
            status=result.get("status"),
            fault_layer="tcp_transport",
        )
        return result

    def _closed_port(self) -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        return port

    def _process(self, envelope: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            key = self._key(envelope)
            stream, sequence = key
            self._receive_counts[key] = self._receive_counts.get(key, 0) + 1
            self.audit.add(
                "relay_received",
                stream=stream,
                subject=envelope.get("subject"),
                sequence=sequence,
                captured_at=envelope.get("captured_at"),
                delivered_at=envelope.get("delivered_at"),
                payload_hash=envelope.get("payload_hash"),
                fault_layer="tcp_transport",
            )
            if key in self.profile.stale_replay and self._receive_counts[key] > 1:
                self.audit.add(
                    "stale_replay",
                    stream=stream,
                    sequence=sequence,
                    fault_layer="application_record",
                )
            if key in self.profile.drop:
                self.audit.add(
                    "record_drop",
                    stream=stream,
                    sequence=sequence,
                    fault_layer="application_record",
                )
                return {"status": "record_dropped", "stream": stream, "sequence": sequence}
            if key in self.profile.hold_for_reorder:
                self._held.setdefault(stream, []).append(dict(envelope))
                self.audit.add(
                    "record_held_for_reorder",
                    stream=stream,
                    sequence=sequence,
                    fault_layer="application_record",
                )
                return {"status": "record_held", "stream": stream, "sequence": sequence}

            delay_ms = self.profile.delay_ms.get(key, 0)
            if delay_ms:
                self.audit.add(
                    "record_delay",
                    stream=stream,
                    sequence=sequence,
                    delay_ms=delay_ms,
                    fault_layer="application_record",
                )
                time.sleep(delay_ms / 1000.0)

            if key in self.profile.outage_then_retry:
                bad_port = self._closed_port()
                self.audit.add(
                    "transport_attempt",
                    stream=stream,
                    sequence=sequence,
                    destination="closed_localhost_port",
                    fault_layer="tcp_transport",
                )
                try:
                    tcp_request(self.downstream_host, bad_port, envelope, timeout=0.25)
                except OSError as exc:
                    self.audit.add(
                        "transport_failure",
                        stream=stream,
                        sequence=sequence,
                        error=type(exc).__name__,
                        fault_layer="tcp_transport",
                    )
                time.sleep(self.profile.retry_delay_ms / 1000.0)
                self.audit.add(
                    "transport_retry",
                    stream=stream,
                    sequence=sequence,
                    same_payload_hash=envelope.get("payload_hash"),
                    fault_layer="tcp_transport",
                )

            result = self._deliver(envelope)
            if key in self.profile.duplicate:
                self.audit.add(
                    "duplicate_emit",
                    stream=stream,
                    sequence=sequence,
                    fault_layer="application_record",
                )
                duplicate_result = self._deliver(envelope)
                result = {"status": "duplicate_delivered", "results": [result, duplicate_result]}

            held = self._held.pop(stream, [])
            held_results = []
            for old in held:
                old_key = self._key(old)
                self.audit.add(
                    "reordered_emit",
                    stream=old_key[0],
                    sequence=old_key[1],
                    after_sequence=sequence,
                    fault_layer="application_record",
                )
                held_results.append(self._deliver(old))
            if held_results:
                result = {"status": "reordered_delivery", "current": result, "held": held_results}
            return result


def start_transport(
    *,
    gateway: EvidenceGateway,
    profile: RelayProfile,
    audit: AuditLog | None = None,
    delivery_clock: Callable[[], float] | None = None,
) -> tuple[AuditLog, GatewayTCPServer, FaultRelayTCPServer]:
    """Start the real two-hop localhost transport for a campaign profile."""
    event_log = audit or AuditLog()
    gateway_server = GatewayTCPServer(gateway, event_log, delivery_clock=delivery_clock)
    gateway_server.start()
    relay_server = FaultRelayTCPServer(
        downstream_host=gateway_server.host,
        downstream_port=gateway_server.port,
        profile=profile,
        audit=event_log,
    )
    relay_server.start()
    return event_log, gateway_server, relay_server
