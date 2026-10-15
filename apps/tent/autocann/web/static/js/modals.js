/**
 * Modals: opening, closing, where the focus goes — and the dialogs that
 * replaced `confirm()` and `alert()`.
 *
 * Its own module so that analytics does not have to import grows just to open
 * the weekly report, and so that nothing else has to know how a dialog is
 * built.
 *
 * Two things live here that used not to:
 *
 * - **The focus stays inside an open modal, and goes back where it came from.**
 *   Without it, tabbing out of a dialog lands on the buttons behind it: a
 *   keyboard or screen-reader user is operating the page the modal is covering,
 *   with no way to tell. And closing one dropped focus on `<body>`, so the next
 *   Tab started from the top of the document rather than from the button that
 *   had just been pressed.
 * - **`confirm()` and `alert()` are gone.** They freeze the whole tab — every
 *   poller included — until somebody clicks, they cannot be styled, and the
 *   confirm button says "OK" on a dialog about ending a grow. A button on a
 *   destructive action has to say what it does.
 */

/** Where focus was when each modal opened, so it can be put back. */
const openers = new Map();

/**
 * What counts as focusable for the trap.
 *
 * `:not([disabled])` matters: the save button is disabled while a request is
 * in flight, and Tab would otherwise stop on something that cannot be pressed.
 */
const FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(', ');

function focusableIn(modal) {
    return Array.from(modal.querySelectorAll(FOCUSABLE))
        // An element inside a hidden branch of the dialog — the cancel button
        // of an alert — is not somewhere Tab may land.
        .filter(el => el.offsetParent !== null || el === document.activeElement);
}

/** The modal on top, or null when none is open. */
function topModal() {
    return Array.from(document.querySelectorAll('.modal.show')).pop() || null;
}

function showModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    // Stored before the class flips: afterwards the page may have moved focus.
    openers.set(modalId, document.activeElement);
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');

    // The first field, not the dialog box: somebody opening "Nuevo cultivo"
    // wants to type a name, and the first thing a screen reader should read is
    // what it is being asked for.
    const focusable = focusableIn(modal);
    (focusable[0] || modal).focus();
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');

    const opener = openers.get(modalId);
    openers.delete(modalId);
    // Only if it is still in the page and still focusable: the grow list
    // re-renders after a grow is activated, and the button that opened the
    // dialog may not exist any more.
    if (opener && opener.isConnected && typeof opener.focus === 'function') {
        opener.focus();
    }
}

/** Close whichever modal is on top; used by the Escape handler. */
function closeTopModal() {
    const open = topModal();
    if (!open) return false;
    closeModal(open.id);
    return true;
}

/**
 * Where Tab should land, given what is focusable and where focus is now.
 *
 * Pure, and exported, because this is the whole of the trap worth getting
 * right and the only part that can be tested without a browser. Returns the
 * element to move focus to, or `null` to let the browser do what it was going
 * to — which is the right answer in the middle of the list, where the native
 * order is already correct.
 *
 * Three cases, and the third is the one that is usually missed:
 *
 * - Tab on the last element wraps to the first;
 * - **Shift+Tab on the first wraps to the last.** Only fixing the forward
 *   direction is how a trap ends up letting people out backwards;
 * - focus that is not in the list at all — it escaped through a click on the
 *   page behind, or the element it was on was re-rendered away — comes back to
 *   the first rather than being allowed to walk off from wherever it is.
 *
 * @param {Array} focusable  the modal's focusable elements, in document order
 * @param {*} active  whatever has focus right now
 * @param {boolean} shiftKey
 */
function nextFocus(focusable, active, shiftKey = false) {
    if (!focusable || !focusable.length) return null;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (!focusable.includes(active)) return first;
    if (shiftKey && active === first) return last;
    if (!shiftKey && active === last) return first;
    return null;
}

