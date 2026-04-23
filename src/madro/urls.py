from django.urls import path
from madro.views import AgentSubscribeView, ThreadView

urlpatterns = [
    path("agent", AgentSubscribeView.as_view()),
    path("thread", ThreadView.as_view()),
]
