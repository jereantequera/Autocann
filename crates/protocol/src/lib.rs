//! El contrato entre un equipo y la plataforma.
//!
//! Todo lo que un equipo publica y todo lo que la plataforma le manda pasa por
//! los tipos de este crate. No hace I/O: así lo pueden usar el ingestor, el
//! simulador y, más adelante, el firmware.

pub mod device;
pub mod profile;
pub mod telemetry;
pub mod topics;
pub mod vpd;

pub use device::{DeviceId, InvalidDeviceId};
pub use profile::{CropProfile, InvalidProfile, Range, Stage};
pub use telemetry::{InvalidTelemetry, Reading, Telemetry};
pub use topics::{Channel, DeviceTopic};

/// Versión del formato de los mensajes. Un equipo la manda en cada mensaje
/// (`"v"`), para que la plataforma pueda convivir con firmware viejo en la
/// calle mientras se actualiza.
pub const PROTOCOL_VERSION: u8 = 1;
