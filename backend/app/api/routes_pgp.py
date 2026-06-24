"""PGP key management API routes.

POST   /api/pgp/generate   – generate a keypair for current user + email
GET    /api/pgp/keys       – list keypairs (private keys excluded)
DELETE /api/pgp/keys/{id}  – delete a keypair
POST   /api/pgp/import     – import a contact's public key
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import get_current_user
from app.core.database import db
from app.core.security import utc_iso
from app.services.pgp_crypto import generate_keypair

router = APIRouter(prefix="/api/pgp", tags=["pgp"])


# ── request / response models ─────────────────────────────────────────────

class PgpGenerateRequest(BaseModel):
    email_address: EmailStr


class PgpImportRequest(BaseModel):
    email_address: EmailStr
    public_key_pem: str = Field(min_length=1, max_length=8192)


class PgpKeyOut(BaseModel):
    id: int
    email_address: str
    public_key_pem: str
    created_at: str


# ── routes ─────────────────────────────────────────────────────────────────

@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate(user: dict = Depends(get_current_user), body: PgpGenerateRequest = None) -> dict:  # type: ignore[assignment]
    """Generate a new RSA-2048 keypair for *user* and store it in pgp_keys."""
    private_pem, public_pem = generate_keypair()
    now = utc_iso()
    key_id = db.execute(
        "INSERT INTO pgp_keys(user_id, email_address, public_key_pem, private_key_pem, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user["user_id"], str(body.email_address), public_pem, private_pem, now),
    ).lastrowid

    return {
        "id": key_id,
        "email_address": str(body.email_address),
        "public_key_pem": public_pem,
        "private_key_pem": private_pem,
        "created_at": now,
        "message": "密钥对已生成。请妥善保管私钥。",
    }


@router.get("/keys")
def list_keys(user: dict = Depends(get_current_user)) -> list[PgpKeyOut]:
    """Return all PGP keypairs owned by *user* (private keys excluded)."""
    rows = db.query_all(
        "SELECT id, email_address, public_key_pem, created_at FROM pgp_keys WHERE user_id = ? ORDER BY created_at DESC",
        (user["user_id"],),
    )
    return [PgpKeyOut(id=row["id"], email_address=row["email_address"], public_key_pem=row["public_key_pem"], created_at=row["created_at"]) for row in rows]


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Delete a PGP keypair owned by *user*."""
    cur = db.execute(
        "DELETE FROM pgp_keys WHERE id = ? AND user_id = ?",
        (key_id, user["user_id"]),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="密钥对不存在。")
    return {"message": "密钥对已删除。"}


@router.post("/import", status_code=status.HTTP_201_CREATED)
def import_key(user: dict = Depends(get_current_user), body: PgpImportRequest = None) -> dict:  # type: ignore[assignment]
    """Import a contact's public key (no private key)."""
    now = utc_iso()
    key_id = db.execute(
        "INSERT INTO pgp_keys(user_id, email_address, public_key_pem, private_key_pem, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user["user_id"], str(body.email_address), body.public_key_pem, "", now),
    ).lastrowid
    return {
        "id": key_id,
        "email_address": str(body.email_address),
        "created_at": now,
        "message": "公钥已导入。",
    }
