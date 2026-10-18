/**
 * ¿Anda? — el estado de los sensores, del lazo y del histórico, en un solo lugar.
 *
 * Antes esto estaba repartido en tres fuentes que podían contradecirse, y se
 * contradecían: el badge del hero salía de `sensor_status` (la opinión del lazo
 * sobre el primario), cada tarjeta de la antigüedad de su propia lectura en
 * Redis, y el cartel de anomalías de la antigüedad de la última fila en la base
 * de datos. Con el sistema en pausa los tres decían cosas distintas a la vez:
 * un cartel rojo de "sensor posiblemente desconectado" arriba de dos tarjetas
 * verdes, con los sensores posteando perfecto.
 *
 * Todo lo que esta pantalla dice sobre si algo anda sale ahora de /api/health,
 * que lo calcula una vez mirando todas las fuentes juntas. Dos elementos de la
 * pantalla no pueden discrepar si leen del mismo objeto.
 *
 * Este módulo también dibuja los carteles de arriba; cuál se muestra lo decide
 * alerts.js, para que no haya dos cajas pegadas diciendo lo mismo.
 */

import { renderAlerts, setAnomalies } from './health-alerts.js';
import {
    DRIFT_FLAG_LABELS, SENSOR_ROLES, healthChecks, sensorsBadge, verdictFor,
} from './health-model.js';
import { fetchHealth } from './health-render.js';
import { CALIB_CELLS, SENSOR_READ_CELLS } from './health-sensors.js';
import { latestHealth } from './health-state.js';

export {
    fetchHealth, healthChecks, latestHealth, renderAlerts, sensorsBadge, setAnomalies, verdictFor,
    SENSOR_READ_CELLS, CALIB_CELLS,
    SENSOR_ROLES, DRIFT_FLAG_LABELS,
};
