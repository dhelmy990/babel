import pytest


def test_title_context_preserves_a_distinct_first_body_heading():
    from study.markdown import prepare_article

    prepared = prepare_article("# First section\n\nBody.", {}, titles=("Article title",))
    assert "<h1>First section</h1>" in prepared.html


def test_sources_heading_does_not_look_like_a_suppressed_body_title():
    from study.markdown import prepare_article, suppressed_title

    markdown = "# Opening\n\nBody.\n\n## Sources\n\n> # Quoted title\n\n[Reference](https://example.com)"
    prepared = prepare_article(markdown, {}, titles=("Article title",))
    assert suppressed_title(markdown, prepared.html) is None


@pytest.mark.parametrize("title", ["Article title", "Previous title"])
def test_title_context_suppresses_only_matching_legacy_titles(title):
    from study.markdown import prepare_article

    prepared = prepare_article(f"# {title}\n\n# First section\n\nBody.", {}, titles=("Article title", "Previous title"))
    assert f"<h1>{title}</h1>" not in prepared.html
    assert "<h1>First section</h1>" in prepared.html


def test_image_alt_text_uses_parsed_text_instead_of_markdown_escapes():
    from io import BytesIO
    from PIL import Image
    from study.markdown import prepare_article

    image = BytesIO()
    Image.new("RGB", (1, 1)).save(image, "PNG")
    prepared = prepare_article(r"![A \[caption\]](image.png)", {"image.png": image.getvalue()})
    assert 'alt="A [caption]"' in prepared.html


def test_prepare_article_rejects_a_missing_image_without_parsing_html():
    from study.markdown import prepare_article

    with pytest.raises(ValueError, match="Missing image"):
        prepare_article("# Title\n\n![diagram](images/missing.png)", {})


def test_prepare_article_strips_raw_html_and_extracts_sources():
    from study.markdown import prepare_article

    prepared = prepare_article(
        "# Title\n\nFirst paragraph.\n\n<script>alert(1)</script>\n\n"
        "## Sources\n\n[Reference](https://example.com/reference)",
        {},
    )

    assert "<script>" not in prepared.html
    assert "First paragraph." in prepared.excerpt
    assert prepared.sources == (("Reference", "https://example.com/reference"),)


def test_final_sources_collect_each_link_with_its_own_label():
    from study.markdown import prepare_article

    prepared = prepare_article(
        "# Title\n\nParagraph.\n\n## Sources\n\n[First](https://one.example) · [Second](https://two.example)",
        {},
    )

    assert prepared.sources == (("First", "https://one.example"), ("Second", "https://two.example"))


def test_normalizes_dot_and_percent_encoded_image_names_for_rendering():
    from io import BytesIO
    from PIL import Image
    from study.markdown import prepare_article, render_article

    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    prepared = prepare_article(
        "![one](./images/x.png)\n\n![two](<images/my pic.png>)",
        {"images/x.png": output.getvalue(), "images/my pic.png": output.getvalue()},
    )

    rendered = render_article(prepared, {"images/x.png": "/assets/one", "images/my pic.png": "/assets/two"})
    assert 'src="/assets/one"' in rendered
    assert 'src="/assets/two"' in rendered


def test_nested_sources_heading_remains_article_body():
    from study.markdown import prepare_article

    prepared = prepare_article("Body\n\n> ## Sources\n> [Ref](https://example.com)", {})

    assert prepared.sources == ()
    assert "blockquote" in prepared.html
    assert "Ref" in prepared.html


@pytest.mark.parametrize("markdown", [
    "# Code\n\n```text\n[example](javascript:alert(1))\n```",
    "# Code\n\nUse `[example](javascript:alert(1))` literally.",
])
def test_literal_code_with_executable_link_syntax_is_not_rejected(markdown):
    from study.markdown import prepare_article

    prepared = prepare_article(markdown, {})

    assert "javascript:alert(1)" in prepared.html


def test_sources_must_be_a_final_heading_and_keep_each_link_label():
    from study.markdown import prepare_article

    prepared = prepare_article(
        "# Title\n\nFirst **body** paragraph.\n\n## Sources\n\n"
        "[One](https://one.example) and [Two](https://two.example)\n\n## Later\n\nBody",
        {},
    )

    assert prepared.sources == ()
    assert "Sources" in prepared.html
    assert prepared.excerpt == "First body paragraph."


@pytest.mark.django_db
def test_images_with_special_logical_names_receive_a_private_asset_url(publisher, tmp_path, settings):
    from io import BytesIO
    from uuid import uuid4
    from PIL import Image
    from study.services.content import publish_article

    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    article = publish_article(
        publisher, title="Special", color="#1a5276", markdown="# Special\n\n![x](images/a&b.png)",
        images={"images/a&b.png": output.getvalue()}, submission_id=uuid4(),
    )

    assert f'/assets/{article.assets.get().pk}' in article.rendered_html
    assert "asset:" not in article.rendered_html


@pytest.mark.parametrize("markdown", ["[bad](javascript:alert(1))", "![remote](https://example.com/image.png)", "![escape](../image.png)"])
def test_prepare_article_rejects_unsafe_links_and_image_paths(markdown):
    from study.markdown import prepare_article

    with pytest.raises(ValueError):
        prepare_article(markdown, {})


@pytest.mark.parametrize("definition_before_sources", [True, False])
def test_reference_links_keep_document_wide_definitions_across_sources(definition_before_sources):
    from study.markdown import prepare_article

    definition = "[book]: https://example.com/book\n"
    markdown = (
        "# References\n\n" + (definition if definition_before_sources else "") +
        "Read [the book][book].\n\n## Sources\n\n[Book][book]\n\n" +
        ("" if definition_before_sources else definition)
    )
    prepared = prepare_article(markdown, {})

    assert 'href="https://example.com/book"' in prepared.html
    assert prepared.sources == (("Book", "https://example.com/book"),)


def test_reference_image_definition_after_sources_renders_asset_and_still_requires_upload():
    from io import BytesIO
    from PIL import Image
    from study.markdown import prepare_article, render_article

    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    markdown = "# Image\n\n![diagram][diagram]\n\n## Sources\n\n[Reference](https://example.com)\n\n[diagram]: images/x.png"
    prepared = prepare_article(markdown, {"images/x.png": output.getvalue()})
    assert 'src="/assets/diagram"' in render_article(prepared, {"images/x.png": "/assets/diagram"})
    with pytest.raises(ValueError, match="Missing image"):
        prepare_article(markdown, {})


def test_sources_heading_inside_a_code_fence_remains_body_content():
    from study.markdown import prepare_article

    prepared = prepare_article("# Code\n\n```markdown\n## Sources\n[Ref](https://example.com)\n```", {})

    assert prepared.sources == ()
    assert "## Sources" in prepared.html
