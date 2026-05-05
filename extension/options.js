const input = document.getElementById("backend");
const status = document.getElementById("status");

chrome.storage.local.get(["backend"]).then(({ backend }) => {
  input.value = backend || "http://localhost:8001";
});

document.getElementById("save").addEventListener("click", async () => {
  const value = input.value.trim() || "http://localhost:8001";
  await chrome.storage.local.set({ backend: value });
  status.textContent = `Saved: ${value}`;
});
