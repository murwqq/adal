from django.urls import path

from . import views

app_name = "exams"

urlpatterns = [
    path("subjects/new/", views.subject_create, name="subject_create"),
    path("subjects/<int:pk>/delete/", views.subject_delete, name="subject_delete"),
    path("bank/", views.question_list, name="question_list"),
    path("bank/new/", views.question_edit, name="question_new"),
    path("bank/import/", views.question_import, name="question_import"),
    path("bank/<int:pk>/", views.question_edit, name="question_edit"),
    path("bank/<int:pk>/delete/", views.question_delete, name="question_delete"),
    path("exams/", views.exam_list, name="exam_list"),
    path("exams/new/", views.exam_edit, name="exam_new"),
    path("exams/<int:pk>/edit/", views.exam_edit, name="exam_edit"),
    path("exams/<int:pk>/questions/", views.exam_questions, name="exam_questions"),
    path("exams/<int:pk>/delete/", views.exam_delete, name="exam_delete"),
    path("exams/<int:pk>/results/", views.exam_results, name="exam_results"),
    path("exams/<int:pk>/analysis/", views.exam_analysis, name="exam_analysis"),
    path("exams/<int:pk>/analysis/<int:qid>/ai/", views.exam_question_ai, name="exam_question_ai"),
    path("exams/<int:pk>/monitor/", views.exam_monitor, name="exam_monitor"),
    path("exams/<int:pk>/monitor/data/", views.exam_monitor_data, name="exam_monitor_data"),
    path("exams/<int:pk>/start/", views.exam_start, name="exam_start"),
    path("attempts/<int:pk>/", views.attempt_detail, name="attempt_detail"),
    path("attempts/<int:pk>/manage/", views.attempt_manage, name="attempt_manage"),
    path("attempt/<int:pk>/", views.attempt_take, name="attempt_take"),
    path("attempt/<int:pk>/q/<int:position>/", views.attempt_question, name="attempt_question"),
    path("attempt/<int:pk>/answer/", views.attempt_answer, name="attempt_answer"),
    path("attempt/<int:pk>/event/", views.attempt_event, name="attempt_event"),
    path("attempt/<int:pk>/ping/", views.attempt_ping, name="attempt_ping"),
    path("attempt/<int:pk>/finish/", views.attempt_finish, name="attempt_finish"),
    path("attempt/<int:pk>/result/", views.attempt_result, name="attempt_result"),
    path("attempt/<int:pk>/tutor/<int:item_pk>/", views.attempt_tutor, name="attempt_tutor"),
]
