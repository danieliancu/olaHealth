// Staff > Learn: a small "Word-like" editor for the article body.
// The page keeps saving the same safe text format (paragraphs, "## " headings, "- " / "1. "
// lists, **bold**, *italic*, [text](url)); this editor only shows it formatted while typing.
// Progressive enhancement: without JavaScript the plain <textarea> stays.

const TOOLS = [
  ["bold", "B", "Bold (Ctrl+B)"],
  ["italic", "I", "Italic (Ctrl+I)"],
  ["h2", "H2", "Heading"],
  ["h3", "H3", "Subheading"],
  ["p", "¶", "Normal text"],
  ["ul", "• List", "Bullet list"],
  ["ol", "1. List", "Numbered list"],
  ["link", "Link", "Link (a /path or https:// address)"],
  ["clear", "Clear", "Remove bold, italic and links from the selection"],
];

function inline(node) {
  let out = "";
  for (const child of node.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) {
      out += child.textContent.replace(/\s+/g, " ");
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;
    const tag = child.tagName;
    const text = inline(child);
    if (tag === "BR") out += " ";
    else if ((tag === "B" || tag === "STRONG") && text.trim()) out += `**${text.trim()}**`;
    else if ((tag === "I" || tag === "EM") && text.trim()) out += `*${text.trim()}*`;
    else if (tag === "A") {
      const href = child.getAttribute("href") || "";
      out += /^(\/|https:\/\/)/.test(href) ? `[${text.trim()}](${href})` : text;
    } else out += text;
  }
  return out;
}

function toMarkup(root) {
  const blocks = [];
  const add = (s) => {
    const t = s.replace(/[ \t]+/g, " ").trim();
    if (t) blocks.push(t);
  };
  for (const node of root.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      add(node.textContent);
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const tag = node.tagName;
    if (tag === "H1" || tag === "H2") add(`## ${inline(node).trim()}`);
    else if (tag === "H3" || tag === "H4") add(`### ${inline(node).trim()}`);
    else if (tag === "UL" || tag === "OL") {
      const items = [...node.querySelectorAll(":scope > li")].map((li, i) =>
        `${tag === "OL" ? `${i + 1}.` : "-"} ${inline(li).trim()}`);
      if (items.length) blocks.push(items.join("\n"));
    } else add(inline(node));
  }
  return blocks.join("\n\n");
}

function enhance(textarea) {
  const source = document.getElementById(textarea.dataset.richHtml);
  const wrap = document.createElement("div");
  wrap.className = "rich";
  const bar = document.createElement("div");
  bar.className = "rich-bar";
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Formatting");
  const area = document.createElement("div");
  area.className = "rich-area";
  area.contentEditable = "true";
  area.setAttribute("role", "textbox");
  area.setAttribute("aria-multiline", "true");
  area.setAttribute("aria-label", "Article body");
  area.innerHTML = source ? source.innerHTML : "";
  if (!area.innerHTML.trim()) area.innerHTML = "<p><br></p>";

  const sync = () => { textarea.value = toMarkup(area); };
  const run = (cmd) => {
    area.focus();
    if (cmd === "bold" || cmd === "italic") document.execCommand(cmd);
    else if (cmd === "h2" || cmd === "h3" || cmd === "p") document.execCommand("formatBlock", false, cmd.toUpperCase());
    else if (cmd === "ul") document.execCommand("insertUnorderedList");
    else if (cmd === "ol") document.execCommand("insertOrderedList");
    else if (cmd === "clear") { document.execCommand("removeFormat"); document.execCommand("unlink"); }
    else if (cmd === "link") {
      const url = window.prompt("Link address (/learn/… or https://…)", "/");
      if (url && /^(\/|https:\/\/)/.test(url)) document.execCommand("createLink", false, url);
    }
    sync();
  };
  for (const [cmd, label, title] of TOOLS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `rich-btn rich-${cmd}`;
    b.textContent = label;
    b.title = title;
    b.addEventListener("mousedown", (e) => e.preventDefault()); // keep the selection
    b.addEventListener("click", () => run(cmd));
    bar.append(b);
  }
  // Paste as plain text: no foreign styles or markup sneak in.
  area.addEventListener("paste", (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text/plain");
    document.execCommand("insertText", false, text);
  });
  area.addEventListener("input", sync);
  textarea.form?.addEventListener("submit", sync);
  document.execCommand("defaultParagraphSeparator", false, "p");

  wrap.append(bar, area);
  textarea.hidden = true;
  textarea.after(wrap);
}

document.querySelectorAll("textarea[data-rich]").forEach(enhance);
