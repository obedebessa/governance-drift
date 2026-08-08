#!/usr/bin/env python3
"""Contract tests for the controlled localhost evidence gateway and relay."""

from __future__ import annotations

import unittest

from evidence_gateway import (
    AuditLog,
    ControlledReceiverClock,
    EvidenceGateway,
    RelayProfile,
    STREAM_DEPENDENCIES,
    make_envelope,
    start_transport,
    tcp_request,
)


SUBJECT = "cluster-a/payments/payments#uid-test"
STREAMS = tuple(sorted({item for values in STREAM_DEPENDENCIES.values() for item in values}))


def record(stream: str, sequence: int, captured: float) -> dict:
    return make_envelope(
        stream=stream,
        subject=SUBJECT,
        sequence=sequence,
        captured_at=captured,
        delivered_at=None,
        payload={"consistent": True, "stream": stream, "version": sequence},
    )


class TransportFixture:
    def __init__(self, profile: RelayProfile) -> None:
        self.gateway = EvidenceGateway(expected_subject=SUBJECT, max_age_seconds=2.0)
        self.receiver_clock = ControlledReceiverClock()
        self.audit = AuditLog()
        self.audit, self.gateway_server, self.relay = start_transport(
            gateway=self.gateway,
            profile=profile,
            audit=self.audit,
            delivery_clock=self.receiver_clock,
        )

    def send(self, document: dict, observed_delivery_at: float) -> dict:
        self.receiver_clock.set(observed_delivery_at)
        return tcp_request(self.relay.host, self.relay.port, document)

    def seed(self) -> None:
        for stream in STREAMS:
            result = self.send(record(stream, 1, 0.0), 0.05)
            if result["status"] != "accepted":
                raise AssertionError(result)

    def close(self) -> None:
        self.relay.close()
        self.gateway_server.close()


