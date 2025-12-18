import html
import time
import os
import logging
from dotenv import load_dotenv
from db_services import check_spam_protection, verify_admin_action
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from decimal import Decimal, InvalidOperation
from db_services import get_or_create_user, get_user_rating
from utils import get_text
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

# استيراد الخدمات (تأكد أن db_services يحتوي على الدوال الجديدة)
from db_services import (
    solve_dispute_by_admin,
    open_dispute,
    get_or_create_user,
    add_balance_to_user,
    create_new_deal,
    get_deal_details,
    process_deal_payment,
    get_user_active_deals,
    mark_deal_delivered,
    release_deal_funds,
)
from payment_services import create_deposit_invoice, check_invoice_status

# إعداد السجلات (Logs)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
load_dotenv()

# تعريف حالات المحادثة (0, 1, 2 للبائع) و (3, 4 للمشتري)
ASK_PRICE, ASK_DESCRIPTION, CONFIRM_DEAL, PAY_ASK_ID, PAY_CONFIRM = range(5)

USER_COOLDOWNS = {}

def is_spamming(user_id):
    return check_spam_protection(user_id, limit=3, window_seconds=2)
    
    if now - last_time < 1.5:  # إذا ضغط مرة أخرى خلال أقل من ثانية ونصف
        return True
    
    USER_COOLDOWNS[user_id] = now
    return False

# --- 1. القائمة الرئيسية ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # 1. جلب أو إنشاء المستخدم
    db_user = get_or_create_user(user.id, user.full_name, user.username)
    if not db_user:
        await update.message.reply_text("❌ خطأ فني في قاعدة البيانات.")
        return

    # 2. جلب تقييم المستخدم (الميزة الجديدة في UX)
    # هذه الدالة تجلب (⭐ 4.5) أو (جديد 🆕)
    user_rating = get_user_rating(user.id)

    # 3. جلب النص من ملف locales.json (الميزة الجديدة i18n)
    # نمرر المتغيرات (الاسم، الرصيد، التقييم) ليتم تعويضها داخل النص
    msg = get_text(
        "welcome_msg",       # مفتاح النص في ملف JSON
        lang="ar",           # اللغة (يمكنك جعلها ديناميكية لاحقاً)
        name=db_user.full_name,
        balance=db_user.get_balance_display(),
        id=db_user.id,
        rating=user_rating   # <-- مررنا التقييم ليظهر في الرسالة
    )
    
    # 4. الأزرار (يمكنك أيضاً وضع نصوصها في JSON لاحقاً)
    keyboard = [
        [InlineKeyboardButton("📝 إنشاء صفقة (بائع)", callback_data="new_deal_btn")],
        [InlineKeyboardButton("💸 دفع لصفقة (مشتري)", callback_data="new_pay_btn")],
        [InlineKeyboardButton("📂 صفقاتي النشطة", callback_data="my_active_deals")],
        [InlineKeyboardButton("💳 شحن الرصيد", callback_data="deposit_btn")]
    ]
    
    await update.message.reply_text(
        msg, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode='Markdown',
        disable_web_page_preview=True # <--- إضافة حيوية للأمان
    )

# ==========================================
#  نظام البائع (Seller Flow)
# ==========================================
async def start_new_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_spamming(user_id):
        await update.message.reply_text("⏳ مهلاً! أنت تضغط بسرعة كبيرة. انتظر قليلاً.")
        return ConversationHandler.END
    query = update.callback_query
    if query:
        await query.answer()

    target = query.message if query else update.message
    await target.reply_text("1️⃣ حسناً، أرسل سعر السلعة أو الخدمة بالدولار (مثلاً: 50):")
    return ASK_PRICE


