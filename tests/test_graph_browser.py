"""Real Chromium checks against a disposable PostgreSQL test database."""
import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def galaxy_page(live_server, article_factory, publisher, client, request):
    titles = getattr(request, 'param', ['Alpha', 'Beta'])
    articles = [article_factory(title) for title in titles]
    if len(articles) > 2:
        from study.services.graph import add_edge
        for source, target in zip(articles, articles[1:]):
            add_edge(publisher, source.pk, target.pk)
    client.force_login(publisher)
    session = client.session
    session['study.mode'] = 'admin'
    session.save()
    cookie = client.cookies['sessionid'].value
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        # Observe the real library instance without adding production test hooks.
        page.add_init_script("""Object.defineProperty(window, 'ForceGraph3D', {
            configurable: true,
            set(factory) {
                Object.defineProperty(window, 'ForceGraph3D', { value: (...args) => {
                    const mount = factory(...args);
                    return (...mountArgs) => { window.testGraph = mount(...mountArgs); return window.testGraph; };
                }, configurable: true });
            }
        });""")
        yield page, live_server.url, cookie
        browser.close()


def test_public_galaxy_renders_and_falls_back_on_context_loss(galaxy_page):
    page, url, cookie = galaxy_page
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(url + '/galaxy')
    expect(page.locator('#graph-status')).to_have_text('2 articles · 0 read-before links', timeout=20000)
    expect(page.locator('#website-graph canvas')).to_be_visible()
    assert page.locator('#edge-controls').count() == 0
    # The same real renderer uses the existing shader/planet factory.
    assert page.evaluate("typeof Rendering.createNodeObject === 'function'")
    page.screenshot(path='/tmp/babel-p4-galaxy.png', full_page=True)
    page.locator('#website-graph canvas').evaluate("canvas => canvas.dispatchEvent(new Event('webglcontextlost', {cancelable: true}))")
    expect(page.locator('#graph-status')).to_contain_text('article list')
    expect(page.locator('#website-graph')).to_be_hidden()
    page.locator('#graph-articles a').filter(has_text='Alpha').click()
    expect(page).to_have_url(url + '/alpha')
    page.goto(url + '/galaxy')
    page.get_by_role('link', name='Back to timeline').click()
    expect(page).to_have_url(url + '/')
    assert errors == []


def test_graph_load_retry_and_missing_vendor_keep_article_links(galaxy_page):
    page, url, cookie = galaxy_page
    page.route('**/api/graph', lambda route: route.fulfill(status=503, body='unavailable'))
    page.route('**/vendor/3d-force-graph.min.js', lambda route: route.abort())
    page.goto(url + '/galaxy')
    expect(page.locator('#graph-retry')).to_be_visible()
    expect(page.locator('#graph-articles a')).to_have_count(2)
    page.unroute('**/api/graph')
    page.locator('#graph-retry').click()
    expect(page.locator('#graph-status')).to_contain_text('article list')
    page.locator('#graph-articles a').filter(has_text='Beta').click()
    expect(page).to_have_url(url + '/beta')


def test_owner_directed_controls_keep_graph_when_reverse_is_rejected(galaxy_page):
    page, url, cookie = galaxy_page
    page.context.add_cookies([{'name': 'sessionid', 'value': cookie, 'url': url}])
    page.goto(url + '/galaxy')
    expect(page.locator('#graph-status')).to_contain_text('2 articles', timeout=20000)
    page.locator('#edge-source').select_option(label='Alpha')
    page.locator('#edge-target').select_option(label='Beta')
    page.locator('#edge-add').click()
    expect(page.locator('#graph-status')).to_have_text('2 articles · 1 read-before links')
    page.locator('#edge-source').select_option(label='Beta')
    page.locator('#edge-target').select_option(label='Alpha')
    page.locator('#edge-add').click()
    expect(page.locator('#graph-status')).to_contain_text('cannot form a cycle')
    assert page.evaluate("fetch('/api/graph').then(r => r.json()).then(g => g.edges.length)") == 1
    expect(page.locator('#website-graph canvas')).to_be_visible()


