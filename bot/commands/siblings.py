"""원정대 관리 UI (뷰, 모달, 셀렉트)"""
import discord
from core.http_client import http_client
from services.lostark_service import search_lostark_siblings
from services.character_sync import save_character_to_db
from typing import List, Dict, Any


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
async def _get_siblings(user_id: int) -> List[Dict[str, Any]]:
    resp = await http_client.get(f"/siblings/{user_id}")
    if resp.status_code == 200:
        return (resp.json() or {}).get("characters") or []
    return []


async def _register_char(user_id: int, char_name: str) -> str:
    """단일 캐릭터 등록. 결과 문자열 반환."""
    resp = await http_client.post(f"/siblings/{user_id}/register", json={"char_name": char_name})
    if resp.status_code not in (200, 201):
        return "fail"
    action = (resp.json() or {}).get("action", "")
    return action  # "registered" | "already_exists" | "fail"


def _build_status_embed(user_id: int, char_count: int) -> discord.Embed:
    embed = discord.Embed(
        title="🏴‍☠️ 원정대 등록기능!",
        description=(
            "원정대 등록 기능을 통해 레이드 참가 시 등록된 캐릭터 목록에서 바로 선택할 수 있어요.\n\n"
            "캐릭터를 등록하면 레이드 일정 참가가 더 편해집니다!"
        ),
        color=0x5865F2,
    )
    embed.add_field(name="📊 현재 등록 상태", value=f"등록된 캐릭터: **{char_count}개**", inline=False)
    embed.add_field(
        name="🔧 기능 안내",
        value=(
            "➕ **캐릭터 등록** — 캐릭터 이름 직접 입력\n"
            "📋 **원정대 일괄 등록** — 캐릭터 하나로 원정대 전체 조회 후 선택\n"
            "🔄 **원정대 일괄 갱신** — 등록된 캐릭터 정보를 최신으로 업데이트\n"
            "🗑️ **캐릭터 삭제** — 등록된 캐릭터 개별 삭제\n"
            "🧹 **원정대 초기화** — 등록된 캐릭터 전체 삭제"
        ),
        inline=False,
    )
    return embed


