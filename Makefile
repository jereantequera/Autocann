# Monorepo de Autocann.
#
# Cada parte tiene su propio tooling: la carpa de pruebas usa uv y su propio
# Makefile (apps/tent/Makefile), Rust usa cargo. Esto sólo los junta para que
# `make test` y `make lint` miren todo.
#
# Lo de la carpa se alcanza con el prefijo `tent-`: `make tent-deploy` es
# `make -C apps/tent deploy`.

export PATH := $(HOME)/.cargo/bin:$(HOME)/.local/bin:$(PATH)

PKI := deploy/pki/out
DEVICE ?= carpa-01
MOSQUITTO ?= mosquitto

# Las variables que leen el ingestor y el simulador (crates/mqtt).
MQTT_ENV = AUTOCANN_MQTT_CA=$(PKI)/ca-chain.pem
INGEST_ENV = $(MQTT_ENV) AUTOCANN_MQTT_CERT=$(PKI)/services/ingest.pem AUTOCANN_MQTT_KEY=$(PKI)/services/ingest.key
DEVICE_ENV = $(MQTT_ENV) AUTOCANN_MQTT_CERT=$(PKI)/devices/$(DEVICE).pem AUTOCANN_MQTT_KEY=$(PKI)/devices/$(DEVICE).key

.PHONY: help test test-rust lint lint-rust build build-release \
	pki-init pki-device pki-service dev-broker dev-ingest dev-sim

help:
	@echo "Autocann — monorepo"
	@echo ""
	@echo "  make test            tests de todo (carpa + Rust)"
	@echo "  make lint            ruff + clippy + rustfmt"
	@echo "  make build           compila el workspace de Rust"
	@echo "  make build-release   compila optimizado"
	@echo ""
	@echo "  Plataforma en local (ver docs/platform.md):"
	@echo "  make pki-init                 CA de desarrollo + certificados del broker y el ingestor"
	@echo "  make pki-device DEVICE=id     certificado para un equipo"
	@echo "  make dev-broker               Mosquitto con mTLS en :8883"
	@echo "  make dev-ingest               el ingestor contra ese broker"
	@echo "  make dev-sim DEVICE=id        un equipo simulado publicando"
	@echo ""
	@echo "  make tent-<target>   cualquier target de apps/tent (make tent-help)"

test: tent-test test-rust

test-rust:
	cargo test --workspace

lint: tent-lint lint-rust

lint-rust:
	cargo fmt --check
	cargo clippy --workspace --all-targets -- -D warnings

build:
	cargo build --workspace

build-release:
	cargo build --release --workspace

pki-init:
	deploy/pki/dev-pki.sh init
	deploy/pki/dev-pki.sh service ingest

pki-device:
	deploy/pki/dev-pki.sh device $(DEVICE)

pki-service:
	@test -n "$(NAME)" || { echo "uso: make pki-service NAME=<nombre>"; exit 1; }
	deploy/pki/dev-pki.sh service $(NAME)

# Mosquitto se niega (en versiones futuras) a leer una ACL que otros pueden leer,
# y git no guarda esos permisos.
dev-broker:
	@test -f $(PKI)/broker/broker.pem || { echo "❌ Primero: make pki-init"; exit 1; }
	chmod 600 deploy/mosquitto/acl.conf
	$(MOSQUITTO) -c deploy/mosquitto/mosquitto.conf

dev-ingest:
	$(INGEST_ENV) cargo run -p autocann-ingest

dev-sim:
	@test -f $(PKI)/devices/$(DEVICE).pem || { echo "❌ Primero: make pki-device DEVICE=$(DEVICE)"; exit 1; }
	$(DEVICE_ENV) cargo run -p autocann-device-sim -- $(DEVICE)

tent-%:
	$(MAKE) -C apps/tent $*
