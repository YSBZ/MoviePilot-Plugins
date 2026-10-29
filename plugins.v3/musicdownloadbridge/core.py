"""HTTP client and persistent request state for the Music Download Bridge."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from threading import Lock, RLock
from time import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests


class BridgeError(RuntimeError):
    """A safe, user-visible error from the music service integration."""


class BridgeHTTPError(BridgeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"音乐服务返回 HTTP {status_code}")


class BridgeNotFound(BridgeError):
    """The server has not seen a request ID yet."""


class BridgeResponseError(BridgeError):
    """The server response did not match the JSON API contract."""


class SessionUnavailable(BridgeError):
    """An expired, unknown, or non-owned session/request."""


class MusicBridgeClient:
    """Bounded synchronous requests client for the bridge HTTP contract."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: tuple[float, float] = (3.05, 8.0),
        session: requests.Session | None = None,
    ) -> None:
        base_url = str(base_url or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("服务地址必须以 http:// 或 https:// 开头")
        if not token or not str(token).strip():
            raise ValueError("必须配置 Bearer token")
        if len(timeout) != 2 or timeout[0] <= 0 or timeout[1] <= 0:
            raise ValueError("HTTP timeout 必须是正数")

        self._api_url = f"{base_url}/api/music-bridge"
        self._token = str(token).strip()
        self._timeout = timeout
        self._session = session or requests.Session()
        self._closed = False
        self._lock = Lock()

    def search(
        self,
        query: str,
        sources: Sequence[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "sources": ",".join(str(source).strip() for source in (sources or []) if str(source).strip()),
            "limit": max(1, min(int(limit), 10)),
        }
        payload = self._request("GET", "/search", params=params)
        songs = payload.get("songs")
        if not isinstance(songs, list):
            raise BridgeResponseError("音乐搜索响应缺少 songs 列表")
        validated: list[dict[str, Any]] = []
        for song in songs[:10]:
            if not isinstance(song, Mapping):
                raise BridgeResponseError("音乐搜索响应包含无效候选")
            item = dict(song)
            if not item.get("id") or not item.get("source") or not item.get("name"):
                raise BridgeResponseError("音乐搜索候选缺少 id、source 或 name")
            item.setdefault("artist", "")
            item.setdefault("album", "")
            item.setdefault("cover", "")
            item.setdefault("extra", "{}")
            if not isinstance(item["extra"], str):
                raise BridgeResponseError("音乐搜索候选 extra 必须是 JSON 字符串")
            validated.append(item)
        return validated

    def submit_download(self, request_id: str, song: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/downloads",
            json={"request_id": request_id, "song": dict(song)},
        )
        if str(payload.get("request_id") or "") != request_id:
            raise BridgeResponseError("下载提交响应的 request_id 不匹配")
        if not payload.get("status"):
            raise BridgeResponseError("下载提交响应缺少 status")
        return payload

    def download_status(self, request_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/downloads/{quote(request_id, safe='')}")
        if str(payload.get("request_id") or "") != request_id:
            raise BridgeResponseError("下载状态响应的 request_id 不匹配")
        if not payload.get("status"):
            raise BridgeResponseError("下载状态响应缺少 status")
        return payload

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._session.close()

    def redact(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value).replace(self._token, "[已隐藏]")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise BridgeError("音乐服务客户端已关闭")
            try:
                response = self._session.request(
                    method,
                    f"{self._api_url}{path}",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                    timeout=self._timeout,
                    allow_redirects=False,
                    **kwargs,
                )
            except requests.RequestException as error:
                # Do not include the request repr or response body: either could
                # contain user data or an echoed credential.
                raise BridgeError(
                    f"音乐服务连接失败（{type(error).__name__}）"
                ) from None

            try:
                if response.status_code == 404 and method == "GET" and path.startswith("/downloads/"):
                    raise BridgeNotFound("服务端尚无此下载请求")
                if not 200 <= response.status_code < 300:
                    raise BridgeHTTPError(response.status_code)
                try:
                    payload = response.json()
                except (ValueError, requests.JSONDecodeError):
                    raise BridgeResponseError("音乐服务返回的不是有效 JSON") from None
                if not isinstance(payload, dict):
                    raise BridgeResponseError("音乐服务响应必须是 JSON 对象")
                return payload
            finally:
                response.close()


def _normalize_identity(identity: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key in ("user", "source", "channel"):
        value = identity.get(key)
        if value is None:
            raise SessionUnavailable("此消息缺少用户、来源或渠道身份，不能执行下载")
        value = getattr(value, "value", value)
        text = str(value).strip()
        if not text:
            raise SessionUnavailable("此消息缺少用户、来源或渠道身份，不能执行下载")
        normalized[key] = text
    return normalized


class MusicDownloadService:
    """Own search sessions, idempotent selections, status polling and ACLs."""

    _TERMINAL_STATUSES = {"completed", "failed", "skipped", "canceled"}

    def __init__(
        self,
        client: MusicBridgeClient,
        load_state: Callable[[], Any],
        save_state: Callable[[dict[str, Any]], None],
        *,
        now: Callable[[], float] = time,
        session_ttl_seconds: int = 900,
        request_retention_seconds: int = 7 * 24 * 60 * 60,
        max_sessions: int = 50,
        max_requests: int = 200,
        poll_batch_size: int = 5,
    ) -> None:
        self._client = client
        self._load_state = load_state
        self._save_state = save_state
        self._now = now
        self._session_ttl = max(60, int(session_ttl_seconds))
        self._request_retention = max(300, int(request_retention_seconds))
        self._max_sessions = max(1, int(max_sessions))
        self._max_requests = max(1, int(max_requests))
        self._poll_batch_size = max(1, int(poll_batch_size))
        self._lock = RLock()
        self._state = self._read_state()

    def search(
        self,
        query: str,
        sources: Sequence[str] | None,
        limit: int,
        identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner = _normalize_identity(identity)
        query = str(query or "").strip()
        if not query:
            raise BridgeError("请输入要搜索的歌名或歌手")
        if len(query) > 200:
            raise BridgeError("搜索内容不能超过 200 个字符")

        songs = self._client.search(query, sources, limit)
        now = self._now()
        with self._lock:
            self._prune(now)
            session_id = uuid4().hex[:12]
            self._state["sessions"][session_id] = {
                "identity": owner,
                "songs": songs,
                "created_at": now,
                "expires_at": now + self._session_ttl,
                "selection_requests": {},
            }
            self._prune(now)
            self._persist()
            return {
                "session_id": session_id,
                "songs": deepcopy(songs),
                "expires_at": now + self._session_ttl,
            }

    def select(
        self,
        session_id: str,
        index: int,
        identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner = _normalize_identity(identity)
        try:
            choice = int(index)
        except (TypeError, ValueError):
            raise BridgeError("候选编号必须是正整数") from None
        now = self._now()
        with self._lock:
            self._prune(now)
            session = self._state["sessions"].get(str(session_id))
            if not session or session["expires_at"] <= now or session["identity"] != owner:
                raise SessionUnavailable("会话不存在、已过期或不属于当前用户")
            if choice < 1 or choice > len(session["songs"]):
                raise BridgeError("候选编号无效，请重新搜索并使用列表中的编号")

            choice_key = str(choice)
            existing_id = session["selection_requests"].get(choice_key)
            existing = self._state["requests"].get(existing_id) if existing_id else None
            if existing and existing.get("status") != "failed":
                return self._public_request(existing)
            if len(self._state["requests"]) >= self._max_requests:
                self._prune(now, force_request_trim=True)
                if len(self._state["requests"]) >= self._max_requests:
                    raise BridgeError("待查询下载任务已达上限，请稍后再试")

            request_id = uuid4().hex
            song = deepcopy(session["songs"][choice - 1])
            record = {
                "request_id": request_id,
                "session_id": str(session_id),
                "choice": choice,
                "identity": owner,
                "song": song,
                "status": "submitting",
                "path": None,
                "error": None,
                "warning": None,
                "created_at": now,
                "updated_at": now,
                "notified": False,
            }
            self._state["requests"][request_id] = record
            session["selection_requests"][choice_key] = request_id
            # The idempotency key is durable before the HTTP call. A restart can
            # query or safely resubmit this same request_id if the POST was lost.
            self._persist()

        try:
            remote = self._client.submit_download(request_id, song)
            return self._apply_remote(request_id, remote)
        except BridgeError as error:
            failed = (
                isinstance(error, BridgeHTTPError)
                and 400 <= error.status_code < 500
                and error.status_code not in (408, 429)
            )
            return self._update_error(request_id, error, failed=failed)

    def get_request(self, request_id: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        owner = _normalize_identity(identity)
        now = self._now()
        with self._lock:
            self._prune(now)
            record = self._state["requests"].get(str(request_id))
            if not record or record["identity"] != owner:
                raise SessionUnavailable("未找到此用户可查看的下载任务")
        try:
            remote = self._client.download_status(str(request_id))
        except BridgeError as error:
            return self._update_error(str(request_id), error, failed=False)
        return self._apply_remote(str(request_id), remote)

    def poll_pending(self) -> list[dict[str, Any]]:
        now = self._now()
        with self._lock:
            self._prune(now)
            pending = sorted(
                (
                    record
                    for record in self._state["requests"].values()
                    if record.get("status") not in self._TERMINAL_STATUSES
                ),
                key=lambda record: (record.get("created_at", 0), record["request_id"]),
            )[: self._poll_batch_size]
            request_ids = [record["request_id"] for record in pending]
            if request_ids:
                self._persist()

        results: list[dict[str, Any]] = []
        for request_id in request_ids:
            try:
                remote = self._client.download_status(request_id)
            except BridgeNotFound:
                # Covers a process restart or lost POST response. The server
                # contract treats a repeated request_id as the same operation.
                with self._lock:
                    record = self._state["requests"].get(request_id)
                    song = deepcopy(record["song"]) if record else None
                if not record or not song:
                    continue
                try:
                    remote = self._client.submit_download(request_id, song)
                except BridgeError as error:
                    results.append(self._update_error(
                        request_id, error, failed=isinstance(error, BridgeHTTPError)
                        and 400 <= error.status_code < 500
                        and error.status_code not in (408, 429),
                    ))
                    continue
            except BridgeError as error:
                results.append(self._update_error(
                    request_id, error, failed=isinstance(error, BridgeHTTPError)
                    and 400 <= error.status_code < 500
                    and error.status_code not in (408, 429),
                ))
                continue
            results.append(self._apply_remote(request_id, remote))
        return results

    def pending_notifications(self) -> list[dict[str, Any]]:
        """Recover persisted terminal results not yet acknowledged as notified."""
        with self._lock:
            return [
                self._public_request(record)
                for record in self._state["requests"].values()
                if record.get("status") in self._TERMINAL_STATUSES
                and not record.get("notified")
            ][:self._poll_batch_size]

    def mark_notified(self, request_id: str) -> None:
        with self._lock:
            record = self._state["requests"].get(str(request_id))
            if record and not record.get("notified"):
                record["notified"] = True
                record["updated_at"] = self._now()
                self._persist()

    def notification_target(self, request_id: str) -> dict[str, Any] | None:
        """Return the stored, verified destination for one terminal notification."""
        with self._lock:
            record = self._state["requests"].get(str(request_id))
            if (
                not record
                or record.get("status") not in self._TERMINAL_STATUSES
                or record.get("notified")
            ):
                return None
            return {
                "identity": deepcopy(record["identity"]),
                "request": self._public_request(record),
            }

    def _apply_remote(self, request_id: str, remote: Mapping[str, Any]) -> dict[str, Any]:
        raw_status = str(remote.get("status") or "unknown").strip().lower()
        if raw_status in {"complete", "completed", "success", "succeeded", "done"}:
            status = "completed"
        elif raw_status in {"failed", "error"}:
            status = "failed"
        elif raw_status in {"cancelled", "canceled"}:
            status = "canceled"
        elif raw_status in {"skipped", "skip"}:
            status = "skipped"
        else:
            status = raw_status or "unknown"
        with self._lock:
            record = self._state["requests"].get(request_id)
            if not record:
                raise SessionUnavailable("下载任务不存在")
            record.update(
                {
                    "status": status,
                    "path": self._client.redact(remote.get("path")),
                    "error": self._client.redact(remote.get("error")),
                    "warning": self._client.redact(remote.get("warning")),
                    "updated_at": self._now(),
                }
            )
            self._persist()
            return self._public_request(record)

    def _update_error(self, request_id: str, error: BridgeError, *, failed: bool) -> dict[str, Any]:
        with self._lock:
            record = self._state["requests"].get(request_id)
            if not record:
                raise SessionUnavailable("下载任务不存在")
            record.update(
                {
                    "status": "failed" if failed else "unknown",
                    "error": self._client.redact(str(error)),
                    "updated_at": self._now(),
                }
            )
            self._persist()
            return self._public_request(record)

    def _read_state(self) -> dict[str, dict[str, Any]]:
        loaded = self._load_state()
        if not isinstance(loaded, Mapping):
            loaded = {}
        sessions = loaded.get("sessions")
        requests_state = loaded.get("requests")
        return {
            "sessions": dict(sessions) if isinstance(sessions, Mapping) else {},
            "requests": dict(requests_state) if isinstance(requests_state, Mapping) else {},
        }

    def _prune(self, now: float, *, force_request_trim: bool = False) -> None:
        sessions = self._state["sessions"]
        for session_id, session in list(sessions.items()):
            if not isinstance(session, Mapping) or float(session.get("expires_at", 0)) <= now:
                del sessions[session_id]
        if len(sessions) > self._max_sessions:
            oldest = sorted(
                sessions,
                key=lambda key: float(sessions[key].get("created_at", 0)),
            )[: len(sessions) - self._max_sessions]
            for session_id in oldest:
                del sessions[session_id]

        requests_state = self._state["requests"]
        for request_id, record in list(requests_state.items()):
            if not isinstance(record, Mapping):
                del requests_state[request_id]
                continue
            if float(record.get("updated_at", record.get("created_at", 0))) + self._request_retention <= now:
                del requests_state[request_id]

        if force_request_trim and len(requests_state) >= self._max_requests:
            terminal = sorted(
                (
                    (request_id, record)
                    for request_id, record in requests_state.items()
                    if record.get("status") in {"completed", "failed"}
                ),
                key=lambda pair: float(pair[1].get("updated_at", 0)),
            )
            for request_id, _record in terminal:
                if len(requests_state) < self._max_requests:
                    break
                del requests_state[request_id]

    def _persist(self) -> None:
        self._save_state(deepcopy(self._state))

    def _public_request(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(record.get(key))
            for key in (
                "request_id",
                "status",
                "path",
                "error",
                "warning",
                "created_at",
                "updated_at",
                "notified",
            )
        }
