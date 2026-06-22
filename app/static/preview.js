const buttons = Array.from(document.querySelectorAll("[data-process-url]"));
const statusPanel = document.querySelector("#process-status");

if (buttons.length) {
  const processUrl = buttons[0].dataset.processUrl;
  let processing = false;

  const startProcessing = async () => {
    if (processing) {
      return;
    }
    processing = true;
    buttons.forEach((button) => {
      button.disabled = true;
      button.textContent = "Processing...";
    });
    statusPanel.hidden = false;

    try {
      const response = await fetch(processUrl, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Processing failed: ${response.status}`);
      }
      const payload = await response.json();
      window.location.href = payload.redirect_url;
    } catch (error) {
      statusPanel.hidden = true;
      buttons.forEach((button) => {
        button.disabled = false;
        button.textContent = button.id === "process-button-secondary" ? "Process receipt" : "Process";
      });
      processing = false;
      alert(error.message || "Processing failed.");
    }
  };

  buttons.forEach((button) => {
    button.addEventListener("click", startProcessing);
  });
}
