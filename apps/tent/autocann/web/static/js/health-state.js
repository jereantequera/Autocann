/**
 * The last /api/health answer and the last anomalies, shared by the health
 * modules. One object so every module reads the same copy.
 */

/** El último estado conocido, para que otros módulos no vuelvan a pedirlo. */
const healthState = {
    snapshot: null,
    /** Anomalías de la última consulta de analytics.js, y si el usuario las descartó. */
    anomalyItems: [],
    anomaliesDismissed: false,
};

const latestHealth = () => healthState.snapshot;

export { healthState, latestHealth };
