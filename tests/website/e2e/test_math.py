import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.django_db(transaction=True)

SOURCE = r'''A formula $a_1 + b_2$ and \(c^2\).

$$
\begin{aligned}2x + 3 &= 11 \\ x &= 4\end{aligned}
$$

\[
E = mc^2
\]

`$literal$`

```latex
$$not rendered$$
```

Last paragraph.
'''


def test_import_math_edit_preview_publish_reopen(publisher_page, live_server):
    page = publisher_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(f'{live_server.url}/publish')
    page.get_by_label('Title', exact=True).fill('Math article')
    page.get_by_label('Import Markdown', exact=True).set_input_files(
        {'name': 'equations.md', 'mimeType': 'text/markdown', 'buffer': SOURCE.encode()})
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    expect(editor.locator('[data-type="block-math"] .katex-display')).to_have_count(2)
    expect(editor.locator('[data-type="inline-math"] .katex')).to_have_count(2)
    expect(editor.locator('pre .katex, code .katex')).to_have_count(0)
    editor.get_by_text('Last paragraph.', exact=True).click()
    editor.press('End', delay=50)
    editor.press_sequentially(' Edited.')
    page.get_by_role('button', name='Markdown', exact=True).click()
    source = page.get_by_role('textbox', name='Markdown source', exact=True).input_value()
    assert r'\begin{aligned}2x + 3 &= 11 \\ x &= 4\end{aligned}' in source
    assert r'\(a_1 + b_2\)' in source
    page.get_by_role('button', name='Preview', exact=True).click()
    expect(page.locator('[data-preview-body] .katex-display')).to_have_count(2)
    expect(page.locator('[data-preview-body] code .katex')).to_have_count(0)
    page.get_by_role('button', name='Publish', exact=True).click()
    page.wait_for_url('**/math-article')
    expect(page.locator('.article-body .katex-display')).to_have_count(2)
    expect(page.locator('.article-body')).to_contain_text('Edited.')
    page.get_by_role('button', name='Admin mode', exact=True).click()
    page.get_by_role('link', name='Edit article', exact=True).click()
    expect(page.locator('.tiptap [data-type="block-math"] .katex-display')).to_have_count(2)
    page.locator('.tiptap [data-type="block-math"]').last.click()
    page.get_by_role('textbox', name='LaTeX equation', exact=True).fill(r'E = mc^3')
    page.get_by_role('button', name='Apply equation', exact=True).click()
    page.get_by_role('button', name='Save', exact=True).click()
    page.wait_for_url('**/math-article')
    expect(page.locator('.article-body annotation').last).to_have_text('E = mc^3')
    assert errors == []


def paste_text(editor, text):
    editor.evaluate('''(element, text) => {
      const clipboardData = new DataTransfer();
      clipboardData.setData('text/plain', text);
      element.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true}));
    }''', text)


def test_paste_math_and_insert_equation_with_keyboard(publisher_page, live_server):
    page = publisher_page
    page.goto(f'{live_server.url}/publish')
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    editor.click()
    paste_text(editor, SOURCE)
    expect(editor.locator('.katex-display')).to_have_count(2)
    page.get_by_role('button', name='Insert equation', exact=True).click()
    page.get_by_role('textbox', name='LaTeX equation', exact=True).fill(r'\frac{a}{b}')
    page.get_by_role('button', name='Apply equation', exact=True).click()
    expect(editor.locator('.katex-display')).to_have_count(3)
    # A code block is still a literal code block when math is pasted into it.
    editor.locator('pre').click()
    editor.press('End', delay=50)
    paste_text(editor, '$$x^2$$')
    expect(editor.locator('pre .katex')).to_have_count(0)
    expect(editor.locator('pre')).to_contain_text('$$x^2$$')


def test_invalid_math_and_long_equations_do_not_break_mobile_layout(publisher_page, live_server):
    page = publisher_page
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(f'{live_server.url}/publish')
    page.get_by_label('Title', exact=True).fill('Wide equations')
    page.get_by_role('button', name='Markdown', exact=True).click()
    page.get_by_role('textbox', name='Markdown source', exact=True).fill(
        '$$\n' + ' + '.join(['x^2'] * 60) + '\n$$\n\n$$\n\\frac{\n$$')
    page.get_by_role('button', name='Write', exact=True).click()
    expect(page.locator('.tiptap .katex-error')).to_have_count(1)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Preview', exact=True).click()
    expect(page.locator('[data-preview-body] .katex-error')).to_have_count(1)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Publish', exact=True).click()
    page.wait_for_url('**/wide-equations')
    expect(page.locator('.article-body .katex-error')).to_have_count(1)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    equation = page.locator('.article-body [data-type="block-math"]').first
    assert equation.evaluate('e => e.scrollWidth > e.clientWidth')
    page.screenshot(path='/tmp/babel-math-mobile.png', full_page=True)


