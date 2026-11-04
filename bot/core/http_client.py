"""In-process API client (single-process build).

Replaces the old bot→API HTTP client. Exposes the same `http_client.get/post/
patch/delete` interface the bot already uses, but dispatches each path to the
matching router coroutine in-process (no network). Router functions return
FastAPI JSONResponse objects or raise HTTPException; we wrap both into a small
response object that mimics the httpx.Response surface the bot relies on
(`status_code`, `.json()`, `.raise_for_status()`).
"""
import json
import re
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, unquote

from fastapi import HTTPException

from routers import character as r_character
from routers import discord as r_discord
from routers import party as r_party
from routers import raid as r_raid
from routers import siblings as r_siblings


class _Resp:
    def __init__(self, status_code: int, body: Any = None):
        self.status_code = int(status_code)
        if isinstance(body, (bytes, bytearray)):
            self._raw = bytes(body)
            self._json = None
        else:
            self._raw = None
            self._json = body
        self.headers: Dict[str, str] = {"content-type": "application/json"}

    @property
    def content(self) -> bytes:
        if self._raw is not None:
            return self._raw
        return json.dumps(self._json, default=str).encode("utf-8")

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        if self._raw:
            return json.loads(self._raw)
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"in-process call failed: status={self.status_code}")


def _wrap(resp: Any) -> _Resp:
    """Wrap a FastAPI Response/JSONResponse into _Resp."""
    status = int(getattr(resp, "status_code", 200) or 200)
    body = getattr(resp, "body", None)
    if body is None:
        return _Resp(status, None)
    return _Resp(status, bytes(body))


async def _call(coro) -> _Resp:
    try:
        return _wrap(await coro)
    except HTTPException as exc:
        return _Resp(exc.status_code, {"detail": exc.detail})


# (method, compiled_pattern, handler). First match wins; order matters so that
# more specific paths are tested before the catch-all /party/{id}.
_ROUTES: list[tuple[str, re.Pattern, Callable]] = []


def _route(method: str, pattern: str):
    def deco(fn: Callable):
        _ROUTES.append((method.upper(), re.compile(pattern), fn))
        return fn
    return deco


@_route("GET", r"^/raid/?$")
async def _raids(m, q, body):
    return await _call(r_raid.get_raids())


@_route("GET", r"^/party/list$")
async def _party_list(m, q, body):
    gid = q.get("guild_id")
    return await _call(r_party.get_party_list(guild_id=int(gid) if gid else None))


@_route("POST", r"^/party/(\d+)/create$")
async def _party_create(m, q, body):
    return await _call(r_party.create_party(guild_id=int(m.group(1)), request=r_party.PartyCreateRequest(**(body or {}))))


@_route("PATCH", r"^/party/(\d+)/edit$")
async def _party_edit(m, q, body):
    return await _call(r_party.edit_party(party_id=int(m.group(1)), request=r_party.PartyEditRequest(**(body or {}))))


@_route("POST", r"^/party/(\d+)/join$")
async def _party_join(m, q, body):
    return await _call(r_party.join_party(party_id=int(m.group(1)), request=r_party.PartyJoinRequest(**(body or {}))))


@_route("POST", r"^/party/(\d+)/toggle/(\d+)$")
async def _party_toggle(m, q, body):
    return await _call(r_party.toggle_party_recruit_status(party_id=int(m.group(1)), role=int(m.group(2))))


@_route("DELETE", r"^/party/(\d+)/delete$")
async def _party_delete(m, q, body):
    return await _call(r_party.delete_party(party_id=int(m.group(1))))


@_route("POST", r"^/party/public/(\d+)$")
async def _party_public(m, q, body):
    return await _call(r_party.toggle_party_public(party_id=int(m.group(1))))


@_route("GET", r"^/party/(\d+)/waitlist/me$")
async def _waitlist_me(m, q, body):
    return await _call(r_party.get_my_waitlist_position(
        party_id=int(m.group(1)), user_id=str(q.get("user_id") or ""), role=int(q.get("role") or 0)))


