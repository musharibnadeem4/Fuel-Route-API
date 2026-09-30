"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()


# Load lookup data now rather than on the first request (cuts ~0.4 s off the first response).
try:
    from routing.services.trip import warm_caches

    warm_caches()
except Exception:  # never stop the server from starting; requests will load lazily
    import logging

    logging.getLogger(__name__).warning("Cache warm-up failed; loading on first request.", exc_info=True)
