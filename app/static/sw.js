"use strict";

const VERSION = "20260820.7";
const CACHE_NAME = "vocabtool-shell-" + VERSION;
const OFFLINE_URL = "/static/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add(OFFLINE_URL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // API 和登录态相关请求永远走网络，不缓存用户数据。
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    // 把 ?v= 版本参数计入缓存键：模板升级版本号后，浏览器会直接拉新文件，
    // 不再依赖 VERSION 整体刷新（VERSION 只负责升级 sw 本身）。
    const cacheRequest = request;
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(cacheRequest).then((cached) => {
          if (cached) return cached;
          return fetch(cacheRequest).then((response) => {
            if (response.ok) cache.put(cacheRequest, response.clone());
            return response;
          });
        })
      )
    );
  }
});
