# -*- encoding: utf-8 -*-
# Dissemin: open access policy enforcement tool
# Copyright (C) 2014 Antonin Delpeuch
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Affero General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#


import json

from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.signals import pre_social_login
from allauth.account.signals import user_logged_in
from haystack.generic_views import SearchView

from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.http import HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template import loader
from django.urls import reverse
from django.utils.encoding import escape_uri_path
from django.utils.translation import ugettext as _
from django.utils.http import urlencode
from django.utils.six.moves.urllib.parse import unquote
from django.views.generic.edit import FormView

from deposit.models import DepositRecord
from notification.models import Notification
from papers.doi import to_doi
from papers.doi import doi_to_url
from papers.errors import MetadataSourceException
from papers.forms import PaperSearchForm
from papers.models import Paper
from papers.models import Researcher
from papers.user import is_admin
from papers.user import is_authenticated
from papers.utils import validate_orcid
from publishers.views import SlugDetailView
from search import SearchQuerySet
from statistics.models import BareAccessStatistics


def fetch_on_orcid_login(sender, sociallogin, **kwargs):
    account = sociallogin.account

    # Only prefetch if the social login refers to a valid ORCID account
    orcid = validate_orcid(account.uid)
    if not orcid:
        raise ImmediateHttpResponse(
            render(
                kwargs['request'],
                'dissemin/error.html',
                {'message':_('Invalid ORCID identifier.')}
            )
        )

    profile = None # disabled account.extra_data because of API version mismatches
    user = None
    if '_user_cache' in account.__dict__:
        user = account.user
    r = Researcher.get_or_create_by_orcid(orcid, profile, user)

    if not r: # invalid ORCID profile (e.g. no name provided)
        raise ImmediateHttpResponse(
            render(
                kwargs['request'],
                'dissemin/error.html',
                {'message': _(
                    'Dissemin requires access to your ORCID name, '
                    'which is marked as private in your ORCID profile.'
                )}
            )
        )

def complete_researcher_profile_on_orcid_login(sender, user, **kwargs):
    orcid = user.socialaccount_set.first().uid
    r = Researcher.objects.get(orcid=orcid)

    if r.user_id is None and user is not None:
        r.user = user
        r.save(update_fields=['user'])
    if r.empty_orcid_profile is None:
        r.init_from_orcid()
    else:
        r.fetch_everything_if_outdated()

pre_social_login.connect(fetch_on_orcid_login)
user_logged_in.connect(complete_researcher_profile_on_orcid_login)

# Number of papers shown on a search results page
NB_RESULTS_PER_PAGE = 20


class AdvancedPaperSearchView(FormView):
    """Displays the full search form."""
    template_name = 'papers/advanced_search.html'
    form_class = PaperSearchForm


