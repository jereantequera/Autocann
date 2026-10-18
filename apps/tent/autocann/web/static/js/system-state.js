/**
 * The calibration state shared by the Sistema modules: the stored offsets by
 * sensor id, the version that marks a row as stale, and the sensors saved in
 * this page session.
 */

/**
 * Stored offsets by sensor id.
 *
 * /api/calibration answers with a list of rows, not a map; keying it here is
 * what lets a row find its own sensor instead of whatever row happened to be
 * in that position.
 */
const systemState = {
    calibration: {},
    /** Bumped on every calibration fetch: a row is rebuilt when its version is stale. */
    calibrationVersion: 0,
    /**
     * Sensors saved in this page session.
     *
     * The caveat that matters - saved is not applied until the loop restarts - is
     * said once, where the save button is, and stays until the page is reloaded,
     * which is what restarting the loop leads to anyway.
     */
    savedThisSession: new Set(),
};

export { systemState };
