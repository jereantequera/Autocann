"""The age of an ESP32 payload is measured from its post, never from the read."""

import json

import pytest

from autocann.control.reading_age import parse_esp32_sample


def _payload(posted, temperature=24.0, humidity=60.0):
    return json.dumps({"timestamp": posted, "temperature": temperature, "humidity": humidity})


def test_the_age_is_the_time_since_the_post():
    sample = parse_esp32_sample(_payload(1000), now=1050.5)
    assert sample.age == 50
    assert sample.data_age == pytest.approx(50.5)
    assert (sample.temperature, sample.humidity) == (24.0, 60.0)


def test_a_post_from_the_future_does_not_make_a_negative_age():
    assert parse_esp32_sample(_payload(1010), now=1000.0).data_age == 0.0


def test_a_payload_without_readings_still_reports_its_age():
    sample = parse_esp32_sample(json.dumps({"timestamp": 1000}), now=1030.0)
    assert (sample.temperature, sample.humidity, sample.age) == (None, None, 30)


def test_bad_json_raises_for_the_caller_to_handle():
    with pytest.raises(ValueError):
        parse_esp32_sample(b"{not json", now=1000.0)