@_route("GET", r"^/party/(\d+)/waitlist$")
async def _waitlist_get(m, q, body):
    role = q.get("role")
    return await _call(r_party.get_party_waitlist(party_id=int(m.group(1)), role=int(role) if role is not None else None))


@_route("DELETE", r"^/party/(\d+)/waitlist$")
async def _waitlist_cancel(m, q, body):
    return await _call(r_party.cancel_waitlist(party_id=int(m.group(1)), request=r_party.WaitlistCancelRequest(**(body or {}))))


@_route("DELETE", r"^/party/guilds/(\d+)/participants/([^/]+)$")
async def _purge_member(m, q, body):
    return await _call(r_party.purge_user_participations_in_guild(guild_id=m.group(1), user_id=unquote(m.group(2))))


@_route("DELETE", r"^/party/(\d+)/participants/([^/]+)$")
async def _party_leave(m, q, body):
    return await _call(r_party.leave_party(party_id=int(m.group(1)), user_id=unquote(m.group(2))))


@_route("GET", r"^/party/(\d+)/thread$")
async def _party_by_thread(m, q, body):
    return await _call(r_party.get_party_by_thread_id(thread_id=int(m.group(1))))


@_route("GET", r"^/party/(\d+)$")
async def _party_detail(m, q, body):
    return await _call(r_party.get_party_detail(party_id=int(m.group(1))))


@_route("GET", r"^/discord/server/(\d+)$")
async def _server_get(m, q, body):
    return await _call(r_discord.get_server_config(guild_id=int(m.group(1))))


@_route("POST", r"^/discord/server/(\d+)$")
async def _server_save(m, q, body):
    return await _call(r_discord.save_server_config(guild_id=int(m.group(1)), config=r_discord.ServerConfigRequest(**(body or {}))))


@_route("DELETE", r"^/discord/server/(\d+)$")
async def _server_delete(m, q, body):
    return await _call(r_discord.delete_server_config(guild_id=int(m.group(1))))


@_route("GET", r"^/siblings/(\d+)$")
async def _siblings_list(m, q, body):
    return await _call(r_siblings.list_user_characters(user_id=int(m.group(1))))


@_route("POST", r"^/siblings/(\d+)/register$")
async def _siblings_register(m, q, body):
    return await _call(r_siblings.register_character(user_id=int(m.group(1)), body=r_siblings.RegisterBody(**(body or {}))))


@_route("DELETE", r"^/siblings/reset/(\d+)$")
async def _siblings_reset(m, q, body):
    return await _call(r_siblings.reset_user_characters(user_id=int(m.group(1))))


@_route("DELETE", r"^/siblings/(\d+)/(\d+)$")
async def _siblings_unregister(m, q, body):
    return await _call(r_siblings.unregister_character(user_id=int(m.group(1)), char_id=int(m.group(2))))


@_route("GET", r"^/character/search/(.+)$")
async def _char_search(m, q, body):
    return await _call(r_character.search_character(nickname=m.group(1)))


@_route("PATCH", r"^/character/update$")
async def _char_update(m, q, body):
    return await _call(r_character.update_character(request=r_character.CharacterUpdateRequest(**(body or {}))))


class InProcessClient:
    async def request(self, method: str, path: str, *, json: Optional[dict] = None,
                      params: Optional[dict] = None, **_ignored) -> _Resp:
        raw_path, _, query = path.partition("?")
        q: Dict[str, Any] = {k: v[0] for k, v in parse_qs(query).items()} if query else {}
        if params:
            q.update({k: v for k, v in params.items() if v is not None})

        method = method.upper()
        for route_method, pattern, handler in _ROUTES:
            if route_method != method:
                continue
            match = pattern.match(raw_path)
            if match:
                return await handler(match, q, json)
        return _Resp(404, {"detail": f"no in-process route for {method} {raw_path}"})

    async def get(self, path: str, **kwargs) -> _Resp:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> _Resp:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs) -> _Resp:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs) -> _Resp:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> _Resp:
        return await self.request("DELETE", path, **kwargs)

    async def aclose(self) -> None:
        return None


http_client = InProcessClient()
