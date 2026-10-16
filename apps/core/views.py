from django.shortcuts import render


def help_page(request):
    return render(request, "core/help.html")
