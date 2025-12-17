# payment_services.py
import os
from aiocryptopay import AioCryptoPay, Networks
from dotenv import load_dotenv

load_dotenv()

# جلب التوكن من ملف .env
token = os.getenv("CRYPTO_BOT_TOKEN")
network_env = os.getenv("NETWORK", "testnet")

if token is None:
    print("❌ خطأ: لم يتم العثور على CRYPTO_BOT_TOKEN في ملف .env")

# تحديد الشبكة (تجريبي أم حقيقي)
network = Networks.TEST_NET if network_env == "testnet" else Networks.MAIN_NET

# إنشاء كائن الدفع
crypto = AioCryptoPay(token=token, network=network)


async def get_exchange_rates():
    """
    دالة إضافية لجلب أسعار العملات لو أردت التحويل
    """
    rates = await crypto.get_exchange_rates()
    return rates


async def create_deposit_invoice(user_id, amount_usd):
    """
    تنشئ رابط دفع لعملة USDT
    """
    try:
        invoice = await crypto.create_invoice(
            asset="USDT",  # العملة المطلوبة
            amount=amount_usd,  # المبلغ (مثلاً 10.5)
            description=f"Top up balance for user {user_id}",
            payload=str(user_id),  # نخبئ هوية المستخدم هنا
            expires_in=900,  # 15 دقيقة
            allow_comments=False,  # لا نريد تعليقات من المستخدم
            allow_anonymous=False  # يفضل أن نعرف من دفع
        )

        return {
            "invoice_id": invoice.invoice_id,
            "pay_url": invoice.bot_invoice_url,  # الرابط الذي سنرسله للمستخدم
            "hash": invoice.hash,  # نحتاجه للتحقق لاحقاً
        }
    except Exception as e:
        print(f"Error creating invoice: {e}")
        return None


async def check_invoice_status(invoice_id):
    """
    نسأل CryptoBot: ما هي حالة هذه الفاتورة الآن؟
    """
    try:
        invoices = await crypto.get_invoices(invoice_ids=[invoice_id])
        if invoices:
            return invoices[0].status  # (paid, active, expired)
    except Exception as e:
        print(f"Error checking invoice: {e}")
    return None
