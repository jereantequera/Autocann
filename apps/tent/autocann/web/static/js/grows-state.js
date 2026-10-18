/**
 * The Cultivo state the grow pieces share: the active grow with its stage
 * timeline, and the stage order the server defines (fetched once).
 *
 * One object, so every module that reads or writes these sees the same values.
 */
export const growState = {
    /** The active grow and its stage history, as the Cultivo header and bar draw them. */
    activeGrow: null,
    timelineData: null,
    /** The stage order and expected lengths the server defines; fetched once. */
    stageConfig: null,
    stageConfigRequest: null,
};
