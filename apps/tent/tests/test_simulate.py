from __future__ import annotations

import subprocess
import sys

import pytest

from autocann.cli import simulate as sim
from autocann.config import RELAY_MODE_REMOTE
from autocann.paths import DATA_DIR

# ---------------------------------------------------------------------------
# Tent model
# ---------------------------------------------------------------------------


def test_humidifier_raises_rh():
    model = sim.TentModel(temp=24.0, rh=60.0)
    model.step(60, {"humidity_up": True})
    assert model.rh > 60.0


def test_dehumidifier_lowers_rh():
    model = sim.TentModel(temp=24.0, rh=70.0)
    model.step(60, {"humidity_down": True})
    assert model.rh < 70.0


def test_extractor_pulls_rh_toward_outdoor_faster_than_leaking():
    leaking = sim.TentModel(temp=24.0, rh=80.0)
    extracting = sim.TentModel(temp=24.0, rh=80.0)
    leaking.step(600, {})
    extracting.step(600, {"ventilation": True})
    assert extracting.rh < leaking.rh
    assert extracting.rh > sim.OUTDOOR_RH


def test_everything_off_drifts_toward_outdoor_air():
    model = sim.TentModel(temp=30.0, rh=80.0)
    previous = model.rh
    for _ in range(600):  # five model hours, in 30 s steps
        model.step(30, {})
        assert model.rh <= previous
        previous = model.rh
    assert abs(model.rh - sim.OUTDOOR_RH) < 1.0
    assert abs(model.temp - sim.OUTDOOR_TEMP_C) < 1.0


def test_model_never_leaves_0_to_100():
    high = sim.TentModel(temp=24.0, rh=99.9)
    high.step(1000, {"humidity_up": True})
    assert high.rh == 100.0
    low = sim.TentModel(temp=24.0, rh=0.1)
    low.step(1000, {"humidity_down": True})
    assert low.rh == 0.0


def test_zero_step_changes_nothing():
    model = sim.TentModel(temp=24.0, rh=60.0)
    assert model.step(0, {"humidity_up": True}) == (24.0, 60.0)
    assert model.seconds == 0.0


# ---------------------------------------------------------------------------
# Scenario timelines
# ---------------------------------------------------------------------------


def test_every_scenario_is_known_and_described():
    assert set(sim.SCENARIOS) == {
        "normal", "humid", "dry", "stale", "spike", "drift", "offline-relay",
    }
    for spec in sim.SCENARIOS.values():
        assert spec.description
    with pytest.raises(ValueError):
        sim.scenario_spec("sauna")


def test_stale_goes_silent_after_its_cutoff():
    model = sim.initial_model("stale")
    before = sim.STALE_AFTER_SECONDS - 5
    assert sim.scenario_readings("stale", before, model) is not None
    assert sim.scenario_readings("stale", sim.STALE_AFTER_SECONDS, model) is None
    assert sim.scenario_readings("stale", 500.0, model) is None


@pytest.mark.parametrize("name", [n for n in sim.SCENARIOS if n != "stale"])
def test_other_scenarios_keep_posting(name):
    model = sim.initial_model(name)
    readings = sim.scenario_readings(name, 1000.0, model)
    assert readings is not None
    assert readings[0].sensor_id == "a"


@pytest.mark.parametrize("interval", [1.0, 5.0, 7.0, 13.0])
def test_spike_emits_exactly_one_outlier(interval):
    model = sim.initial_model("spike")
    outliers = []
    prev_t = None
    t = 0.0
    while t <= 300.0:
        for reading in sim.scenario_readings("spike", t, model, prev_t) or ():
            if reading.sensor_id == "a" and reading.temperature > model.temp + 10:
                outliers.append(reading)
        prev_t = t
        t += interval
    assert len(outliers) == 1
    assert outliers[0].temperature == pytest.approx(model.temp + sim.SPIKE_TEMP_DELTA_C)


def test_no_spike_outside_the_spike_scenario():
    model = sim.initial_model("normal")
    for t in range(0, 300, 5):
        for reading in sim.scenario_readings("normal", float(t), model, float(t) - 5) or ():
            assert reading.temperature < model.temp + 10


