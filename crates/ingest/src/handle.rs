//! Qué hace el ingestor con un mensaje, sin red ni base: tópico y bytes de
//! entrada, lecturas aceptadas o un motivo de rechazo de salida.

use autocann_protocol::{Channel, DeviceId, DeviceTopic, InvalidTelemetry, Telemetry, vpd::vpd_kpa};

/// Un mensaje de un equipo es como mucho esto. Lo que pase de acá no se
/// intenta parsear.
pub const MAX_PAYLOAD_BYTES: usize = 8 * 1024;

#[derive(Debug, Clone, PartialEq)]
pub struct Accepted {
    pub device: DeviceId,
    pub seq: u64,
    pub readings: Vec<AcceptedReading>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AcceptedReading {
    pub sensor: String,
    pub temperature_c: f64,
    pub humidity_pct: f64,
    pub vpd_kpa: f64,
}

#[derive(Debug, thiserror::Error)]
pub enum Rejected {
    #[error("tópico inesperado {0:?}")]
    Topic(String),
    #[error("mensaje de {size} bytes (máximo {MAX_PAYLOAD_BYTES})")]
    TooLarge { size: usize },
    #[error("JSON inválido: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Invalid(#[from] InvalidTelemetry),
}

pub fn handle_telemetry(topic: &str, payload: &[u8]) -> Result<Accepted, Rejected> {
    let device = match DeviceTopic::parse(topic) {
        Some(DeviceTopic {
            device,
            channel: Channel::Telemetry,
        }) => device,
        _ => return Err(Rejected::Topic(topic.to_owned())),
    };
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err(Rejected::TooLarge { size: payload.len() });
    }
    let telemetry: Telemetry = serde_json::from_slice(payload)?;
    telemetry.validate()?;
    Ok(Accepted {
        device,
        seq: telemetry.seq,
        readings: telemetry
            .readings
            .into_iter()
            .map(|r| AcceptedReading {
                vpd_kpa: vpd_kpa(r.temperature_c, r.humidity_pct),
                sensor: r.sensor,
                temperature_c: r.temperature_c,
                humidity_pct: r.humidity_pct,
            })
            .collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &[u8] = br#"{"v":1,"seq":7,"readings":[{"sensor":"a","temperature_c":24.5,"humidity_pct":62.0}]}"#;

    #[test]
    fn accepts_and_computes_vpd() {
        let a = handle_telemetry("v1/devices/carpa-01/telemetry", GOOD).unwrap();
        assert_eq!(a.device.as_str(), "carpa-01");
        assert_eq!(a.seq, 7);
        assert_eq!(a.readings.len(), 1);
        assert!((a.readings[0].vpd_kpa - 1.17).abs() < 0.01);
    }

    #[test]
    fn rejects_other_channels_and_garbage_topics() {
        for topic in ["v1/devices/carpa-01/state", "v1/devices/../telemetry", "otra/cosa"] {
            assert!(
                matches!(handle_telemetry(topic, GOOD), Err(Rejected::Topic(_))),
                "{topic}"
            );
        }
    }

    #[test]
    fn rejects_oversized_before_parsing() {
        let big = vec![b' '; MAX_PAYLOAD_BYTES + 1];
        assert!(matches!(
            handle_telemetry("v1/devices/carpa-01/telemetry", &big),
            Err(Rejected::TooLarge { .. })
        ));
    }

    #[test]
    fn rejects_bad_json_and_invalid_readings() {
        let topic = "v1/devices/carpa-01/telemetry";
        assert!(matches!(handle_telemetry(topic, b"{"), Err(Rejected::Json(_))));
        let hot = br#"{"v":1,"seq":1,"readings":[{"sensor":"a","temperature_c":99,"humidity_pct":50}]}"#;
        assert!(matches!(handle_telemetry(topic, hot), Err(Rejected::Invalid(_))));
    }
}