/** Keep Tab inside the modal on top. */
function trapTab(event) {
    const modal = topModal();
    if (!modal) return;

    const focusable = focusableIn(modal);
    if (!focusable.length) {
        // Nothing to tab to, but the page behind is still not somewhere Tab
        // may go while a modal is over it.
        event.preventDefault();
        return;
    }

    const target = nextFocus(focusable, document.activeElement, event.shiftKey);
    if (target) {
        event.preventDefault();
        target.focus();
    }
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------
// One shell in the template, filled in here. The text is written with
// textContent, never innerHTML: some of it is a server error message, and a
// dialog is the last place to open an injection hole.

const DIALOG_ID = 'appDialog';

/** Resolves the dialog currently open, or null. */
let pendingDialog = null;

function settleDialog(answer) {
    const resolve = pendingDialog;
    pendingDialog = null;
    closeModal(DIALOG_ID);
    if (resolve) resolve(answer);
}

function openDialog({ title, message, confirmLabel, cancelLabel, danger }) {
    const modal = document.getElementById(DIALOG_ID);
    if (!modal) {
        // No shell in the page: resolving false rather than throwing, so a
        // destructive action never goes ahead because its confirmation failed
        // to render.
        return Promise.resolve(false);
    }
    // A second dialog over the first: answer the first as a cancel rather than
    // leaving its promise hanging forever.
    if (pendingDialog) settleDialog(false);

    modal.querySelector('#dialog-title').textContent = title;
    modal.querySelector('#dialog-message').textContent = message;

    const confirm = modal.querySelector('#dialog-confirm');
    const cancel = modal.querySelector('#dialog-cancel');
    confirm.textContent = confirmLabel;
    confirm.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
    cancel.textContent = cancelLabel || 'Cancelar';
    cancel.hidden = !cancelLabel;

    const answer = new Promise(resolve => { pendingDialog = resolve; });
    showModal(DIALOG_ID);
    // The cancel button, not the confirm one: the dialog asks about something
    // destructive, and Enter on an autofocused "Finalizar cultivo" is exactly
    // the accident it exists to prevent.
    (cancelLabel ? cancel : confirm).focus();
    return answer;
}

/**
 * Ask before something destructive. Resolves true when it was confirmed.
 *
 * `confirmLabel` is required and has no default on purpose: "OK" is what the
 * native dialog said, and the whole point is that the button names the act —
 * "Finalizar cultivo", "Borrar riego", "Pausar el sistema".
 */
function confirmDialog({ title, message, confirmLabel, cancelLabel = 'Cancelar',
                         danger = true }) {
    return openDialog({ title, message, confirmLabel, cancelLabel, danger });
}

/** Say something that has to be acknowledged. Resolves when it is dismissed. */
function alertDialog({ title, message, label = 'Entendido' }) {
    return openDialog({ title, message, confirmLabel: label, cancelLabel: null,
                        danger: false });
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------
// What replaces `alert('✅ ...')`. A thing that worked does not need to be
// acknowledged: swapping a blocking native dialog for a blocking custom one
// would have kept the part people actually complained about. Errors still get
// a dialog, because an error that scrolls away unread is an error nobody saw.

const TOAST_MS = 4000;

function toast(message, { kind = 'ok', ms = TOAST_MS } = {}) {
    const tray = document.getElementById('toast-tray');
    if (!tray) return null;

    const node = document.createElement('div');
    node.className = `toast toast-${kind}`;
    node.textContent = message;
    tray.appendChild(node);
    setTimeout(() => node.remove(), ms);
    return node;
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function initModals() {
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            // A dialog closes as a cancel, so its promise resolves instead of
            // leaving the caller waiting for an answer that never comes.
            if (pendingDialog && topModal()?.id === DIALOG_ID) {
                event.preventDefault();
                settleDialog(false);
                return;
            }
            if (closeTopModal()) event.preventDefault();
            return;
        }
        if (event.key === 'Tab') trapTab(event);
    });

    // Click on the backdrop closes the modal — through closeModal, so focus
    // still goes back to whatever opened it.
    window.addEventListener('click', event => {
        if (!event.target.classList?.contains('modal')) return;
        if (event.target.id === DIALOG_ID) settleDialog(false);
        else closeModal(event.target.id);
    });

    const dialog = document.getElementById(DIALOG_ID);
    if (!dialog) return;
    dialog.querySelector('#dialog-confirm').addEventListener('click', () => settleDialog(true));
    dialog.querySelector('#dialog-cancel').addEventListener('click', () => settleDialog(false));
}

export {
    alertDialog, closeModal, closeTopModal, confirmDialog, initModals, nextFocus,
    showModal, toast,
};
