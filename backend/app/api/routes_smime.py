"""S/MIME certificate routes."""

from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_current_user
from app.core.database import db
from app.core.security import utc_iso
from app.models import SmimeCertImport

router = APIRouter(prefix="/api/smime", tags=["smime"])

@router.get("/certs")
def list_certs(current_user: dict = Depends(get_current_user)):
    rows = db.query_all("SELECT id, email_address, is_contact, created_at FROM smime_certs WHERE user_id = ?", (current_user["user_id"],))
    return [{"id": r["id"], "email_address": r["email_address"], "is_contact": bool(r["is_contact"]), "created_at": r["created_at"]} for r in rows]

@router.post("/certs")
def import_cert(payload: SmimeCertImport, current_user: dict = Depends(get_current_user)):
    import ssl, cryptography.x509
    try:
        cert = cryptography.x509.load_pem_x509_certificate(payload.cert_pem.encode())
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 X.509 证书 PEM。")
    now = utc_iso()
    cursor = db.execute(
        "INSERT INTO smime_certs(user_id, email_address, cert_pem, private_key_pem, is_contact, created_at) VALUES (?,?,?,?,?,?)",
        (current_user["user_id"], payload.email_address, payload.cert_pem, payload.private_key_pem, 0 if payload.private_key_pem else 1, now))
    return {"id": cursor.lastrowid, "message": "S/MIME 证书已导入。"}

@router.delete("/certs/{cert_id}")
def delete_cert(cert_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM smime_certs WHERE id = ? AND user_id = ?", (cert_id, current_user["user_id"]))
    if not row: raise HTTPException(status_code=404, detail="证书不存在。")
    db.execute("DELETE FROM smime_certs WHERE id = ?", (cert_id,))
    return {"message": "证书已删除。"}
