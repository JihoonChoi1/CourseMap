from django.urls import path

from planapi.views import PlanJobView, StudentPlanJobView, StudentPlanView

urlpatterns = [
    path("students/<str:student_id>/plan", StudentPlanView.as_view(), name="student-plan"),
    path("students/<str:student_id>/plan-jobs", StudentPlanJobView.as_view(), name="student-plan-jobs"),
    path("plan-jobs/<str:job_id>", PlanJobView.as_view(), name="plan-job"),
]
