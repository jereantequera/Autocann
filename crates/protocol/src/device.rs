use std::fmt;

/// La identidad de un equipo.
///
/// Es el CN de su certificado, y el broker la usa como nombre de usuario para
/// decidir en qué tópicos puede publicar. Por eso el formato es estricto: va a
/// parar a un tópico MQTT y a una regla de ACL, y nada de lo que la rompa
/// (`/`, `+`, `#`, espacios) puede entrar.
///
/// Las identidades de servicio (el ingestor, por ejemplo) llevan `:`, que acá no
/// es válido. Así un equipo nunca puede tener el mismo nombre que un servicio y
/// heredar sus permisos en el broker.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct DeviceId(String);

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("id de equipo inválido {0:?}: minúsculas, dígitos y '-', de 3 a 64 caracteres, sin '-' en los bordes")]
pub struct InvalidDeviceId(pub String);

impl DeviceId {
    pub const MIN_LEN: usize = 3;
    pub const MAX_LEN: usize = 64;

    pub fn parse(raw: &str) -> Result<Self, InvalidDeviceId> {
        let ok_len = (Self::MIN_LEN..=Self::MAX_LEN).contains(&raw.len());
        let ok_chars = raw
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
        let ok_edges = !raw.starts_with('-') && !raw.ends_with('-');
        if ok_len && ok_chars && ok_edges {
            Ok(Self(raw.to_owned()))
        } else {
            Err(InvalidDeviceId(raw.to_owned()))
        }
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for DeviceId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_plain_ids() {
        for id in ["abc", "carpa-01", "a1b2c3d4e5f6", &"x".repeat(64)] {
            assert!(DeviceId::parse(id).is_ok(), "{id}");
        }
    }

    #[test]
    fn refuses_anything_that_could_escape_a_topic_or_acl() {
        for id in [
            "",
            "ab",
            "Carpa",
            "a/b",
            "a+b",
            "a#b",
            "a b",
            "svc:ingest",
            "-abc",
            "abc-",
            &"x".repeat(65),
        ] {
            assert!(DeviceId::parse(id).is_err(), "{id}");
        }
    }
}
