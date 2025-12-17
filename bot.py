import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from db_services import get_or_create_user
from payment_services import create_deposit_invoice
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from payment_services import check_invoice_status
from db_services import add_balance_to_user

# 1. تحميل المفاتيح السرية من ملف .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# 2. دالة الرد على أمر /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # تسجيل المستخدم في قاعدة البيانات فوراً
    db_user = get_or_create_user(
        telegram_id=user.id,
        full_name=user.full_name,
        username=user.username
    )
    
    # الرد برسالة تظهر رصيده
    # لاحظ كيف نستخدم دالة get_balance_display التي كتبناها في المودل
    msg = (
        f"أهلاً بك يا {db_user.full_name}! 👋\n"
        f"رقمك التعريفي: `{db_user.id}`\n"
        f"رصيدك الحالي: {db_user.get_balance_display()} $\n"
        f"سمعتك: {db_user.reputation} نجوم ⭐"
    )
    
    await update.message.reply_text(msg)

# 3. نقطة التشغيل الرئيسية
if __name__ == '__main__':
    # التأكد من وجود التوكن
    if not TOKEN:
        print("Error: BOT_TOKEN not found in .env file")
        exit()

    # بناء التطبيق
    print("Bot is starting...")
    app = ApplicationBuilder().token(TOKEN).build()

    # إضافة "مستمع" لأمر start
    app.add_handler(CommandHandler("start", start_command))

    # تشغيل البوت (Polling)
    print("Bot is running! Go to Telegram and press /start")
    app.run_polling()

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # 1. استخراج المبلغ من الرسالة
    try:
        # الرسالة تأتي هكذا: "/deposit 10"
        # نقسم النص ونأخذ الجزء الثاني
        amount = float(context.args[0])
        if amount <= 0:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("❌ الاستخدام الخاطئ.\nاكتب الأمر ثم المبلغ.\nمثال: `/deposit 10`")
        return

    # 2. إنشاء الفاتورة
    msg = await update.message.reply_text("⏳ جاري إنشاء رابط الدفع...")
    
    invoice_data = await create_deposit_invoice(user_id, amount)
    
    if not invoice_data:
        await msg.edit_text("حدث خطأ في بوابة الدفع. حاول لاحقاً.")
        return

    # 3. حفظ بيانات الفاتورة في ذاكرة مؤقتة (لزر التحقق)
    # ملاحظة: context.user_data يحفظ البيانات طالما البوت يعمل
    context.user_data['last_invoice_id'] = invoice_data['invoice_id']
    context.user_data['pending_amount'] = amount

    # 4. إرسال الرابط مع زر التحقق
    keyboard = [
        [InlineKeyboardButton("🔗 اضغط هنا للدفع", url=invoice_data['pay_url'])],
        [InlineKeyboardButton("✅ لقد دفعت، تحقق الآن", callback_data="check_payment")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await msg.edit_text(
        f"💳 **فاتورة شحن رصيد**\n\n"
        f"المبلغ: {amount} USDT\n"
        f"صلاحية الرابط: 15 دقيقة\n\n"
        f"بعد الدفع، اضغط على زر التحقق بالأسفل.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def check_payment_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري التحقق...") # إشعار سريع للمستخدم
    
    # استرجاع رقم الفاتورة من الذاكرة
    invoice_id = context.user_data.get('last_invoice_id')
    amount = context.user_data.get('pending_amount')
    
    if not invoice_id:
        await query.edit_message_text("❌ لا توجد فاتورة معلقة.")
        return

    # فحص الحالة عبر API
    status = await check_invoice_status(invoice_id)
    
    if status == 'paid':
        # --- اللحظة الحاسمة: إضافة الرصيد ---
        user_id = query.from_user.id
        
        # استدعاء دالة قاعدة البيانات (التي شرحناها في الفصل 9)
        # ملاحظة: يجب أن ننشئ دالة add_balance_to_user(id, amount) في db_services
        success = add_balance_to_user(user_id, amount)
        
        if success:
            await query.edit_message_text(f"✅ **تم الدفع بنجاح!**\n\nأضيف مبلغ {amount}$ إلى رصيدك.")
            # مسح الفاتورة من الذاكرة لكي لا يضغط مرة أخرى
            del context.user_data['last_invoice_id']
        else:
            await query.edit_message_text("⚠️ تم الدفع ولكن حدث خطأ في قاعدة البيانات. تواصل مع الدعم.")
            
    elif status == 'active':
        await query.edit_message_text("⏳ الفاتورة ما زالت بانتظار الدفع.\nاضغط الزر مرة أخرى بعد إتمام التحويل.", 
                                      reply_markup=query.message.reply_markup) # نعيد نفس الأزرار
    
    elif status == 'expired':
        await query.edit_message_text("❌ انتهت صلاحية هذه الفاتورة. أنشئ واحدة جديدة.")