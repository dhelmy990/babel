import pytest


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
