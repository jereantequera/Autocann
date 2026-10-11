/**
 * Modal open/close.
 *
 * Its own module so that analytics does not have to import grows just to open
 * the weekly report.
 */

function showModal(modalId) {
    document.getElementById(modalId).classList.add('show');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('show');
}

/** Close whichever modal is on top; used by the Escape handler. */
function closeTopModal() {
    const open = Array.from(document.querySelectorAll('.modal.show')).pop();
    if (open) open.classList.remove('show');
    return Boolean(open);
}

export { showModal, closeModal, closeTopModal };
