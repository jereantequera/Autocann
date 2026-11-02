/**
 * The last /api/health answer and the last anomalies, shared by the health
 * modules. One object so every module reads the same copy.
 */

/** El último estado conocido, para que otros módulos no vuelvan a pedirlo. */
const healthState = {
    snapshot: null,
    /** Las alertas de la última consulta de analytics.js, cada una con si ya se vio. */
    anomalyItems: [],
    /** Si se muestran también las vistas: la campanita o el "N vistas" lo prenden. */
    showSeen: false,
};

const latestHealth = () => healthState.snapshot;

export { healthState, latestHealth };