def test_drift_witness_diverges_with_model_time():
    model = sim.initial_model("drift")
    model.seconds = 0.0
    early = sim.scenario_readings("drift", 10.0, model)
    assert early[1].temperature - early[0].temperature == pytest.approx(0.0)

    model.seconds = 1800.0
    half = sim.scenario_readings("drift", 10.0, model)
    model.seconds = 3600.0
    full = sim.scenario_readings("drift", 10.0, model)
    half_gap = half[1].temperature - half[0].temperature
    full_gap = full[1].temperature - full[0].temperature
    assert 0.0 < half_gap < full_gap
    assert full_gap == pytest.approx(sim.DRIFT_RATE_C_PER_HOUR)


def test_witness_drift_is_capped():
    assert sim.witness_offset_c(10 * 3600.0) == sim.DRIFT_MAX_C


def test_observer_sits_warmer_and_drier_than_primary_outside_drift():
    model = sim.initial_model("normal")
    primary, observer = sim.scenario_readings("normal", 10.0, model)
    assert observer.temperature == pytest.approx(primary.temperature + sim.OBSERVER_TEMP_OFFSET_C)
    assert observer.humidity < primary.humidity


def test_readings_are_exact_without_an_rng_and_noisy_with_one():
    import random

    model = sim.initial_model("normal")
    exact = sim.scenario_readings("normal", 10.0, model)
    noisy = sim.scenario_readings("normal", 10.0, model, rng=random.Random(7))
    assert exact[0].temperature == model.temp
    assert abs(noisy[0].temperature - model.temp) <= sim.SENSOR_NOISE
    assert abs(noisy[0].humidity - model.rh) <= sim.SENSOR_NOISE


def test_sensor_roles_match_the_scenario():
    normal = sim.indoor_sensor_env("normal")
    assert normal == {"AUTOCANN_INDOOR_PRIMARY": "a", "AUTOCANN_INDOOR_OBSERVERS": "b"}
    drift = sim.indoor_sensor_env("drift")
    assert drift == {"AUTOCANN_INDOOR_PRIMARY": "a", "AUTOCANN_INDOOR_WITNESS": "b"}


def test_only_the_offline_relay_scenario_skips_polling():
    assert sim.relay_polls("normal")
    assert not sim.relay_polls("offline-relay")


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def test_apply_desired_follows_the_answer():
    outputs = sim.apply_desired({"outputs": {"humidity_up": True, "ventilation": True}, "failsafe": False})
    assert outputs == {"humidity_up": True, "humidity_down": False, "ventilation": True}


def test_apply_desired_fails_safe_to_everything_off():
    payload = {"outputs": {"humidity_up": True}, "failsafe": True, "reason": "stale"}
    assert sim.apply_desired(payload) == {name: False for name in sim.OUTPUT_NAMES}


def test_build_sensor_payload_matches_the_firmware_body():
    payload = sim.build_sensor_payload(sim.Reading("a", 24.123, 65.456))
    assert payload == {"temperature": 24.12, "humidity": 65.46, "sensor_id": "a"}


def test_status_line_reports_outputs_and_vpd():
    line = sim.status_line(70.0, sim.TentModel(temp=24.0, rh=60.0), {name: False for name in sim.OUTPUT_NAMES})
    assert line.startswith("[01:10]")
    assert "on: none" in line
    assert "kPa" in line


# ---------------------------------------------------------------------------
# Sandbox guards
# ---------------------------------------------------------------------------


def test_refuses_the_real_tents_redis_db():
    with pytest.raises(sim.SandboxError):
        sim.check_redis_db(0)
    assert sim.check_redis_db(15) == 15


def test_refuses_redis_dbs_that_do_not_exist():
    with pytest.raises(sim.SandboxError):
        sim.check_redis_db(16)


@pytest.mark.parametrize("port", [5000, 5055])
def test_refuses_the_real_tents_ports(port):
    with pytest.raises(sim.SandboxError):
        sim.check_port(port)


