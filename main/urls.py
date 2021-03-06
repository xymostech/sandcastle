from django.conf.urls.defaults import patterns, url

urlpatterns = patterns('main.views',
    url(r'^$', 'home'),
    url(r'^pull/(?P<number>\d+)/?$', 'pull', name='pull'),
    url(r'^branch/(?P<branch>[^/]*)/?$', 'branch', name='branch'),
    url(r'^phab/(?P<id>\d+)/?$', 'phab', name='phab'),
    url(r'^castles/(?P<branch>[^/]*)/(?P<path>.*)$', 'fileserve',
        name='fileserve'),
    url(r'^media/castles/(?P<branch>[^/]*)/(?P<path>.*)$', 'castle_redirect'),
)
