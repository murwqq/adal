from django.contrib import admin

from .models import Attempt, Choice, Exam, Question, Subject, Violation


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["text", "subject", "topic", "difficulty", "author", "is_active"]
    list_filter = ["subject", "difficulty", "is_active"]
    search_fields = ["text", "topic"]
    inlines = [ChoiceInline]


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ["title", "subject", "author", "opens_at", "closes_at", "is_published"]
    list_filter = ["subject", "is_published"]


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ["student", "exam", "status", "score", "max_score", "violation_count"]
    list_filter = ["status", "exam"]


admin.site.register(Subject)
admin.site.register(Violation)