def test_free_port_is_never_a_reserved_one():
    for _ in range(5):
        port = sim.free_port()
        assert port not in sim.RESERVED_PORTS
        assert 1024 <= port <= 65535


def test_refuses_an_existing_database_file(tmp_path):
    existing = tmp_path / "real.db"
    existing.write_text("")
    with pytest.raises(sim.SandboxError):
        sim.check_db_path(existing)


def test_accepts_a_fresh_database_path(tmp_path):
    assert sim.check_db_path(tmp_path / "fresh.db") == (tmp_path / "fresh.db").resolve()


def test_refuses_the_real_data_directory():
    with pytest.raises(sim.SandboxError):
        sim.check_db_path(DATA_DIR / "autocann.db")


def test_main_refuses_redis_db_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        sim.main(["--redis-db", "0"])
    assert exc.value.code == 2
    assert "DB 0" in capsys.readouterr().err


def test_main_refuses_non_positive_speed(capsys):
    with pytest.raises(SystemExit) as exc:
        sim.main(["--speed", "0"])
    assert exc.value.code == 2


def test_scenario_choices_come_from_the_table():
    parser = sim.build_parser()
    action = next(a for a in parser._actions if a.dest == "scenario")
    assert list(action.choices) == list(sim.SCENARIOS)


# ---------------------------------------------------------------------------
# Child environment and processes
# ---------------------------------------------------------------------------


def test_child_env_forces_remote_relays_and_a_non_zero_redis_db(tmp_path):
    base = {"AUTOCANN_RELAY_MODE": "gpio", "AUTOCANN_REDIS_DB": "0", "PORT": "5000"}
    env = sim.build_child_env(
        redis_db=15, db_path=tmp_path / "sim.db", port=39123, scenario="normal", base=base,
    )
    assert env["AUTOCANN_RELAY_MODE"] == RELAY_MODE_REMOTE
    assert int(env["AUTOCANN_REDIS_DB"]) != 0
    assert env["AUTOCANN_REDIS_DB"] == "15"
    assert env["AUTOCANN_DB"] == str(tmp_path / "sim.db")
    assert env["AUTOCANN_WEB_PORT"] == "39123"
    assert env["AUTOCANN_WEB_HOST"] == "127.0.0.1"
    assert "PORT" not in env


def test_child_env_refuses_redis_db_zero_and_reserved_ports(tmp_path):
    with pytest.raises(sim.SandboxError):
        sim.build_child_env(redis_db=0, db_path=tmp_path / "s.db", port=39123, scenario="normal", base={})
    with pytest.raises(sim.SandboxError):
        sim.build_child_env(redis_db=15, db_path=tmp_path / "s.db", port=5055, scenario="normal", base={})


def test_child_env_drops_inherited_indoor_roles(tmp_path):
    base = {"AUTOCANN_INDOOR_WITNESS": "zz", "AUTOCANN_INDOOR_OBSERVERS": "yy"}
    env = sim.build_child_env(
        redis_db=15, db_path=tmp_path / "s.db", port=39123, scenario="normal", base=base,
    )
    assert env["AUTOCANN_INDOOR_PRIMARY"] == "a"
    assert env["AUTOCANN_INDOOR_OBSERVERS"] == "b"
    assert "AUTOCANN_INDOOR_WITNESS" not in env


def test_loop_bootstrap_blocks_gpio_and_dht_imports():
    prefix = sim.LOOP_BOOTSTRAP.split("from autocann")[0]
    for module in ("gpiozero", "board", "adafruit_dht"):
        result = subprocess.run(
            [sys.executable, "-c", prefix + f"\nimport {module}\n"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0, module


def test_stop_children_signals_only_the_handles_it_is_given():
    ours = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    kept = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        sim.stop_children([("ours", ours)], grace_seconds=10)
        assert ours.poll() is not None
        assert kept.poll() is None
    finally:
        sim.stop_children([("kept", kept)], grace_seconds=10)
    assert kept.poll() is not None
