from __future__ import annotations

import discord
import httpx
import asyncio
import contextlib
import time
from typing import Awaitable, Any

from core.config import BOT_TOKEN
from core.raid_data import load_raids
from core.http_client import http_client
from handler.party import (
    handle_party_join,
    handle_party_leave,
    handle_party_force_cancel,
    handle_party_mention,
    handle_party_delete,
    handle_party_public,
    handle_party_waitlist,
)
from handler.raid_role import handle_raid_role_manage
from services.scheduler import party_scheduler
from utils.http_client import init_http_client, close_http_client
from database.connection import close_db_pool

_background_tasks: set[asyncio.Task] = set()
LOOP_LAG_WARN_SEC = 1.5
LOOP_LAG_CHECK_INTERVAL_SEC = 10.0
GATEWAY_RECOVERY_WARN_SEC = 45.0


def _bg_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"[BG Task] result check failed: {e}")
        return
    if exc is not None:
        task_name = getattr(task, "get_name", lambda: "")() or "unnamed"
        print(f"[BG Task Error] {task_name}: {exc!r}")


def create_bg_task(coro: Awaitable[Any], *, name: str | None = None) -> asyncio.Task:
    t = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_bg_task_done)
    return t


async def cancel_all_bg_tasks():
    if not _background_tasks:
        return
    for t in list(_background_tasks):
        t.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()


async def run_periodic(interval_sec: float, coro_fn, *args, immediate: bool = False, **kwargs):
    if immediate:
        with contextlib.suppress(Exception):
            await coro_fn(*args, **kwargs)
    while True:
        try:
            await asyncio.sleep(interval_sec)
            await coro_fn(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Periodic] {coro_fn.__name__} error: {e}")


async def monitor_event_loop_lag(interval_sec: float = LOOP_LAG_CHECK_INTERVAL_SEC, warn_sec: float = LOOP_LAG_WARN_SEC):
    loop = asyncio.get_running_loop()
    next_tick = loop.time() + interval_sec
    while True:
        await asyncio.sleep(interval_sec)
        now = loop.time()
        lag = now - next_tick
        if lag > warn_sec:
            print(f"[Loop Lag] {lag:.2f}s (threshold={warn_sec:.2f}s)")
        next_tick = now + interval_sec


