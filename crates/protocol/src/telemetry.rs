use serde::{Deserialize, Serialize};

use crate::PROTOCOL_VERSION;

/// Un mensaje de telemetría, tal como lo publica un equipo.
///
/// ```json
/// {"v":1,"seq":42,"readings":[{"sensor":"a","temperature_c":24.5,"humidity_pct":62.0}]}
/// ```
///
/// No trae hora: un equipo sin reloj de tiempo real no sabe qué hora es hasta
/// que sincroniza, y la plataforma no tiene por qué creerle. La hora la pone
/// quien recibe. `seq` crece con cada mensaje y vuelve a cero cuando el equipo
/// reinicia; sirve para ver huecos y reinicios.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Telemetry {
    pub v: u8,
    pub seq: u64,
    pub readings: Vec<Reading>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reading {
    /// Qué sensor del equipo es. Mismo formato que el `sensor_id` de la carpa.
    pub sensor: String,
    pub temperature_c: f64,
    pub humidity_pct: f64,
}

#[derive(Debug, Clone, PartialEq, thiserror::Error)]
pub enum InvalidTelemetry {
    #[error("versión de protocolo {0} no soportada (esta plataforma habla {PROTOCOL_VERSION})")]
    Version(u8),
    #[error("el mensaje no trae lecturas")]
    Empty,
    #[error("demasiadas lecturas en un mensaje: {0} (máximo {max})", max = Telemetry::MAX_READINGS)]
    TooManyReadings(usize),
    #[error("id de sensor inválido {0:?}")]
    SensorId(String),
    #[error("sensor {0:?} repetido en el mismo mensaje")]
    DuplicateSensor(String),
    #[error("sensor {sensor:?}: temperatura {value} °C fuera de rango (-40 a 80)")]
    Temperature { sensor: String, value: f64 },
    #[error("sensor {sensor:?}: humedad {value} % fuera de rango (0 a 100)")]
    Humidity { sensor: String, value: f64 },
}

impl Telemetry {
    /// Un equipo con más sensores que esto está mandando basura o algo que
    /// todavía no se diseñó.
    pub const MAX_READINGS: usize = 16;

    pub fn new(seq: u64, readings: Vec<Reading>) -> Self {
        Self {
            v: PROTOCOL_VERSION,
            seq,
            readings,
        }
    }

    /// Valida contra los mismos rangos físicos que la carpa
    /// (`POST /api/sensor/indoor`). Un mensaje con una sola lectura mala se
    /// rechaza entero: un sensor que manda basura merece verse, no perderse en
    /// un promedio.
    pub fn validate(&self) -> Result<(), InvalidTelemetry> {
        if self.v != PROTOCOL_VERSION {
            return Err(InvalidTelemetry::Version(self.v));
        }
        if self.readings.is_empty() {
            return Err(InvalidTelemetry::Empty);
        }
        if self.readings.len() > Self::MAX_READINGS {
            return Err(InvalidTelemetry::TooManyReadings(self.readings.len()));
        }
        for (i, r) in self.readings.iter().enumerate() {
            if !is_valid_sensor_id(&r.sensor) {
                return Err(InvalidTelemetry::SensorId(r.sensor.clone()));
            }
            if self.readings[..i].iter().any(|prev| prev.sensor == r.sensor) {
                return Err(InvalidTelemetry::DuplicateSensor(r.sensor.clone()));
            }
            // `contains` es falso para NaN, así que NaN también se rechaza.
            if !(-40.0..=80.0).contains(&r.temperature_c) {
                return Err(InvalidTelemetry::Temperature {
                    sensor: r.sensor.clone(),
                    value: r.temperature_c,
                });
            }
            if !(0.0..=100.0).contains(&r.humidity_pct) {
                return Err(InvalidTelemetry::Humidity {
                    sensor: r.sensor.clone(),
                    value: r.humidity_pct,
                });
            }
        }
        Ok(())
    }
}

/// Minúsculas, dígitos, `_` y `-`, de 1 a 16 caracteres: el mismo formato que el
/// firmware de la carpa.
fn is_valid_sensor_id(id: &str) -> bool {
    (1..=16).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reading(sensor: &str, t: f64, rh: f64) -> Reading {
        Reading {
            sensor: sensor.into(),
            temperature_c: t,
            humidity_pct: rh,
        }
    }

    #[test]
    fn parses_the_documented_example() {
        let raw = r#"{"v":1,"seq":42,"readings":[{"sensor":"a","temperature_c":24.5,"humidity_pct":62.0}]}"#;
        let t: Telemetry = serde_json::from_str(raw).unwrap();
        assert_eq!(t, Telemetry::new(42, vec![reading("a", 24.5, 62.0)]));
        assert_eq!(t.validate(), Ok(()));
    }

    #[test]
    fn refuses_unknown_fields() {
        let raw = r#"{"v":1,"seq":1,"readings":[],"device":"otro"}"#;
        assert!(serde_json::from_str::<Telemetry>(raw).is_err());
    }

    #[test]
    fn refuses_out_of_range_and_nan() {
        for r in [
            reading("a", 81.0, 50.0),
            reading("a", -41.0, 50.0),
            reading("a", f64::NAN, 50.0),
        ] {
            assert!(matches!(
                Telemetry::new(1, vec![r]).validate(),
                Err(InvalidTelemetry::Temperature { .. })
            ));
        }
        for r in [
            reading("a", 20.0, 100.1),
            reading("a", 20.0, -0.1),
            reading("a", 20.0, f64::NAN),
        ] {
            assert!(matches!(
                Telemetry::new(1, vec![r]).validate(),
                Err(InvalidTelemetry::Humidity { .. })
            ));
        }
    }

    #[test]
    fn refuses_structural_problems() {
        assert_eq!(Telemetry::new(1, vec![]).validate(), Err(InvalidTelemetry::Empty));
        let dup = Telemetry::new(1, vec![reading("a", 20.0, 50.0), reading("a", 21.0, 50.0)]);
        assert_eq!(dup.validate(), Err(InvalidTelemetry::DuplicateSensor("a".into())));
        let bad_id = Telemetry::new(1, vec![reading("A/b", 20.0, 50.0)]);
        assert_eq!(bad_id.validate(), Err(InvalidTelemetry::SensorId("A/b".into())));
        let mut old = Telemetry::new(1, vec![reading("a", 20.0, 50.0)]);
        old.v = 0;
        assert_eq!(old.validate(), Err(InvalidTelemetry::Version(0)));
        let many = (0..17).map(|i| reading(&format!("s{i}"), 20.0, 50.0)).collect();
        assert_eq!(
            Telemetry::new(1, many).validate(),
            Err(InvalidTelemetry::TooManyReadings(17))
        );
    }
}
