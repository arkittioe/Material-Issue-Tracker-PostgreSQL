# file: data/constants.py
"""
ثابت‌ها و مقادیر مشترک بین سرویس‌های مختلف پروژه
"""

# نگاشت نوع اسپول به کلمات کلیدی معادل در داده‌ها
SPOOL_TYPE_MAPPING = {
    "FLANGE": ("FLG", "FLAN", "FLN"),
    "ELBOW": ("ELB", "ELL", "ELBO"),
    "TEE": ("TEE",),
    "REDUCER": ("RED", "REDU", "CON", "CONN", "ECC"),
    "CAP": ("CAP",),
    "PIPE": ("PIPE", "PIP"),
    # در صورت اضافه شدن انواع جدید، اینجا اضافه کنید
}
