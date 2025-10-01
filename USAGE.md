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

