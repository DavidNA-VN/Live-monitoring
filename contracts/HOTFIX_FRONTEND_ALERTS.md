# Hotfix: Dashboard khong hien thi alert

## Hien tuong

Alert da duoc ghi vao Redis outbox va REST history tra ve dung, nhung Dashboard
khong hien thi alert sau khi ket noi stream.

## Nguyen nhan

1. `AlertClient` chi tai history sau khi WebSocket mo thanh cong. Neu WebSocket
   chua ket noi duoc, UI khong co du lieu du REST API van hoat dong.
2. `MediaSession.start()` chay truoc `AlertClient.start()`. Loi HLS, autoplay hoac
   Web Audio co the dung flow truoc khi alert subscription duoc khoi tao.
3. Khi reconnect mot stream dang `PAUSED`, frontend chi cho trang thai `RUNNING`
   di tiep nen khong gan alert client.
4. Browser co the giu JavaScript cu neu API process chua duoc restart sau hotfix.
5. Runtime chi cai `uvicorn` toi thieu, khong co `websockets` hoac `wsproto`, nen
   WebSocket upgrade bi xu ly nhu HTTP va tra ve 404.

## Thay doi

- `AlertClient.start()` tai recent alert history ngay, doc lap voi WebSocket.
- Lifecycle khoi dong alert client va status polling truoc media player.
- Loi media player duoc log rieng va khong chan alert.
- START chap nhan runtime da o `RUNNING` hoac `PAUSED`; ca hai deu gan alert
  history va WebSocket.
- Static responses co `Cache-Control: no-cache, no-store, must-revalidate`.
- Entry module va cac module import truc tiep deu co version query de browser
  khong tai lai dependency JavaScript cu tu module cache.
- Khai bao `websockets` la runtime dependency de Uvicorn phuc vu ASGI WebSocket.

## Xac minh

- Frontend regression: history duoc tai khi WebSocket chua mo.
- Frontend regression: loi media startup khong chan alert subscription.
- Presentation API test: Dashboard response co no-cache header.
- Redis alert history van la source de bu gap khi WebSocket reconnect.

## Van hanh sau khi cap nhat

API process phai duoc restart de nap middleware moi. Sau do hard refresh browser
(`Ctrl+F5`) va Connect lai stream. Redis, worker va API phai cung dung mot
`REDIS_PREFIX`; HLS server chi can thiet cho player va worker doc media.
