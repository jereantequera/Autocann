/**
 * The line a form shows when the server refuses what it sent.
 *
 * The API answers with an English `error` (or `errors`) for logs, and a `code`
 * for the cases a form can show (see autocann/web/errors.py). This maps the
 * code to a Spanish sentence. Anything else — no code, or a code not listed
 * here — gets the generic line, never the English text.
 */

const MESSAGES = {
    invalid_stage: 'Esa etapa no existe en el perfil elegido.',
    no_active_grow: 'No hay un cultivo activo.',
    unknown_profile: 'Ese perfil ya no existe.',
    invalid_name: 'Ponele un nombre.',
    invalid_body: 'Faltan datos en el formulario.',
    missing_field: 'Faltan datos en el formulario.',
    invalid_interval: 'Ese intervalo no existe.',
    invalid_stages: 'Revisá las etapas del perfil.',
    read_only_profile: 'Ese perfil no se puede cambiar.',
    profile_in_use: 'Ese perfil está en uso por un cultivo.',
    invalid_mode: 'Ese modo no existe.',
    unknown_output: 'Esa salida no existe.',
    invalid_state: 'Elegí encendido o apagado.',
    invalid_duration: 'La duración no es válida.',
    not_paused: 'Los relés sólo se mueven a mano con el sistema en pausa.',
    invalid_litres: 'Los litros tienen que ser un número mayor a 0.',
    invalid_items: 'Los productos del riego no tienen un formato válido.',
    irrigation_not_found: 'Ese riego ya no existe.',
    invalid_offset: 'El ajuste tiene que ser un número.',
    offset_out_of_range: 'Ese ajuste está fuera de rango.',
    save_failed: 'No se pudo guardar. Probá de nuevo.',
    unknown_button: 'Ese botón no existe en el control.',
    humidifier_off: 'El humidificador está sin energía: el relé está apagado.',
    ir_busy: 'Todavía se está mandando el botón anterior.',
};

export const FALLBACK_MESSAGE = 'No se pudo guardar. Probá de nuevo.';

/** The Spanish line for a server response body, judged by its `code` alone. */
export function errorMessage(result) {
    const code = result?.code;
    return typeof code === 'string' && Object.hasOwn(MESSAGES, code)
        ? MESSAGES[code]
        : FALLBACK_MESSAGE;
}
