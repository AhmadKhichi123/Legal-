// Shows an "Install app" button when the browser allows the site to be installed.
if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
}

let installPrompt = null;
const installButton = document.getElementById("install-button");

window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();          // keep the prompt for when the button is clicked
    installPrompt = event;
    if (installButton) installButton.hidden = false;
});

if (installButton) {
    installButton.addEventListener("click", async () => {
        if (!installPrompt) return;
        installPrompt.prompt();
        await installPrompt.userChoice;
        installPrompt = null;
        installButton.hidden = true;
    });
}

window.addEventListener("appinstalled", () => {
    if (installButton) installButton.hidden = true;
});
