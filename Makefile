.PHONY: help install install-rpi sync update run-vpd run-backend run-watering clean logs check ssh ssh-setup ssh-logs ssh-status ssh-restart deploy

# Configuración de la Raspberry Pi (valores por defecto)
RPI_USER ?= autocann
RPI_HOST ?= autocann.local
RPI_PATH ?= /home/autocann/Autocann

# Cargar configuración local si existe (config.mk)
-include config.mk

help:
	@echo "Comandos disponibles:"
	@echo ""
	@echo "  Instalación y desarrollo:"
	@echo "    make install       - Instala dependencias (auto-detecta Raspberry Pi)"
	@echo "    make install-rpi   - Fuerza instalación con dependencias de Raspberry Pi"
	@echo "    make sync          - Sincroniza las dependencias"
	@echo "    make update        - Actualiza todas las dependencias"
	@echo "    make check         - Verifica el sistema y dependencias"
	@echo ""
	@echo "  Ejecución local:"
	@echo "    make run-vpd       - Ejecuta el control de VPD (early_veg por defecto)"
	@echo "    make run-backend   - Ejecuta el servidor web"
	@echo "    make run-watering  - Ejecuta el sistema de riego"
	@echo "    make logs          - Muestra los últimos logs"
	@echo "    make clean         - Limpia archivos temporales"
	@echo ""
	@echo "  Raspberry Pi remota:"
	@echo "    make ssh-setup     - Configura SSH key (solo primera vez)"
	@echo "    make ssh           - Conecta por SSH a la Raspberry Pi"
	@echo "    make ssh-logs      - Ver logs remotos en la Raspberry Pi"
	@echo "    make ssh-status    - Ver estado de servicios en la Raspberry Pi"
	@echo "    make ssh-restart   - Reiniciar servicios en la Raspberry Pi"
	@echo "    make deploy        - Hace git push, pull y reinicia servicios"
	@echo ""
	@echo "  Variables de configuración SSH:"
	@echo "    RPI_USER=$(RPI_USER)"
	@echo "    RPI_HOST=$(RPI_HOST)"
	@echo "    RPI_PATH=$(RPI_PATH)"
	@echo ""
	@echo "  Ejemplo: make ssh RPI_HOST=192.168.1.100"

install:
	@echo "Instalando dependencias con uv..."
	@if [ -f /proc/cpuinfo ] && grep -q "Raspberry Pi" /proc/cpuinfo; then \
		echo "Raspberry Pi detectada, instalando con dependencias de hardware..."; \
		uv sync --extra rpi; \
	else \
		echo "Sistema de desarrollo detectado, instalando solo dependencias base..."; \
		uv sync; \
	fi

install-rpi:
	@echo "Instalando dependencias con soporte para Raspberry Pi..."
	uv sync --extra rpi

sync:
	@echo "Sincronizando dependencias..."
	uv sync

update:
	@echo "Actualizando dependencias..."
	uv sync --upgrade

check:
	@echo "Verificando sistema..."
	uv run scripts/check_system.py

run-vpd:
	@echo "Iniciando control de VPD..."
	uv run scripts/fix-vpd.py early_veg

run-backend:
	@echo "Iniciando servidor web..."
	uv run scripts/backend.py

run-watering:
	@echo "Iniciando sistema de riego..."
	uv run scripts/watering.py

clean:
	@echo "Limpiando archivos temporales..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pid" -delete

logs:
	@echo "=== Backend logs (últimas 50 líneas) ==="
	@tail -n 50 logs/backend_$$(date +'%Y-%m-%d').log 2>/dev/null || echo "No hay logs de backend hoy"
	@echo ""
	@echo "=== VPD logs (últimas 50 líneas) ==="
	@tail -n 50 logs/vpd_$$(date +'%Y-%m-%d').log 2>/dev/null || echo "No hay logs de VPD hoy"

# Comandos SSH para administración remota
ssh:
	@echo "Conectando a la Raspberry Pi..."
	@echo "Usuario: $(RPI_USER)@$(RPI_HOST)"
	ssh $(RPI_USER)@$(RPI_HOST)

ssh-logs:
	@echo "Mostrando logs remotos de la Raspberry Pi..."
	ssh $(RPI_USER)@$(RPI_HOST) "cd $(RPI_PATH) && make logs"

ssh-status:
	@echo "Estado de los servicios en la Raspberry Pi..."
	ssh $(RPI_USER)@$(RPI_HOST) "pgrep -a python | grep -E '(backend|fix-vpd|watering)' || echo 'No hay servicios corriendo'"

ssh-restart:
	@echo "Reiniciando servicios en la Raspberry Pi..."
	@ssh $(RPI_USER)@$(RPI_HOST) "pkill -f 'python.*fix-vpd' || true; pkill -f 'python.*backend' || true"
	@sleep 1
	@ssh $(RPI_USER)@$(RPI_HOST) "cd $(RPI_PATH) && setsid ./scripts/start_services.sh > /dev/null 2>&1 < /dev/null &"
	@echo "✅ Servicios reiniciados"

deploy:
	@echo "Desplegando cambios en la Raspberry Pi..."
	@echo "1. Haciendo push al repositorio..."
	@git push
	@echo "2. Actualizando código en la Raspberry Pi..."
	@ssh $(RPI_USER)@$(RPI_HOST) 'export PATH="$$HOME/.cargo/bin:$$HOME/.local/bin:$$PATH" && cd $(RPI_PATH) && git pull && uv sync --extra rpi'
	@echo "3. Reiniciando servicios..."
	@ssh $(RPI_USER)@$(RPI_HOST) "pkill -f 'python.*fix-vpd' || true; pkill -f 'python.*backend' || true"
	@sleep 1
	@ssh $(RPI_USER)@$(RPI_HOST) "cd $(RPI_PATH) && setsid ./scripts/start_services.sh > /dev/null 2>&1 < /dev/null &" || true
	@sleep 2
	@echo "4. Verificando estado..."
	@ssh $(RPI_USER)@$(RPI_HOST) "pgrep -a python | grep -E '(backend|fix-vpd)' || echo '⚠️  Servicios no detectados (pueden tardar en iniciar)'"
	@echo "✅ Despliegue completado"

ssh-setup:
	@echo "Configurando SSH key para conexión sin contraseña..."
	@echo ""
	@echo "Este comando copiará tu clave SSH pública a la Raspberry Pi."
	@echo "Después de esto, no necesitarás ingresar contraseña."
	@echo ""
	@if [ ! -f ~/.ssh/id_rsa.pub ] && [ ! -f ~/.ssh/id_ed25519.pub ]; then \
		echo "No se encontró clave SSH. Generando una nueva..."; \
		ssh-keygen -t ed25519 -C "$(shell whoami)@$(shell hostname)" -f ~/.ssh/id_ed25519 -N ""; \
	fi
	@echo "Copiando clave SSH a $(RPI_USER)@$(RPI_HOST)..."
	ssh-copy-id $(RPI_USER)@$(RPI_HOST)
	@echo ""
	@echo "✅ Configuración completada. Probá con: make ssh"

