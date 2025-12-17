import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    ConversationHandler,
    CallbackQueryHandler
)

# استيراد الخدمات (تأكد أن db_services يحتوي على الدوال الجديدة)
from db_services import (
    get_or_create_user, 
    add_balance_to_user, 
    create_new_deal,
    get_deal_details,      # دالة جديدة
    process_deal_payment   # دالة جديدة
)
from payment_services import create_deposit_invoice, check_invoice_status

# إعداد السجلات (Logs)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
load_dotenv()

# تعريف حالات المحادثة (0, 1, 2 للبائع) و (3, 4 للمشتري)
ASK_PRICE, ASK_DESCRIPTION, CONFIRM_DEAL, PAY_ASK_ID, PAY_CONFIRM = range(5)

# --- 1. القائمة الرئيسية ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(user.id, user.full_name, user.username)
    
    if not db_user:
        await update.message.reply_text("❌ خطأ فني في قاعدة البيانات.")
        return

    msg = (
        f"أهلاً بك {db_user.full_name} في بوت الوسيط 🛡️\n\n"
        f"💰 رصيدك الحالي: {db_user.get_balance_display()}$\n"
        f"🆔 رقمك: `{db_user.id}`\n\n"
        "ماذا تريد أن تفعل؟"
    )
    
    keyboard = [
        [InlineKeyboardButton("📝 إنشاء صفقة (بائع)", callback_data="new_deal_btn")],
        [InlineKeyboardButton("💸 دفع لصفقة (مشتري)", callback_data="new_pay_btn")], # الزر الجديد
        [InlineKeyboardButton("💳 شحن الرصيد", callback_data="deposit_btn")]
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

# ==========================================
#  نظام البائع (Seller Flow)
# ==========================================
async def start_new_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    
    target = query.message if query else update.message
    await target.reply_text("1️⃣ حسناً، أرسل سعر السلعة أو الخدمة بالدولار (مثلاً: 50):")
    return ASK_PRICE

async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        if price <= 0: raise ValueError
        context.user_data['temp_price'] = price
        await update.message.reply_text("2️⃣ عظيم! الآن أرسل وصفاً مختصراً للصفقة:")
        return ASK_DESCRIPTION
    except ValueError:
        await update.message.reply_text("⚠️ خطأ! يرجى إرسال رقم صحيح (مثلاً: 25.5).")
        return ASK_PRICE

async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_desc'] = update.message.text
    price = context.user_data['temp_price']
    desc = context.user_data['temp_desc']

    msg = (
        f"⚠️ **مراجعة الصفقة قبل النشر:**\n\n"
        f"💰 السعر: {price}$\n"
        f"📝 الوصف: {desc}\n\n"
        "هل تريد تأكيد إنشاء الصفقة؟"
    )
    keyboard = [[InlineKeyboardButton("✅ تأكيد ونشر", callback_data="confirm_publish"),
                 InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return CONFIRM_DEAL

async def finalize_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    seller_id = query.from_user.id
    price = context.user_data['temp_price']
    desc = context.user_data['temp_desc']
    
    deal_id = create_new_deal(seller_id, price, desc)
    
    if deal_id:
        await query.edit_message_text(
            f"✅ **تم إنشاء الصفقة بنجاح!**\n\n"
            f"رقم الصفقة: `{deal_id}`\n\n"
            f"الخطوة التالية: أرسل هذا الرقم للمشتري ليقوم بالدفع."
        )
    else:
        await query.edit_message_text("❌ فشل إنشاء الصفقة في قاعدة البيانات.")
    return ConversationHandler.END

# ==========================================
#  نظام المشتري (Buyer Flow) - الجديد
# ==========================================
async def start_pay_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "💸 **دفع قيمة صفقة**\n\n"
        "أرسل لي **رقم الصفقة** التي تريد دفع قيمتها (مثلاً: 105):",
        parse_mode='Markdown'
    )
    return PAY_ASK_ID

async def preview_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        deal_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("⚠️ يرجى إرسال أرقام فقط.")
        return PAY_ASK_ID

    # جلب التفاصيل
    deal = get_deal_details(deal_id)
    
    # فحوصات الأمان
    if not deal:
        await update.message.reply_text("❌ لم يتم العثور على صفقة بهذا الرقم. حاول مرة أخرى:")
        return PAY_ASK_ID
    
    if deal['seller_id'] == update.effective_user.id:
        await update.message.reply_text("⛔ لا يمكنك شراء صفقتك الخاصة!")
        return ConversationHandler.END

    if deal['status'] != 'pending':
        await update.message.reply_text(f"⛔ هذه الصفقة غير متاحة (الحالة: {deal['status']}).")
        return ConversationHandler.END

    # عرض الفاتورة
    context.user_data['paying_deal_id'] = deal_id
    msg = (
        f"🧾 **تفاصيل الصفقة #{deal['id']}**\n\n"
        f"👤 البائع: **{deal['seller_name']}**\n"
        f"💰 المبلغ المطلوب: **{deal['amount']}$**\n"
        f"📝 الوصف: {deal['description']}\n\n"
        "هل تريد دفع المبلغ وحجز الصفقة الآن؟"
    )
    keyboard = [
        [InlineKeyboardButton("✅ موافق ودفع الآن", callback_data="confirm_pay")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return PAY_CONFIRM

async def execute_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    deal_id = context.user_data['paying_deal_id']
    buyer_id = query.from_user.id
    
    # تنفيذ عملية الدفع الذرية
    result = process_deal_payment(deal_id, buyer_id)
    
    if result == "SUCCESS":
        # إشعار المشتري
        await query.edit_message_text(
            f"✅ **تم الدفع وحجز الأموال!**\n\n"
            f"الصفقة #{deal_id} أصبحت نشطة الآن.\n"
            f"لقد قمنا بإبلاغ البائع ليبدأ التنفيذ."
        )
        
        # محاولة إشعار البائع
        deal_info = get_deal_details(deal_id)
        if deal_info:
            try:
                await context.bot.send_message(
                    chat_id=deal_info['seller_id'],
                    text=f"🔔 **تنبيه جديد!**\n\n"
                         f"قام المشتري بدفع قيمة الصفقة #{deal_id}.\n"
                         f"المال محجوز لدينا (Escrow). يمكنك تسليم السلعة/الخدمة الآن بأمان."
                )
            except Exception:
                pass # قد يكون البائع حظر البوت

    elif result == "INSUFFICIENT_FUNDS":
        await query.edit_message_text(
            "⛔ **رصيدك غير كافٍ!**\n\n"
            "يرجى شحن رصيدك أولاً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 شحن الرصيد", callback_data="deposit_btn")]])
        )

    elif result == "DEAL_NOT_PENDING":
        await query.edit_message_text("❌ عذراً، يبدو أن هذه الصفقة تم دفعها بالفعل.")

    else:
        await query.edit_message_text("❌ حدث خطأ غير متوقع.")
        
    return ConversationHandler.END

