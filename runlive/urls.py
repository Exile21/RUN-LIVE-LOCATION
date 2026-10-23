from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from tracking.views import RunnerLoginView, runner_signup

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(pattern_name="event_list", permanent=False)),
    path("", include("tracking.urls")),
    path("accounts/signup/", runner_signup, name="signup"),
    path("accounts/login/", RunnerLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
]
