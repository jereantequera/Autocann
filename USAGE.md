# Uso del Sistema de Almacenamiento de Datos

## Arquitectura

El sistema ahora utiliza una arquitectura híbrida para almacenamiento de datos:

### Redis (Datos en Tiempo Real)
- **Propósito**: Datos actuales y estados de control
- **Claves almacenadas**:
  - `sensors`: Última lectura de sensores
  - `humidity_control_up`: Estado del humidificador
  - `humidity_control_down`: Estado del deshumidificador
  - `ventilation_control`: Estado de la ventilación
  - `historical_data_*`: Ventanas de tiempo cortas (6h, 12h, 24h, 1w)

### SQLite (Datos Históricos)
- **Propósito**: Persistencia a largo plazo de todas las lecturas
- **Ubicación**: `/data/autocann.db`
- **Tablas**:
  - `sensor_data`: Todas las lecturas de sensores
  - `control_events`: Historial de cambios de control

## API Endpoints

### 1. Datos Actuales (Redis)

```bash
# Obtener datos en tiempo real
GET /api/current-data
```

### 2. Historial con Períodos Predefinidos

```bash
# Última hora
GET /api/sensor-history?period=1h

# Últimas 6 horas
GET /api/sensor-history?period=6h

# Últimas 24 horas
GET /api/sensor-history?period=24h

# Últimos 7 días
GET /api/sensor-history?period=7d

# Últimos 30 días
GET /api/sensor-history?period=30d

# Últimos 90 días
GET /api/sensor-history?period=90d
```

### 3. Historial con Agregación

```bash
# Últimas 24 horas con datos cada hora
GET /api/sensor-history?period=24h&aggregate=3600

# Últimos 7 días con datos cada 6 horas
GET /api/sensor-history?period=7d&aggregate=21600

# Últimos 30 días con datos diarios
GET /api/sensor-history?period=30d&aggregate=86400
```

### 4. Historial Agregado (Formato Simplificado)

```bash
# Últimas 24 horas con intervalos horarios
GET /api/history/aggregated?days=1&interval=hourly

# Últimos 7 días con intervalos de 6 horas
GET /api/history/aggregated?days=7&interval=6hourly

# Últimos 30 días con intervalos diarios
GET /api/history/aggregated?days=30&interval=daily
```

### 5. Rango de Tiempo Personalizado

```bash
# Datos entre timestamps específicos
GET /api/sensor-history?start=1696118400&end=1696204800

# Con límite de registros
GET /api/sensor-history?start=1696118400&end=1696204800&limit=1000
```

### 6. Estadísticas de la Base de Datos

```bash
# Información sobre la base de datos
GET /api/database-stats
```

Respuesta:
```json
{
  "sensor_data_count": 28800,
  "control_events_count": 450,
  "oldest_record": "2025-09-01 10:00:00",
  "newest_record": "2025-10-01 15:30:00",
  "database_size_mb": 12.5,
  "database_path": "/Users/jeremiasantequera/Documents/Autocann/data/autocann.db"
}
```

## Formato de Respuesta

### Datos Crudos

```json
{
  "data": [
    {
      "id": 1,
      "timestamp": 1696118400,
      "datetime": "2025-10-01 10:00:00",
      "temperature": 24.5,
      "humidity": 65.2,
      "vpd": 1.15,
      "outside_temperature": 22.3,
      "outside_humidity": 70.5,
      "leaf_temperature": 23.0,
      "leaf_vpd": 1.25,
      "target_humidity": 65.0
    }
  ],
  "count": 1,
  "aggregated": false
}
```

### Datos Agregados

```json
{
  "data": [
    {
      "timestamp": 1696118400,
      "datetime": "2025-10-01 10:00:00",
      "temperature": 24.5,
      "humidity": 65.2,
      "vpd": 1.15,
      "outside_temperature": 22.3,
      "outside_humidity": 70.5,
      "leaf_temperature": 23.0,
      "leaf_vpd": 1.25,
      "min_temperature": 23.8,
      "max_temperature": 25.2,
      "min_humidity": 63.5,
      "max_humidity": 67.0,
      "sample_count": 120
    }
  ],
  "count": 1,
  "aggregated": true,
  "interval_seconds": 3600
}
```

## Mantenimiento de la Base de Datos

El módulo `database.py` incluye funciones de mantenimiento:

