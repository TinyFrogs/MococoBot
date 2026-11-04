"""원정대(캐릭터-유저 매핑) 라우터"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from database.connection import get_db
from typing import Optional
from datetime import datetime

router = APIRouter()


class RegisterBody(BaseModel):
    char_name: str = Field(..., description="등록할 캐릭터 이름")


async def _is_registered(db, user_id: int, char_id: int) -> bool:
    result = await db.execute(
        "SELECT 1 FROM `siblings` WHERE user_id = ? AND character_id = ?",
        (user_id, char_id),
    )
    return bool(result)


@router.get("/{user_id}")
async def list_user_characters(user_id: int):
    async with get_db() as db:
        rows = await db.execute(
            """
            SELECT c.id AS char_id, c.char_name, c.item_lvl, c.combat_power,
                   cl.name AS class_name, cl.emoji AS class_emoji, c.updated_at
            FROM `siblings` s
            JOIN `character` c ON c.id = s.character_id
            LEFT JOIN class cl ON cl.id = c.class_id
            WHERE s.user_id = ?
            ORDER BY c.item_lvl DESC
            """,
            (user_id,),
        ) or []
        serialized = []
        for row in rows:
            r = dict(row)
            if isinstance(r.get("updated_at"), datetime):
                r["updated_at"] = r["updated_at"].isoformat()
            serialized.append(r)
        return JSONResponse(content={"user_id": str(user_id), "characters": serialized, "count": len(serialized)})


@router.post("/{user_id}/register")
async def register_character(user_id: int, body: RegisterBody):
    from services.character_sync import save_character_to_db
    from services.lostark_service import search_lostark_character

    async with get_db() as db:
        existing = await db.execute(
            "SELECT id FROM `character` WHERE char_name = ?", (body.char_name,)
        )
        if existing:
            char_id = existing[0]["id"]
        else:
            api_data = await search_lostark_character(body.char_name)
            if not api_data:
                raise HTTPException(status_code=404, detail=f"캐릭터 '{body.char_name}'을 찾을 수 없습니다.")
            saved = await save_character_to_db(api_data)
            if not saved:
                raise HTTPException(status_code=500, detail="캐릭터 저장 실패")
            char_id = saved["id"]

        if await _is_registered(db, user_id, char_id):
            return JSONResponse(content={"ok": True, "action": "already_exists", "char_id": char_id})

        await db.execute(
            "INSERT INTO `siblings` (user_id, character_id) VALUES (?, ?)", (user_id, char_id)
        )
        await db.commit()
        return JSONResponse(content={"ok": True, "action": "registered", "char_id": char_id})


@router.delete("/reset/{user_id}")
async def reset_user_characters(user_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM `siblings` WHERE user_id = ?", (user_id,))
        await db.commit()
    return JSONResponse(content={"ok": True, "user_id": str(user_id)})


@router.delete("/{user_id}/{char_id}")
async def unregister_character(user_id: int, char_id: int):
    async with get_db() as db:
        if not await _is_registered(db, user_id, char_id):
            raise HTTPException(status_code=404, detail="등록되지 않은 캐릭터입니다.")
        await db.execute(
            "DELETE FROM `siblings` WHERE user_id = ? AND character_id = ?", (user_id, char_id)
        )
        await db.commit()
    return JSONResponse(content={"ok": True, "char_id": char_id})
