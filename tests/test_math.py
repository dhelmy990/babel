import pytest

from study.markdown import prepare_article


@pytest.mark.parametrize('opening,closing', [('$$', '$$'), (r'\[', r'\]')])
def test_display_math_preserves_latex_and_is_a_separate_block(opening, closing):
    latex = r'\begin{aligned}a_1 &= b^2 \\ c &= \frac{1}{2}\end{aligned}'
    prepared = prepare_article(f'Before.\n\n{opening}\n{latex}\n{closing}\n\nAfter.', {})
    assert '<div data-type="block-math">' in prepared.html
    assert latex.replace('&', '&amp;') in prepared.html
    assert '<p>After.</p>' in prepared.html


@pytest.mark.parametrize('opening,closing', [('$', '$'), (r'\(', r'\)')])
def test_inline_math_keeps_markdown_characters_literal(opening, closing):
    prepared = prepare_article(f'The value is {opening}a_1 * b_2{closing}.', {})
    assert '<span data-type="inline-math">a_1 * b_2</span>' in prepared.html


def test_math_leaves_code_currency_and_escaped_dollars_alone():
    source = 'Costs $5 and $10. Escaped \\$x\\$.\n\n`$x$`\n\n```latex\n$$\nx^2\n$$\n```'
    prepared = prepare_article(source, {})
    assert 'data-type=' not in prepared.html
    assert '<code>$x$</code>' in prepared.html
    assert 'Costs $5 and $10.' in prepared.html


def test_math_source_is_html_escaped():
    prepared = prepare_article('$$\n<script>alert(1)</script>\n$$', {})
    assert '<script>' not in prepared.html
    assert '&lt;script&gt;' in prepared.html


@pytest.mark.django_db
def test_markdown_file_upload_renders_math_in_preview_and_publication(client, publisher):
    from uuid import uuid4
    from django.core.files.uploadedfile import SimpleUploadedFile
    from study.models import Article

    client.force_login(publisher)
    for endpoint in ('/api/articles/preview', '/api/articles'):
        response = client.post(endpoint, {
            'title': 'Uploaded math', 'color': '#1a5276', 'submission_id': str(uuid4()),
            'markdown_file': SimpleUploadedFile('equations.md', b'$$\nE = mc^2\n$$'),
        })
        assert response.status_code in (200, 201)
        html = response.json().get('html') or Article.objects.get(slug='uploaded-math').rendered_html
        assert '<div data-type="block-math">E = mc^2</div>' in html


@pytest.mark.parametrize('source', [r'\[x\] trailing prose', r'\[x\] (explanation)', r'$$x$$ trailing prose'])
def test_display_delimiters_never_discard_trailing_prose(source):
    html = prepare_article(source, {}).html
    assert source.split('] ', 1)[-1].split('$$ ', 1)[-1] in html


@pytest.mark.parametrize('delimiters', [('$$', '$$'), (r'\[', r'\]')])
def test_display_math_interrupts_paragraph_without_blank_lines(delimiters):
    opening, closing = delimiters
    html = prepare_article(f'Before\n{opening}x{closing}\nAfter', {}).html
    assert '<div data-type="block-math">x</div>' in html
    assert '<p>After</p>' in html


def test_quoted_display_math_uses_logical_lines():
    html = prepare_article('> $$\n> x^2\n> $$', {}).html
    assert '<div data-type="block-math">x^2</div>' in html


def test_unclosed_math_in_list_does_not_consume_following_paragraph():
    html = prepare_article('- $$\n\nOutside\n\n$$', {}).html
    assert '<p>Outside</p>' in html
    assert 'data-type="block-math"' not in html