class PaperSearchView(SearchView):
    """Displays a list of papers and a search form."""

    paginate_by = NB_RESULTS_PER_PAGE
    template_name = 'papers/search.html'
    form_class = PaperSearchForm
    queryset = SearchQuerySet().models(Paper)

    def get(self, request, *args, **kwargs):
        """
        If the user is no admin, we remove visivle, availability and oa_status GET statements
        """
        if not is_admin(request.user):
            request.GET = request.GET.copy()
            request.GET.pop('visible', None)
            request.GET.pop('availability', None)
            request.GET.pop('oa_status', None)

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """
        We add some context data.
        """
        context = super().get_context_data(**kwargs)
        search_description = _('Papers')
        query_string = self.request.META.get('QUERY_STRING', '')
        context['breadcrumbs'] = [(search_description, '')]
        context['search_description'] = search_description if query_string else _('All papers')
        context['search_description_title'] = _('Papers')
        context['nb_results'] = self.queryset.count()
        context['search_stats'] = BareAccessStatistics.from_search_queryset(self.queryset)
        context['on_statuses'] = json.dumps(context['form'].on_statuses())

        # Eventually remove sort by parameter
        search_params_without_sort_by = self.request.GET.copy()
        try:
            del search_params_without_sort_by['sort_by']
        except KeyError:
            pass

        # Make a clean URL with useless GET params
        for key in list(search_params_without_sort_by.keys()):
            if not search_params_without_sort_by[key]:
                del search_params_without_sort_by[key]
        context['search_params_without_sort_by'] = search_params_without_sort_by.urlencode()

        # Get current sort_by value
        current_sort_by_value = self.request.GET.get('sort_by', None)
        try:
            current_sort_by = next(v for k, v in self.form_class.SORT_CHOICES if k == current_sort_by_value)
        except StopIteration:
            current_sort_by = self.form_class.SORT_CHOICES[0][1]
        context['current_sort_by'] = current_sort_by

        # Notifications
        if self.request.user.is_authenticated:
            context['messages'] = Notification.objects.filter(inbox__user=self.request.user).order_by('-date')[:3]

        return context

    def get_form_kwargs(self):
        """
        We make sure the search is valid even if no parameter
        was passed, in which case we add a default empty query.
        Otherwise the search form is not bound and search fails.
        """
        args = super().get_form_kwargs()

        if 'data' not in args:
            args['data'] = {self.search_field: ''}

        return args

    def render_to_response(self, context, **kwargs):
        """
        If JSON is requested, we deliver JSON, else normal HTML
        """
        if self.request.META.get('CONTENT_TYPE') == 'application/json':
            response = self.raw_response(context, **kwargs)
            return HttpResponse(
                json.dumps(response),
                content_type='application/json'
            )
        return super().render_to_response(context, **kwargs)

    def raw_response(self, context, **kwargs):
        """
        A raw response, containing meta information and some HTML
        """
        context['request'] = self.request
        listPapers = loader.render_to_string('papers/paper_list.html', context)
        messages = loader.render_to_string('papers/messages.html', context)
        stats = context['search_stats'].pie_data()
        stats['on_statuses'] = context['form'].on_statuses()
        return {
            'listPapers': listPapers,
            'messages' : messages,
            'stats': stats,
            'nb_results': context['nb_results'],
        }

    def url_with_query_string(self, url=None, query_string=None):
        """
        Returns the current URL with its query string.

        Both the URL and the query string can be overriden.
        """
        url = url or escape_uri_path(self.request.path)
        if query_string is None:
            query_string = self.request.META.get('QUERY_STRING', '')
        if query_string:
            url += '?' + query_string
        return url


class MyTodoListView(LoginRequiredMixin, PaperSearchView):

    def get(self, request, *args, **kwargs):
        """
        Fetch pk of the papers on users todo list und put them into ES, then pass to PaperSearchView as this handles everything for us
        """
        papers = Paper.objects.filter(todolist=request.user).values_list('pk', flat=True)
        self.queryset = self.queryset.filter(id__in=papers)

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """
        Prepare the context, i.e. set ajax_url, breadcrums etc.
        We use view for fadeout and minor change in template
        """
        context = super().get_context_data(**kwargs)

        context['breadcrumbs'] = [(_('To-do list'), None)]
        context['search_description_title'] = context['search_description'] = _('Papers on my to-do list')
        context['todolist_fadeout'] = True

        return context