async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_price = Decimal(update.message.text)
        
        # 1. الحماية من الأرقام السالبة أو الصفر
        if raw_price <= 0:
             raise ValueError("السعر يجب أن يكون أكبر من صفر")

        # 2. الفلترة الصارمة للكسور (Rounding Strategy)
        # هذا السطر يحول أي رقم مثل 10.559 إلى 10.56 ويقطع أي كسور زائدة بدقة
        price = raw_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        # (اختياري) إذا كنت تريد رفض الكسور الزائدة بدلاً من تقريبها، احتفظ بالتحقق القديم.
        # لكن التقريب هنا أكثر سلاسة للمستخدم:
        
        context.user_data["temp_price"] = price
        await update.message.reply_text("2️⃣ عظيم! الآن أرسل وصفاً مختصراً للصفقة:")
        return ASK_DESCRIPTION
    except (ValueError, InvalidOperation):  # InvalidOperation هي خطأ Decimal
        await update.message.reply_text("⚠️ خطأ! يرجى إرسال رقم صحيح (مثلاً: 25.5).")
        return ASK_PRICE


async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text
    if len(raw_text) > 500:
        await update.message.reply_text("❌ الوصف طويل جداً! يرجى الاختصار (الحد الأقصى 500 حرف).")
        return ASK_DESCRIPTION
    clean_desc = html.escape(raw_text)
    context.user_data['temp_desc'] = clean_desc
    price = context.user_data['temp_price']
    desc = context.user_data["temp_desc"]

    msg = (
        f"⚠️ **مراجعة الصفقة قبل النشر:**\n\n"
        f"💰 السعر: {price}$\n"
        f"📝 الوصف: {desc}\n\n"
        "هل تريد تأكيد إنشاء الصفقة؟"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ تأكيد ونشر", callback_data="confirm_publish"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv"),
        ]
    ]
    await update.message.reply_text(
        msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return CONFIRM_DEAL


async def finalize_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    seller_id = query.from_user.id
    price = context.user_data["temp_price"]
    desc = context.user_data["temp_desc"]

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
        parse_mode="Markdown",
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
        await update.message.reply_text(
            "❌ لم يتم العثور على صفقة بهذا الرقم. حاول مرة أخرى:"
        )
        return PAY_ASK_ID

    if deal["seller_id"] == update.effective_user.id:
        await update.message.reply_text("⛔ لا يمكنك شراء صفقتك الخاصة!")
        return ConversationHandler.END

    if deal["status"] != "pending":
        await update.message.reply_text(
            f"⛔ هذه الصفقة غير متاحة (الحالة: {deal['status']})."
        )
        return ConversationHandler.END

    # عرض الفاتورة
    context.user_data["paying_deal_id"] = deal_id
    msg = (
        f"🧾 **تفاصيل الصفقة #{deal['id']}**\n\n"
        f"👤 البائع: **{deal['seller_name']}**\n"
        f"💰 المبلغ المطلوب: **{deal['amount']}$**\n"
        f"📝 الوصف: {deal['description']}\n\n"
        "هل تريد دفع المبلغ وحجز الصفقة الآن؟"
    )
    keyboard = [
        [InlineKeyboardButton("✅ موافق ودفع الآن", callback_data="confirm_pay")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")],
    ]
    await update.message.reply_text(
        msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return PAY_CONFIRM


async def execute_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    deal_id = context.user_data["paying_deal_id"]
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
                    chat_id=deal_info["seller_id"],
                    text=f"🔔 **تنبيه جديد!**\n\n"
                    f"قام المشتري بدفع قيمة الصفقة #{deal_id}.\n"
                    f"المال محجوز لدينا (Escrow). يمكنك تسليم السلعة/الخدمة الآن بأمان.",
                )
            except Exception:
                pass  # قد يكون البائع حظر البوت

    elif result == "INSUFFICIENT_FUNDS":
        await query.edit_message_text(
            "⛔ **رصيدك غير كافٍ!**\n\n" "يرجى شحن رصيدك أولاً.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("💳 شحن الرصيد", callback_data="deposit_btn")]]
            ),
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
        amount = Decimal(context.args[0])
        if amount <= 0:
            raise ValueError
    except (IndexError, ValueError, InvalidOperation):
        await update.message.reply_text(
            "❌ خطأ!\nاكتب الأمر ثم المبلغ.\nمثال: `/deposit 10`"
        )
        return

    msg = await update.message.reply_text("⏳ جاري إنشاء رابط الدفع...")

    # استدعاء خدمة الدفع
    invoice_data = await create_deposit_invoice(user_id, amount)

    if invoice_data:
        # حفظ رقم الفاتورة للتحقق
        context.user_data["invoice_id"] = invoice_data["invoice_id"]
        context.user_data["deposit_amount"] = amount

        keyboard = [
            [InlineKeyboardButton("🔗 اضغط للدفع", url=invoice_data["pay_url"])],
            [InlineKeyboardButton("✅ لقد دفعت", callback_data="check_deposit")],
        ]
        await msg.edit_text(
            f"💳 **شحن رصيد: {amount}$**\nصلاحية الرابط 15 دقيقة.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await msg.edit_text("❌ خطأ في بوابة الدفع.")


# دالة التحقق من الدفع (عند ضغط زر "لقد دفعت")
async def check_deposit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري التحقق...")

    invoice_id = context.user_data.get("invoice_id")
    amount = context.user_data.get("deposit_amount")

    if not invoice_id:
        await query.edit_message_text("❌ لا توجد عملية معلقة.")
        return

    status = await check_invoice_status(invoice_id)

    if status == "paid":
        add_balance_to_user(query.from_user.id, amount)
        await query.edit_message_text(f"✅ **تم الشحن بنجاح!**\nأضيف {amount}$ لرصيدك.")
    elif status == "active":
        await query.edit_message_text(
            "⏳ الفاتورة لم تدفع بعد. حاول مجدداً بعد الدفع.",
            reply_markup=query.message.reply_markup,
        )
    else:
        await query.edit_message_text("❌ انتهت صلاحية الفاتورة.")


async def simple_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "لشحن الرصيد، استخدم الأمر: `/deposit 10` (استبدل 10 بالمبلغ)."
    )


async def list_deals_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    deals = get_user_active_deals(user_id)  # الدالة الجديدة أعلاه

    if not deals:
        await query.edit_message_text("📭 لا توجد لديك صفقات نشطة حالياً.")
        return

    keyboard = []
    for deal in deals:
        # شكل الزر: "صفقة #10 - بائع - 50$"
        btn_text = f"#{deal['id']} | {deal['role']} | {deal['amount']}$"
        # عند الضغط، نرسل أمر: manage_deal_10
        keyboard.append(
            [InlineKeyboardButton(btn_text, callback_data=f"manage_deal_{deal['id']}")]
        )

    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_home")])

    await query.edit_message_text(
        "📂 **صفقاتك الجارية:**\nاضغط على الصفقة لإدارتها.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def manage_deal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # استخراج رقم الصفقة من الزر (manage_deal_105)
    deal_id = int(query.data.split("_")[2])

    # جلب التفاصيل
    deal = get_deal_details(deal_id)  # موجودة سابقاً
    user_id = query.from_user.id

    if not deal:
        await query.edit_message_text("❌ الصفقة غير موجودة.")
        return

    # تحديد هوية المستخدم (بائع أم مشتري؟)
    is_seller = user_id == deal["seller_id"]

    msg = (
        f"⚙️ **إدارة الصفقة #{deal_id}**\n"
        f"الحالة: `{deal['status']}`\n"
        f"المبلغ: {deal['amount']}$\n"
        f"الوصف: {deal['description']}\n"
    )

    keyboard = []

    if is_seller:
        if deal["status"] == "active":
            msg += "\n💡 **المطلوب:** قم بتنفيذ الخدمة/تسليم السلعة للمشتري (خارج البوت أو في الشات)، ثم اضغط الزر أدناه."
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🚚 تم التسليم", callback_data=f"seller_done_{deal_id}"
                    )
                ]
            )
        elif deal["status"] == "delivered":
            msg += "\n⏳ **ننتظر المشتري:** لقد أبلغت عن التسليم. ننتظر تأكيد المشتري."

    else:  # هو المشتري
        if deal["status"] == "active":
            msg += "\n⏳ **ننتظر البائع:** لم يقم البائع بتسليم الطلب بعد."
        elif deal["status"] == "delivered":
            msg += "\n✅ **البائع أبلغ عن التسليم!**\nتحقق من السلعة/الخدمة. إذا كان كل شيء تمام، اضغط تأكيد."
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "💰 استلمت - حرر المال",
                        callback_data=f"buyer_confirm_{deal_id}",
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🚨 مشكلة / نزاع", callback_data=f"dispute_{deal_id}"
                    )
                ]
            )

    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="my_active_deals")])

    await query.edit_message_text(
        msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def seller_delivered_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    deal_id = int(query.data.split("_")[2])
    seller_id = query.from_user.id

    result = mark_deal_delivered(deal_id, seller_id)  # دالة القاعدة

    if result == "SUCCESS" or isinstance(result, dict):  # لأننا أعدنا قاموساً
        await query.answer("✅ تم تحديث الحالة!")
        # إشعار المشتري
        buyer_id = result["buyer_id"]
        try:
            await context.bot.send_message(
                buyer_id,
                f"📢 **تحديث بخصوص الصفقة #{deal_id}**\n"
                f"يخبرنا البائع أنه أتم التسليم.\n"
                f"يرجى التحقق ثم تأكيد الاستلام من قائمة 'صفقاتي النشطة'.",
            )
        except:
            pass

        # تحديث رسالة البائع
        await query.edit_message_text(
            "✅ **ممتاز!**\nتم إبلاغ المشتري. سننتظر تأكيده لتحرير أموالك."
        )
    else:
        await query.answer("❌ خطأ! ربما الحالة لا تسمح.", show_alert=True)


# 2. المشتري يضغط "تأكيد الاستلام" (تحرير المال)
async def buyer_confirm_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    deal_id = int(query.data.split("_")[2])
    buyer_id = query.from_user.id

    # تحرير الأموال
    res = release_deal_funds(deal_id, buyer_id)  # دالة القاعدة

    if isinstance(res, dict) and res["status"] == "SUCCESS":
        await query.edit_message_text(
            f"🎉 **ألف مبروك! تمت العملية بنجاح.**\n\n"
            f"💸 تم تحويل {res['net_amount']}$ للبائع.\n"
            f"🤝 شكراً لثقتكم بنا.\n\n"
            f"👇 **كيف كان أداء البائع؟** يرجى التقييم:"
        )
        
        seller_id = res['seller_id']
        keyboard = [
            [
                InlineKeyboardButton("⭐ 1", callback_data=f"rate_{deal_id}_1_{seller_id}"),
                InlineKeyboardButton("⭐ 2", callback_data=f"rate_{deal_id}_2_{seller_id}"),
                InlineKeyboardButton("⭐ 3", callback_data=f"rate_{deal_id}_3_{seller_id}"),
                InlineKeyboardButton("⭐ 4", callback_data=f"rate_{deal_id}_4_{seller_id}"),
                InlineKeyboardButton("⭐ 5", callback_data=f"rate_{deal_id}_5_{seller_id}"),
            ]
        ]
        await query.message.reply_text("مقياس الجودة:", reply_markup=InlineKeyboardMarkup(keyboard))

        # إشعار البائع بالمال
        try:
            await context.bot.send_message(
                res["seller_id"],
                f"💵 **مبروك! وصلتك أرباح جديدة.**\n\n"
                f"تم إكمال الصفقة #{deal_id}.\n"
                f"المبلغ الصافي: {res['net_amount']}$\n"
                f"عمولة المنصة: {res['fee']}$\n\n"
                f"رصيدك الحالي قد تم تحديثه.",
            )
        except:
            pass
    else:
        await query.answer("❌ خطأ! لا يمكن إتمام العملية.", show_alert=True)

async def rate_seller_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # تفكيك البيانات: rate_105_5_99999
    data = query.data.split("_")
    deal_id = int(data[1])
    stars = int(data[2])
    seller_id = int(data[3])
    buyer_id = query.from_user.id
    
    from db_services import add_review
    new_avg = add_review(deal_id, buyer_id, seller_id, stars)
    
    if new_avg == "ALREADY_REVIEWED":
        await query.edit_message_text("⚠️ لقد قمت بتقييم هذه الصفقة مسبقاً.")
    elif new_avg:
        await query.edit_message_text(f"✅ **شكراً لك!**\nأصبح تقييم البائع الآن: ⭐ {new_avg:.1f}")
    else:
        await query.edit_message_text("❌ حدث خطأ أثناء التقييم.")

async def dispute_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # الزر يأتي بصيغة: dispute_105
    deal_id = int(query.data.split("_")[1])
    user_id = query.from_user.id

    # محاولة فتح النزاع في القاعدة
    if open_dispute(deal_id, user_id):
        await query.edit_message_text(
            f"⚠️ **تم رفع حالة نزاع للصفقة #{deal_id}**\n\n"
            f"🔒 تم تجميد الأموال.\n"
            f"👮‍♂️ تم استدعاء المشرفين لمراجعة المحادثة.\n\n"
            f"يرجى الانتظار، سيتواصل معك الدعم قريباً."
        )

        # --- إشعار الأدمن (أنت) ---
        admin_id = os.getenv("ADMIN_ID")
        if admin_id:
            try:
                # نرسل لك رابط حساباتهم لتتكلم معهم
                deal_details = get_deal_details(deal_id)  # دالة قديمة نستفيد منها
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🚨 **إنذار: نزاع جديد!**\n\n"
                    f"رقم الصفقة: `{deal_id}`\n"
                    f"المبلغ: {deal_details['amount']}$\n"
                    f"الأطراف: البائع `{deal_details['seller_id']}` ضد المشتري `{user_id}`\n\n"
                    f"للحل استخدم الأمر:\n"
                    f"`/resolve {deal_id} seller` (للبائع)\n"
                    f"`/resolve {deal_id} buyer` (للمشتري)",
                    parse_mode="Markdown",
                )
            except Exception as e:
                print(f"Failed to notify admin: {e}")

    else:
        await query.answer("❌ لا يمكن فتح نزاع لهذه الصفقة حالياً.", show_alert=True)