class EvidenceGatewayTests(unittest.TestCase):
    def test_real_tcp_baseline_is_consistent(self) -> None:
        fixture = TransportFixture(RelayProfile("baseline"))
        try:
            fixture.seed()
            result = fixture.gateway.evaluate(now=0.1)
            self.assertEqual(result["verdict"], "consistent")
            self.assertTrue(all(result["mask"].values()))
            gateway_event = next(
                item for item in fixture.audit.snapshot()
                if item["event_type"] == "gateway_ingest"
            )
            self.assertIsNone(gateway_event["wire_delivered_at"])
            self.assertEqual(gateway_event["delivered_at"], 0.05)
            self.assertEqual(gateway_event["delivery_source"], "gateway_receiver_clock")
        finally:
            fixture.close()

    def test_invalid_lineage_hash_masks_only_authorization(self) -> None:
        fixture = TransportFixture(RelayProfile("invalid-hash"))
        try:
            fixture.seed()
            invalid = record("lineage", 2, 1.0)
            invalid["payload_hash"] = "f" * 64
            response = fixture.send(invalid, 1.1)
            self.assertEqual(response["status"], "invalid_rejected")
            result = fixture.gateway.evaluate(now=1.2)
            self.assertEqual(result["components"]["authorization"], "undecidable")
            self.assertEqual(
                {key for key, value in result["components"].items() if value == "undecidable"},
                {"authorization"},
            )
        finally:
            fixture.close()

    def test_duplicate_delivery_is_idempotent(self) -> None:
        fixture = TransportFixture(
            RelayProfile("duplicate", duplicate={("authorization", 2)})
        )
        try:
            fixture.seed()
            response = fixture.send(record("authorization", 2, 1.0), 1.1)
            self.assertEqual(response["status"], "duplicate_delivered")
            statuses = [
                item["status"] for item in fixture.audit.snapshot()
                if item["event_type"] == "gateway_ingest"
                and item.get("stream") == "authorization"
                and item.get("sequence") == 2
            ]
            self.assertEqual(statuses, ["accepted", "duplicate_ignored"])
            self.assertEqual(fixture.gateway.evaluate(now=1.2)["verdict"], "consistent")
        finally:
            fixture.close()

    def test_reordered_record_cannot_regress_sequence(self) -> None:
        fixture = TransportFixture(
            RelayProfile("reorder", hold_for_reorder={("policy", 2)})
        )
        try:
            fixture.seed()
            self.assertEqual(fixture.send(record("policy", 2, 1.0), 1.1)["status"], "record_held")
            response = fixture.send(record("policy", 3, 1.1), 1.2)
            self.assertEqual(response["status"], "reordered_delivery")
            result = fixture.gateway.evaluate(now=1.3)
            self.assertEqual(result["streams"]["policy"]["sequence"], 3)
            self.assertEqual(result["components"]["policy"], "consistent")
        finally:
            fixture.close()

    def test_stale_replay_does_not_refresh_freshness(self) -> None:
        fixture = TransportFixture(
            RelayProfile("stale", stale_replay={("inventory", 1)})
        )
        try:
            fixture.seed()
            for stream in STREAMS:
                if stream != "inventory":
                    fixture.send(record(stream, 2, 3.0), 3.05)
            replay = record("inventory", 1, 1.5)
            self.assertEqual(fixture.send(replay, 3.1)["status"], "duplicate_ignored")
            result = fixture.gateway.evaluate(now=3.2)
            self.assertEqual(result["components"]["environment"], "undecidable")
            self.assertEqual(result["streams"]["inventory"]["captured_at"], 0.0)
        finally:
            fixture.close()

    def test_tcp_outage_retry_is_not_application_record_drop(self) -> None:
        fixture = TransportFixture(
            RelayProfile(
                "outage",
                outage_then_retry={("authorization", 2)},
                retry_delay_ms=5,
            )
        )
        try:
            fixture.seed()
            response = fixture.send(record("authorization", 2, 1.0), 1.1)
            self.assertEqual(response["status"], "accepted")
            events = fixture.audit.snapshot()
            self.assertEqual(sum(item["event_type"] == "transport_failure" for item in events), 1)
            self.assertEqual(sum(item["event_type"] == "transport_retry" for item in events), 1)
            self.assertEqual(sum(item["event_type"] == "record_drop" for item in events), 0)
            self.assertEqual(fixture.gateway.evaluate(now=1.2)["verdict"], "consistent")
        finally:
            fixture.close()

    def test_application_record_drop_requires_new_record(self) -> None:
        fixture = TransportFixture(RelayProfile("drop", drop={("policy", 2)}))
        try:
            fixture.seed()
            for stream in STREAMS:
                if stream != "policy":
                    fixture.send(record(stream, 2, 3.0), 3.05)
            self.assertEqual(fixture.send(record("policy", 2, 3.0), 3.1)["status"], "record_dropped")
            self.assertEqual(
                fixture.gateway.evaluate(now=3.2)["components"]["policy"],
                "undecidable",
            )
            self.assertEqual(fixture.send(record("policy", 3, 3.3), 3.4)["status"], "accepted")
            self.assertEqual(fixture.gateway.evaluate(now=3.4)["verdict"], "consistent")
        finally:
            fixture.close()

    def test_malformed_payloads_are_rejected_without_partial_state_update(self) -> None:
        fixture = TransportFixture(RelayProfile("malformed-payloads"))
        try:
            fixture.seed()
            invalid_payloads = (
                {},
                {"consistent": "false"},
                ["not", "an", "object"],
            )
            for sequence, payload in enumerate(invalid_payloads, start=2):
                document = make_envelope(
                    stream="configuration",
                    subject=SUBJECT,
                    sequence=sequence,
                    captured_at=1.0,
                    delivered_at=None,
                    payload=payload,  # type: ignore[arg-type]
                )
                response = fixture.send(document, 1.1)
                self.assertEqual(response["status"], "invalid_rejected")

            result = fixture.gateway.evaluate(now=1.2)
            self.assertEqual(result["streams"]["configuration"]["sequence"], 1)
            self.assertEqual(result["components"]["configuration"], "undecidable")
            self.assertEqual(
                fixture.send(record("configuration", 5, 1.3), 1.4)["status"],
                "accepted",
            )
            self.assertEqual(
                fixture.gateway.evaluate(now=1.4)["components"]["configuration"],
                "consistent",
            )
        finally:
            fixture.close()

    def test_nonfinite_timestamps_and_observation_time_fail_closed(self) -> None:
        gateway = EvidenceGateway(expected_subject=SUBJECT)
        valid = make_envelope(
            stream="configuration",
            subject=SUBJECT,
            sequence=1,
            captured_at=0.0,
            delivered_at=0.05,
            payload={"consistent": True},
        )
        self.assertEqual(gateway.ingest(valid)["status"], "accepted")
        invalid = make_envelope(
            stream="configuration",
            subject=SUBJECT,
            sequence=2,
            captured_at=float("nan"),
            delivered_at=1.0,
            payload={"consistent": True},
        )
        self.assertEqual(gateway.ingest(invalid)["status"], "invalid_rejected")
        self.assertEqual(
            gateway.evaluate(now=1.1)["components"]["configuration"],
            "undecidable",
        )
        nonfinite_observation = gateway.evaluate(now=float("nan"))
        self.assertEqual(nonfinite_observation["verdict"], "undecidable")
        self.assertTrue(
            all(value == "undecidable" for value in nonfinite_observation["components"].values())
        )


if __name__ == "__main__":
    unittest.main()
