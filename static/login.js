async function handleGoogleCredential(response) {
  const status = document.getElementById("login-status");
  status.textContent = "Anmeldung wird geprüft ...";
  try {
    const result = await fetch("/auth/callback", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
      },
      body: JSON.stringify({ credential: response.credential }),
    });
    if (!result.ok) {
      status.textContent = "Anmeldung fehlgeschlagen. Bitte lade die Seite neu und versuche es erneut.";
      return;
    }
    const data = await result.json();
    window.location.assign(data.redirect);
  } catch {
    status.textContent = "Der Server ist gerade nicht erreichbar. Bitte versuche es erneut.";
  }
}
