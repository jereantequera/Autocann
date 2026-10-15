//! Los tópicos MQTT.
//!
//! ```text
//! v1/devices/{device_id}/telemetry   equipo → plataforma   lecturas
//! v1/devices/{device_id}/state       equipo → plataforma   salidas, perfil activo, firmware
//! v1/devices/{device_id}/commands    plataforma → equipo   cambios de perfil, órdenes acotadas
//! ```
//!
//! El `device_id` del tópico es confiable **sólo porque el broker lo garantiza**:
//! la ACL deja que cada certificado publique únicamente bajo su propio CN. Si se
//! cambia de broker, esa regla tiene que viajar con él.

use crate::device::DeviceId;

const PREFIX: &str = "v1/devices";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Channel {
    Telemetry,
    State,
    Commands,
}

impl Channel {
    pub fn as_str(self) -> &'static str {
        match self {
            Channel::Telemetry => "telemetry",
            Channel::State => "state",
            Channel::Commands => "commands",
        }
    }

    fn parse(raw: &str) -> Option<Self> {
        match raw {
            "telemetry" => Some(Channel::Telemetry),
            "state" => Some(Channel::State),
            "commands" => Some(Channel::Commands),
            _ => None,
        }
    }

    /// El filtro con el que un servicio escucha este canal de todos los equipos.
    pub fn wildcard(self) -> String {
        format!("{PREFIX}/+/{}", self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeviceTopic {
    pub device: DeviceId,
    pub channel: Channel,
}

impl DeviceTopic {
    pub fn new(device: DeviceId, channel: Channel) -> Self {
        Self { device, channel }
    }

    pub fn parse(topic: &str) -> Option<Self> {
        let rest = topic.strip_prefix(PREFIX)?.strip_prefix('/')?;
        let (device, channel) = rest.split_once('/')?;
        Some(Self {
            device: DeviceId::parse(device).ok()?,
            channel: Channel::parse(channel)?,
        })
    }

    pub fn to_topic(&self) -> String {
        format!("{PREFIX}/{}/{}", self.device, self.channel.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips() {
        let t = DeviceTopic::new(DeviceId::parse("carpa-01").unwrap(), Channel::Telemetry);
        assert_eq!(t.to_topic(), "v1/devices/carpa-01/telemetry");
        assert_eq!(DeviceTopic::parse(&t.to_topic()), Some(t));
    }

    #[test]
    fn refuses_foreign_or_malformed_topics() {
        for topic in [
            "v1/devices/carpa-01",
            "v1/devices/carpa-01/telemetry/extra",
            "v1/devices/carpa-01/unknown",
            "v1/devices/Carpa/telemetry",
            "v2/devices/carpa-01/telemetry",
            "x/v1/devices/carpa-01/telemetry",
        ] {
            assert_eq!(DeviceTopic::parse(topic), None, "{topic}");
        }
    }

    #[test]
    fn wildcard_matches_the_layout() {
        assert_eq!(Channel::Telemetry.wildcard(), "v1/devices/+/telemetry");
    }
}
