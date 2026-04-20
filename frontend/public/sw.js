// Kill Switch Service Worker
// 기존 등록된 SW를 자동 해제하고 캐시를 삭제합니다.
// FDA 챗봇은 실시간 LLM 응답이 필요하므로 오프라인 캐싱은 잘못된 정보를 반환하는 역효과가 있어 제거됨.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // 1. 모든 캐시 삭제
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));

    // 2. 자기 자신 unregister
    await self.registration.unregister();

    // 3. 모든 클라이언트 새로고침
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach((client) => client.navigate(client.url));
  })());
});
