"""
Configuration, read from the environment and split by area.

Every name the old single module defined is re-exported here, so
`from autocann.config import X` works wherever X now lives. Areas: `redis`,
`relays` (relay modes, output pins, remote node), `sensors` (indoor roles and
max ages), `tuning` (control timing from the environment) and `modes` (pause and
control modes).
"""

from autocann.config.modes import (
    CONTROL_MODES,
    MODE_ACTIVE,
    MODE_MONITOR,
    MODE_PAUSED,
    TEST_MODE_KEY,
    TEST_MODE_OUTPUTS_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
)
from autocann.config.redis import (
    RedisConfig,
    redis_config_from_env,
)
from autocann.config.relays import (
    DESIRED_OUTPUTS_KEY,
    DESIRED_OUTPUTS_MAX_AGE_SECONDS,
    RELAY_MODE_GPIO,
    RELAY_MODE_REMOTE,
    RELAY_MODES,
    GpioPins,
    RelayNode,
    RelayNodePins,
    _pin_from_env,
    _reject_shared_pins,
    gpio_pins_from_env,
    relay_mode_from_env,
    relay_node_from_env,
)
from autocann.config.sensors import (
    _SENSOR_ID_PATTERN,
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    LEGACY_INDOOR_KEY,
    OBSERVER,
    PRIMARY,
    WITNESS,
    IndoorSensors,
    indoor_sensor_key,
    indoor_sensors_from_env,
    is_valid_sensor_id,
)
from autocann.config.tuning import (
    ControlTuning,
    control_tuning_from_env,
)

__all__ = [
    "RedisConfig",
    "redis_config_from_env",
    "_pin_from_env",
    "_reject_shared_pins",
    "GpioPins",
    "gpio_pins_from_env",
    "RELAY_MODE_GPIO",
    "RELAY_MODE_REMOTE",
    "RELAY_MODES",
    "DESIRED_OUTPUTS_KEY",
    "DESIRED_OUTPUTS_MAX_AGE_SECONDS",
    "relay_mode_from_env",
    "RelayNodePins",
    "RelayNode",
    "relay_node_from_env",
    "ControlTuning",
    "control_tuning_from_env",
    "LEGACY_INDOOR_KEY",
    "INDOOR_SENSOR_MAX_AGE_SECONDS",
    "PRIMARY",
    "WITNESS",
    "OBSERVER",
    "_SENSOR_ID_PATTERN",
    "is_valid_sensor_id",
    "indoor_sensor_key",
    "IndoorSensors",
    "indoor_sensors_from_env",
    "TEST_MODE_KEY",
    "TEST_MODE_OUTPUTS_KEY",
    "MODE_ACTIVE",
    "MODE_MONITOR",
    "MODE_PAUSED",
    "CONTROL_MODES",
    "UNCONTROLLED_MODES",
    "TEST_MODE_SETTING",
]
