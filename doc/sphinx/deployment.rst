==================
Deploying Dissemin
==================

You have two options to run the web server: development or production settings.

Development
===========

Simply run ``./launch.sh``.
This uses the `default Django server <https://docs.djangoproject.com/en/2.2/ref/django-admin/#runserver>`_ and serves the website locally on the port 8080.
Note that the standard port for django-admins runserver-command is _8000_, but this ensures compatibility with the Vagrant installation.

This runs with ``DEBUG = True``, which means that Django will report to the user any internal error in a transparent way.
This is useful to debug your installation but should not be used for production as it exposes your internal settings.

Whenever you have a change of the database layout, run::

    ./manage migrate


Production
==========

As any Django website, Dissemin can be served by various web servers.
These settings are not specific to Dissemin itself so you should refer to `the relevant Django documentation <https://docs.djangoproject.com/en/2.2/howto/deployment/>`_.

There are some deployment steps that you always have to do in case of deployment (which includes rolling out updates).
You should keep this order.
Make sure to have the virtual environment activated.

#. Apply migrations with ``./manage.py migrate``
#. Compile scss files with ``./manage.py compilescss``
#. Collect static files with ``./manage.py collectstatic --ignore=*.scss``
#. Compile translations with ``./manage.py compilemessages --exclude qqq``
#. Tell WSGI to reload with ``touch dissemin/wsgi.py``
#. Restart celery with ``systemctl``

Make sure that your `media/` directory is writable by the user under which the application will run (`www-data` on Debian).

Self-hosting MathJax
--------------------

Dissemin requires `MathJax <https://www.mathjax.org/>`_ for rendering LaTeX formatting in the abstracts.
Out of the box, Dissemin will use a CDN-hosted version of MathJax.

An easy solution to this is to self-host MathJax. You can follow the `installation instructions <https://docs.mathjax.org/en/latest/start.html#downloading-and-installing-mathjax>`_ from MathJax to get a local copy.
Ideally, you should put it in the static directory (under ``/home/dissemin/www/static/`` in the example below).

Note that MathJax consists of many small files which can slow down a lot the built-in Django webserver.
Hence, it is better to serve it directly by Apache and avoid having all these files in the ``papers/static/libs`` directory of Dissemin.

Once MathJax is downloaded and available by your webserver, you can use the setting ``MATHJAX_SELFHOST_URL`` (in ``dissemin/settings``) to specify a location to load MathJax from.
In the example below, this would be ``//dissemin.myuni.edu/static/mathjax/MathJax.js?config=TeX-AMS-MML_HTMLorMML``.

Apache with WSGI
----------------

A sample VirtualHost, assuming that the root of the Dissemin source code is at ``/home/dissemin/prod`` and you use a ``python3.5`` virtualenv is available in the `Dissemin Git repository <https://github.com/dissemin/dissemin/blob/master/provisioning/apache2-vhost.conf>`_.

You should only have to change the path to the application and the domain name of the service.
