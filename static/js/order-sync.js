(() => {
  const form = document.querySelector("form[method='post']");
  const status = document.getElementById("order-sync-status");
  if (!form || !status) return;

  const databaseName = "nitt-sales-orders";
  const storeName = "outbox";
  const uuidField = form.querySelector("input[name='client_uuid']");
  const serviceWorkerRegistration = "serviceWorker" in navigator
    ? navigator.serviceWorker.register("/service-worker.js").catch(() => null)
    : Promise.resolve(null);
  const newUuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };

  function showStatus(message, kind = "info") {
    status.textContent = message;
    status.className = `alert alert-${kind}`;
  }

  function openDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(databaseName, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore(storeName, { keyPath: "id" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function saveOrder(order) {
    const database = await openDatabase();
    await new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, "readwrite");
      transaction.objectStore(storeName).put(order);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
    database.close();
    const registration = await serviceWorkerRegistration;
    if (registration && "sync" in registration) {
      await registration.sync.register("sync-sales-orders");
    }
  }

  async function readOrders(database) {
    return new Promise((resolve, reject) => {
      const request = database.transaction(storeName).objectStore(storeName).getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function removeOrder(database, id) {
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, "readwrite");
      transaction.objectStore(storeName).delete(id);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
  }

  async function flushQueue() {
    if (!navigator.onLine) return;
    const database = await openDatabase();
    const orders = await readOrders(database);
    for (const order of orders) {
      try {
        const response = await fetch(order.url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: new URLSearchParams(order.data)
        });
        const destination = new URL(response.url);
        if (!response.ok || !response.redirected || destination.pathname !== "/orders/") {
          showStatus(`Order ${order.id} is still queued; sync returned ${response.status}. Sign in and retry.`, "warning");
          break;
        }
        await removeOrder(database, order.id);
        showStatus(`Queued order ${order.id} synchronized.`, "success");
      } catch {
        showStatus(`Order ${order.id} is still queued. Check your connection and sign in again if needed.`, "warning");
        break;
      }
    }
    database.close();
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const formData = new FormData(form);
    const order = {
      id: uuidField.value,
      url: form.action || window.location.href,
      data: Array.from(formData.entries()),
      createdAt: new Date().toISOString()
    };

    if (navigator.onLine) {
      try {
        const response = await fetch(order.url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: new URLSearchParams(order.data)
        });
        if (response.redirected) {
          window.location.assign(response.url);
        } else {
          const html = await response.text();
          document.open();
          document.write(html);
          document.close();
        }
        return;
      } catch {
        showStatus("Connection lost. Saving this order on the device.", "warning");
      }
    }

    try {
      await saveOrder(order);
      form.reset();
      uuidField.value = newUuid();
      showStatus("Order saved on this device. It will sync when a connection returns.", "success");
    } catch {
      showStatus("This browser could not save the order offline. Keep the page open and retry online.", "danger");
    }
  });

  window.addEventListener("online", flushQueue);
  serviceWorkerRegistration.then(registration => {
    if (registration) {
      flushQueue();
    } else {
      showStatus("Background sync is unavailable in this browser; online orders still work.", "warning");
    }
  });
})();
