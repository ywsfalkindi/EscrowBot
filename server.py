import os
import hashlib
import hmac
from fastapi import FastAPI, Request, HTTPException
from db_services import add_balance_to_user, log_audit_event
from models import Session, User # للتحقق السريع
import httpx # لإرسال إشعار للمستخدم عبر تليجرام

app = FastAPI()

# توكن الكريبتو (نفس الموجود في .env)
CRYPTO_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
BOT_TOKEN = os.getenv("BOT_TOKEN") # توكن البوت لإرسال الإشعارات

def verify_signature(body: bytes, signature: str):
    """التحقق الأمني: هل الطلب فعلاً من CryptoBot؟"""
    secret = hashlib.sha256(CRYPTO_TOKEN.encode()).digest()
    hmac_check = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if hmac_check != signature:
        raise HTTPException(status_code=403, detail="Invalid Signature")

@app.post("/webhook/crypto")
async def crypto_webhook(request: Request):
    # 1. قراءة الترويسة والجسم
    signature = request.headers.get("crypto-pay-api-signature")
    body = await request.body()
    
    # 2. التحقق الأمني (نوقف الهكرز هنا)
    if not signature:
         raise HTTPException(status_code=400, detail="Missing Signature")
    verify_signature(body, signature)

    # 3. قراءة البيانات
    data = await request.json()
    
    # نحن نهتم فقط بتحديثات الفواتير (Invoice)
    if data.get("update_type") == "invoice_paid":
        payload = data.get("payload") # يحتوي على بيانات الفاتورة
        
        invoice_id = payload.get("invoice_id")
        amount = float(payload.get("amount")) # المبلغ
        user_id_str = payload.get("payload")  # خزنا فيه الـ Telegram ID سابقاً
        
        if not user_id_str:
            return {"status": "ignored", "reason": "no user id"}
            
        user_id = int(user_id_str)

        print(f"💰 Webhook received: Invoice {invoice_id} paid by {user_id}")

        # 4. تنفيذ الشحن في قاعدة البيانات
        # نمرر تفاصيل الفاتورة لمنع التكرار في السجلات
        success = add_balance_to_user(user_id, amount)
        
        if success:
            # نسجل في الـ Audit Log أن المصدر هو Webhook
            log_audit_event(user_id, "WEBHOOK_DEPOSIT", int(amount*100), f"Invoice #{invoice_id}")
            
            # 5. إرسال إشعار للمستخدم في تليجرام (ميزة UX)
            async with httpx.AsyncClient() as client:
                msg_text = f"✅ **تم استلام دفعتك!**\nتم إضافة {amount}$ إلى رصيدك فوراً."
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": user_id, "text": msg_text, "parse_mode": "Markdown"}
                )
                
    return {"status": "ok"}