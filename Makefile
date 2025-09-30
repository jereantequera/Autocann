.PHONY: help install install-rpi sync update run-vpd run-backend run-watering clean logs check

help:
	@echo "Comandos disponibles:"
	@echo "  make install       - Instala dependencias (auto-detecta Raspberry Pi)"
	@echo "  make install-rpi   - Fuerza instalación con dependencias de Raspberry Pi"
	@echo "  make sync          - Sincroniza las dependencias"
	@echo "  make update        - Actualiza todas las dependencias"
	@echo "  make check         - Verifica el sistema y dependencias"
	@echo "  make run-vpd       - Ejecuta el control de VPD (early_veg por defecto)"
	@echo "  make run-backend   - Ejecuta el servidor web"
	@echo "  make run-watering  - Ejecuta el sistema de riego"
	@echo "  make clean         - Limpia archivos temporales"
	@echo "  make logs          - Muestra los últimos logs"

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
	@echo "=== VPD errors (últimas 50 líneas) ==="
	@tail -n 50 logs/errors_vpd_$$(date +'%Y-%m-%d').log 2>/dev/null || echo "No hay logs de errores de VPD hoy"