# ──────────────────────────────────────────────
# 단일 캐릭터 등록 모달
# ──────────────────────────────────────────────
class CharacterRegisterModal(discord.ui.Modal):
    def __init__(self, user_id: int):
        super().__init__(title="캐릭터 등록")
        self.user_id = user_id
        self.add_item(discord.ui.InputText(
            label="캐릭터 이름",
            placeholder="등록할 캐릭터 이름을 입력하세요",
            max_length=16,
            required=True,
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        char_name = (self.children[0].value or "").strip()
        if not char_name:
            return await interaction.followup.send("❌ 캐릭터 이름을 입력해주세요.", ephemeral=True)

        action = await _register_char(self.user_id, char_name)
        if action == "registered":
            await interaction.followup.send(f"✅ **{char_name}** 캐릭터가 등록되었습니다!", ephemeral=True)
        elif action == "already_exists":
            await interaction.followup.send(f"⏭️ **{char_name}** 은 이미 등록되어 있습니다.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"❌ **{char_name}** 캐릭터를 찾을 수 없거나 등록에 실패했습니다.\n"
                "로스트아크에서 캐릭터 이름을 확인해주세요.", ephemeral=True
            )


# ──────────────────────────────────────────────
# 원정대 일괄 등록 — 조회 모달
# ──────────────────────────────────────────────
class ExpeditionRegisterModal(discord.ui.Modal):
    def __init__(self, user_id: int):
        super().__init__(title="원정대 일괄 등록")
        self.user_id = user_id
        self.add_item(discord.ui.InputText(
            label="캐릭터 이름",
            placeholder="원정대에 속한 캐릭터 이름 하나를 입력하세요",
            max_length=16,
            required=True,
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        char_name = (self.children[0].value or "").strip()
        if not char_name:
            return await interaction.followup.send("❌ 캐릭터 이름을 입력해주세요.", ephemeral=True)

        siblings = await search_lostark_siblings(char_name)
        if not siblings:
            return await interaction.followup.send(
                f"❌ **{char_name}** 캐릭터의 원정대 정보를 가져올 수 없습니다.", ephemeral=True
            )

        options = []
        for c in siblings[:25]:
            name = c.get("char_name", "")
            cls = c.get("class_name", "")
            ilvl = c.get("item_lvl", 0)
            options.append(discord.SelectOption(
                label=name,
                description=f"{cls} | Lv.{ilvl}",
                value=name,
                emoji="⚔️",
            ))

        view = ExpeditionSelectView(self.user_id, siblings)
        embed = discord.Embed(
            title="📋 원정대 캐릭터 선택",
            description=f"**{char_name}** 의 원정대 캐릭터 **{len(siblings)}개**를 조회했습니다.\n등록할 캐릭터를 선택하세요.",
            color=0x57F287,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ──────────────────────────────────────────────
# 원정대 캐릭터 멀티셀렉트
# ──────────────────────────────────────────────
class ExpeditionCharacterSelect(discord.ui.Select):
    def __init__(self, user_id: int, siblings: List[Dict]):
        self.user_id = user_id
        self.siblings_map = {c["char_name"]: c for c in siblings}
        options = [
            discord.SelectOption(
                label=c.get("char_name", ""),
                description=f"{c.get('class_name','')} | Lv.{c.get('item_lvl',0)}",
                value=c.get("char_name", ""),
                emoji="⚔️",
            )
            for c in siblings[:25]
        ]
        super().__init__(
            placeholder="등록할 캐릭터를 선택하세요 (다중 선택 가능)",
            options=options,
            min_values=1,
            max_values=min(len(options), 25),
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, skip, fail = [], [], []
        for name in self.values:
            char_data = self.siblings_map.get(name)
            if char_data:
                await save_character_to_db(char_data)
            action = await _register_char(self.user_id, name)
            if action == "registered":
                ok.append(name)
            elif action == "already_exists":
                skip.append(name)
            else:
                fail.append(name)

        lines = ["**원정대 일괄 등록 결과**"]
        if ok:
            lines.append(f"✅ 등록 완료 ({len(ok)}): " + ", ".join(ok))
        if skip:
            lines.append(f"⏭️ 이미 등록 ({len(skip)}): " + ", ".join(skip))
        if fail:
            lines.append(f"❌ 실패 ({len(fail)}): " + ", ".join(fail))
        await interaction.followup.send("\n".join(lines), ephemeral=True)


class ExpeditionSelectView(discord.ui.View):
    def __init__(self, user_id: int, siblings: List[Dict]):
        super().__init__(timeout=120)
        self.add_item(ExpeditionCharacterSelect(user_id, siblings))


# ──────────────────────────────────────────────
# 캐릭터 삭제 셀렉트
# ──────────────────────────────────────────────
class DeleteCharacterSelect(discord.ui.Select):
    def __init__(self, user_id: int, characters: List[Dict]):
        self.user_id = user_id
        options = [
            discord.SelectOption(
                label=c.get("char_name", ""),
                description=f"{c.get('class_name','')} | Lv.{c.get('item_lvl',0)}",
                value=str(c["char_id"]),
                emoji="🗑️",
            )
            for c in characters[:25]
        ]
        super().__init__(placeholder="삭제할 캐릭터를 선택하세요", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        char_id = int(self.values[0])
        resp = await http_client.delete(f"/siblings/{self.user_id}/{char_id}")
        if resp.status_code in (200, 204):
            await interaction.followup.send("🗑️ 캐릭터가 삭제되었습니다.", ephemeral=True)
        else:
            await interaction.followup.send("❌ 삭제에 실패했습니다.", ephemeral=True)


class DeleteCharacterView(discord.ui.View):
    def __init__(self, user_id: int, characters: List[Dict]):
        super().__init__(timeout=120)
        self.add_item(DeleteCharacterSelect(user_id, characters))


# ──────────────────────────────────────────────
# 일괄 갱신 셀렉트
# ──────────────────────────────────────────────
class BulkUpdateSelect(discord.ui.Select):
    def __init__(self, characters: List[Dict]):
        options = [
            discord.SelectOption(
                label=c.get("char_name", ""),
                description=f"{c.get('class_name','')} | Lv.{c.get('item_lvl',0)}",
                value=c.get("char_name", ""),
                emoji="🔄",
            )
            for c in characters[:25]
        ]
        super().__init__(
            placeholder="갱신할 캐릭터를 선택하세요 (다중 선택 가능)",
            options=options,
            min_values=1,
            max_values=min(len(options), 25),
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        results = []
        for char_name in self.values:
            resp = await http_client.patch("/character/update", json={"char_name": char_name, "update_discord": True})
            data = resp.json() or {}
            if resp.status_code == 200 and data.get("total_updated", 0) > 0:
                results.append(f"✅ {char_name}")
            else:
                results.append(f"⚠️ {char_name} (변경 없음)")
        await interaction.followup.send("**갱신 결과**\n" + "\n".join(results), ephemeral=True)


class BulkUpdateView(discord.ui.View):
    def __init__(self, characters: List[Dict]):
        super().__init__(timeout=120)
        self.add_item(BulkUpdateSelect(characters))


# ──────────────────────────────────────────────
# 메인 관리 뷰 (버튼 5개)
# ──────────────────────────────────────────────
class ExpeditionManageView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="캐릭터 등록", style=discord.ButtonStyle.primary, emoji="➕")
    async def register_single(self, button, interaction: discord.Interaction):
        await interaction.response.send_modal(CharacterRegisterModal(self.user_id))

    @discord.ui.button(label="원정대 일괄 등록", style=discord.ButtonStyle.secondary, emoji="📋")
    async def register_bulk(self, button, interaction: discord.Interaction):
        await interaction.response.send_modal(ExpeditionRegisterModal(self.user_id))

    @discord.ui.button(label="원정대 일괄 갱신", style=discord.ButtonStyle.success, emoji="🔄")
    async def update_bulk(self, button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        chars = await _get_siblings(self.user_id)
        if not chars:
            return await interaction.followup.send("등록된 캐릭터가 없습니다.", ephemeral=True)
        view = BulkUpdateView(chars)
        await interaction.followup.send("갱신할 캐릭터를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="캐릭터 삭제", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_char(self, button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        chars = await _get_siblings(self.user_id)
        if not chars:
            return await interaction.followup.send("등록된 캐릭터가 없습니다.", ephemeral=True)
        view = DeleteCharacterView(self.user_id, chars)
        await interaction.followup.send("삭제할 캐릭터를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="원정대 초기화", style=discord.ButtonStyle.danger, emoji="🧹")
    async def reset_all(self, button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        resp = await http_client.delete(f"/siblings/reset/{self.user_id}")
        if resp.status_code in (200, 204):
            await interaction.followup.send("🧹 원정대 캐릭터가 모두 초기화되었습니다.", ephemeral=True)
        else:
            await interaction.followup.send("❌ 초기화에 실패했습니다.", ephemeral=True)


# ──────────────────────────────────────────────
# 채널 상시 게시용 퍼시스턴트 버튼
# ──────────────────────────────────────────────
class ExpeditionRegisterButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="원정대 등록하기",
        style=discord.ButtonStyle.primary,
        emoji="🏴‍☠️",
        custom_id="expedition_register_button",
    )
    async def open_manage(self, button, interaction: discord.Interaction):
        from handler.siblings import handle_expedition_register_button
        await handle_expedition_register_button(interaction)
