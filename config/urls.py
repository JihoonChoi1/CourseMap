from django.urls import include, path

from planapi.web import index

urlpatterns = [
    path("", index, name="index"),
    path("api/", include("planapi.urls")),
]