```python
from scripts.database import cleanup_old_data, get_database_stats

# Eliminar datos más antiguos de 90 días
sensor_deleted, control_deleted = cleanup_old_data(days_to_keep=90)
print(f"Eliminados: {sensor_deleted} lecturas, {control_deleted} eventos")

# Obtener estadísticas
stats = get_database_stats()
print(f"Base de datos: {stats['database_size_mb']} MB")
print(f"Registros: {stats['sensor_data_count']}")
```

## Frecuencia de Almacenamiento

- **SQLite**: Cada lectura (cada ~3 segundos)
- **Redis**: Cada lectura para tiempo real, ventanas agregadas según configuración

## Backup

Para hacer backup de los datos históricos:

```bash
# Copiar la base de datos
cp data/autocann.db data/autocann_backup_$(date +%Y%m%d).db

# O usando SQLite dump
sqlite3 data/autocann.db .dump > backup.sql
```

## Consultas Directas con SQLite

También podés consultar directamente la base de datos:

```bash
# Abrir la base de datos
sqlite3 data/autocann.db

# Ver últimas 10 lecturas
SELECT datetime, temperature, humidity, vpd FROM sensor_data ORDER BY timestamp DESC LIMIT 10;

# Ver eventos de control
SELECT datetime, event_type, value FROM control_events ORDER BY timestamp DESC LIMIT 20;

# Temperatura promedio por día
SELECT 
  DATE(datetime) as date,
  AVG(temperature) as avg_temp,
  AVG(humidity) as avg_humidity
FROM sensor_data
GROUP BY date
ORDER BY date DESC
LIMIT 7;
```

## Rendimiento

- **SQLite** puede manejar millones de registros eficientemente
- Los índices en `timestamp` optimizan consultas por rango de tiempo
- La agregación en la base de datos es más eficiente que en memoria
- Con lecturas cada 3 segundos:
  - ~28,800 registros por día
  - ~864,000 registros por mes
  - Tamaño aproximado: ~1 MB por día

## Migración desde Redis

Si tenés datos históricos en Redis y querés migrarlos a SQLite, podés crear un script de migración. Los datos en Redis eventualmente se perderán al reiniciar, pero SQLite los mantiene permanentemente.

---

## Analytics y Monitoreo

El sistema incluye funcionalidades avanzadas de analytics para monitorear la salud del cultivo y detectar problemas.

### VPD Score

El VPD Score mide el porcentaje del tiempo que el VPD (Vapor Pressure Deficit) estuvo dentro del rango óptimo para la etapa actual del cultivo.

```bash
# Score de los últimos 7 días
GET /api/vpd-score?days=7

# Score de los últimos 30 días
GET /api/vpd-score?days=30

# Score de un cultivo específico
GET /api/vpd-score?days=7&grow_id=1
```

**Respuesta:**

```json
{
  "overall_score": 78.5,
  "samples_total": 2016,
  "samples_in_range": 1582,
  "vpd_range": {"min": 0.8, "max": 1.2},
  "stage": "late_veg",
  "days": 7,
  "daily_scores": [
    {"date": "2025-01-10", "day_name": "Friday", "score": 82.3, "samples_total": 288, "samples_in_range": 237},
    {"date": "2025-01-11", "day_name": "Saturday", "score": 75.1, "samples_total": 288, "samples_in_range": 216}
  ]
}
```

**Interpretación del Score:**
- 🏆 **≥85%**: Excelente - Condiciones óptimas
- ✅ **70-84%**: Bueno - Condiciones aceptables
- ⚠️ **50-69%**: Regular - Necesita atención
- ❌ **<50%**: Necesita mejorar - Revisar configuración

### Reporte Semanal

Genera un reporte completo con estadísticas, tendencias y insights de los últimos 7 días.

```bash
GET /api/weekly-report

# Para un cultivo específico
GET /api/weekly-report?grow_id=1
```

**Respuesta:**

```json
{
  "grow_name": "Cultivo #1",
  "stage": "flowering",
  "report_period": {
    "start": "2025-01-10",
    "end": "2025-01-17"
  },
  "summary": {
    "temperature": {"avg": 24.5, "min": 21.2, "max": 27.8},
    "humidity": {"avg": 62.3, "min": 55.0, "max": 72.5},
    "vpd": {"avg": 1.25, "min": 0.95, "max": 1.55},
    "sample_count": 2016
  },
  "vpd_score": {
    "overall": 78.5,
    "daily": [...],
    "range": {"min": 1.2, "max": 1.5}
  },
  "trends": {
    "temperature": 1.2,
    "humidity": -3.5,
    "vpd_score": 5.2
  },
  "insights": {
    "best_hour": 14,
    "worst_hour": 6,
    "best_hour_score": 92.5,
    "worst_hour_score": 45.3
  },
  "hourly_distribution": [...]
}
```