def test_planet_single_click_navigates_and_hover_is_escaped(galaxy_page):
    page, url, cookie = galaxy_page

    def hostile_preview(route):
        response = route.fetch()
        data = response.json()
        node = next(node for node in data['nodes'] if node['slug'] == 'alpha')
        node['title'] = 'Alpha <img src=x onerror=alert(1)>'
        node['excerpt'] = '<script>alert(2)</script>'
        route.fulfill(response=response, json=data)

    page.route('**/api/graph', hostile_preview)
    page.goto(url + '/galaxy')
    expect(page.locator('#website-graph canvas')).to_be_visible()
    page.wait_for_function("window.testGraph && testGraph.graphData().nodes.length === 2")
    page.evaluate("""async () => {
        testGraph.cooldownTicks(0);
        await new Promise(resolve => requestAnimationFrame(resolve));
        testGraph.zoomToFit(0, 80);
        await new Promise(resolve => requestAnimationFrame(resolve));
    }""")
    page.wait_for_function("testGraph.graphData().nodes.every(n => Number.isFinite(n.x))")
    point = page.evaluate("""() => {
        const node = testGraph.graphData().nodes.find(n => n.slug === 'alpha');
        const position = testGraph.graph2ScreenCoords(node.x, node.y, node.z);
        const rect = document.querySelector('#website-graph').getBoundingClientRect();
        return {x: rect.left + position.x, y: rect.top + position.y};
    }""")
    page.mouse.move(point['x'], point['y'])
    expect(page.locator('.graph-preview')).to_contain_text('Alpha <img src=x onerror=alert(1)>')
    assert page.locator('.graph-preview img, .graph-preview script').count() == 0
    page.mouse.move(point['x'] + 1, point['y'])
    page.wait_for_function("Rendering.hoveredMaterial?.uniforms.isHovering.value === true")
    page.mouse.click(point['x'], point['y'])
    expect(page).to_have_url(url + '/alpha')


def test_archived_articles_disappear_after_server_reload(galaxy_page):
    page, url, cookie = galaxy_page
    page.context.add_cookies([{'name': 'sessionid', 'value': cookie, 'url': url}])
    page.goto(url + '/galaxy')
    expect(page.locator('#graph-status')).to_contain_text('2 articles')
    assert page.evaluate("""async () => {
        const graph = await fetch('/api/graph').then(r => r.json());
        const id = graph.nodes.find(n => n.slug === 'alpha').id;
        const token = document.cookie.split('; ').find(c => c.startsWith('csrftoken=')).slice(10);
        return (await fetch(`/api/articles/${id}/archive`, {method: 'POST', headers: {'X-CSRFToken': token}})).status;
    }""") == 200
    page.reload()
    expect(page.locator('#graph-status')).to_contain_text('1 articles')
    expect(page.locator('#graph-articles a')).to_have_count(1)
    assert page.evaluate("testGraph.graphData().nodes.map(n => n.slug)") == ['beta']


@pytest.mark.parametrize('galaxy_page', [['Foundations', 'Ownership', 'Lifetimes', 'Concurrency']], indirect=True)
def test_linked_galaxy_visual_and_shared_resource_cleanup(galaxy_page):
    page, url, cookie = galaxy_page
    page.goto(url + '/galaxy')
    expect(page.locator('#graph-status')).to_have_text('4 articles · 3 read-before links')
    page.wait_for_function("LevelCircles.levelCirclesGroup?.children.length === 4")
    page.screenshot(path='/tmp/babel-p4-linked-galaxy.png', full_page=True)
    # Replacing hierarchy circles releases their GPU resources.
    assert page.evaluate("""() => {
        const old = LevelCircles.levelCirclesGroup;
        let disposed = 0;
        old.children.forEach(circle => {
            circle.geometry.addEventListener('dispose', () => disposed++);
            circle.material.addEventListener('dispose', () => disposed++);
        });
        LevelCircles.updateLevelCircles(testGraph);
        return disposed === 8 && !testGraph.scene().children.includes(old);
    }""")
    # Stopping the shared loop prevents any further frame work.
    assert page.evaluate("""async () => {
        let sceneCalls = 0;
        const stop = Animation.startLoop({scene: () => { sceneCalls++; return null; }});
        stop();
        await new Promise(resolve => requestAnimationFrame(resolve));
        return sceneCalls === 0;
    }""")


