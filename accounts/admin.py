from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["last_name"]
    list_display = ["iin", "last_name", "first_name", "role", "group", "is_active"]
    list_filter = ["role", "group", "is_active"]
    search_fields = ["iin", "last_name", "first_name", "email"]
    fieldsets = (
        (None, {"fields": ("iin", "password")}),
        ("Личные данные", {"fields": ("last_name", "first_name", "patronymic", "email",
                                      "birth_date", "group")}),
        ("Анкета", {"fields": ("degree", "application_type", "citizenship", "doc_type",
                               "doc_number", "study_language", "interface_language")}),
        ("Доступ", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("iin", "last_name", "first_name", "role", "group",
                           "password1", "password2")}),
    )