**Campos importantes:**
- **trends**: Comparación con la semana anterior (positivo = aumentó, negativo = disminuyó)
- **insights.best_hour/worst_hour**: Las horas del día con mejor y peor rendimiento de VPD
- **hourly_distribution**: Estadísticas desglosadas por hora del día

### Detección de Anomalías

El sistema detecta automáticamente problemas en los sensores y datos anómalos.

```bash
# Anomalías de las últimas 24 horas
GET /api/anomalies?hours=24

# Anomalías de las últimas 6 horas
GET /api/anomalies?hours=6
```

**Respuesta:**

```json
{
  "status": "warning",
  "anomalies": [
    {
      "type": "stale_data",
      "severity": "critical",
      "message": "Último dato hace 45 minutos - sensor posiblemente desconectado",
      "timestamp": "2025-01-17 14:30:00",
      "minutes_ago": 45
    }
  ],
  "warnings": [
    {
      "type": "temperature_spike",
      "severity": "warning",
      "message": "Cambio brusco de temperatura: 12.5°C en 5 min",
      "timestamp": "2025-01-17 10:15:00",
      "from_value": 22.5,
      "to_value": 35.0
    }
  ],
  "checked_period": {
    "hours": 24,
    "samples_checked": 288,
    "start": "2025-01-16 15:00:00",
    "end": "2025-01-17 15:00:00"
  }
}
```

#### Tipos de Anomalías Detectadas

| Tipo | Severidad | Descripción | Causa Probable |
|------|-----------|-------------|----------------|
| `no_data` | 🔴 Critical | No hay datos en el período analizado | Sensor completamente desconectado o sistema apagado |
| `stale_data` | 🔴 Critical | Último dato hace más de 15 minutos | Sensor desconectado o proceso VPD detenido |
| `invalid_temperature` | 🔴 Critical | Temperatura fuera del rango físico (-10°C a 60°C) | Sensor defectuoso o conexión suelta |
| `invalid_humidity` | 🔴 Critical | Humedad fuera del rango 0-100% | Sensor defectuoso o conexión suelta |
| `data_gap` | 🟡 Warning | Sin datos por más de 15 minutos | Interrupción temporal, reinicio del sistema |
| `temperature_spike` | 🟡 Warning | Cambio >10°C en menos de 10 minutos | Sensor tocado, puerta abierta, o lectura errónea |
| `humidity_spike` | 🟡 Warning | Cambio >30% en menos de 10 minutos | Humidificador encendido/apagado bruscamente, o sensor errático |
| `stuck_temperature` | 🟡 Warning | Mismo valor exacto por >30 minutos | Sensor congelado o defectuoso |
| `stuck_humidity` | 🟡 Warning | Mismo valor exacto por >30 minutos | Sensor congelado o defectuoso |

#### Estados del Sistema

- **`ok`**: Sin problemas detectados
- **`warning`**: Solo advertencias menores
- **`critical`**: Hay anomalías críticas que requieren atención inmediata
- **`error`**: Error al ejecutar la detección

#### Umbrales de Detección

| Parámetro | Umbral |
|-----------|--------|
| Intervalo esperado entre samples | 5 minutos |
| Gap máximo antes de alerta | 15 minutos (3 samples perdidos) |
| Cambio brusco de temperatura | >10°C en 10 min |
| Cambio brusco de humedad | >30% en 10 min |
| Tiempo para detectar valor estancado | 30 minutos (6 samples idénticos) |
| Rango válido de temperatura | -10°C a 60°C |
| Rango válido de humedad | 0% a 100% |

### Dashboard

Todas estas funcionalidades están integradas en el dashboard web:

- **VPD Score Card**: Muestra el score de los últimos 7 días con barras por día
- **Botón "📊 Reporte"**: Abre un modal con el reporte semanal completo
- **Banner de Anomalías**: Aparece automáticamente cuando se detectan problemas

El dashboard actualiza automáticamente:
- VPD Score: cada 5 minutos
- Anomalías: cada 1 minuto
