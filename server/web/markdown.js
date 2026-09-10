function renderMarkdown(text) {
  const html = marked.parse(String(text || ""), {gfm: true, breaks: true, async: false});
  const fragment = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ["p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "s", "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead", "tbody", "tr", "th", "td", "a", "input"],
    ALLOWED_ATTR: ["href", "title", "start", "align", "type", "checked", "disabled"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    RETURN_DOM_FRAGMENT: true
  });
  fragment.querySelectorAll("a").forEach(link => {
    const href = link.getAttribute("href") || "";
    if (!/^https?:\/\//i.test(href)) link.removeAttribute("href");
    else { link.target = "_blank"; link.rel = "noopener noreferrer"; }
  });
  fragment.querySelectorAll("input").forEach(input => {
    if (input.type !== "checkbox") input.remove();
    else input.disabled = true;
  });
  fragment.querySelectorAll("table").forEach(table => {
    const wrapper = document.createElement("div");
    wrapper.className = "markdown-table";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "回答表格");
    table.replaceWith(wrapper);
    wrapper.appendChild(table);
  });
  const container = document.createElement("div");
  container.appendChild(fragment);
  return container.innerHTML;
}
