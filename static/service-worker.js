const DATABASE_NAME = "nitt-sales-orders";
const STORE_NAME = "outbox";

self.addEventListener("install", event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("sync", event => {
  if (event.tag === "sync-sales-orders") {
    event.waitUntil(syncQueuedOrders());
  }
});

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE_NAME, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function queuedOrders(database) {
  return new Promise((resolve, reject) => {
    const request = database.transaction(STORE_NAME).objectStore(STORE_NAME).getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function removeOrder(database, id) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(id);
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
}

async function syncQueuedOrders() {
  const database = await openDatabase();
  const orders = await queuedOrders(database);
  for (const order of orders) {
    const body = new URLSearchParams(order.data);
    const response = await fetch(order.url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body
    });
    const destination = new URL(response.url);
    if (!response.ok || !response.redirected || destination.pathname !== "/orders/") {
      throw new Error(`Order ${order.id} could not be synchronized (${response.status}).`);
    }
    await removeOrder(database, order.id);
  }
  database.close();
}