class MococoBot(discord.AutoShardedBot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        if hasattr(intents, "moderation"):
            intents.moderation = True
        elif hasattr(intents, "bans"):
            intents.bans = True
        super().__init__(
            intents=intents,
            chunk_guilds_at_startup=False,
        )
        self.start_time_ts: float | None = None
        self._bg_init_lock = asyncio.Lock()
        self._bg_init_started = False
        self._bg_init_done = False
        self._shutting_down = False
        self._scheduler_started = False
        self._gw_watchdog_task: asyncio.Task | None = None
        self._gw_watchdog_token = 0

    async def update_status(self):
        try:
            server_count = len(self.guilds)
            activity = discord.Activity(
                type=discord.ActivityType.playing,
                name=f"{server_count}개 서버에서 활동"
            )
            await self.change_presence(activity=activity, status=discord.Status.online)
        except Exception as e:
            print(f"[Presence] 업데이트 실패: {e}")

    async def on_ready(self):
        print(f"[Gateway] on_ready fired user={self.user!s}")
        print(f"[Gateway] guild_count={len(self.guilds)}")
        self._mark_gateway_recovered("on_ready")
        await self._ensure_bg_init_once()

    async def on_connect(self):
        print("[Gateway] on_connect")

    async def on_disconnect(self):
        print("[Gateway] on_disconnect")
        self._arm_gateway_watchdog("on_disconnect")

    async def on_shard_connect(self, shard_id: int):
        print(f"[Gateway] shard_connect shard={shard_id}")

    async def on_shard_ready(self, shard_id: int):
        print(f"[Gateway] shard_ready shard={shard_id}")
        self._mark_gateway_recovered(f"on_shard_ready:{shard_id}")

    async def on_shard_disconnect(self, shard_id: int):
        print(f"[Gateway] shard_disconnect shard={shard_id}")
        self._arm_gateway_watchdog(f"on_shard_disconnect:{shard_id}")

    async def on_resumed(self):
        print("[Gateway] on_resumed")
        self._mark_gateway_recovered("on_resumed")

    def _arm_gateway_watchdog(self, reason: str):
        if self._shutting_down:
            return
        self._gw_watchdog_token += 1
        token = self._gw_watchdog_token
        if self._gw_watchdog_task and not self._gw_watchdog_task.done():
            self._gw_watchdog_task.cancel()
        self._gw_watchdog_task = create_bg_task(
            self._gateway_recovery_watchdog(token, reason),
            name="gateway_recovery_watchdog"
        )

    def _mark_gateway_recovered(self, reason: str):
        if self._gw_watchdog_task and not self._gw_watchdog_task.done():
            self._gw_watchdog_task.cancel()
            self._gw_watchdog_task = None
            print(f"[Gateway Watchdog] recovered via {reason}")

    async def _gateway_recovery_watchdog(self, token: int, reason: str):
        try:
            await asyncio.sleep(GATEWAY_RECOVERY_WARN_SEC)
            if self._shutting_down or token != self._gw_watchdog_token:
                return
            if self.is_closed():
                return
            print(
                f"[Gateway Watchdog] no recovery signal for {GATEWAY_RECOVERY_WARN_SEC:.0f}s "
                f"after {reason} (ready={self.is_ready()} guilds={len(self.guilds)} latency={self.latency:.3f}s)"
            )
        except asyncio.CancelledError:
            raise
        finally:
            if self._gw_watchdog_task is asyncio.current_task():
                self._gw_watchdog_task = None

    async def _ensure_bg_init_once(self):
        if self._bg_init_done:
            return
        async with self._bg_init_lock:
            if self._bg_init_done or self._bg_init_started:
                return
            self._bg_init_started = True
            create_bg_task(self._run_bg_init_once(), name="bg_init_once")

    async def _run_bg_init_once(self):
        try:
            await self.register_commands(force=True, delete_existing=True)
            print("[Startup] application commands force-registered")
        except Exception as e:
            print(f"[Startup] application commands sync failed: {e}")

        try:
            await self._bg_init_tasks_once()
        finally:
            self._bg_init_done = True
            self._bg_init_started = False

    async def _bg_init_tasks_once(self):
        if not self._scheduler_started:
            try:
                party_scheduler.start()
                self._scheduler_started = True
                print("[Startup] party scheduler started")
            except Exception as e:
                print(f"[Startup] scheduler start failed: {e}")

        await self.update_status()

        if not any(t for t in _background_tasks if not t.cancelled() and "presence" in ((getattr(t, "get_name", lambda: "")() or "").lower())):
            create_bg_task(run_periodic(300.0, self.update_status, immediate=False), name="presence_update")

        if not any(t for t in _background_tasks if not t.cancelled() and "loop_lag" in ((getattr(t, "get_name", lambda: "")() or "").lower())):
            create_bg_task(monitor_event_loop_lag(), name="loop_lag_monitor")

        self.start_time_ts = time.time()
        from commands.siblings import ExpeditionRegisterButtonView
        self.add_view(ExpeditionRegisterButtonView())
        print("봇 초기화 완료")

    async def on_guild_join(self, guild: discord.Guild):
        create_bg_task(self._send_welcome(guild), name="guild_join_welcome")
        await self.update_status()

    async def _send_welcome(self, guild: discord.Guild):
        target_channel = None
        try:
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                target_channel = guild.system_channel
            elif guild.rules_channel and guild.rules_channel.permissions_for(guild.me).send_messages:
                target_channel = guild.rules_channel
            else:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        target_channel = channel
                        break
            if not target_channel:
                print(f"서버 입장 가이드: {guild.name} - 메시지를 보낼 수 있는 채널이 없습니다.")
                return
            embed = discord.Embed(
                title="🎉 모코코 봇이 서버에 참가했어요!",
                description="안녕하세요! 로스트아크 레이드 일정관리 봇이에요.\n레이드 모집·파티 편성·알림을 도와드릴게요!",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="🚀 시작하기",
                value=("1️⃣ `/setups` 으로 레이드 채널들을 생성해주세요.\n"
                       "2️⃣ `/서버설정` 으로 기본 설정을 해주세요.\n"
                       "3️⃣ `/레이드` 로 첫 번째 일정을 만들어보세요!"),
                inline=False
            )
            if self.user and self.user.display_avatar:
                embed.set_footer(text="💡 설정 명령어는 관리자 권한이 필요해요.", icon_url=self.user.display_avatar.url)
                embed.set_thumbnail(url=self.user.display_avatar.url)
            await target_channel.send(embed=embed)
        except Exception as e:
            print(f"서버 입장 가이드 전송 실패: {guild.name if guild else 'Unknown'} - {e}")

    async def on_guild_remove(self, guild: discord.Guild):
        print(f"서버 퇴장: {guild.name} ({guild.id})")

        async def _cleanup_party_and_configs():
            with contextlib.suppress(Exception):
                r = await http_client.get(f"/party/list?guild_id={guild.id}")
                if getattr(r, "status_code", 0) == 200:
                    data = r.json()
                    parties = data.get("data", []) if isinstance(data.get("data"), list) else []
                    for p in parties:
                        pid = p.get("id")
                        if pid:
                            with contextlib.suppress(Exception):
                                await http_client.delete(f"/party/{pid}/delete")
            with contextlib.suppress(Exception):
                await http_client.delete(f"/discord/server/{guild.id}")

        create_bg_task(_cleanup_party_and_configs(), name="guild_remove_cleanup")
        await self.update_status()

    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        try:
            await http_client.delete(f"/party/guilds/{member.guild.id}/participants/{member.id}")
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPError) as e:
            print(f"party delete failed guild={member.guild.id} member={member.id} err={e!r}")

    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if user.bot:
            return
        try:
            await http_client.delete(f"/party/guilds/{guild.id}/participants/{user.id}")
        except Exception as e:
            print(f"party ban delete failed guild={guild.id} user={user.id} err={e!r}")

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            await self.process_application_commands(interaction)

        try:
            data = getattr(interaction, "data", None) or {}
            custom_id = data.get("custom_id", "")
            if not custom_id:
                return

            if custom_id.startswith("party_join_"):
                await handle_party_join(interaction, int(custom_id.replace("party_join_", "")))
            elif custom_id.startswith("party_leave_"):
                await handle_party_leave(interaction, int(custom_id.replace("party_leave_", "")))
            elif custom_id.startswith("party_force_cancel_"):
                await handle_party_force_cancel(interaction, int(custom_id.replace("party_force_cancel_", "")))
            elif custom_id.startswith("party_mention_"):
                await handle_party_mention(interaction, int(custom_id.replace("party_mention_", "")))
            elif custom_id.startswith("party_public_"):
                await handle_party_public(interaction, int(custom_id.replace("party_public_", "")))
            elif custom_id.startswith("party_waitlist_"):
                await handle_party_waitlist(interaction, int(custom_id.rsplit("_", 1)[-1]))
            elif custom_id.startswith("party_delete_"):
                await handle_party_delete(interaction, int(custom_id.replace("party_delete_", "")))
            elif custom_id == "raid_role_manage":
                await handle_raid_role_manage(interaction)
        except Exception as e:
            print(f"[Interaction Error] {e}")

    async def on_thread_delete(self, thread: discord.Thread):
        with contextlib.suppress(Exception):
            r = await http_client.get(f"/party/{thread.id}/thread")
            if getattr(r, "status_code", 0) == 200:
                party_id = (r.json() or {}).get("id")
                if party_id:
                    await http_client.delete(f"/party/{party_id}/delete")

    async def close(self):
        self._shutting_down = True
        if self._gw_watchdog_task and not self._gw_watchdog_task.done():
            self._gw_watchdog_task.cancel()
        with contextlib.suppress(Exception):
            party_scheduler.stop()
        await cancel_all_bg_tasks()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(close_http_client(), timeout=5.0)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(close_db_pool(), timeout=5.0)
        await super().close()


