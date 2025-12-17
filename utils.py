import json

# تحميل الملف مرة واحدة عند التشغيل
with open("locales.json", "r", encoding="utf-8") as f:
    TEXTS = json.load(f)

def get_text(key, lang="ar", **kwargs):
    """
    key: مفتاح النص (مثلاً welcome)
    lang: لغة المستخدم (سنثبتها ar حالياً)
    kwargs: المتغيرات لتعويض الأقواس {}
    """
    try:
        text = TEXTS[key][lang]
        return text.format(**kwargs)
    except KeyError:
        return f"MISSING_TEXT: {key}"