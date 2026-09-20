from study.forms import ArticleSubmissionForm


def test_markdown_text_preserves_indented_code_whitespace():
    form = ArticleSubmissionForm(data={"title": "Code", "color": "#1a5276", "markdown": "    literal"})

    assert form.is_valid()
    assert form.cleaned_data["markdown"] == "    literal"
