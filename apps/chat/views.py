import json

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.llms.models import LLMModel

from .models import ChatMessage, Conversation
from .services.chat import MAX_MESSAGE_CHARS, message_meta, stream_reply, title_from

SIDEBAR_SIZE = 50


def _default_llm(conversation=None):
    active = LLMModel.objects.filter(is_active=True)
    if conversation and conversation.llm and conversation.llm.is_active:
        return conversation.llm
    return active.filter(selected_by_default=True).first() or active.first()


def chat(request, pk=None):
    conversation = get_object_or_404(Conversation, pk=pk) if pk else None
    history = list(conversation.messages.select_related("llm")) if conversation else []
    return render(request, "chat/chat.html", {
        "conversation": conversation,
        "history": history,
        "last_reply_id": history[-1].pk if history and not history[-1].is_user else None,
        "awaiting_reply": bool(history) and history[-1].is_user,
        "conversations": Conversation.objects.all()[:SIDEBAR_SIZE],
        "llms": LLMModel.objects.filter(is_active=True),
        "current_llm": _default_llm(conversation),
        "max_chars": MAX_MESSAGE_CHARS,
    })


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stream_response(conversation, llm, start: dict):
    def events():
        yield _sse("start", start)
        for event, data in stream_reply(conversation, llm):
            yield _sse(event, data)

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # stop proxies from holding the stream back
    return response


def _pick_llm(request):
    return LLMModel.objects.filter(pk=request.POST.get("model") or 0, is_active=True).first()


@require_POST
def send(request):
    """Save the patient's message, then stream the model's reply as server-sent events."""
    text = request.POST.get("message", "").strip()
    llm = _pick_llm(request)
    if not text:
        return JsonResponse({"error": "Type a message first."}, status=400)
    if len(text) > MAX_MESSAGE_CHARS:
        return JsonResponse({"error": f"Messages are limited to {MAX_MESSAGE_CHARS} characters."}, status=400)
    if not llm:
        return JsonResponse({"error": "Pick a model."}, status=400)

    conv_id = request.POST.get("conversation")
    created = not conv_id
    conversation = get_object_or_404(Conversation, pk=conv_id) if conv_id else Conversation(title=title_from(text))
    conversation.llm = llm
    conversation.save()
    user_msg = ChatMessage.objects.create(conversation=conversation, role=ChatMessage.Role.USER, content=text)
    return _stream_response(conversation, llm, {
        "conversation": conversation.pk,
        "url": reverse("chat:conversation", args=[conversation.pk]),
        "title": conversation.title,
        "created": created,
        "user_message": user_msg.pk,
    })


@require_POST
def regenerate(request, pk):
    """Replace the last reply (failed, stopped or just not liked) with a new one, from any model."""
    conversation = get_object_or_404(Conversation, pk=pk)
    llm = _pick_llm(request)
    if not llm:
        return JsonResponse({"error": "Pick a model."}, status=400)
    last = conversation.messages.last()
    if last and not last.is_user:
        last.delete()
    if not conversation.messages.filter(role=ChatMessage.Role.USER).exists():
        return JsonResponse({"error": "There is no message to reply to."}, status=400)
    conversation.llm = llm
    conversation.save(update_fields=["llm", "updated_at"])
    return _stream_response(conversation, llm, {
        "conversation": conversation.pk,
        "url": reverse("chat:conversation", args=[conversation.pk]),
        "title": conversation.title,
        "created": False,
    })


@require_POST
def delete(request, pk):
    get_object_or_404(Conversation, pk=pk).delete()
    return redirect("chat:new")


def export(request, pk):
    """The whole conversation as JSON, for analysis outside the app."""
    conversation = get_object_or_404(Conversation, pk=pk)
    data = {
        "id": conversation.pk,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "messages": [
            {"role": m.role, "content": m.content, "error": m.error, "stopped": m.stopped,
             "created_at": m.created_at.isoformat(), "openrouter_id": m.openrouter_id, **message_meta(m)}
            for m in conversation.messages.all()
        ],
    }
    response = JsonResponse(data, json_dumps_params={"indent": 2})
    response["Content-Disposition"] = f'attachment; filename="chat_{conversation.pk}.json"'
    return response
