import json
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError
from madro.data_wrappers import AgentIn, AgentOut, TopicOut
from madro.models import Agent
from madro.workflows.topic_categorization_agent import categorize_and_assign


@method_decorator(csrf_exempt, name="dispatch")
class AgentSubscribeView(View):
    async def post(self, request):
        try:
            payload = AgentIn.model_validate(json.loads(request.body))
        except (json.JSONDecodeError, ValidationError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        agent = await Agent.objects.acreate(
            name=payload.name,
            description=payload.description,
            uri=payload.uri,
            mcp_schema=payload.mcp_schema,
            candidate_topics=payload.candidate_topics,
        )

        topics = await categorize_and_assign(agent)

        return JsonResponse(
            AgentOut(
                id=agent.id,
                topics=[TopicOut(id=t.id, name=t.name, description=t.description) for t in topics],
            ).model_dump(mode="json"),
            status=201,
        )