class ResearcherView(PaperSearchView):
    """
    Displays the papers of a given researcher.
    """

    template_name = 'papers/researcher.html'

    def get(self, request, *args, **kwargs):
        if 'researcher' in kwargs:
            researcher = get_object_or_404(Researcher, pk=kwargs['researcher'])
        elif 'orcid' in kwargs:
            try:
                researcher = Researcher.objects.get(orcid=kwargs['orcid'])
            except Researcher.DoesNotExist:
                try:
                    orcid = validate_orcid(kwargs['orcid'])
                    researcher = Researcher.get_or_create_by_orcid(orcid)
                    if not researcher:
                        raise Http404(_("Invalid ORCID profile. Please make sure it includes a public name."))
                    researcher.init_from_orcid()
                except MetadataSourceException:
                    raise Http404(_('Invalid ORCID profile.'))

        if not researcher.visible:
            name = researcher.name.full
            return HttpResponsePermanentRedirect(reverse('search')+'?'+urlencode({'authors':name}))

        if kwargs.get('slug') != researcher.slug:
            view_args = {'researcher': researcher.id, 'slug': researcher.slug}
            url = reverse('researcher', kwargs=view_args)
            self.url = self.url_with_query_string(url=url)
            return HttpResponsePermanentRedirect(self.url)

        self.queryset = self.queryset.filter(researchers=researcher.id)
        self.researcher = researcher
        return super(ResearcherView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(ResearcherView, self).get_context_data(**kwargs)
        researcher = self.researcher
        # researcher corresponding to the currently logged in user
        try:
            Researcher.objects.get(user=self.request.user)
        except (Researcher.DoesNotExist, TypeError):
            pass # no logged in user
        else:
            context['profile_fadeout'] = True
        context['researcher'] = researcher
        context['researcher_id'] = researcher.id
        context['search_description'] += ' ' + _('authored by') + ' ' +str(researcher)
        context['search_description_title'] = str(researcher)
        context['breadcrumbs'] = researcher.breadcrumbs()

        return context

    def raw_response(self, context, **kwargs):
        response = super(ResearcherView, self).raw_response(context, **kwargs)
        researcher = self.researcher
        if researcher.current_task:
            response['status'] = researcher.current_task
            response['display'] = researcher.get_current_task_display()
        return response


class MyProfileView(LoginRequiredMixin, ResearcherView):
    """
    View for my profile. It is basically a researcher view
    """

    template_name = 'papers/profile.html'

    def get(self, request, *args, **kwargs):
        """
        We fetch the researcher, set a queryset and then pass to super of ResearcherView, as we don't want ResearcherViews get in this case
        """
        self.researcher = Researcher.objects.get(user=request.user)
        self.queryset = self.queryset.filter(researchers=self.researcher.id)

        return super(ResearcherView, self).get(request, *args, **kwargs)


# TODO: this should be moved to /ajax/


@user_passes_test(is_authenticated)
def refetch_researcher(request, pk):
    researcher = get_object_or_404(Researcher, pk=pk)
    if researcher.user != request.user and not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden("Not authorized to update papers for this researcher.")
    from backend.tasks import fetch_everything_for_researcher
    fetch_everything_for_researcher.delay(pk=pk)

    view_args = {'researcher': researcher.id, 'slug': researcher.slug}
    return redirect(reverse('researcher', kwargs=view_args))


class PaperView(SlugDetailView):
    model = Paper
    template_name = 'papers/paper.html'
    view_name = 'paper'

    def get_object(self, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        pk = self.kwargs.get('pk', None)
        doi = self.kwargs.get('doi', None)
        if doi:
            doi = unquote(doi)
            doi = to_doi(doi)

        paper = None
        try:
            if pk is not None:
                paper = queryset.get(pk=pk)
            elif doi is not None:
                paper = Paper.get_by_doi(doi)
            else:
                raise Http404(_("Paper view expects a DOI or a pk"))
        except ObjectDoesNotExist:
            pass

        if paper is None or paper.is_orphan():
            raise Http404(_("No %(verbose_name)s found matching the query") %
                          {'verbose_name': Paper._meta.verbose_name})

        if not paper.visible:
            raise Http404(_("This paper has been deleted."))

        paper = queryset.prefetch_related('oairecord_set').get(pk=paper.pk)

        return paper

    def get_context_data(self, **kwargs):
        context = super(PaperView, self).get_context_data(**kwargs)
        context['breadcrumbs'] = self.object.breadcrumbs()

        context['deposit'] = None
        if 'deposit' in self.request.GET:
            try:
                pk = int(self.request.GET['deposit'])
                dr = DepositRecord.objects.select_related('repository', 'repository__letter_declaration', 'oairecord').get(pk=pk)
                if dr.paper_id == self.object.id and dr.status in ['pending', 'published']:
                    if self.request.user.is_authenticated and self.request.user == dr.user and dr.repository.letter_declaration:
                        dr.letter = True
                    context['deposit'] = dr
            except (TypeError, ValueError, DepositRecord.DoesNotExist):
                pass

        # Pending deposits
        if not context['deposit']:
            context['pending_deposits'] = self.object.depositrecord_set.filter(status='pending')
            context['embargoed_deposits'] = self.object.depositrecord_set.filter(status='embargoed')

        return context

    def redirect(self, **kwargs):
        if 'pk' not in kwargs:
            del kwargs['doi']
            kwargs['pk'] = self.object.pk
        return super(PaperView, self).redirect(**kwargs)


def redirect_by_doi(request, doi):
    """
    This view is inherited from doai.io, migrated to this code base
    to preserve the existing behaviour. We could instead
    redirect to unpaywall, but that would not include ResearchGate urls.
    """
    doi = unquote(doi)
    doi = to_doi(doi)
    if not doi:
        raise Http404(_("Invalid DOI."))
    paper = Paper.get_by_doi(doi)
    if paper and paper.pdf_url:
        return HttpResponsePermanentRedirect(paper.pdf_url)
    return HttpResponsePermanentRedirect(doi_to_url(doi))

