from django.contrib import admin

from .models import ChatMessage, Conversation


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ("role", "model_name", "content", "error", "latency_ms", "completion_tokens", "stopped")
    readonly_fields = fields


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "llm", "created_at", "updated_at")
    search_fields = ("title", "messages__content")
    inlines = [ChatMessageInline]
