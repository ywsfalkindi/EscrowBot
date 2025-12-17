from models import Session, User
from models import Deal, DealStatus

def get_or_create_user(telegram_id, full_name, username):
    session = Session() # فتح اتصال
    try:
        # ابحث عن المستخدم بالـ ID
        user = session.query(User).filter_by(id=telegram_id).first()
        
        if not user:
            # إذا لم يوجد، أنشئ واحداً جديداً
            user = User(
                id=telegram_id,
                full_name=full_name,
                username=username
            )
            session.add(user)
            session.commit() # احفظ التغييرات (Save)
            print(f"➕ New user added: {full_name}")
        else:
            # تحديث البيانات لو تغير اسمه
            if user.full_name != full_name or user.username != username:
                user.full_name = full_name
                user.username = username
                session.commit()
                
        return user
    except Exception as e:
        session.rollback() # لو حصل خطأ، الغِ العملية
        print(f"Error: {e}")
    finally:
        session.close() # أغلق الاتصال دائماً!

def create_new_deal(seller_id, amount_dollars, description):
    session = Session()
    try:
        # تحويل الدولار لسنتات
        amount_cents = int(amount_dollars * 100)
        
        new_deal = Deal(
            seller_id=seller_id,
            amount_cents=amount_cents,
            description=description,
            status=DealStatus.PENDING # الحالة الافتراضية
            # buyer_id ما زال فارغاً لأن المشتري لم يدخل بعد
        )
        
        session.add(new_deal)
        session.commit()
        
        # نحتاج الـ ID لنعطيه للبائع
        # (refresh) تجلب الـ ID الذي تولد تلقائياً
        session.refresh(new_deal) 
        
        print(f"📝 Deal #{new_deal.id} created by {seller_id}")
        return new_deal.id
        
    except Exception as e:
        print(f"Error creating deal: {e}")
        session.rollback()
        return None
    finally:
        session.close()

def get_deal_by_id(deal_id):
    session = Session()
    try:
        # نبحث عن الصفقة بالرقم
        deal = session.query(Deal).filter_by(id=deal_id).first()
        if deal:
            # خدعة بسيطة: SQLAlchemy يغلق الجلسة، والبيانات قد تختفي
            # سنقوم باستخراج البيانات المهمة قبل إغلاق الجلسة
            # (في المشاريع الكبيرة نستخدم طرقاً أفضل، لكن هذا يكفي الآن)
            session.expunge(deal) 
            return deal
    except Exception as e:
        print(f"Error fetching deal: {e}")
    finally:
        session.close()
    return None

def process_deal_payment(deal_id, buyer_id):
    session = Session()
    try:
        # 1. جلب الصفقة
        deal = session.query(Deal).filter_by(id=deal_id).first()
        if not deal:
            return "DEAL_NOT_FOUND"
            
        # هل الصفقة ما زالت معلقة؟ (لا ندفع لصفقة مدفوعة أصلاً!)
        if deal.status != DealStatus.PENDING:
            return "DEAL_NOT_PENDING"
            
        # 2. جلب المشتري
        buyer = session.query(User).filter_by(id=buyer_id).first()
        
        # 3. التحقق من الرصيد
        if buyer.balance_cents < deal.amount_cents:
            return "INSUFFICIENT_FUNDS" # ليس لديه مال كافٍ
            
        # --- اللحظة الحاسمة (Atomic Transaction) ---
        
        # أ. نربط المشتري بالصفقة
        deal.buyer_id = buyer_id
        
        # ب. نخصم المال من المشتري
        buyer.balance_cents -= deal.amount_cents
        
        # ج. نغير حالة الصفقة لنشطة
        deal.status = DealStatus.ACTIVE
        
        # د. الحفظ النهائي
        session.commit()
        print(f"🔒 Funds locked for Deal #{deal_id}. Buyer: {buyer_id}")
        return "SUCCESS"
        
    except Exception as e:
        session.rollback() # تراجع فوراً عند أي خطأ
        print(f"Payment Error: {e}")
        return "ERROR"
    finally:
        session.close()

