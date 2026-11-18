from django.http import JsonResponse


def health(request):
    return JsonResponse({"status": "ok"})


def home(request):
    return JsonResponse({"name": "Order-to-Audit", "phase": 1})
