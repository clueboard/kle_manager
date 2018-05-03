import logging
from time import time, localtime, strftime
from codecs import open as copen
from urllib.parse import urlparse, parse_qs

from flask import Flask, redirect, render_template, request, make_response, url_for
from kle2svg import KLE2SVG
from os.path import exists

from os import environ, stat, remove, makedirs
from requests import Session

app = Flask(__name__)

app.config['SECRET_KEY'] = 'Hello Computer The Quick Brown Fox Jumped Over The Lazy Dog'
app.config['CACHE_DIR'] = 'kle_cache'
app.config['CACHE_TIME'] = 60
app.config['GITHUB_CLIENT_ID'] = environ.get('GITHUB_CLIENT_ID')
app.config['GITHUB_CLIENT_SECRET'] = environ.get('GITHUB_CLIENT_SECRET')


## Functions
def render_page(page_name, **args):
    """Render a page.
    """
    arguments = {
        'enumerate': enumerate,
        'int': int,
        'isinstance': isinstance,
        'len': len,
        'sorted': sorted,
        'str': str,
        'type': type,
        'zip': zip
    }
    arguments.update(args)

    if 'title' not in arguments:
        arguments['title'] = ''

    return render_template('%s.html' % page_name, **arguments)


## Views
@app.route('/', methods=['GET'])
def index():
    """Tell the user about what is going on and let them login.
    """
    return render_page('index')


def fetch_gist(gist_id):
    """Fetch a gist, maybe from a cache.
    """
    github_oauth_cookie = request.cookies.get('github_oauth_token')
    cache_file = app.config['CACHE_DIR'] + '/' + gist_id + '.gist'
    headers = {}
    payload = {}

    if exists(cache_file):
        # We have a cached copy
        file_stat = stat(cache_file)
        file_date = file_stat.st_mtime
        file_size = file_stat.st_size
        file_age = time() - file_date

        if file_size == 0:
            # Invalid cache
            logging.warning('Removing zero-length cache file %s', cache_file)
            remove(cache_file)
        elif file_age < app.config['CACHE_TIME']:
            logging.warning('Cache file %s is less than %ss old, skipping HTTP check.', cache_file, app.config['CACHE_TIME'])
            return copen(cache_file, encoding='UTF-8').read()
        else:
            headers['If-Modified-Since'] = strftime('%a, %d %b %Y %H:%M:%S %Z', localtime(file_date))
            logging.warning('Adding If-Modified-Since: %s to headers.', headers['If-Modified-Since'])

    if github_oauth_cookie:
        # Opportunistically auth this request
        payload['access_token'] = github_oauth_cookie

    with Session() as github_api:
        github_api.headers.update({'Accept': 'application/vnd.github.v3+json'})
        gist = github_api.get('https://api.github.com/gists/' + gist_id, params=payload).json()
        for file in gist['files'].values():
            if file['filename'].endswith('.kbd.json'):
                # We have KLE Data!
                content = '\n'.join(file['content'].split('\n')[1:-1])  # Remove first/last line
                if not exists(app.config['CACHE_DIR']):
                    makedirs(app.config['CACHE_DIR'])

                with copen(cache_file, 'w', encoding='UTF-8') as fd:
                    fd.write(content)  # Write this to a cache file

                return content


@app.route('/render/<gist_id>', methods=['GET'])
def render_gist(gist_id):
    """Fetch the KLE code from a gist and return a rendered SVG.
    """
    gist = fetch_gist(gist_id)
    svg = KLE2SVG(gist)
    file = svg.create().tostring()
    return file, {'Content-Type': 'image/svg+xml'}


@app.route('/list_gists', methods=['GET'])
def list_gists():
    """Pull a list of gists for the logged in user.
    """
    github_oauth_cookie = request.cookies.get('github_oauth_token')

    # Redirect to index if no github token
    if not github_oauth_cookie:
        return redirect(url_for('index'))

    # Figure out what page we're on
    page = int(request.args.get('page', 1))

    # Pull a list of gists
    with Session() as github_api:
        github_api.headers.update({'Accept': 'application/vnd.github.v3+json'})
        url = 'https://api.github.com/gists'
        payload = {
            'access_token': github_oauth_cookie,
            'per_page': 25
        }

        if page:
            payload['page'] = str(page)

        api_request = github_api.get(url, params=payload)
        gists = api_request.json()
        pagination = {}
        if 'Link' in api_request.headers:
            for pair in api_request.headers['Link'].split(','):
                url, rel = pair.strip().split(';')
                url = url[1:-1] # Remove the surrounding brackets
                rel = rel.split('"')[1]
                pagination[rel] = url
                url = urlparse(url)
                args = parse_qs(url.query)
                pagination[rel] = args['page'][0]
                if rel == 'last':
                    pagination['pages'] = range(1, int(args['page'][0]))
                    print('pages', pagination['pages'])

    KLEs = []
    for gist in gists:
        # Check to see if this gist is a KLE
        for file in gist['files']:
            if file.endswith('.kbd.json'):
                KLEs.append(gist)
                break

    return render_page('gists', KLEs=KLEs, page=page, pagination=pagination)


@app.route('/callback')
def callback():
    if 'code' in request.args:
        with Session() as github_api:
            github_api.headers.update({'Accept': 'application/json'})
            url = 'https://github.com/login/oauth/access_token'
            payload = {
                'client_id': app.config['GITHUB_CLIENT_ID'],
                'client_secret': app.config['GITHUB_CLIENT_SECRET'],
                'code': request.args['code']
            }
            r = github_api.post(url, params=payload)
            response = r.json()
            if 'access_token' in response:
                # store access_token from response in a cookie
                resp = make_response(redirect(url_for('list_gists')))
                resp.set_cookie('github_oauth_token', response['access_token'])
                return resp
            else:
                app.logger.error("github didn't return an access token")
                return redirect(url_for('index'))
    return '', 404


if __name__ == '__main__':
    # Start the webserver
    app.run(debug=True, port=5000)
