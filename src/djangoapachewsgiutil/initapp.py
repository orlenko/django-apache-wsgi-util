#!/usr/bin/env python
'''Initialize web application on Linode.

Created by Vlad Orlenko, vlad@bjola.ca.
License: do whatever the hell you want with this code, but don't blame me if it does not work or damages your server.

NOTE: before deploying the app, make sure you have the following:

Python 2.7
libapache-wsgi compiled with Python 2.7
Virtualenv with Django 1.4 (or any Django, for that matter). The WSGI config has to point to this virtualenv

'''

from argparse import ArgumentParser
import os
import psutil
import pwd
import subprocess
import logging
import sys
from distutils.dir_util import copy_tree


log = logging.getLogger(__file__)


def p(path):
    return os.path.normpath(os.path.abspath(path))

j = os.path.join


class Options:
    def __init__(self):
        self.setup_options()
        self.parse_options()

    def setup_options(self):
        p = self.parser = ArgumentParser()
        p.add_argument('--domain',
                       required=True,
                       dest='domain',
                       help='Domain name for the new web application, e.g. www.myapp.com.',
                       metavar='DOMAIN')
        p.add_argument('--project_dir',
                       required=True,
                       dest='project_dir',
                       help='Path to the directory containing the Django project. '
                            'The top-level directory of the project, containing the manage.py script, '
                            'should be located inside this directory.',
                       metavar='PROJECT_DIR')
        p.add_argument('--settings_dir',
                       required=False,
                       dest='settings_dir',
                       help='Path to the directory containing the Django settings.py file. '
                            'For Django 1.3 and earlier, this is same as PROJECT_DIR. '
                            'For Django 1.4 and later, this is usually one level below.',
                       metavar='SETTINGS_DIR')
        p.add_argument('--mysqluser',
                       required=True,
                       dest='mysqluser',
                       help='MySQL user name. The user will be created if it does not exist.',
                       metavar='MYSQL_USER')
        p.add_argument('--mysqldb',
                       required=True,
                       dest='mysqldb',
                       help='MySQL database name. The database will be created if it does not exist. '
                            'If the database does not exist, ',
                       metavar='MYSQL_DB')
        p.add_argument('--mysqlpass',
                       required=True,
                       dest='mysqlpass',
                       help='MySQL password.',
                       metavar='MYSQL_PASS')
        p.add_argument('--approot',
                       required=True,
                       dest='approot',
                       help='Root directory for web applications. The new application will be placed in its subdirectory.',
                       metavar='ROOT_APP_DIR')
        p.add_argument('--sitesdir',
                       required=False,
                       default='/etc/apache2/sites-enabled',
                       dest='sitesdir',
                       help='Apache sites-enabled directory, optional. By default, /etc/apache2/sites-available is used.',
                       metavar='APACHE_SITES_DIR')
        p.add_argument('--apacheuser',
                       required=False,
                       default='www-data',
                       dest='apacheuser',
                       help='User account used by Apache, optional. By default, www-data is used.',
                       metavar='APACHE_USER')
        p.add_argument('--mysqlrootpass',
                       required=False,
                       dest='mysqlrootpass',
                       help='MySQL root password. Required if MYSQL_USER and MYSQ_DB need to be created.',
                       metavar='MYSQL_ROOT_PASS')

    def parse_options(self):
        self.args = self.parser.parse_args()

    def __getattr__(self, attrname):
        return getattr(self.args, attrname)


def get_owner(path):
    stat_info = os.stat(path)
    return pwd.getpwuid(stat_info.st_uid)


def check_dir(dirname, pw_uid):
    dirname = p(dirname)
    log.debug('Checking directory %s' % dirname)
    if not os.path.isdir(dirname):
        os.makedirs(dirname, 0740)
    os.chown(dirname, pw_uid.pw_uid, pw_uid.pw_gid)


def check_file(filename, dirname=None):
    if dirname:
        filename = j(dirname, filename)
    filename = p(filename)
    log.debug('Checking if file exists: %s' % filename)
    if not os.path.isfile(filename):
        raise RuntimeError('File not found: %s' % filename)


