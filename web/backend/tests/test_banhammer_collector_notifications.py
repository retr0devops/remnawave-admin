"""Unit tests for Banhammer -> Bedolaga collector notification helpers."""

from types import SimpleNamespace

from web.backend.api.v2.collector import (
    _build_warning_message,
    _resolve_network_notification_type,
)


def test_resolve_network_notification_type_mobile_actual():
    mismatches = [
        {
            "node_uuid": "11111111-1111-1111-1111-111111111111",
            "expected_connection_types": ["residential", "isp"],
            "actual_connection_types": ["mobile"],
        }
    ]

    notification_type, network_hint = _resolve_network_notification_type(mismatches)

    assert notification_type == "network_mobile"
    assert network_hint == "mobile"


def test_resolve_network_notification_type_wifi_when_mobile_expected():
    mismatches = [
        {
            "node_uuid": "11111111-1111-1111-1111-111111111111",
            "expected_connection_types": ["mobile", "mobile_isp"],
            "actual_connection_types": ["residential"],
        }
    ]

    notification_type, network_hint = _resolve_network_notification_type(mismatches)

    assert notification_type == "network_wifi"
    assert network_hint == "residential"


def test_build_warning_message_includes_compact_mismatch_details():
    result = SimpleNamespace(
        message="Banhammer warning",
        mismatches=[
            {
                "node_uuid": "11111111-1111-1111-1111-111111111111",
                "expected_connection_types": ["mobile"],
                "actual_connection_types": ["residential"],
            }
        ],
    )

    message = _build_warning_message(result)

    assert message.startswith("Banhammer warning")
    assert "node=11111111" in message
    assert "expected=mobile" in message
    assert "actual=residential" in message
