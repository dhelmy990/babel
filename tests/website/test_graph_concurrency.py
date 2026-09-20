from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection, connections

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('multi_edge', [False, True])
def test_concurrent_cycle_writes_serialize_on_postgres(publisher, article_factory, multi_edge):
    from django.contrib.auth import get_user_model
    from study.models import Edge
    from study.services.graph import CycleError, add_edge
    assert connection.vendor == 'postgresql'
    a, b, c, d = [article_factory(title) for title in ('A', 'B', 'C', 'D')]
    if multi_edge:
        add_edge(publisher, a.pk, b.pk)
        add_edge(publisher, c.pk, d.pk)
        pairs = [(b.pk, c.pk), (d.pk, a.pk)]
    else:
        pairs = [(a.pk, b.pk), (b.pk, a.pk)]
    barrier = Barrier(2)

    def write(pair):
        connections.close_all()
        try:
            user = get_user_model().objects.get(pk=publisher.pk)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            barrier.wait(timeout=10)
            try:
                add_edge(user, *pair)
                return pid, 'committed'
            except CycleError:
                return pid, 'cycle'
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, pairs))
    assert len({pid for pid, _ in results}) == 2
    assert sorted(outcome for _, outcome in results) == ['committed', 'cycle']
    assert Edge.objects.count() == (3 if multi_edge else 1)