def check_proc_name(proc_name):
    for proc in psutil.process_iter():
        if proc.name == proc_name:
            return True
    raise RuntimeError('Process not found: %s' % proc_name)


def check_mysql(dbname, username, userpass, rootpass):
    log.debug('Checking database %s...' % dbname)
    db_match = None
    try:
        db_match = subprocess.check_output('mysql --batch --skip-column-names '
                                           '-u %(username)s --password=%(userpass)s '
                                           '-e "SHOW DATABASES LIKE \'%(dbname)s\'"'
                                           % locals(), shell=True)
        log.debug('SHOW DATABASES said: %r' % db_match)
    except subprocess.CalledProcessError, e:
        log.debug('show databases returned %s' % e.returncode)
    if not db_match:
        commands = [
            'mysql -u root --password=%(rootpass)s -e "CREATE DATABASE %(dbname)s"',
            'mysql -u root --password=%(rootpass)s -e "GRANT USAGE ON *.* TO %(username)s@localhost IDENTIFIED BY \'%(userpass)s\'"',
            'mysql -u root --password=%(rootpass)s -e "GRANT ALL PRIVILEGES ON %(dbname)s.* TO %(username)s@localhost"',
        ]
        for cmd in commands:
            thecmd = cmd % locals()
            log.debug('Running command: %s' % thecmd)
            error = subprocess.call(thecmd, shell=True)
            if error:
                raise RuntimeError('Failed to set up database. Last command: %(thecmd)s. Error: %(error)s' % locals())


def check_djangosettings(settings_dir, dbname, username, userpass):
    settings_fname = p(j(settings_dir, 'settings.py'))
    settings = open(settings_fname)
    settings_text = settings.read()
    settings.close()
    if not ('from localsettings import *' in settings_text):
        # Need to fix settings module to import localsettings
        log.debug('Adding import localsettings to settings module: %s' % settings_fname)
        settings_text += ('\n'
                          'try:\n'
                          '    from localsettings import *\n'
                          'except:\n'
                          '    pass\n')
        f = open(settings_fname, 'w')
        f.write(settings_text)
        f.close()
    fname = p(j(settings_dir, 'localsettings.py'))
    if os.path.exists(fname):
        log.debug('Localsettings module already exists: %s' % fname)
        return
    log.debug('Creating localsettings module: %s' % fname)
    f = open(fname, 'w')
    f.write('''
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': '%(dbname)s',
        'USER': '%(username)s',
        'PASSWORD': '%(userpass)s',
        'HOST': '',
        'PORT': '',
    }
}
    ''' % locals())
    f.close()



def check_sanity(opt):
    '''Sanity check:
        - apacheuser  must exist,
        - approot and sitesdir must exist and belong to apacheuser,
        - Apache2 and MySQL must be installed,
        - Project dir must contain manage.py,
        - Settings dir must contain settings.py.
    '''
    pw_uid = pwd.getpwnam(opt.apacheuser)
    check_dir(opt.approot, pw_uid)
    check_dir(opt.sitesdir, pw_uid)
    check_proc_name('apache2')
    check_proc_name('mysqld')
    check_file('manage.py', opt.project_dir)
    check_file('settings.py', (opt.settings_dir or opt.project_dir))


def check_wsgi(project_dir, settings_dir):
    fname = p(j(settings_dir, 'wsgi.py'))
    if os.path.isfile(fname):
        return
    if project_dir == settings_dir:
        settings_module = 'settings'
    else:
        settings_module = '%s.settings' % os.path.basename(project_dir)
    site_packages = p(j(os.path.dirname(sys.executable),
                        '..',
                        'lib',
                        ('python%s.%s' % (sys.version_info.major, sys.version_info.minor)),
                        'site-packages'))
    f = open(fname, 'w')
    f.write('''
import os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "%(settings_module)s")

CWD = os.path.abspath(os.path.normpath(os.path.dirname(__file__)))
PROJECT_DIR = os.path.dirname(CWD)

sys.path.append('%(site_packages)s')
sys.path.append(PROJECT_DIR)

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
    ''' % locals())
    f.close()


