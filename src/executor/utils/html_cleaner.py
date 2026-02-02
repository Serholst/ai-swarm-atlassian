"""HTML cleaning utilities for Confluence and Jira content.

Purpose: Prevent "garbage in, garbage out" by cleaning HTML/rich text to Markdown.
"""

import re
from bs4 import BeautifulSoup
import html2text


def clean_confluence_html(html_content: str) -> str:
    """
    Clean Confluence HTML storage format to clean Markdown.

    Confluence uses custom HTML tags and macros. This function:
    1. Parses HTML with BeautifulSoup
    2. Removes Confluence-specific macros
    3. Converts to Markdown using html2text
    4. Cleans up whitespace and formatting

    Args:
        html_content: Raw HTML from Confluence storage format

    Returns:
        Clean Markdown text
    """
    if not html_content or html_content.strip() == "":
        return ""

    # Parse HTML
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove Confluence-specific elements
    _remove_confluence_macros(soup)
    _clean_confluence_tables(soup)
    _clean_confluence_links(soup)

    # Convert to HTML string
    cleaned_html = str(soup)

    # Convert to Markdown
    markdown = _html_to_markdown(cleaned_html)

    # Final cleanup
    markdown = _cleanup_markdown(markdown)

    return markdown


def clean_jira_html(html_content: str) -> str:
    """
    Clean Jira HTML/ADF to Markdown.

    Jira uses Atlassian Document Format (ADF) which is JSON-based,
    but sometimes returns HTML for comments.

    Args:
        html_content: Raw HTML from Jira

    Returns:
        Clean Markdown text
    """
    if not html_content or html_content.strip() == "":
        return ""

    # Parse HTML
    soup = BeautifulSoup(html_content, "html.parser")

    # Convert to Markdown
    markdown = _html_to_markdown(str(soup))

    # Cleanup
    markdown = _cleanup_markdown(markdown)

    return markdown


def _remove_confluence_macros(soup: BeautifulSoup) -> None:
    """
    Remove Confluence macros (structured-macro tags).

    Macros like {panel}, {code}, {info} are converted to simpler equivalents.
    """
    # Remove all ac:structured-macro tags except code blocks
    for macro in soup.find_all("ac:structured-macro"):
        macro_name = macro.get("ac:name", "")

        if macro_name == "code":
            # Convert code macro to <pre><code>
            code_body = macro.find("ac:plain-text-body")
            if code_body:
                code_tag = soup.new_tag("pre")
                code_tag.string = code_body.get_text()
                macro.replace_with(code_tag)
        elif macro_name in ["panel", "info", "note", "warning"]:
            # Convert panel to blockquote
            rich_text = macro.find("ac:rich-text-body")
            if rich_text:
                blockquote = soup.new_tag("blockquote")
                blockquote.string = rich_text.get_text()
                macro.replace_with(blockquote)
        else:
            # Remove unknown macros
            macro.decompose()

    # Remove other Confluence-specific tags
    for tag in soup.find_all(["ac:parameter", "ac:link", "ac:image"]):
        tag.decompose()


def _clean_confluence_tables(soup: BeautifulSoup) -> None:
    """Clean Confluence table formatting."""
    # Tables are generally OK in Markdown, but clean up attributes
    for table in soup.find_all("table"):
        # Remove all attributes except basic structure
        table.attrs = {}


def _clean_confluence_links(soup: BeautifulSoup) -> None:
    """Convert Confluence links to standard <a> tags."""
    for link in soup.find_all("ri:page"):
        # Confluence internal page link
        title = link.get("ri:content-title", "")
        if title:
            a_tag = soup.new_tag("a", href=f"#{title}")
            a_tag.string = title
            link.replace_with(a_tag)


def _html_to_markdown(html: str) -> str:
    """
    Convert HTML to Markdown using html2text.

    Configured for clean, readable output.
    """
    h = html2text.HTML2Text()

    # Configuration
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_emphasis = False
    h.body_width = 0  # Don't wrap lines
    h.unicode_snob = True
    h.skip_internal_links = True

    return h.handle(html)


def _cleanup_markdown(markdown: str) -> str:
    """
    Final cleanup of Markdown text.

    - Remove excessive newlines
    - Trim whitespace
    - Normalize list formatting
    """
    # Remove excessive newlines (more than 2)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    # Remove trailing whitespace from lines
    markdown = "\n".join(line.rstrip() for line in markdown.split("\n"))

    # Normalize list indentation
    markdown = re.sub(r"^(\s*)-\s+", r"\1- ", markdown, flags=re.MULTILINE)

    # Trim leading/trailing whitespace
    markdown = markdown.strip()

    return markdown


def extract_confluence_metadata(html_content: str) -> dict[str, str]:
    """
    Extract metadata from Confluence page HTML.

    Useful for extracting structured data like:
    - Page title
    - Section headings
    - Key-value pairs from tables

    Returns:
        Dictionary of metadata
    """
    soup = BeautifulSoup(html_content, "html.parser")
    metadata = {}

    # Extract headings
    headings = []
    for i in range(1, 7):
        for heading in soup.find_all(f"h{i}"):
            headings.append((i, heading.get_text().strip()))
    metadata["headings"] = headings

    # Extract tables as structured data
    tables = []
    for table in soup.find_all("table"):
        table_data = []
        for row in table.find_all("tr"):
            cells = [cell.get_text().strip() for cell in row.find_all(["td", "th"])]
            table_data.append(cells)
        tables.append(table_data)
    metadata["tables"] = tables

    return metadata