def add_balance_to_user(telegram_id, amount_usd):
    """
    تقوم بإضافة مبلغ بالدولار إلى رصيد المستخدم في قاعدة البيانات.
    يتم تحويل المبلغ لسنتات لضمان الدقة المالية.
    """
    session = Session()
    try:
        # 1. البحث عن المستخدم
        user = session.query(User).filter_by(id=telegram_id).first()
        
        if not user:
            print(f"❌ User {telegram_id} not found in database!")
            return False

        # 2. تحويل المبلغ لسنتات (الضرب في 100)
        # نستخدم int لضمان عدم وجود كسور عشرية في قاعدة البيانات
        cents_to_add = int(amount_usd * 100)

        # 3. تحديث الرصيد
        user.balance_cents += cents_to_add
        
        # 4. حفظ التغييرات قطعياً
        session.commit()
        print(f"💰 Balance Updated: User {telegram_id} received {amount_usd}$.")
        return True

    except Exception as e:
        # في حال حدوث أي خطأ (انقطاع كهرباء، خطأ في الهاردسك) تراجع فوراً
        session.rollback()
        print(f"❌ Database Error in add_balance: {e}")
        return False
    finally:
        # إغلاق الجلسة لتحرير موارد السيرفر
        session.close()

def get_deal_details(deal_id):
    """
    تجلب تفاصيل الصفقة ليعاينها المشتري قبل الدفع.
    تعيد قاموساً (Dictionary) أو None إذا لم توجد.
    """
    session = Session()
    try:
        deal = session.query(Deal).filter_by(id=deal_id).first()
        
        if not deal:
            return None
            
        # نحتاج اسم البائع لنعرضه للمشتري (زيادة في الثقة)
        # بما أننا نستخدم expire_on_commit=False في models.py، يمكننا الوصول للعلاقات
        seller_name = deal.seller.full_name if deal.seller else "مستخدم غير معروف"
        
        return {
            "id": deal.id,
            "seller_id": deal.seller_id,
            "seller_name": seller_name,
            "amount": deal.amount_cents / 100.0, # تحويل لدولار
            "description": deal.description,
            "status": deal.status
        }
    except Exception as e:
        print(f"❌ Error fetching deal details: {e}")
        return None
    finally:
        session.close()

def mark_deal_delivered(deal_id, seller_id):
    """
    يقوم البائع بتحويل حالة الصفقة إلى 'تم التسليم'.
    """
    session = Session()
    try:
        # 1. جلب الصفقة والتأكد أن هذا المستخدم هو البائع فعلاً
        deal = session.query(Deal).filter_by(id=deal_id, seller_id=seller_id).first()
        
        if not deal:
            return "NOT_FOUND" # صفقة غير موجودة أو ليس هو البائع
            
        # 2. هل الصفقة في حالة نشطة؟ (لا يمكن تسليم صفقة ملغاة أو منتهية)
        if deal.status != DealStatus.ACTIVE:
            return "WRONG_STATUS"
            
        # 3. تغيير الحالة
        deal.status = DealStatus.DELIVERED
        session.commit()
        
        # نعيد ID المشتري لنرسل له تنبيهاً
        return {"status": "SUCCESS", "buyer_id": deal.buyer_id}
        
    except Exception as e:
        session.rollback()
        print(f"Error marking delivered: {e}")
        return "ERROR"
    finally:
        session.close()

def release_deal_funds(deal_id, buyer_id):
    """
    يقوم المشتري بتأكيد الاستلام، فيتم تحويل المال للبائع بعد خصم العمولة.
    """
    session = Session()
    try:
        # 1. جلب الصفقة
        deal = session.query(Deal).filter_by(id=deal_id, buyer_id=buyer_id).first()
        
        if not deal:
            return "NOT_FOUND"
            
        # هل الحالة تسمح؟ (يجب أن تكون ACTIVE أو DELIVERED)
        if deal.status not in [DealStatus.ACTIVE, DealStatus.DELIVERED]:
            return "WRONG_STATUS"
            
        # 2. جلب البائع (لنعطيه المال)
        seller = session.query(User).filter_by(id=deal.seller_id).first()
        
        # --- الحسابات المالية (The Money Logic) ---
        total_amount = deal.amount_cents
        
        # حساب العمولة (مثلاً 5%)
        # معادلة: المبلغ * 0.05
        fee_cents = int(total_amount * 0.05) 
        
        # المبلغ الصافي للبائع
        net_amount = total_amount - fee_cents
        
        # 3. تنفيذ التحويل (Atomic Transaction)
        seller.balance_cents += net_amount  # زيادة رصيد البائع
        deal.status = DealStatus.COMPLETED  # إغلاق الصفقة
        
        # (اختياري) يمكنك إضافة جدول للأرباح لتسجيل الـ fee_cents لك
        
        session.commit()
        
        return {
            "status": "SUCCESS",
            "seller_id": seller.id,
            "net_amount": net_amount / 100.0, # للطباعة
            "fee": fee_cents / 100.0          # للطباعة
        }
        
    except Exception as e:
        session.rollback()
        print(f"Error releasing funds: {e}")
        return "ERROR"
    finally:
        session.close()

