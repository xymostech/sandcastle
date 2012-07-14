from django.conf.urls.defaults import patterns, url

urlpatterns = patterns('main.views',
    url(r'^$', 'home'),
    url(r'^pull/(?P<number>\d+)$', 'pull', name='pull'),
    url(r'^branch/(?P<branch>[^/]*)$', 'branch', name='branch'),
    url(r'^phab/(?P<id>\d+)$', 'phab', name='phab'),
    url(r'^castles/(?P<branch>[^/]*)/$', 'dirserve', name='castle'),
    url(r'^castles/(?P<branch>[^/]*)/(?P<path>.*)/$', 'dirserve'),
    url(r'^castles/(?P<branch>[^/]*)/(?P<path>.*)$', 'fileserve')
)
