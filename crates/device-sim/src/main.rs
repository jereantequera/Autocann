//! `autocann-device-sim`: un equipo de mentira.
//!
//! Se conecta con el certificado de un equipo y publica telemetría plausible
//! cada pocos segundos. Sirve para probar el broker, la ACL y el ingestor sin
//! hardware, y para hacer de muchos equipos a la vez corriendo varias copias.
//!
//! ```text
//! autocann-device-sim <device-id> [segundos-entre-mensajes]
//! ```
//!
//! El `device-id` tiene que ser el CN del certificado en `AUTOCANN_MQTT_CERT`.
//! Si no lo es, el broker rechaza las publicaciones: es justamente lo que hay
//! que poder probar.

use std::time::Duration;

use anyhow::{Context, Result, bail};
use autocann_mqtt::BrokerConfig;
use autocann_protocol::{Channel, DeviceId, DeviceTopic, Reading, Telemetry};
use rumqttc::{AsyncClient, Event, Packet, QoS};
use tracing::{info, warn};

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    let mut args = std::env::args().skip(1);
    let Some(raw_id) = args.next() else {
        bail!("uso: autocann-device-sim <device-id> [segundos-entre-mensajes]");
    };
    let device = DeviceId::parse(&raw_id)?;
    let every = match args.next() {
        Some(s) => Duration::from_secs(s.parse().with_context(|| format!("intervalo inválido: {s:?}"))?),
        None => Duration::from_secs(5),
    };

    let broker = BrokerConfig::from_env()?;
    let (client, mut events) = AsyncClient::new(broker.mqtt_options(device.as_str())?, 16);
    let topic = DeviceTopic::new(device.clone(), Channel::Telemetry).to_topic();

    // El event loop de rumqttc tiene que girar para que algo salga por la red.
    tokio::spawn(async move {
        loop {
            match events.poll().await {
                Ok(Event::Incoming(Packet::ConnAck(_))) => info!("conectado"),
                Ok(_) => {}
                Err(e) => {
                    warn!(error = %e, "conexión perdida, reintentando en 5 s");
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            }
        }
    });

    info!(%device, %topic, every_s = every.as_secs(), "publicando");
    let mut ticker = tokio::time::interval(every);
    for seq in 0u64.. {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            _ = ticker.tick() => {}
        }
        let telemetry = Telemetry::new(seq, vec![simulated_reading(seq)]);
        client
            .publish(&topic, QoS::AtLeastOnce, false, serde_json::to_vec(&telemetry)?)
            .await?;
    }
    client.disconnect().await.ok();
    Ok(())
}

/// Un día de carpa comprimido en diez minutos: la temperatura sube con la luz y
/// la humedad baja, como pasa de verdad.
fn simulated_reading(seq: u64) -> Reading {
    let phase = (seq % 120) as f64 / 120.0 * std::f64::consts::TAU;
    Reading {
        sensor: "a".into(),
        temperature_c: ((24.0 + 3.0 * phase.sin()) * 10.0).round() / 10.0,
        humidity_pct: ((60.0 - 8.0 * phase.sin()) * 10.0).round() / 10.0,
    }
}
