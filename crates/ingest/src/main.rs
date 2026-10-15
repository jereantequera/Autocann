//! `autocann-ingest`: escucha la telemetría de todos los equipos en el broker.
//!
//! Por defecto se suscribe con una suscripción común: una sola instancia recibe
//! todo. Con `AUTOCANN_INGEST_SHARE_GROUP` usa una compartida
//! (`$share/<grupo>/...`) y el broker reparte los mensajes entre varias
//! instancias. **Sólo en un broker que niegue la suscripción por ACL**, como
//! EMQX: Mosquitto acepta que un equipo cualquiera se sume al grupo y le manda
//! su parte de los mensajes, que después la ACL de lectura descarta. Probado:
//! con un intruso en el grupo, el ingestor real recibió la mitad.
//!
//! Por ahora las lecturas aceptadas se escriben en el log. Dónde se guardan
//! (TimescaleDB, Postgres) es la próxima decisión, y entra acá sin tocar
//! `handle`.

mod handle;

use anyhow::Result;
use autocann_mqtt::BrokerConfig;
use autocann_protocol::Channel;
use rumqttc::{AsyncClient, Event, Packet, QoS};
use tracing::{info, warn};

/// El CN del certificado del ingestor. Lleva `:` para que ningún equipo pueda
/// llamarse igual (ver `DeviceId`).
const SERVICE_IDENTITY: &str = "svc:ingest";

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    let broker = BrokerConfig::from_env()?;
    // El client id tiene que ser único por instancia: dos conexiones con el
    // mismo id se patean entre sí en el broker.
    let client_id = format!("{SERVICE_IDENTITY}-{}", std::process::id());
    let (client, mut events) = AsyncClient::new(broker.mqtt_options(&client_id)?, 64);
    let filter = match std::env::var("AUTOCANN_INGEST_SHARE_GROUP") {
        Ok(group) => format!("$share/{group}/{}", Channel::Telemetry.wildcard()),
        Err(_) => Channel::Telemetry.wildcard(),
    };

    info!(host = %broker.host, port = broker.port, %filter, "conectando");
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                info!("cortando");
                client.disconnect().await.ok();
                return Ok(());
            }
            event = events.poll() => match event {
                // Se vuelve a suscribir en cada conexión: con clean session el
                // broker se olvida de la suscripción al reconectar.
                Ok(Event::Incoming(Packet::ConnAck(_))) => {
                    info!("conectado");
                    client.subscribe(&filter, QoS::AtLeastOnce).await?;
                }
                Ok(Event::Incoming(Packet::SubAck(ack))) => {
                    info!(codes = ?ack.return_codes, "suscripción confirmada");
                }
                Ok(Event::Incoming(Packet::Publish(p))) => match handle::handle_telemetry(&p.topic, &p.payload) {
                    Ok(a) => {
                        for r in &a.readings {
                            info!(
                                device = %a.device, seq = a.seq, sensor = %r.sensor,
                                temperature_c = r.temperature_c, humidity_pct = r.humidity_pct,
                                vpd_kpa = format!("{:.2}", r.vpd_kpa),
                                "lectura",
                            );
                        }
                    }
                    Err(e) => warn!(topic = %p.topic, error = %e, "mensaje rechazado"),
                },
                Ok(_) => {}
                Err(e) => {
                    // rumqttc reconecta solo en el próximo poll; esto sólo evita
                    // martillar al broker mientras está caído.
                    warn!(error = %e, "conexión perdida, reintentando en 5 s");
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                }
            }
        }
    }
}
