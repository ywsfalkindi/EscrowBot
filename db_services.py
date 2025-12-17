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

def add_balance(telegram_id, amount_in_dollars):
    session = Session()
    try:
        user = session.query(User).filter_by(id=telegram_id).first()
        if user:
            # نحول الدولار لسنتات
            cents = int(amount_in_dollars * 100)
            user.balance_cents += cents
            session.commit()
            print(f"💰 Added {amount_in_dollars}$ to user {telegram_id}")
            return True
    except Exception as e:
        print(f"Error adding balance: {e}")
    finally:
        session.close()
    return False

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