def check_db_schema(project_dir):
    '''SyncDB and migrations
    '''
    log.debug('Updating database schema from %s' % project_dir)
    cwd = os.getcwd()
    os.chdir(project_dir)
    for cmd in ('syncdb', 'migrate -all'):
        full_cmd = '%s manage.py %s' % (sys.executable, cmd)
        result = subprocess.call(full_cmd, shell=True)
        log.debug('%s returned: %s' % (full_cmd, result))
    os.chdir(cwd)


def copy_files(source, destination):
    log.debug('Copying %s -> %s' % (source, destination))
    copy_tree(source, destination)


def check_log_dir(approot, app_name, apacheuser):
    check_dir(j(approot, app_name, 'logs'), pwd.getpwnam(apacheuser))


def check_apache_site(sites_dir, domain, app_name, approot, wsgi_path):
    sites_dir = p(sites_dir)
    approot = p(approot)
    log.debug('Checking Apache2 configuration for %s (%s) in %s' % (domain, app_name, sites_dir))
    filename = p(j(sites_dir, app_name))
    if os.path.exists(filename):
        log.debug('File already exists: %s' % filename)
        return
    f = open(filename, 'w')
    f.write('''
<VirtualHost *:80>
    ServerAdmin admin-%(app_name)s@bjola.ca
    ServerName www.%(domain)s
    ServerAlias %(domain)s

    Alias /static/ %(approot)s/%(app_name)s/static/

    <Directory %(approot)s/%(app_name)s/static>
        Order deny,allow
        Allow from all
    </Directory>

    Alias /uploads/ %(approot)s/%(app_name)s/uploads/

    <Directory %(approot)s/%(app_name)s/uploads>
        Order deny,allow
        Allow from all
    </Directory>

    LogLevel warn
    ErrorLog  %(approot)s/%(app_name)s/logs/apache_error.log
    CustomLog %(approot)s/%(app_name)s/logs/apache_access.log combined

    WSGIDaemonProcess %(app_name)s user=www-data group=www-data threads=20 processes=2
    WSGIProcessGroup %(app_name)s

    WSGIScriptAlias / %(wsgi_path)s
</VirtualHost>
    ''' % locals())
    f.close()
    asite = filename
    ensite = p(j(sites_dir, '..', 'sites-enabled', app_name))
    subprocess.call('ln -s %s %s' % (asite, ensite), shell=True)


def restart_apache():
    subprocess.call('apache2ctl restart', shell=True)


def init_app(opt):
    check_sanity(opt)
    check_mysql(opt.mysqldb, opt.mysqluser, opt.mysqlpass, opt.mysqlrootpass)
    check_djangosettings((opt.settings_dir or opt.project_dir), opt.mysqldb, opt.mysqluser, opt.mysqlpass)
    check_wsgi(opt.project_dir, (opt.settings_dir or opt.project_dir))
    check_db_schema(opt.project_dir)
    app_name = opt.domain.replace('.', '_')
    copy_files(opt.project_dir, j(opt.approot, app_name))
    check_dir(p(j(opt.approot, app_name, 'uploads')), pwd.getpwnam(opt.apacheuser))
    check_log_dir(opt.approot, app_name, opt.apacheuser)
    wsgi_path = p(j(opt.approot, app_name, 'wsgi.py'))
    if opt.settings_dir and (opt.settings_dir != opt.project_dir):
        wsgi_app_name = os.path.basename(opt.settings_dir)
        wsgi_path = p(j(opt.approot, app_name, wsgi_app_name, 'wsgi.py'))
    check_apache_site(opt.sitesdir, opt.domain, app_name, opt.approot, wsgi_path)
    check_sanity(opt)
    restart_apache()


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    init_app(Options())