# ==========================================
#  وظائف عامة وتشغيل
# ==========================================
async def cancel_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text("تم إلغاء العملية.")
    else:
        await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        # استخراج المبلغ: /deposit 10
        amount = float(context.args[0])
        if amount <= 0: raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("❌ خطأ! اكتب الأمر ثم المبلغ.\nمثال: `/deposit 10`")
        return

    msg = await update.message.reply_text("⏳ جاري إنشاء رابط الدفع...")
    
    # استدعاء خدمة الدفع
    invoice_data = await create_deposit_invoice(user_id, amount)
    
    if invoice_data:
        # حفظ رقم الفاتورة للتحقق
        context.user_data['invoice_id'] = invoice_data['invoice_id']
        context.user_data['deposit_amount'] = amount

        keyboard = [
            [InlineKeyboardButton("🔗 اضغط للدفع", url=invoice_data['pay_url'])],
            [InlineKeyboardButton("✅ لقد دفعت", callback_data="check_deposit")]
        ]
        await msg.edit_text(
            f"💳 **شحن رصيد: {amount}$**\nصلاحية الرابط 15 دقيقة.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await msg.edit_text("❌ خطأ في بوابة الدفع.")

# دالة التحقق من الدفع (عند ضغط زر "لقد دفعت")
async def check_deposit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري التحقق...")
    
    invoice_id = context.user_data.get('invoice_id')
    amount = context.user_data.get('deposit_amount')
    
    if not invoice_id:
        await query.edit_message_text("❌ لا توجد عملية معلقة.")
        return

    status = await check_invoice_status(invoice_id)
    
    if status == 'paid':
        add_balance_to_user(query.from_user.id, amount)
        await query.edit_message_text(f"✅ **تم الشحن بنجاح!**\nأضيف {amount}$ لرصيدك.")
    elif status == 'active':
        await query.edit_message_text("⏳ الفاتورة لم تدفع بعد. حاول مجدداً بعد الدفع.", reply_markup=query.message.reply_markup)
    else:
        await query.edit_message_text("❌ انتهت صلاحية الفاتورة.")

async def simple_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("لشحن الرصيد، استخدم الأمر: `/deposit 10` (استبدل 10 بالمبلغ).")

if __name__ == '__main__':
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN missing")
        exit()
        
    app = ApplicationBuilder().token(TOKEN).build()

    # معالج البائع
    seller_handler = ConversationHandler(
        entry_points=[
            CommandHandler('new_deal', start_new_deal),
            CallbackQueryHandler(start_new_deal, pattern="new_deal_btn")
        ],
        states={
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_price)],
            ASK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
            CONFIRM_DEAL: [CallbackQueryHandler(finalize_deal, pattern="confirm_publish")]
        },
        fallbacks=[CommandHandler('cancel', cancel_process), CallbackQueryHandler(cancel_process, pattern="cancel_conv")]
    )

    # معالج المشتري (الجديد)
    buyer_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_pay_deal, pattern="new_pay_btn")],
        states={
            PAY_ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, preview_deal)],
            PAY_CONFIRM: [
                CallbackQueryHandler(execute_payment, pattern="confirm_pay"),
                CallbackQueryHandler(cancel_process, pattern="cancel_conv")
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_process), CallbackQueryHandler(cancel_process, pattern="cancel_conv")]
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(seller_handler)
    app.add_handler(buyer_handler)
    
    # معالج زر الشحن (مؤقت)
    app.add_handler(CallbackQueryHandler(simple_deposit, pattern="deposit_btn"))
    app.add_handler(CommandHandler("deposit", deposit_command)) # <-- هام جداً
    app.add_handler(CallbackQueryHandler(check_deposit_handler, pattern="check_deposit")) # <-- هام جداً

    print("🚀 البوت يعمل الآن بنظام البائع والمشتري الكامل...")
    app.run_polling()