from sqlalchemy import create_engine, Column, BigInteger, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from sqlalchemy import ForeignKey, Text, Enum # استيرادات إضافية
from sqlalchemy.orm import relationship

# 1. إنشاء "القاعدة" (Base) التي سنبني عليها الجداول
Base = declarative_base()

class User(Base):
    # اسم الجدول كما سيظهر في قاعدة البيانات
    __tablename__ = 'users'

    # 1. عمود الهوية (المفتاح الأساسي)
    # primary_key=True: يعني لا يمكن تكرار هذا الرقم، وهو وسيلة البحث السريعة
    id = Column(BigInteger, primary_key=True)

    # 2. بيانات تليجرام (قابلة للتغيير)
    username = Column(String, nullable=True)  # قد لا يكون لديه يوزرنيم
    full_name = Column(String, nullable=False) # لابد أن يكون له اسم

    # 3. المنطقة المالية (أهم جزء)
    # نخزن القيمة بالسنت (Cents). مثال: 1000 تعني 10.00$
    balance_cents = Column(BigInteger, default=0) 

    # 4. السمعة والثقة
    # default=0: أي مستخدم جديد يبدأ بتقييم صفر
    reputation = Column(Integer, default=0)
    deals_count = Column(Integer, default=0) # عدد الصفقات الناجحة

    # 5. الأمان
    is_banned = Column(Boolean, default=False) # هل هو محظور؟
    is_admin = Column(Boolean, default=False)  # هل هو مشرف؟

    # 6. التوقيت
    # default=datetime.utcnow: يسجل وقت الانضمام تلقائياً
    joined_at = Column(DateTime, default=datetime.utcnow)

    # --- دوال مساعدة داخل الكلاس (Utility Methods) ---
    
    # دالة لتحويل الرصيد من سنتات (قاعدة البيانات) إلى دولار (للعرض)
    def get_balance_display(self):
        return self.balance_cents / 100.0

    # دالة لطباعة شكل المستخدم برمجياً (للمبرمج فقط)
    def __repr__(self):
        return f"<User(id={self.id}, name={self.full_name}, balance={self.get_balance_display()}$)>"

# 1. تعريف الحالات كثوابت (عشان ما نغلط في الإملاء لاحقاً)
class DealStatus:
    PENDING = "pending"       # بانتظار الدفع
    ACTIVE = "active"         # الفلوس محجوزة
    DELIVERED = "delivered"   # البائع سلم
    COMPLETED = "completed"   # انتهت بنجاح
    CANCELED = "canceled"     # ألغيت
    DISPUTE = "dispute"       # في مشكلة

class Deal(Base):
    __tablename__ = 'deals'

    # 1. رقم الصفقة الفريد
    id = Column(Integer, primary_key=True)

    # 2. ربط الصفقة بالبشر (Foreign Keys)
    # المشتري: يربط مع users.id
    buyer_id = Column(BigInteger, ForeignKey('users.id'), nullable=True)
    # البائع: يربط مع users.id
    seller_id = Column(BigInteger, ForeignKey('users.id'), nullable=False)

    # 3. المال والوصف
    amount_cents = Column(BigInteger, default=0) # المبلغ بالسنت (5000 = 50$)
    description = Column(Text, nullable=False)   # تفاصيل الاتفاق

    # 4. الحالة (أخطر عمود)
    status = Column(String, default=DealStatus.PENDING)

    # 5. التوقيتات
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- العلاقات البرمجية (Relationships) ---
    # هذه الأسماء (buyer, seller) سنستخدمها في بايثون للوصول لبيانات المستخدم بسهولة
    # مثال: deal.buyer.full_name سيجلب اسم المشتري مباشرة
    buyer = relationship("User", foreign_keys=[buyer_id], backref="purchases")
    seller = relationship("User", foreign_keys=[seller_id], backref="sales")

    # دالة للعرض الجميل
    def __repr__(self):
        return f"<Deal(id={self.id}, status={self.status}, amount={self.amount_cents})>"

# اسم ملف قاعدة البيانات (SQLite)
DB_NAME = "escrow_bot.db"

# إنشاء المحرك
engine = create_engine(f"sqlite:///{DB_NAME}", echo=False)

# إنشاء جلسة (Session) للتعامل مع البيانات
Session = sessionmaker(bind=engine, expire_on_commit=False)

# دالة لإنشاء الجداول فعلياً
def init_db():
    Base.metadata.create_all(engine)
    print(f"✅ Database '{DB_NAME}' created successfully with all tables!")

if __name__ == "__main__":
    # هذا السطر يعمل فقط لو شغلت الملف مباشرة للتجربة
    init_db()        