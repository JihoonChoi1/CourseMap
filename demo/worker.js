// Runs the Go engine (coursemap.wasm) off the main thread, so a slow search never freezes the page.
// Messages in: {id, snapshot}. Messages out: {ready: true}, {id, result, ms}, or {loadError}.
importScripts("wasm_exec.js");

const go = new Go();

function instantiate() {
  return WebAssembly.instantiateStreaming(fetch("coursemap.wasm"), go.importObject).catch(function () {
    // some servers send .wasm without the application/wasm type, which instantiateStreaming rejects
    return fetch("coursemap.wasm")
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (bytes) { return WebAssembly.instantiate(bytes, go.importObject); });
  });
}

const ready = instantiate().then(function (m) {
  go.run(m.instance); // never resolves: main() blocks so coursemapPlan stays callable
  postMessage({ ready: true });
});

ready.catch(function (e) { postMessage({ loadError: String(e) }); });

onmessage = function (e) {
  ready.then(function () {
    const start = performance.now();
    const result = self.coursemapPlan(e.data.snapshot);
    postMessage({ id: e.data.id, result: result, ms: performance.now() - start });
  });
};
