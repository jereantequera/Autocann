//! La conexión al broker, igual para todos: siempre mTLS.
//!
//! No hay modo sin TLS, ni siquiera para desarrollo. Un modo inseguro que existe
//! termina prendido en producción; la CA de desarrollo (`deploy/pki/dev-pki.sh`)
//! hace que usar TLS en local cueste un comando.

use std::{env, fs, path::Path, time::Duration};

use anyhow::{Context, Result};
use rumqttc::{MqttOptions, TlsConfiguration, Transport};

/// Dónde está el broker y con qué identidad se presenta este cliente.
#[derive(Debug, Clone)]
pub struct BrokerConfig {
    pub host: String,
    pub port: u16,
    /// Cadena de CAs con la que se verifica al broker.
    pub ca_path: String,
    /// Certificado propio. Su CN es la identidad que ve el broker.
    pub cert_path: String,
    pub key_path: String,
}

impl BrokerConfig {
    /// Lee `AUTOCANN_MQTT_{HOST,PORT,CA,CERT,KEY}`. Host y puerto tienen default;
    /// los tres archivos no, a propósito.
    pub fn from_env() -> Result<Self> {
        let var = |name: &str| env::var(name).with_context(|| format!("falta la variable {name}"));
        Ok(Self {
            host: env::var("AUTOCANN_MQTT_HOST").unwrap_or_else(|_| "localhost".into()),
            port: match env::var("AUTOCANN_MQTT_PORT") {
                Ok(p) => p
                    .parse()
                    .with_context(|| format!("AUTOCANN_MQTT_PORT inválido: {p:?}"))?,
                Err(_) => 8883,
            },
            ca_path: var("AUTOCANN_MQTT_CA")?,
            cert_path: var("AUTOCANN_MQTT_CERT")?,
            key_path: var("AUTOCANN_MQTT_KEY")?,
        })
    }

    pub fn mqtt_options(&self, client_id: &str) -> Result<MqttOptions> {
        let read = |p: &str| fs::read(Path::new(p)).with_context(|| format!("no se pudo leer {p}"));
        let tls = TlsConfiguration::Simple {
            ca: read(&self.ca_path)?,
            alpn: None,
            client_auth: Some((read(&self.cert_path)?, read(&self.key_path)?)),
        };
        let mut opts = MqttOptions::new(client_id, &self.host, self.port);
        opts.set_transport(Transport::tls_with_config(tls));
        opts.set_keep_alive(Duration::from_secs(30));
        Ok(opts)
    }
}