async def admin_resolve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    # ملاحظة: admin_id لم نعد نحتاجه بشدة هنا لأننا نعتمد على الجدول وقاعدة البيانات
    # لكن لا بأس بتركه كطبقة أمان إضافية لو أحببت

    try:
        # 1. تحليل المدخلات: نتوقع ID ثم الفائز ثم الرمز السري
        deal_id = int(context.args[0])
        winner = context.args[1].lower()
        pin_input = context.args[2] # الرمز السري
    except (IndexError, ValueError):
        # هذا الـ except يغطي أي نقص في البيانات أو خطأ في الصيغة
        await update.message.reply_text(
            "⚠️ **أمان عالي:**\nاستخدم الأمر مع رمز PIN الخاص بك:\n`/resolve [ID] [winner] [PIN]`",
            parse_mode="Markdown"
        )
        return

    # 2. التحقق الأمني الكامل (صلاحية + 2FA)
    # نستدعي دالة التحقق التي أنشأناها في db_services
    auth_status = verify_admin_action(user_id, pin_input, required_role="dispute_agent")
    
    if auth_status == "NOT_ADMIN":
        return # تجاهل بصمت (ليس أدمن أصلاً)
    elif auth_status == "NO_PERMISSION":
        await update.message.reply_text("⛔ ليس لديك صلاحية حل النزاعات.")
        return
    elif auth_status == "WRONG_PIN":
        await update.message.reply_text("❌ **رمز الأمان (PIN) غير صحيح!**\nتم تسجيل محاولة دخول فاشلة.")
        return

    # 3. إذا وصلنا هنا، فالأدمن موثوق ومعه الرمز الصحيح
    if winner not in ["seller", "buyer"]:
        await update.message.reply_text("❌ الفائز يجب أن يكون 'seller' أو 'buyer'.")
        return

    # 4. تنفيذ الحكم
    result = solve_dispute_by_admin(deal_id, winner)

    if isinstance(result, dict) and result["status"] == "SUCCESS":
        await update.message.reply_text(f"✅ {result['msg']}")

        # إبلاغ الطرفين بالحكم النهائي
        notification = f"⚖️ **حكم المحكمة الرقمية**\n\nبخصوص الصفقة #{deal_id}:\n{result['msg']}"
        try:
            await context.bot.send_message(result["buyer_id"], notification)
            await context.bot.send_message(result["seller_id"], notification)
        except:
            pass

    else:
        await update.message.reply_text(f"❌ خطأ: {result}")