def test_bad_or_cancelled_import_preserves_draft(publisher_page, live_server):
    page = publisher_page
    page.goto(f'{live_server.url}/publish')
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    editor.fill('Keep my draft.')
    upload = page.get_by_label('Import Markdown', exact=True)
    page.once('dialog', lambda dialog: dialog.dismiss())
    upload.set_input_files({'name': 'new.md', 'mimeType': 'text/markdown', 'buffer': SOURCE.encode()})
    expect(editor).to_have_text('Keep my draft.')
    page.once('dialog', lambda dialog: dialog.accept())
    upload.set_input_files({'name': 'bad.md', 'mimeType': 'text/markdown', 'buffer': b'\xff'})
    expect(page.get_by_role('status').filter(has_text='Could not import Markdown')).to_be_visible()
    expect(editor).to_have_text('Keep my draft.')


def test_visual_currency_and_code_survive_mode_switch(publisher_page, live_server):
    page = publisher_page
    page.goto(f'{live_server.url}/publish')
    page.get_by_role('button', name='Markdown', exact=True).click()
    page.get_by_role('textbox', name='Markdown source', exact=True).fill(
        'Costs $5 and $10. Escaped \\$x\\$.\n\n`$code$`\n\nEnd.')
    page.get_by_role('button', name='Write', exact=True).click()
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    expect(editor.locator('.katex')).to_have_count(0)
    editor.get_by_text('End.', exact=True).click()
    editor.press('End', delay=50)
    editor.press_sequentially(' Edited.')
    page.get_by_role('button', name='Markdown', exact=True).click()
    text = page.get_by_role('textbox', name='Markdown source', exact=True).input_value()
    assert '\\$x\\$' in text


def test_typing_dollars_uses_inline_and_centered_math(publisher_page, live_server):
    page = publisher_page
    page.goto(f'{live_server.url}/publish')
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    editor.click()
    editor.press_sequentially('$$x^2$$')
    expect(editor.locator('[data-type="block-math"] .katex-display')).to_have_count(1)
    editor.press('Control+End', delay=50)
    editor.press('Enter')
    editor.press_sequentially('Inline $y^2$')
    expect(editor.locator('[data-type="inline-math"]')).to_have_count(1)


def test_inline_math_next_to_digits_survives_visual_edit(publisher_page, live_server):
    page = publisher_page
    page.goto(f'{live_server.url}/publish')
    page.get_by_label('Title', exact=True).fill('Adjacent math')
    page.get_by_role('button', name='Markdown', exact=True).click()
    page.get_by_role('textbox', name='Markdown source', exact=True).fill(r'2\(x\) and \(y\)2.' + '\n\nEnd.')
    page.get_by_role('button', name='Write', exact=True).click()
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    expect(editor.locator('[data-type="inline-math"]')).to_have_count(2)
    editor.get_by_text('End.', exact=True).click()
    editor.press('End', delay=50)
    editor.press_sequentially(' Edit.')
    page.get_by_role('button', name='Preview', exact=True).click()
    expect(page.locator('[data-preview-body] .katex')).to_have_count(2)


@pytest.mark.parametrize('source,blocks,inlines', [
    ('Before\n$$x$$\nAfter', 1, 0),
    (r'$$x\$$$', 1, 0),
    (r'$x$$y$', 0, 2),
    (r'\[x\] trailing prose', 0, 0),
])
def test_math_parser_edge_cases_agree_in_editor_and_preview(publisher_page, live_server, source, blocks, inlines):
    page = publisher_page
    page.goto(f'{live_server.url}/publish')
    page.get_by_label('Title', exact=True).fill('Math parsing')
    page.get_by_role('button', name='Markdown', exact=True).click()
    page.get_by_role('textbox', name='Markdown source', exact=True).fill(source)
    page.get_by_role('button', name='Write', exact=True).click()
    editor = page.get_by_role('textbox', name='Article body', exact=True)
    expect(editor.locator('[data-type="block-math"]')).to_have_count(blocks)
    expect(editor.locator('[data-type="inline-math"]')).to_have_count(inlines)
    page.get_by_role('button', name='Preview', exact=True).click()
    expect(page.locator('#publish-status')).to_have_text('Preview ready.')
    expect(page.locator('[data-preview-body] [data-type="block-math"]')).to_have_count(blocks)
    expect(page.locator('[data-preview-body] [data-type="inline-math"]')).to_have_count(inlines)
    if 'trailing prose' in source:
        expect(page.locator('[data-preview-body]')).to_contain_text('trailing prose')
    if r'\$' in source:
        expect(editor.locator('annotation')).to_have_text(r'x\$')
        expect(page.locator('[data-preview-body] annotation')).to_have_text(r'x\$')


def test_unexpected_math_error_does_not_stop_remaining_equations(publisher_page, live_server):
    page = publisher_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(f'{live_server.url}/publish')
    page.get_by_label('Title', exact=True).fill('Deep nesting')
    page.get_by_role('button', name='Markdown', exact=True).click()
    page.get_by_role('textbox', name='Markdown source', exact=True).fill(
        '$$\n' + '{' * 20000 + 'x' + '}' * 20000 + '\n$$\n\n$$y$$')
    page.get_by_role('button', name='Preview', exact=True).click()
    expect(page.locator('[data-preview-body] .math-error')).to_have_count(1)
    expect(page.locator('[data-preview-body] .katex')).to_have_count(1)
    assert errors == []
