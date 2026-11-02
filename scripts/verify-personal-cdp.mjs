// Guest-only probe of the real T3 renderer; Node supplies fetch and WebSocket.
const port = process.argv[2];
if (!/^\d+$/.test(port ?? "")) throw new Error("Expected a local debugging port");
const response = await fetch(`http://127.0.0.1:${port}/json/list`, {
  signal: AbortSignal.timeout(2000),
});
if (!response.ok) throw new Error(`Debugging endpoint returned ${response.status}`);
const pages = await response.json();
const page = pages.find((entry) => entry.type === "page" &&
  /^(t3code:|https?:|file:)/.test(entry.url) && entry.webSocketDebuggerUrl);
if (!page) throw new Error("No T3 application renderer found");

const socket = new WebSocket(page.webSocketDebuggerUrl);
const timeout = setTimeout(() => {
  console.error("T3 renderer probe timed out");
  process.exit(1);
}, 5000);
try {
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const result = new Promise((resolve, reject) => {
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== 1) return;
      if (message.error || message.result?.exceptionDetails) {
        reject(new Error(JSON.stringify(message)));
      } else {
        resolve(message.result.result.value);
      }
    });
  });
  socket.send(JSON.stringify({
    id: 1,
    method: "Runtime.evaluate",
    params: {
      returnByValue: true,
      expression: `(() => {
        if (!window.__duxAppearanceProbe) {
          const media = matchMedia('(prefers-color-scheme: dark)');
          window.__duxAppearanceProbe = { media, changes: 0 };
          media.addEventListener('change', () => window.__duxAppearanceProbe.changes++);
        }
        const probe = window.__duxAppearanceProbe;
        return {
          scheme: probe.media.matches ? 'dark' : 'light',
          changes: probe.changes,
          darkClass: document.documentElement.classList.contains('dark'),
          background: getComputedStyle(document.body).backgroundColor,
          ready: document.readyState,
          url: location.href
        };
      })()`,
    },
  }));
  console.log(JSON.stringify(await result));
} finally {
  clearTimeout(timeout);
  socket.close();
}
