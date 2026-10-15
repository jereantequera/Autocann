/**
 * El tema: Grafito (el de siempre) o Noche (negro y ámbar, para el tablero de
 * la pared con las luces de la carpa apagadas).
 *
 * Noche no es un color más: es para no iluminar la carpa de noche. Por eso
 * vive en un atributo de <html> y no en una clase de un contenedor: todos los
 * tokens cambian a la vez, sin que ninguna vista tenga que saber del tema.
 */

const STORAGE_KEY = 'autocann.theme';
const NIGHT = 'night';
/** Grafito is the default: no attribute at all, so the page is dark without JS. */
const THEMES = ['graphite', NIGHT];

/**
 * Lo que se guardó, validado. Cualquier otra cosa es Grafito: un valor viejo
 * o escrito a mano no tiene por qué dejar la pantalla en un tema inexistente.
 */
export function normalizeTheme(value) {
    return value === NIGHT ? NIGHT : 'graphite';
}

/** Lee la preferencia; sin acceso a localStorage (ventana privada) es Grafito. */
export function readTheme(storage = globalThis.localStorage) {
    try {
        return normalizeTheme(storage.getItem(STORAGE_KEY));
    } catch {
        // Blocked site data throws here; the default is the same answer as a
        // first visit, so nothing is lost by not remembering.
        return 'graphite';
    }
}

function storeTheme(name, storage = globalThis.localStorage) {
    try {
        storage.setItem(STORAGE_KEY, name);
    } catch {
        // The choice lasts until the page is closed. Not worth a message.
    }
}

/**
 * Pone el tema en la página y lo recuerda.
 *
 * Quita el atributo para Grafito en vez de ponerlo en "graphite": así la
 * hoja de tokens es la única que define el tema por defecto.
 */
export function applyTheme(name, { root = globalThis.document?.documentElement, storage = globalThis.localStorage } = {}) {
    const theme = normalizeTheme(name);
    if (root) {
        if (theme === NIGHT) root.setAttribute('data-theme', NIGHT);
        else root.removeAttribute('data-theme');
    }
    storeTheme(theme, storage);
    return theme;
}

/** The option list the Sistema control offers, in the order it shows them. */
export const THEME_OPTIONS = [
    { name: 'graphite', label: 'Grafito' },
    { name: NIGHT, label: 'Noche' },
];

export { THEMES };
