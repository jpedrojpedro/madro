from django.urls import path
from madro.views import AgentSubscribeView

urlpatterns = [
    path("agent", AgentSubscribeView.as_view()),
]
