"""원정대 등록 버튼 핸들러"""
import discord
from core.http_client import http_client
from commands.siblings import ExpeditionManageView, _build_status_embed


async def handle_expedition_register_button(interaction: discord.Interaction):
    try:
        user_id = interaction.user.id
        resp = await http_client.get(f"/siblings/{user_id}")
        char_count = 0
        if resp.status_code == 200:
            char_count = (resp.json() or {}).get("count", 0)

        embed = _build_status_embed(user_id, char_count)
        view = ExpeditionManageView(user_id)

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
        except Exception:
            pass