async def send_deal_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # التحقق: هل الرسالة نصية أم صورة؟
    if update.message.photo:
        # إذا كانت صورة، النص يكون في الـ caption
        args = context.args  # قد تكون فارغة في الصور
        msg_text = update.message.caption if update.message.caption else ""
        file_id = update.message.photo[-1].file_id  # نأخذ أعلى دقة

        # استخراج رقم الصفقة من الكابشن (صعب قليلاً للمستخدم)
        # لذلك سنبسط الأمر: المستخدم يكتب /msg 105 في الكابشن
        try:
            # نحاول استخراج الرقم من أول كلمة في الكابشن
            deal_id_str = msg_text.split()[1]  # لأن [0] هي /msg
            deal_id = int(deal_id_str)
            # نحذف الأمر والرقم من النص لنرسل الباقي
            clean_text = " ".join(msg_text.split()[2:])
        except:
            await update.message.reply_text(
                "⚠️ لإرسال صورة: ارفق الصورة واكتب في الوصف: \n`/msg [رقم الصفقة] [تعليقك]`"
            )
            return
    else:
        # رسالة نصية عادية
        try:
            deal_id = int(context.args[0])
            msg_text = " ".join(context.args[1:])
            clean_text = msg_text
            file_id = None
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ خطأ! مثال: `/msg 105 مرحباً`")
            return

    # التحقق من الصفقة
    deal = get_deal_details(deal_id)
    if not deal:
        await update.message.reply_text("❌ صفقة غير موجودة.")
        return

    if user_id not in [deal["seller_id"], deal.get("buyer_id")]:
        await update.message.reply_text("⛔ لست طرفاً في هذه الصفقة.")
        return

    # 1. الحفظ في قاعدة البيانات (الأدلة)
    from db_services import save_message_to_log

    save_message_to_log(deal_id, user_id, text=clean_text, file_id=file_id)

    # 2. الإرسال للطرف الآخر
    receiver_id = (
        deal["buyer_id"] if user_id == deal["seller_id"] else deal["seller_id"]
    )

    try:
        header = f"📩 **رسالة من الطرف الآخر (صفقة #{deal_id}):**\n\n"
        if file_id:
            await context.bot.send_photo(
                chat_id=receiver_id,
                photo=file_id,
                caption=header + clean_text,
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_message(
                chat_id=receiver_id, text=header + clean_text, parse_mode="Markdown"
            )

        await update.message.reply_text("✅ تم الإرسال.")
    except Exception as e:
        await update.message.reply_text(
            f"❌ لم يتمكن الطرف الآخر من استلام الرسالة (ربما حظر البوت)."
        )


async def admin_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    admin_id = os.getenv("ADMIN_ID")

    if user_id != admin_id:
        return  # حماية

    try:
        deal_id = int(context.args[0])
        from db_services import get_deal_logs

        logs = get_deal_logs(deal_id)

        if not logs:
            await update.message.reply_text("📭 السجل فارغ لهذه الصفقة.")
            return

        await update.message.reply_text(f"⚖️ **سجل المحكمة للصفقة #{deal_id}:**")

        for log in logs:
            sender = "البائع"  # يمكنك تحسينها لجلب الاسم
            time_str = log.created_at.strftime("%Y-%m-%d %H:%M")

            if log.is_image:
                await update.message.reply_photo(
                    photo=log.file_id,
                    caption=f"👤 {sender} [{time_str}]\n📎 {log.message_text or 'بدون تعليق'}",
                )
            else:
                await update.message.reply_text(
                    f"👤 {sender} [{time_str}]:\n💬 {log.message_text}"
                )

    except (IndexError, ValueError):
        await update.message.reply_text("استخدم: `/logs [رقم الصفقة]`")


# أمر سري لك فقط لشحن رصيدك وتجربة البوت
async def dev_faucet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # سنضيف 100 دولار وهمية لرصيدك في القاعدة
    from db_services import add_balance_to_user

    add_balance_to_user(user_id, 100)
    await update.message.reply_text(
        "✅ تم إضافة 100$ رصيد وهمي لمحفظتك داخل البوت بنجاح! يمكنك الآن تجربة الشراء."
    )


if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN missing")
        exit()

    app = ApplicationBuilder().token(TOKEN).build()

    # معالج البائع
    seller_handler = ConversationHandler(
        entry_points=[
            CommandHandler("new_deal", start_new_deal),
            CallbackQueryHandler(start_new_deal, pattern="new_deal_btn"),
        ],
        states={
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_price)],
            ASK_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)
            ],
            CONFIRM_DEAL: [
                CallbackQueryHandler(finalize_deal, pattern="confirm_publish")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_process),
            CallbackQueryHandler(cancel_process, pattern="cancel_conv"),
        ],
    )

    # معالج المشتري (الجديد)
    buyer_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_pay_deal, pattern="new_pay_btn")],
        states={
            PAY_ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, preview_deal)],
            PAY_CONFIRM: [
                CallbackQueryHandler(execute_payment, pattern="confirm_pay"),
                CallbackQueryHandler(cancel_process, pattern="cancel_conv"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_process),
            CallbackQueryHandler(cancel_process, pattern="cancel_conv"),
        ],
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(seller_handler)
    app.add_handler(buyer_handler)

    # معالج زر الشحن (مؤقت)
    app.add_handler(CallbackQueryHandler(simple_deposit, pattern="deposit_btn"))
    app.add_handler(CommandHandler("deposit", deposit_command))  # <-- هام جداً
    app.add_handler(
        CallbackQueryHandler(check_deposit_handler, pattern="check_deposit")
    )  # <-- هام جداً
    app.add_handler(CallbackQueryHandler(list_deals_handler, pattern="my_active_deals"))
    app.add_handler(CallbackQueryHandler(manage_deal_handler, pattern="^manage_deal_"))
    app.add_handler(
        CallbackQueryHandler(seller_delivered_action, pattern="^seller_done_")
    )
    app.add_handler(
        CallbackQueryHandler(buyer_confirm_action, pattern="^buyer_confirm_")
    )
    app.add_handler(CallbackQueryHandler(dispute_action_handler, pattern="^dispute_"))
    app.add_handler(CommandHandler("resolve", admin_resolve_command))
    app.add_handler(CommandHandler("msg", send_deal_message))
    app.add_handler(CommandHandler("logs", admin_logs_command))
    app.add_handler(CallbackQueryHandler(rate_seller_handler, pattern="^rate_"))
    app.add_handler(CommandHandler("faucet", dev_faucet))

    print("🚀 البوت يعمل الآن بنظام البائع والمشتري الكامل...")
    app.run_polling()