def get_user_active_deals(user_id):
    """تجلب الصفقات التي يكون فيها المستخدم بائعاً أو مشترياً وحالتها نشطة"""
    session = Session()
    try:
        deals = session.query(Deal).filter(
            ((Deal.seller_id == user_id) | (Deal.buyer_id == user_id)),
            Deal.status.in_([DealStatus.ACTIVE, DealStatus.DELIVERED])
        ).all()
        
        # استخراج البيانات المهمة فقط
        results = []
        for d in deals:
            role = "بائع" if d.seller_id == user_id else "مشتري"
            results.append({"id": d.id, "amount": d.amount_cents/100, "role": role, "status": d.status})
        return results
    finally:
        session.close()

def open_dispute(deal_id, user_id):
    """
    يقوم أحد الطرفين برفع حالة 'نزاع'.
    """
    session = Session()
    try:
        deal = session.query(Deal).filter_by(id=deal_id).first()
        
        # 1. هل الصفقة موجودة؟
        if not deal: return False
        
        # 2. هل الشخص الذي ضغط الزر له علاقة بالصفقة؟ (أمان)
        if user_id not in [deal.buyer_id, deal.seller_id]:
            return False

        # 3. هل الصفقة في حالة تسمح بالنزاع؟ (يجب أن تكون نشطة أو مسلمة)
        if deal.status not in [DealStatus.ACTIVE, DealStatus.DELIVERED]:
            return False

        # 4. تغيير الحالة وتجميد كل شيء
        deal.status = DealStatus.DISPUTE
        session.commit()
        return True
        
    except Exception as e:
        print(f"Error opening dispute: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def solve_dispute_by_admin(deal_id, winner_role):
    """
    الأدمن يقرر الفائز:
    - winner_role = 'seller' -> المال يذهب للبائع (إتمام الصفقة).
    - winner_role = 'buyer'  -> المال يعود للمشتري (إلغاء الصفقة).
    """
    session = Session()
    try:
        deal = session.query(Deal).filter_by(id=deal_id).first()
        
        # التأكد أن الصفقة في حالة نزاع فعلاً
        if not deal or deal.status != DealStatus.DISPUTE:
            return "NOT_DISPUTE"

        # جلب أطراف النزاع
        seller = session.query(User).filter_by(id=deal.seller_id).first()
        buyer = session.query(User).filter_by(id=deal.buyer_id).first()

        # --- السيناريو 1: الحكم للبائع ---
        if winner_role == "seller":
            # نحسب العمولة كالمعتاد
            fee = int(deal.amount_cents * 0.05)
            net_profit = deal.amount_cents - fee
            
            seller.balance_cents += net_profit # إضافة المال للبائع
            deal.status = DealStatus.COMPLETED # إغلاق كصفقة ناجحة
            
            msg = "تم الحكم لصالح البائع."

        # --- السيناريو 2: الحكم للمشتري ---
        elif winner_role == "buyer":
            # نعيد المبلغ كاملاً للمشتري (بدون خصم عمولة عادةً، أو حسب سياستك)
            buyer.balance_cents += deal.amount_cents # استرداد كامل
            deal.status = DealStatus.CANCELED # إلغاء الصفقة
            
            msg = "تم الحكم لصالح المشتري واسترداد المال."
        
        else:
            return "INVALID_WINNER"

        session.commit()
        return {"status": "SUCCESS", "msg": msg, "buyer_id": deal.buyer_id, "seller_id": deal.seller_id}

    except Exception as e:
        print(f"Admin Resolve Error: {e}")
        session.rollback()
        return "ERROR"
    finally:
        session.close()