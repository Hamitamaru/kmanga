from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse_lazy
from django.http import HttpResponseForbidden
from django.http import HttpResponseRedirect
# from django.shortcuts import render
from django.views.generic import DetailView
from django.views.generic import ListView
from django.views.generic.edit import CreateView
from django.views.generic.edit import DeleteView
from django.views.generic.edit import UpdateView
from django.views.generic.list import MultipleObjectMixin

# import django_rq

from .models import Issue
from .models import Manga
from .models import Result
from .models import Subscription


class LoginRequiredMixin(object):
    @classmethod
    def as_view(cls, **initkwargs):
        view = super(LoginRequiredMixin, cls).as_view(**initkwargs)
        return login_required(view)


class SubscriptionOwnerMixin(object):
    def dispatch(self, request, *args, **kwargs):
        if self.get_object().user != self.request.user:
            return HttpResponseForbidden()
        return super(SubscriptionOwnerMixin, self).dispatch(request, *args,
                                                            **kwargs)


class SafeDeleteView(DeleteView):
    """View that provide the ability to safe delete objects."""

    def delete(self, request, *args, **kwargs):
        """Replace delete method with a safe version."""
        self.object = self.get_object()
        success_url = self.get_success_url()
        self.object.deleted = True
        self.object.save()
        return HttpResponseRedirect(success_url)


class MangaListView(LoginRequiredMixin, ListView, MultipleObjectMixin):
    model = Manga
    paginate_by = 9

    def get_context_data(self, **kwargs):
        """Extend the context data with the search query value."""
        context = super(MangaListView, self).get_context_data(**kwargs)
        q = self.request.GET.get('q', None)
        if q and Manga.objects.is_valid(q):
            context['q'] = q
        return context

    def get_queryset(self):
        q = self.request.GET.get('q', None)
        if q and Manga.objects.is_valid(q):
            mangas = Manga.objects.search(q)
        else:
            mangas = Manga.objects.latests()
        return mangas


class MangaDetailView(LoginRequiredMixin, DetailView):
    model = Manga


class MangaCreateView(LoginRequiredMixin, CreateView):
    model = Manga


class MangaUpdateView(LoginRequiredMixin, UpdateView):
    model = Manga


class MangaDeleteView(LoginRequiredMixin, DeleteView):
    model = Manga
    success_url = reverse_lazy('manga-list')


class IssueListView(LoginRequiredMixin, ListView):
    model = Issue


class IssueDetailView(LoginRequiredMixin, DetailView):
    model = Issue


class IssueCreateView(LoginRequiredMixin, CreateView):
    model = Issue


class IssueUpdateView(LoginRequiredMixin, UpdateView):
    model = Issue


class IssueDeleteView(LoginRequiredMixin, DeleteView):
    model = Issue
    success_url = reverse_lazy('issue-list')


class SubscriptionListView(LoginRequiredMixin, ListView, MultipleObjectMixin):
    model = Subscription
    paginate_by = 9

    def get_queryset(self):
        user = self.request.user
        return Subscription.objects.latests(user=user)


class SubscriptionDetailView(LoginRequiredMixin, SubscriptionOwnerMixin,
                             DetailView):
    model = Subscription


class SubscriptionCreateView(LoginRequiredMixin, CreateView):
    model = Subscription
    fields = ['manga', 'user', 'language']

    def get_success_url(self):
        return reverse_lazy('subscription-read',
                            args=[self.object.pk])


class SubscriptionUpdateView(LoginRequiredMixin, SubscriptionOwnerMixin,
                             UpdateView):
    model = Subscription
    fields = ['issues_per_day', 'paused']

    def get_success_url(self):
        return reverse_lazy('subscription-read',
                            args=[self.object.pk])


class SubscriptionDeleteView(LoginRequiredMixin, SubscriptionOwnerMixin,
                             SafeDeleteView):
    model = Subscription
    success_url = reverse_lazy('subscription-list')


class ResultListView(LoginRequiredMixin, ListView):
    model = Result


class ResultDetailView(LoginRequiredMixin, DetailView):
    model = Result


class ResultCreateView(LoginRequiredMixin, CreateView):
    model = Result
    # form_class = ResultForm

    # def form_valid(self, form):
    #     result = super(ResultCreateView, self).form_valid(form)
    #     for issue in range(form.instance.from_issue, form.instance.to_issue+1):
    #         line = form.instance.resultline_set.create(issue=issue)
    #         django_rq.get_queue('default').enqueue(line.send_mobi)
    #     return result


class ResultUpdateView(LoginRequiredMixin, UpdateView):
    model = Result


class ResultDeleteView(LoginRequiredMixin, DeleteView):
    model = Result
    success_url = reverse_lazy('result-list')


class LatestUpdates(ListView):
    """Show latest updates mangas (mangas with new issues)."""
    model = Manga
