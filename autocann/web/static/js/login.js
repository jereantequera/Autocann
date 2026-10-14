/**
 * The PIN form.
 *
 * Its own entry point: login.html is a separate document, and loading main.js
 * here would start a dozen pollers against endpoints that are all answering
 * 401 until this form succeeds.
 */

const form = document.getElementById('login-form');
const field = document.getElementById('login-pin');
const button = document.getElementById('login-submit');
const error = document.getElementById('login-error');

function showError(message) {
    error.textContent = message;
    error.hidden = !message;
    // Cleared rather than left behind: retyping over a wrong PIN character by
    // character is how you end up with half of the old one still in the box.
    field.value = '';
    field.focus();
}

form?.addEventListener('submit', async event => {
    event.preventDefault();
    const pin = field.value.trim();
    if (!pin) return;

    button.disabled = true;
    error.hidden = true;
    try {
        const response = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin }),
        });
        const data = await response.json().catch(() => ({}));
        if (response.ok) {
            // Replace, not assign: the login page should not sit in the back
            // stack waiting to be returned to by a swipe.
            window.location.replace('/');
            return;
        }
        showError(data.error || `No se pudo entrar (HTTP ${response.status})`);
    } catch (e) {
        showError('No se pudo contactar al backend');
    } finally {
        button.disabled = false;
    }
});