def test_persisted_page_restore_rebuilds_galaxy_and_refreshes_articles(galaxy_page):
    page, url, cookie = galaxy_page
    page.context.add_cookies([{'name': 'sessionid', 'value': cookie, 'url': url}])
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url + '/galaxy')
    expect(page.locator('#graph-status')).to_have_text('2 articles · 0 read-before links')
    page.evaluate("""() => {
        window.previousGraph = testGraph;
        window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
    }""")
    expect(page.locator('#website-graph canvas')).to_have_count(0)
    assert page.evaluate('LevelCircles.levelCirclesGroup === null')
    # Content can change while the page is in the browser's back/forward cache.
    assert page.evaluate("""async () => {
        const graph = await fetch('/api/graph').then(response => response.json());
        const id = graph.nodes.find(node => node.slug === 'alpha').id;
        const token = document.cookie.split('; ').find(cookie => cookie.startsWith('csrftoken=')).slice(10);
        return (await fetch(`/api/articles/${id}/archive`, {method: 'POST', headers: {'X-CSRFToken': token}})).status;
    }""") == 200
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(page.locator('#website-graph canvas')).to_be_visible()
    expect(page.locator('#graph-status')).to_have_text('1 articles · 0 read-before links')
    assert page.evaluate('testGraph !== previousGraph')
    expect(page.locator('#graph-articles a')).to_have_count(1)
    # Repeated restoration must not accumulate renderers or lose its listener.
    page.evaluate("""() => {
        window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    }""")
    expect(page.locator('#website-graph canvas')).to_have_count(1)
    expect(page.locator('#website-graph canvas')).to_be_visible()
    assert errors == []


def test_suspended_page_does_not_rebuild_from_an_in_flight_graph_load(galaxy_page):
    page, url, cookie = galaxy_page
    pending = []

    response = page.request.get(url + '/api/graph')
    page.route('**/api/graph', lambda route: pending.append(route))
    with page.expect_request('**/api/graph'):
        page.goto(url + '/galaxy')
    page.evaluate('document.readyState')
    assert len(pending) == 1
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    pending[0].fulfill(response=response)
    page.unroute('**/api/graph')
    # Drain fetch completion and browser frames while the page is suspended.
    page.evaluate("""async () => {
        await new Promise(resolve => requestAnimationFrame(resolve));
        await new Promise(resolve => requestAnimationFrame(resolve));
    }""")
    expect(page.locator('#website-graph canvas')).to_have_count(0)
    expect(page.locator('#graph-retry')).to_be_hidden()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(page.locator('#website-graph canvas')).to_be_visible()
    expect(page.locator('#graph-status')).to_have_text('2 articles · 0 read-before links')


def test_browser_back_returns_to_functional_galaxy(galaxy_page):
    page, url, cookie = galaxy_page
    page.goto(url + '/galaxy')
    expect(page.locator('#website-graph canvas')).to_be_visible()
    page.locator('#graph-articles a').filter(has_text='Alpha').click()
    expect(page).to_have_url(url + '/alpha')
    page.go_back()
    expect(page).to_have_url(url + '/galaxy')
    expect(page.locator('#website-graph canvas')).to_be_visible()
    expect(page.locator('#graph-status')).to_have_text('2 articles · 0 read-before links')