async def main():
    loop = asyncio.get_running_loop()

    def _loop_exception_handler(loop, context):
        msg = context.get("message", "asyncio loop exception")
        exc = context.get("exception")
        if exc is not None:
            print(f"[Asyncio] {msg}: {exc!r}")
        else:
            print(f"[Asyncio] {msg}")

    loop.set_exception_handler(_loop_exception_handler)

    print("[Startup] begin")
    with contextlib.suppress(Exception):
        await init_http_client()

    try:
        t0 = time.perf_counter()
        await asyncio.wait_for(load_raids(), timeout=12.0)
        print(f"[Startup] load_raids ok ({time.perf_counter() - t0:.2f}s)")
    except asyncio.TimeoutError:
        print("[Startup] load_raids timeout (12s) - continue")
    except Exception as e:
        print(f"[Startup] load_raids error - continue: {e!r}")

    bot = MococoBot()
    print("[Startup] bot instance created")

    for ext in ['cogs.raid']:
        try:
            t0 = time.perf_counter()
            bot.load_extension(ext)
            print(f"[Startup] extension ok: {ext} ({time.perf_counter() - t0:.2f}s)")
        except Exception as e:
            print(f"[EXT] load failed: {ext} -> {e}")

    print("[Startup] calling bot.start")
    await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
